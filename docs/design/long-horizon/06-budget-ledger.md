# 06 预算账本

> 状态：讨论稿（2026-11-02；2026-11-03 01–05 决策对齐见 §9，用户回答见 §10）；不是 spec，未承诺实现。
> 实现：P0 账本（budget.jsonl + TaskRuntime/stdio 接线 + 终态结算）已实现，见 [budget-ledger spec](../../specs/budget-ledger.md)；硬上限/预留/per-child 在 P1。
> 所属：长任务与后台任务 / 第 6 块（预算、成本与资源账本）
> 前置/相关：[01 任务运行时](01-task-runtime.md)、[02 后台进程监督](02-process-supervision.md)、[05 跨段目标状态](05-goal-state.md)、[16 子 Agent](16-subagent-integration.md)；相关规格：[practical-use-reliability](../../specs/practical-use-reliability.md)、[history-compression](../../specs/history-compression.md)、[goal-mode](../../specs/goal-mode.md)

## 1 问题与范围

现有预算是“一次 run”的：`max_run_tokens`、`max_run_seconds`、`max_run_cost_usd` 在 `Agent` 内强制执行（app/runtime/agent.py:743-746,810-816,1745-1795）。长任务由多个 run（段）组成，但跨段的累计目前只存在于 TUI 内存与 `goal.json`：`goal.ts` 累加 `totalTokens/totalCostUsd` 并在段结束时检查上限（tui/src/goal.ts:76-78,101-124）。Python 侧没有账本，后台 worker 也无从知道“这个目标已经花了多少”。

2026-11-03 已确认的方向是：Python `TaskRuntime` 为权威、任务统一为 `TaskRecord`（01 §9，D1），后台任务默认开启、每 task 一个 worker（02 §9，D2），目标权威文件为 `goal-record.json`（05 §9，D5）。本文按此设计账本；**硬上限的执行点在 TaskRuntime “开始下一任务/下一段前”，06 只提供账本与检查接口，不在账本内做策略裁决**。

本文讨论：**谁记账、按什么粒度记、哪些消耗目前漏记、如何结算终态**。范围：

- 账本维度：**task / goal / segment 三层**，未来加 per-child；主键沿用 `TaskRecord`（`task_id/goal_id/parent_id/segment`）；
- 指标：tokens in/out、cost、active wall clock、approval wait、tool calls、verify time；
- 从现有 trace/rounds 聚合的最小实现、预留（reserve）、任务终态结算（含 `approval_timeout`）。

不在本文：任务状态机与 start 检查（01）、进程监督与归属（02）、审批队列/过期策略（03）、worktree 与单写者（04）、目标语义与验收标准（05）、协议帧（07）、评测指标（08）、子 Agent 任务模型（16）。

## 2 现状与证据

**per-run 预算（现状）**

- 配置：`max_run_tokens`（默认 0=不限；基准入口覆盖 1M）、`max_run_seconds`（交互 1800s）、`max_run_cost_usd`（默认 0）（app/config.py:23,33,35；app/eval/swebench.py:455-456）。
- 执行：模型调用前按剩余预算判断是否进入 finalizing，付不起则无工具收尾或本地暂停（app/runtime/agent.py:743-746,810-816,1745-1795）；响应后 token/cost 超限转暂停（app/runtime/agent.py:1048-1052,1088-1091）；时间上限在请求前与工具循环检查（app/runtime/agent.py:866-905,1278-1283）；审批等待从 active 时间里扣除（app/runtime/agent.py:1896,1922）。
- 每 run 账本：`TraceRecorder` 累计 prompt/completion/total tokens、cost、tool_calls，`run_end` 写 limit/remaining/duration/usage_estimated（app/session/trace.py:21-44,70-100,152-171）。
- 展示与聚合：stdio 的 `metrics` 是本 run 累计（app/stdio.py:394-403,438-463,542-565）；`/cost` 读 session 的 `cost_summary`，只汇总 `trace.jsonl` 里的 `run_end`（app/session/store.py:203-224；app/stdio.py:306-312）。
- 每段 run 结束写 rounds，含 usage/cost（core/task/rounds.py:48-81；app/session/store.py:342-371）。

**goal 级预算（现状，仅 TUI）**

