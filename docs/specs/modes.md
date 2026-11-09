# 任务模式 P0（chat/act，TaskRecord.mode）

状态：2026-11-06 P0 实现；2026-11-08 已补会话默认模式、当前运行提示与恢复，见文末。早期清单保留演进背景。
设计来源：[10 模式状态机](../design/long-horizon/10-mode-state-machine.md)。

## 已实现

- `app/runtime/modes.py`：`MODE_NAMES=(chat, act, plan, goal)`、`parse_mode`、`kind_for_mode`、`mode_for_kind`；未知 mode fail closed。
- `ModeProfile` 注册表（11 P0 起步）：
  - chat：无工具、`chat_only`、shared_readonly；
  - act：`*` 工具、`default`、shared_readonly；
  - goal：`*` 工具、worktree_write；
  - plan：只读工具 + `submit_plan`、`plan_only`、plan.review capability；
  - `resolve_profile`/`validate_profile_tools` 校验未知 mode/缺失工具。
- `TaskRuntime`：
  - `run.task` 支持 `kind=run`（chat/act）与 `kind=goal`（goal），`mode` 写入 `TaskRecord`；
  - 未指定 mode 时：goal 默认 `goal`，run 默认 `act`；
  - 未知 mode / 不匹配的 attach mode 拒绝；
  - task payload 增加 `mode`。
- stdio：
  - `ready.capabilities` 在任务运行时开启时增加 `modes`；
  - `ready.tasks` / `--tasks` / TUI `/task` 展示 task mode、plan_id、parent_id；
  - `mode=chat` 的 run 临时关闭 agent tools（provider 不收到 tools），run 结束后恢复；
  - `mode=act` 保持正常工具路径。
- TUI：
  - `/chat <任务>`、`/act <任务>`、`/plan <任务>` 本地命令，发送对应 mode 的 run task；已加入命令补全与说明；
  - `/plan` 无参数折叠展示当前会话最近的计划产物；`/plan all` 展开全部步骤；
  - `/explore <任务>` 走只读子 Agent（stdio command explore）。
  - 需要后端 `tasks` capability；运行中不能替换当前任务模式，下一次默认模式可按文末规则调整。
- stdio 启动 run 前解析 mode profile 并校验工具名，写 `mode_profile` 事件；
  `plan` 模式把 agent tools 临时替换为只读子集（`read_file/list_files/grep/git_status/git_diff`），run 结束后恢复。
- 测试：
  - modes：profile 完整性、chat/act/plan 工具与 capability、未知 mode/缺失工具；
  - TaskRuntime：run/chat、未知 mode、attach mode mismatch；
  - stdio：chat 的 provider `seen_tools=[]`、task meta mode；
  - TUI：`/chat` `/act` 提交的 task metadata。

## P0 时的后续项

其中会话 Plan、审批执行绑定已由下节及 [P3 验证记录](../../openspec/changes/implement-improve-workbench-ux-p3/verification.md)覆盖；不是当前未完成项的完整清单。

- budget/approval/isolation profile 的实际执行层强制（当前已强制 chat 无工具、plan 只读子集；budget/approval 仍只声明）；
- `/plan`、plan→act 批准转换（12/13）；
- goal 段内 plan mode；
- 入口记忆上次 mode（项目级/会话级）；
- mode 跨段切换策略与 UI；
- plan 审批面板与执行绑定；
- 非 TUI 入口的显式 mode flag（当前仅 run.task）。

## 会话级 plan mode（2026-11-08 补充）

- 新增 `app/runtime/plan_mode.py`：会话状态 `plan-mode.json` 持久化
  `active`（已生效）/`wanted`（运行中待生效）/`pending`（视图派生）。
- `/plan on|off|status` 与 `/plan <任务>` 可切换；普通 run 在会话计划模式开启时
  自动创建 `mode=plan` task（需要 `VIBE_TASK_RUNTIME`），显式 `task.mode` 优先。
- plan run 额外注入可配置 `plan_policy` 系统提示词，并在 `submit_plan` 成功后终止；
  `plan.review` 批准计划后自动关闭会话 plan mode。
- `mode` 仍是 TaskRecord 属性，不新增跨段切换；会话级开关只决定“下一条普通消息
  以哪种 mode 创建 task”，硬只读边界仍由 plan profile 的工具子集强制。

## 当前运行与下一次默认模式（2026-11-08）

- Ink 从当前运行的 `task_state.mode` 展示「本次：执行/仅对话/只读规划」，从权威 `plan_mode` 展示「下次」；字段缺失或未知时不猜测当前模式。一次性 `/act`、`/chat` 不退出会话 Plan。
- 启用任务运行时的后端声明 `plan-mode-control`：`/plan on|off|status` 使用独立请求，不替换运行 ID、审批对象或取消目标。运行中的选择保存为 pending，当前运行结束后生效；选择原默认值可撤回 pending。旧后端仍要求空闲。
- 前端等待后端状态确认，确认前不自行修改默认模式；超时、错误或断线保留输入并提示查询。晚到的旧请求回执不能覆盖新查询结果；等待工具审批时仍优先完成审批或停止。
- 重开会话会应用已保存的 pending 选择，但不启动任务。未发送草稿不跨进程保存；界面明确提示重新输入。若默认模式写入失败，下一条普通任务拒绝启动并保留输入，不能静默使用旧默认值。
- 已保存选择的恢复由文件状态和 stdio 测试验证；实际进程重开验证默认模式恢复且模型调用不增加。本轮未模拟真实断电，也未验证多连接共同修改模式。

证据：[P5 模式与恢复](../../openspec/changes/implement-improve-workbench-ux-p5/verification.md#当前运行默认模式与恢复2026-11-08)。
