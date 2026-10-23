# W2 切片：运行中纠偏与后续消息

日期：2026-10-22。状态：工程实现与确定性回归完成；真实模型与真实终端使用验收未做，W2 第一条验收不勾选。

## 背景

W2 要求“运行中可以提出一条纠偏……后续模型输入和工具动作采用新约束”。此前 TUI 运行中禁用输入，Ink 只保留草稿、Enter 不发送；Agent 也没有接收外部纠偏的通道。本切片新增两类消息：

| | Steering（纠偏） | Follow-up（后续消息） |
|---|---|---|
| 触发 | 运行中按 Enter 提交普通文本 | 运行中输入 `/followup <内容>` |
| 生效时机 | 当前任务下一次模型请求前 | 当前任务结束后自动开启下一轮 |
| 进入会话/历史 | 是，`role=user`、`name=steering` | 是，作为下一轮普通 user 任务 |
| 跨端协议 | `steer` 请求帧 + `ack` | 界面本地队列，不发帧 |
| 打断当前请求/工具 | 不打断，在下一步边界生效 | — |

## Agent 行为

- 新增 `Agent.steer(text) -> bool`：把纠偏追加到当前 run 的队列；没有运行中的任务时返回 `False`。队列是 run 作用域（`_RunContext.pending_steering`），不跨任务复用。
- `_prepare_step` 在停止条件检查之后、上下文压缩与请求组装之前消费队列：每条纠偏通过 `_record_message` 写成真实的 `user` 消息，因此进入模型请求、历史与 `on_message` 持久化，并可被后续压缩/摘要处理；同时记录 trace 事件 `steering_applied`（`step`、`chars`）。
- 模型回复文本、即将以 `done` 收尾时，如果队列里还有未消费的纠偏，会再走一步而不是直接结束（`_decide_after_model`），避免“刚提交就结束”的静默丢失。取消、预算暂停与错误仍按原优先级立即结束；此时未消费的纠偏随本轮队列丢弃，界面提示“在下一次模型请求生效”不保证在停止轮兑现。
- 纠偏不改变工具审批、风险分级、预算和上下文上限；它只是一条用户消息，不能绕过权限。

## 顺带修复：`steps` 口径

请求发出前因 `max_run_seconds` 停止时，`AgentResult.steps` 应报已完成步数。`_call_model` 第二次时间检查此前已用 `step - 1`，第一次检查误报 `step`；本次统一为 `step - 1`，并在 `tests/test_agent.py::test_max_run_seconds_stops` 增加 `assert result.steps == 0`。`max_run_seconds` 停止时 `stop_reason` 仍为空字符串（原有行为），不在本切片修改。

## stdio 协议

协议版本仍为 `v: 1`，新增请求帧与能力通告；旧前端不发送该帧即可继续工作。

| 请求 | 字段 | 行为 |
|---|---|---|
| `steer` | `id`, `target`, `text` | `target` 必须是当前活跃 run 且未在停止；文本非空。成功回 `ack action=steer`，失败为 `run_mismatch` 或 `invalid_request` |

- `ready.capabilities` 增加 `"steer"`。
- 不在停止中的活跃 run 才接受纠偏；重复请求 ID、字段缺失、非法 JSON 等仍按原规则拒绝。
- `ack` 只表示后端已接收纠偏，表示副作用或“已应用”；实际应用在下一步由 trace `steering_applied` 记录。

## 界面

Textual（已于 2026-10-23 退役；以下为当时实现记录）：运行中不再禁用输入框，Enter 提交纠偏，`/followup` 排队并在终态后继续；该入口已删除，行为以 Ink 为准。

Ink：

- 运行中 Composer 保持可编辑可提交；Enter 发 `steer` 帧，成功提示以对应 `ack` 为准，同请求 id 的 `error` 用 notice 报失败，不把控制请求错误标成整轮“运行异常”。命令执行中（`activeCommand` 非 `resume`）不发纠偏、不排队，提示结束后再发送。
- `/followup` 为前端本地路由：本轮正常结束（`done` 且非 `cancelled`）后按 FIFO 自动 `run`；取消时清空队列并提示丢弃数量，不把“停止”改写成“继续”；自动续跑不清空用户正在编辑的草稿；`closed` 或退出时清空队列。
- 纠偏被后端消费时，stdio 发送 `notice`（“已应用纠偏：…”），Ink 与 Textual 都能实时看到；`cancelled` / `stopped` 的机器状态串不再作为 Agent 回复写进 transcript，只保留已流出的正文；`/help`、Tab 补全与 CLI 帮助共用同一份命令清单。
- 只实现前端队列，不在后端保存；断线或退出后队列丢失，不做自动重放。

## 边界与未做

- 不打断正在进行的模型请求或工具调用：纠偏只保证“下一次模型输入”采用新约束。
- 停止/取消后不重放纠偏；取消后也不会自动启动已排队的后续消息（显式丢弃并提示）。
- 预算、审批与命令结果规则不变。
- Ink 的 follow-up 不进会话记录直到真正作为下一轮运行；队列是界面状态。
- 跨端真实联调只覆盖协议帧形状与 Agent 行为；未新增“真实 Python 子进程 + 运行中纠偏”的 Ink 集成测试。
- W2 的停止边界、审批等待/命令执行中停止与超时核对仍按里程碑单独验收。

## 验证

| 检查 | 结果 | 记录 |
|---|---|---|
| Python 全量回归 | **898 passed**（基线 889 + steering 5 + stdio 2 + TUI 2），80.02s | `.vibe/steering-full-tests.log` |
| 新增 steering 单测 | 5 passed | `tests/test_steering.py`；`.vibe/steering-tests.log` |
| stdio 协议测试 | 2 passed（帧、ack、错误目标、持久化） | `tests/test_stdio_parity.py` |
| Ink 回归（2026-10-23 修复后） | **194 项，193 passed，1 failed**；唯一失败为沙箱无 PTY 的 `interactive CLI enters and restores the alternate screen`（`OSError: out of pty devices`），改动前同样失败 | `.vibe/ink-fixes-tests.log` |
| Python 回归（2026-10-23 修复后） | **901 passed**（新增 recall 越界校验与 5 条控制流测试） | `.vibe/ink-fixes-python-tests.log` |
| Python 回归（Textual 退役后清理） | **820 passed**（含取消 run 工具计数、消息反序列化合并与原子写测试） | `.vibe/cleanup2-python-tests.log` |
| Ink 回归（清理后） | **196 项，195 passed，1 failed**；唯一失败仍是沙箱无 PTY 环境项 | `.vibe/cleanup2-ink-tests.log` |
| 离线预算分层 `./run_eval.sh check-budget` | **3/3 `[PASS]`**（fake 端到端，含压缩与工具执行） | `.vibe/steering-check-budget.log`、`.vibe/benchmarks/budget-checks/20261022-142815-145639/` |

确定性证据使用 fake provider：覆盖“下一请求才注入”“多条顺序”“收尾竞态再走一步”“会话/协议持久化”“无运行目标拒绝”。真实模型是否按新约束修改、真实终端输入体验另按 W2 验收记录。

## 后续

- 真实任务验收：运行中提出“保留公开接口，只改内部实现”，核对后续请求与工具动作。
- Ink 收到 `run_mismatch` 目前沿既有错误关联规则可能不显示；需要时补按请求 ID 关联的纠偏失败提示。
- follow-up 队列是否持久化到会话（跨重启保留）属于 W3 恢复范围，本切片不做。