- `GoalMode` 持有 `totalTokens/totalCostUsd/maxTotalTokens/maxTotalCostUsd`（tui/src/goal.ts:11-14,49-54）；每段 `done` 后累加（tui/src/goal.ts:76-78），达到上限则暂停（tui/src/goal.ts:113-124）。
- 累计值随 `goal.json` 持久化（tui/src/goal-store.ts:19,35-38），但 Python 不读（全仓无 Python reader）。
- 即：**唯一“goal 预算上限”在 TS 里；Python 侧只有 per-run 上限**。05 §9 已确认未来权威文件改为 `goal-record.json`（Python 单写，`goal.json` 只作迁移来源）。

**漏记/口径/结算问题（现状）**

- LLM 摘要：`app.runtime.summarize.LLMSummarizer` 只返回文本、不解析 usage（app/runtime/summarize.py:59-91）；注入位置 app/__main__.py:218-233。主动压缩显式改用确定性摘要（app/runtime/agent.py:1684-1688），但 `build` 路径仍可能调用注入的摘要器（core/context/builder.py:371-380），其 token 不进任何账本；规格也承认该路径未纳入统一 usage（docs/specs/history-compression.md:42）。
- LLM 审计：`ReadOnlyAuditor` 会新起一个 Agent 跑只读审计（app/session/auditor.py:250-272），usage 只塞进 audit 结果（app/session/auditor.py:271），最终作为 `completion_audit` trace 的嵌套字段（app/runtime/agent.py:1250-1265），不计入父 run 的 `run_end`；16 已确认未来子 Agent 用量必须计入父 task。
- 崩溃丢账：stdio 实时把每个 trace 事件（含 `llm_end`）追加到 trace.jsonl（app/stdio.py:438-465），但 `cost_summary` 只统计 `run_end`（app/session/store.py:209-215）；进程被 kill 时最后一段花费在报表里消失。
- 时间口径：`trace.run_end.duration_ms` 从 recorder 构造到结束（含审批等待与压缩 CPU，app/session/trace.py:38,164），而 `max_run_seconds` 的 active time 排除审批等待（app/runtime/agent.py:1896）；同一个“用了多久”存在两个数。
- 审批没有 deadline：`_request_approval` 只监听 cancel（app/runtime/agent.py:1898-1923），stdio 的审批 future 也无超时（app/stdio.py:178-200）。03 §9 已确认 required 审批过期 → 任务失败（`failed`，reason=`approval_timeout`）；现状没有 task 终态概念，因此也谈不上为这种失败结账。
- 验证时间、工具耗时、子 Agent 用量都没有聚合层；`verification_recorded` 事件本身不带 duration（app/runtime/agent.py:1376-1384），只有 `tool_end.duration_ms`（app/session/trace.py:111-129）可 join。

**报告层（现状，非运行时账本）**

- oneshot 的 telemetry 写单次任务 usage/cost/limits（app/eval/oneshot.py:252-280）；benchmark 汇总表格按题显示 steps/tokens/cost（app/eval/swebench.py:125-140）。它们是导出物，不是可供运行时查询的账本。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| goal/task 级账本只在 TUI | 累计在 goal.ts 内存 + goal.json；Python 不可读 | 后台 worker/多客户端无法知道 task/goal 已花多少；TUI 崩溃丢未保存增量 |
| 无 task 维度与终态结算 | 只有 run 级 trace/round；没有 task_id 账本，也没有 failed/cancelled 终态结账（含 `approval_timeout`） | 任务失败/取消后成本与用量悬空，无法回答“这个任务花了多少、为什么停” |
| 无聚合器 | rounds 有 usage/cost，但只按单轮读（`/evidence` 最近一轮，app/stdio.py:342-343） | 无法回答“这个目标/任务一共花了多少” |
| 崩溃漏账 | 只看 `run_end`（app/session/store.py:209-215） | kill -9/断电后已花 token 不计入任何汇总 |
| 摘要/审计漏记 | LLM 摘要无 usage；审计 usage 只在嵌套 audit；16 要求子用量计入父 | 长任务真实成本被低估，父子预算无法对齐 |
| 时间口径分裂 | `duration_ms` 含审批等待，active time 不含（app/session/trace.py:164；app/runtime/agent.py:1896） | 报表与预算判断不一致，排障困难 |
| 无预留/硬停接口 | per-run 靠 finalizing 软收尾；goal 上限只有 TUI 决策，且执行点未定 | 多段之间无法保证“下一段一定付得起”；拒绝启动只能靠 TUI |
| 无 per-child 维度与上限 | 子 Agent 尚未实现；auditor 用量游离；深度/扇出上限未与资源 profile 对齐 | 未来委派无法计入父预算，也无法在超扇出时停在父任务层 |

