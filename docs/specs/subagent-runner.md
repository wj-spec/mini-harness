# 只读子 Agent runner P0

状态：2026-11-06 P0 实现（harness 内部只读 runner + 子 TaskRecord/usage/取消级联/批量扇出上限 + /explore）；父预算硬上限、attach 回放、模型 delegate 待后续。
设计来源：[16 子 Agent 的有机结合](../design/long-horizon/16-subagent-integration.md)。

## 已实现

`app/runtime/subagents.py`：

- `SubagentResult` schema v1：
  - status ∈ ok/error/cancelled/budget_exceeded/timeout；
  - summary/findings/evidence/usage/cost/stop_reason/error/trace_ref；
  - `from_dict` 校验 schema 与 status。
- `run_subagent(provider, workspace, objective, ...)`：
  - 复用 `Agent.run()`，**不是第二套 loop**；
  - tools = `ToolRegistry.subset(READ_ONLY_TOOLS)`，写/执行在 execute 层 `unknown_tool`；
  - 独立 history、无审批（ApprovalGate=None）；
  - 预算参数 max_steps/max_run_tokens/max_run_seconds；
  - cancel event + parent_cancel 级联；depth/max_depth（默认 1）拒绝嵌套；
  - `run_subagent_batch`：max_children（默认 4）、max_concurrency（默认 2），共享父取消，结果按输入顺序返回。
- stdio command `explore`（args `{prompt}`）与 TUI `/explore <任务>`：
  - 使用当前 provider 在只读子集上运行子 Agent；
  - 开始/结束发 notice（含 prompt 前缀/子任务 id）；
  - 结束发 subagent_event 帧（task_id/status/summary），TUI 显示 notice；
  - command_result 返回 `子 Agent <status>：<summary>` + 子任务 id；
  - 创建 TaskRecord(kind=subagent)，可在 `/task`/`--tasks` 看到；
  - 不写工作区。
  - 可选 trace JSONL（child trace 目录约定待定）；
  - 可选 `session_root`/`parent_task_id`：创建 `TaskRecord(kind=subagent, parent_id=...)`，
    start/end segment、任务状态 completed/failed/cancelled、
    `subagent-result.json` 落盘、释放任务锁；
  - usage/cost 以 `node_kind=subagent` + `parent_id` 写入 `budget.jsonl`；
  - 返回结构化结果，报告是 untrusted summary。

## 未完成（P0 剩余）

- 父任务预算硬上限对子 usage 的强制（当前只记账，不阻断）；
- 与后台任务槽位/预算联动的扇出调度；stdio 子任务事件（当前已有子 TaskRecord 与摘要）。
- 父任务收件箱审批归属（requester=child）；
- stdio 事件/attach 显示；
- 模型 `delegate` 工具（P2，需模式/预算闸）；
- A/B 收益评测。

## 测试

`tests/test_subagents.py`：只读探索结构化结果、write_file/bash 被 subset 拒绝、结果 schema 校验、
子 TaskRecord 生命周期与 subagent-result.json、usage 写入父 ledger、父取消级联、depth 拒绝、批量扇出上限。
