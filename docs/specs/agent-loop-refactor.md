# Agent loop 拆分重构

日期：2026-10-22。状态：已完成，行为回归通过；W2 纠偏与 W3 step 级恢复的接入点已明确，但未实现。

按用户 2026-10-22 要求，本次先完成重构、再补本页；后续切片恢复“spec 先于实现”。

## 背景

`app/agent.py` 的 `_run` 同时承担 run 准备、工作区状态、上下文与预算、模型流式与重试、工具审批执行、结束判断，以及 15 个内嵌辅助函数：单函数 799 行，主循环体 441 行，跨步状态靠闭包和 `nonlocal` 共享，29 处提前 `return` 分散在 441 行内。

真实 SWE-bench 首题已通过，下一步 W2（运行中纠偏）和 W3（更细的恢复边界）都要改这段循环。先拆成可独立阅读的阶段函数，降低修改成本；不改对外行为。

## 目标与非目标

目标：

- 主循环只表达“准备请求 → 模型回复 → 判断 → 执行工具”。
- run 作用域状态显式化，不再依赖闭包与 `nonlocal` 共享。
- 每个阶段有明确输入、输出和停止出口，可以单独阅读和定位。

非目标（本次明确不做）：

- 不改循环形态。`for step in count(1)` 保留，它与 `while True` 等价，不新增 `done` 标志。
- 不改变预算、压缩、审批、取消、停滞检测、事件顺序、消息顺序和 `steps` 数值口径。
- 不新增模块；`app/agent.py` 仍是唯一 loop 定义位置，`Agent.run` 签名与 `AgentResult` 字段不变。
- 不重新引入硬步数上限：默认 `max_steps=0` 的行为保持。

## 结构

| 方法 | 行数 | 职责 |
|---|---:|---|
| `_run` | 69 | 技能刷新、TaskState、历史快照、Trace 与 `_RunContext` 构造；`finally` 写回历史 |
| `_loop` | 20 | `for step in count(1)`；依次调用四个阶段，遇到 `AgentResult` 立即返回 |
| `_prepare_step` | 128 | 取消/步数上限检查、压缩时机、上下文构建、收尾请求、窗口检查 |
| `_call_model` | 109 | 取消与时间检查、请求快照 id、重试、usage 估算与记账 |
| `_stream_once` | 72 | 单次流式请求、超时与取消；只采集事件，不做预算判断 |
| `_decide_after_model` | 97 | 错误处理、文本工具协议、工具 id、消息记录、截断续写、空回复、`require_tool_use` |
| `_run_tool_calls` | 99 | 审批、执行、结果回灌、进展检测与批次中止 |

另抽出 16 个辅助方法：`_finish`、`_record_message`、`_budget_update`、`_context_snapshot`、`_input_capacity`、`_history_capacity`、`_automatic_history_target`、`_compact`、`_pause_budget`、`_skip_tools`、`_pause_progress`、`_checkpoint`、`_execute_tool`、`_active_seconds`、`_request_approval`、`_cancel_pending`。

阶段之间用类型化结果交接：`_RunContext`（run 作用域状态）、`_StepPlan`（本步请求与收尾标记）、`_ModelTurn`（模型回复）、`_StreamCapture`（单次流式采集）、`_StepDecision`（继续下一步或执行工具）。

所有终止路径仍收敛到四个出口：`_finish`、`_pause_budget`、`_pause_progress`、`_cancel_pending`；未执行的工具调用由 `_skip_tools` 补齐配对结果。

## 不变的行为

重构按“逐行搬运”处理，以下保持原实现：

- 主循环用 `for step in count(1)`；取消/时间/预算/压缩/进展判断的位置和顺序不变。
- Trace 事件顺序（`run_start`、首个 `budget_update`、`context_snapshot`、`llm_start/end`、工具与审批事件）与重构前一致。
- 消息与回调顺序不变：用户消息、助手消息、续写消息、工具结果、`progress_warning` 的触发点保持一致。
- 各分支的 `status`、`stop_reason`、`steps` 数值保持原样，包括“请求前停止报已完成步数”的现有约定。
- 审批等待不计入运行时长、取消后未执行调用补齐、拒绝后不重复弹审批等规则不变。

## 验证

| 检查 | 结果 | 记录 |
|---|---|---|
| 重构前基线 | 889 passed，78.69s | `.vibe/agent-loop-refactor/tests-baseline.log` |
| 拆分阶段后 | 889 passed，82.78s | `.vibe/agent-loop-refactor/tests-after-split.log` |
| 抽出 `_stream_once` 后 | 889 passed，78.47s | `.vibe/agent-loop-refactor/tests-after-stream-split.log` |
| 仓库标准入口 `./run_tests.sh -q` | 退出码 0 | `.vibe/agent-loop-refactor/final-tests.log` |
| fake 端到端预算分层 | 3/3 `[PASS]` | `.vibe/benchmarks/budget-checks/20261022-132709-177381/` |
| Ink / Node 回归 | 181/182；唯一失败为无 PTY 环境 | `.vibe/agent-loop-refactor/tests-node.log` |

Node 失败项是 `interactive CLI enters and restores the alternate screen`，报错为 `OSError: out of pty devices`，与本次改动无关；同一环境下重构前结果相同。

重构前文件与未提交 diff 已备份在 `.vibe/agent-loop-refactor/agent.py.baseline` 与 `agent.py.uncommitted.diff`，便于对照回退。文件从 1067 行变为 1281 行，增量主要是方法签名、类型和 docstring，循环逻辑本身无新增分支。

## 边界与后续

- **`steps` 口径已修复（2026-10-22）**：`_call_model` 首次请求前的时间检查改为报已完成步数（`step - 1`），`tests/test_agent.py::test_max_run_seconds_stops` 断言该场景 `steps == 0`；详见 [运行中纠偏与后续消息](run-time-correction.md)。
- **W2 纠偏**：注入点在 `_prepare_step` 组装请求之前，`run.correction_instruction` 现在是显式状态，可直接扩展为“运行中收到的新指令”。
- **W3 恢复**：step 级 checkpoint 可落在 `_StepPlan`/`_ModelTurn` 边界，不需要再改主循环形态。
- **多 Agent**：当前没有第二个消费者，仍按 [roadmap](../roadmap.md) 的取舍，等实际任务需要时再评估。
- 本次未运行真实模型任务；行为正确性由确定性回归和 fake 端到端检查证明，真实任务验收仍按 [里程碑](../MILESTONES.md) 另行记录。