## 4 设计选项与推荐

| 方案 | 记账位置 | 崩溃安全 | 可执行上限 | 改动面 |
|---|---|---|---|---|
| A TUI 账本 | 继续 goal.ts + goal.json，修 TUI 累加 | 依赖 TUI 存活 | 仅 TUI 路径 | 小 |
| **B Python BudgetLedger（推荐，随 TaskRuntime 落地）** | 从 trace 事件实时写 `budget.jsonl`，派生 `budget.json` | 事件逐条 append，崩溃可回放 | 由 01 的 TaskRuntime 在 start 前调用 06 的检查接口 | 中：新模块 + worker/stdio 接线 + 读取 API |
| C 外部 accounting 服务 | DB/HTTP | 最强 | 全入口一致 | 大：部署、鉴权、兼容 |

**推荐 B**：

1. 数据源已经在用：`llm_end`、`tool_end`、`approval_wait`、`run_end` 都是现有 trace 事件（app/runtime/agent.py:926-928,1344-1348,1923,1544），不需要改 Agent 主循环。
2. 崩溃安全：账本按事件 append，天然比“进程退出时汇总”可靠（正好修 §2 的漏账）。
3. **归属按 task（D1/D2）**：per-task worker 的 run_end 全部归该 task；supervisor 不执行任务，因此不产生任务账目。后台默认开启后，P0 就必须在 worker 路径记账，而不是只接 TUI。
4. **执行点归 TaskRuntime（D1）**：01 在“开始下一任务/下一段前”调用 `check_start`；06 只返回 `allowed/remaining/reserved` 与解释，拒绝或暂停由 01 决定并写 task 终态。
5. **声明与事实分离（D5）**：`goal-record.json.budget` 是上限声明，06 的 `budget.json` 是实际发生；两者不互相覆盖。
6. **子任务可归并（16）**：kind=subagent 的用量按 parent_id 回传父 task，深度/扇出上限与 09 的资源 profile 对齐。

## 5 最小实现切片

### BudgetLedger schema（草案）

```jsonc
{
  "schema_version": 1,
  "entry_id": "b-000123",
  "task_id": "t-20261103-143000",       // TaskRecord 主键（01）
  "goal_id": "g-20261103-143000",       // kind=goal/run 时可为空
  "parent_id": null,                     // 子任务指向父 task_id
  "node_kind": "run | goal | plan | subagent",
  "segment": 2,
  "run_id": "ab12cd34",
  "worker": {"pid": 12345, "kind": "task"},   // D2：每 task 一个 worker；supervisor 无条目
  "entry_kind": "llm | run_close | segment_close | task_close | child_close",
  "terminal_status": "completed | failed | cancelled | null",
  "terminal_reason": "approval_timeout | token_budget | cost_budget | ... | null",
  "tokens_in": 12000, "tokens_out": 800,
  "cost_usd": 0.0,
  "active_seconds": 0.0,
  "approval_wait_seconds": 0.0,
  "tool_calls": 0,
  "verify_seconds": 0.0,
  "children": [],
  "recorded_at": "2026-11-03T15:06:00Z",
  "source": "trace | runtime",
  "estimated": false
}
```

派生视图 `budget.json`（可重建，不作为事实源）：按 task/goal/segment 的合计 + `remaining`（相对 task/goal cap）+ `reserved`（收尾预留）。上限声明只在 `goal-record.json.budget`（05 §9），06 不复制。

### 记账/查询接口

```text
ledger.on_trace(task_id, event)                  # llm_end/tool_end/approval_wait/run_end → append
ledger.close_segment(task_id, segment, result, round_ref)
ledger.close_task(task_id, status, reason)       # 含 failed(approval_timeout)/cancelled；终态必须结算
ledger.totals(scope="task|goal|segment|child", id) -> {tokens_in, tokens_out, cost, ...}
ledger.check_start(task_id) -> {allowed, reason, remaining, reserved}  # 01 start 前调用；06 只报告
ledger.reserve(task_id, segment, amount) / ledger.release(task_id, segment)
```

### 切片

- **P0**：
  - 新增账本模块（暂定 `app/runtime/budget.py`；core/app 归属见 §8 Q6）：append-only `budget.jsonl`、容错读取、派生 totals；
  - 因后台默认开启（范围待确认，01 §9），接线默认在 per-task worker 路径：worker 的 `llm_end/tool_end/run_end` 全部归 `task_id`；TUI 关闭后 worker 继续时账本继续写；`VIBE_TASK_RUNTIME=0` 回退旧前台行为时，账本仍以“单 task 包装”工作；
  - 段关闭时写入 segment 条目（`segment/run_id/status/stop_reason/usage/verified/round_ref`，与 05 §9 的段账本摘要同构）；
  - 任务终态结算：`completed/failed/cancelled` 都写 `task_close`；`failed(reason=approval_timeout)` 也要落已发生用量与成本（D3），不留“未结算”的 task；
  - 暴露只读查询（先 CLI/命令，协议帧留给 07）。
- **P1**：
  - 硬上限：01 的 TaskRuntime 在“开始下一任务/下一段前”调用 `check_start`；超 goal/task cap 时由 01 拒绝启动或暂停，并写 `goal_token_budget/goal_cost_budget` 类原因；06 不自行裁决；
  - 预留策略：每段开始预留“收尾预算”（建议 ≥ `max(output_cap, 上次 completion)`，参考 core/budget/policy.py:11 与 app/runtime/agent.py:810-816 的 finalizing 行为），未用完释放；
  - per-child：子 Agent 作为 `kind=subagent` 任务节点（16），独立 worker 的 `llm_end` 经 parent_id 汇总到父 task；深度/扇出/并发上限与 09 的资源 profile 对齐，超额时父任务失败/暂停并记账，不静默继续；
  - verify time：用 `tool_call_id` 把 `verification_recorded` 与 `tool_end.duration_ms` 关联聚合（app/runtime/agent.py:1376-1384；app/session/trace.py:111-129）。
- **修改点**：新增账本模块；`app/stdio.py` 的 `record_trace` 接线（app/stdio.py:438-465）；`app/eval/oneshot.py` 的 `on_trace` 接线（app/eval/oneshot.py:206-210）；02 的 worker 入口初始化 ledger；查询先走 CLI/命令，协议帧交给 07。
- **测试点**：sum(segment) == task/goal 总量；`task_close` 幂等且崩溃后可重建；`approval_timeout` 导致的任务失败仍有 terminal 条目与用量；TUI 关闭后后台任务继续记账；重启后 totals 不变；重复事件幂等；estimated usage 标注；审批等待与 active 时间两种口径都能显示；摘要/审计用量按“已计/未计”显式标注；损坏尾行跳过；硬上限在 start 前生效（与 01 联调）；子用量计入父 task（P1）。
- **明确不做**：逐 token/逐请求明细审计（仍以 `llm_end` 为记账粒度）；币种换算与价格表管理；预算预测/机器学习；跨机器账单；把账本当计费依据；不实现审批队列/worktree/协议帧（分别属 03/04/07）。

## 6 与其它块的关系

- **01**：`TaskRecord` 是账本主键；TaskRuntime 的 start 前检查是硬上限唯一执行点（D1）。06 只提供数字与解释。
- **02**：每 task 一个 worker；worker 的 run_end 全部归 task，supervisor 只做 registry/健康检查/attach，因此 supervisor 不记任务账（D2）。后台默认开启意味着 P0 接线默认走 worker 路径。
- **03**：必填审批过期 → `failed(reason=approval_timeout)`；06 必须为这种终态结算已发生成本/用量并写 `task_close`，审批等待时长继续单列。审批队列/pending 清理归 03/TaskRuntime。
- **04**：单写者与 worktree 影响 worker 归属与合并后重验成本；P2 文件集并行时按 task 分别记账，不合并成“工作区总账”。
- **05**：`goal-record.json.budget` 是上限声明（D5）；06 是实际发生；05 段账本只留摘要，06 的 segment 合计供其 `usage` 引用。
- **07/08**：控制面提供 attach/任务帧（07）；长任务评测按 06 的成本口径分别报 tokens/cost/active/approval wait（08）。
- **09/16**：资源 profile 的深度/扇出/并发上限与 06 的 per-child 记账对齐；子任务超限要有明确父级停止原因（16）。

## 7 与子 Agent 的关系

**A 谁发起**——父任务派生（16）：子 Agent 是 `kind=subagent` 的任务节点，带 `parent_id`/`delegation_id`，不新建 goal；每 task 一个 worker（D2），账本按 task 归因。**B 能力边界**——只读子 Agent 先用现成 auditor 形态（app/session/auditor.py:250-272），其 usage 必须回传父账本；写类子 Agent 之后才需要独立预算条目。**C 结果可信度**——子 Agent 自报 usage 不可信，账本只采信子 trace 的 `llm_end`/`tool_end` 事件；子对“目标已达成”的报告只是 claim，父侧仍按证据复核。

对 06 的要求：账本必须能回答“父目标花了多少、其中哪个子任务花了多少”，并与 01 的 `parent_id`、16 的 delegation 模型对齐（docs/specs/multi-agent-computer-use.md:419-423）。当前 auditor 的用量没有聚合（app/session/auditor.py:271），是 P1 要修的第一个实例。

## 8 开放问题与验收标准

待用户拍板：

1. 硬上限的默认策略：达到 goal/task 上限时“暂停等确认”还是“直接失败”？（推荐暂停；注意这与 03 已确认的审批超时→failed 不是同一类）

回答：这部分因为当前做的是通用coding 场景，所以对于用户的任务大小用预算限制是一个很难把握的事情，我想取消预算上限设置，但是我们会通过不断强化harness来降低成本。这部分需要建议。

2. 记账粒度：按 `llm_end` 汇总够用，还是需要每个请求一行明细（体积/隐私）？

回答：按 `llm_end` 汇总是否够用？ 每个请求一行明细（体积/隐私）这样的方案会不会有存储压力，优势是可以进行成本分析。

3. 漏记的摘要/审计成本：强制计入（可能需要对摘要器改造）还是明确标注“未计入”？

回答：强制计入，如果实现难度大 可以未计入

4. 时间口径：对外报表用 active time 还是 wall clock（含审批等待）？

回答：区分记录，区分现实。

5. 收尾预留比例：固定 output cap 还是按历史 completion 动态？

回答：固定 output cap

6. **账本归属**：`app/runtime/`（可依赖 trace/存储/事件类型）还是 `core/`（纯标准库聚合）？——未拍板，P0 暂定 app 侧。

回答：`app/runtime/`

7. **上限冲突优先级**：run 级 `max_run_*`（Agent 内）与 goal/task 级上限同时触发时谁优先（拒绝启动 vs 中途暂停）？——01 §9 未定义。

回答：这个需要回答问题1


验收条件（建议）：

- 后台 `/goal` 跑 3 段（后台默认开启、TUI 关闭后继续）：`budget.json` 的 task/goal 合计 == 三段之和，每段有 run_id/round_ref 可追溯。
- kill -9 后重启：已花费 token 仍可恢复；任何 task 终态（含 `approval_timeout`）都没有“未结算”的账。
- 设置 goal token 上限后：下一段在 start 前被拒绝/暂停（不是跑完再报），停止原因可解释。
- 审计/摘要用量有明确“计入/未计入”标记；`/cost` 与 goal 视图数字一致。
- P1：子任务用量计入父 task 合计，且深度/扇出超限时父任务有明确失败原因。

## 9 决策对齐（2026-11-03）

**受哪些已确认决策影响**：D1（TaskRuntime 权威、task 身份、后台默认开启）、D2（每 task 一个 worker + supervisor、后台默认）、D3（审批过期→任务失败）、D5（`goal-record.json` 权威与段账本摘要）；D4（单写者/worktree）间接影响归属与成本口径。全部按讨论稿处理，不承诺实现。

**本块已按决策修改的内容**：

1. 账本维度从 per-run 改为 **task / goal / segment 三层**，主键改用 01 的 `TaskRecord`（`task_id/goal_id/parent_id/segment`）；schema 增 `parent_id/node_kind/worker/terminal_status/terminal_reason`（§1/§4/§5）。
2. 硬上限执行点移到 **TaskRuntime 开始下一任务/下一段前**；06 只提供 `check_start`/`remaining`/`reserve` 接口，拒绝或暂停由 01 决定（§4 第 4 条、§5 P1）。
3. **后台默认开启 + per-task worker**：用量按 task 归因；worker 1:1 task，supervisor 不执行任务因此不产生任务账目；P0 接线默认走 worker 路径，`VIBE_TASK_RUNTIME=0` 回退旧行为（范围待确认，见 01 §9）（§4 第 3 条、§5 P0）。
4. 目标声明来源改为 **`goal-record.json`**（D5）：`budget` 声明与 06 实际累计分离；`goal.json` 只作迁移来源（§1/§4 第 5 条/§6 05）。
5. **审批过期→任务失败**：新增 `task_close` 结算，`failed(reason=approval_timeout)` 也必须落 terminal 用量与成本，不留未结算 task（§2 时间口径、§5 P0、§6 03）。
6. **per-child 聚合**：子 Agent 作为 `kind=subagent` 任务节点，用量经 `parent_id` 回传父 task；深度/扇出上限与 09 的资源 profile 对齐（§5 P1、§7）。
7. 按 `core/`、`app/` 分域重构刷新了 `path:line`（如 `app/runtime/agent.py`、`app/session/trace.py`、`app/session/store.py`）；未确认的新行号不新增。

**仍未决（保留 §8，不擅自拍板）**：账本归属 app/core（Q6）；run 级与 goal/task 级上限冲突优先级（Q7）；预算到限“暂停 vs 失败”（Q1）；记账明细粒度（Q2）；摘要/审计是否强制计入（Q3）；时间口径（Q4）；收尾预留比例（Q5）。待用户 review 后再收敛为 spec。

## 10 用户回答处理（2026-11-03）

用户回答原文见 §8。处理结果：

**已确认（写入本块设计）**
- Q3：摘要/审计成本**优先强制计入**；短期实现困难时先标 `unaccounted` 并计数，P1 补强制。
- Q4：时间口径**同时记录** active time 与 wall clock，报表分别展示；task/goal 总时长用 wall（含审批等待），并分别统计 active 与 human approval（2026-11-03 由 13-Q4 补充确认）。
- Q5：收尾预留使用固定 output cap。
- Q6：账本归属 `app/runtime/`（可依赖 trace/存储/事件类型），`core/` 不承载账本实现。

**已确认（2026-11-03，采用 Lead 建议）**
- Q1 + Q7：不建议完全取消预算上限，改为**三层**：
  1. `global safety cap`（不可关闭的 runaway 护栏）：默认高值（如 2× 同仓库历史 p95 或一个绝对上限），触发即暂停/取消并写审计；
  2. `task/goal` 级：默认**软预算**（记录、80% 预警），默认不因超预算直接失败；只有配置 `hard_task_budget` 时才失败/暂停等确认；
  3. `run/segment` 级：保留现有 `max_run_*` 作为**段边界**（触发→暂停/收尾，由 TaskRuntime 决定下一段），它不是任务级成本失败。
  通用 coding 任务难预估 → 用历史 p50/p90 做参考、首任务用默认高值；后台任务默认给 generous hard cap。
  Q7 冲突优先级：`global safety > task hard > run segment`；run 级先触发段边界，TaskRuntime 在段起点检查 task 级，global 可随时中断。
- Q2：P0 用 `llm_end` 汇总足够预算/回执；建议 P1 再加 per-request compact usage ledger（每次约 200–500B，1000 次 <0.5MB），用于成本分析，按会话轮转；不先做重存储。

**实现备注**：Q1/Q7 采用三层预算模型；Q2 采用“P0 聚合、P1 per-request compact ledger”；无剩余待拍板项。

**对其它块**：影响 08 的成本 cap、09 的资源 profile、11 的 budget profile、13/14/15 的步级预算与验收；跨块对齐见 11–16 的第二批决策对齐。
