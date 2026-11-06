# 只读子 Agent runner P0

状态：2026-11-06 P0 实现（harness 内部只读 runner + 子 TaskRecord/usage/取消级联）；父预算硬上限、扇出上限、stdio 事件、模型 delegate 待后续。
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
  - 可选 trace JSONL（child trace 目录约定待定）；
  - 可选 `session_root`/`parent_task_id`：创建 `TaskRecord(kind=subagent, parent_id=...)`，
    start/end segment、任务状态 completed/failed/cancelled、
    `subagent-result.json` 落盘、释放任务锁；
  - usage/cost 以 `node_kind=subagent` + `parent_id` 写入 `budget.jsonl`；
  - 返回结构化结果，报告是 untrusted summary。

## 未完成（P0 剩余）

- 父任务预算硬上限对子 usage 的强制（当前只记账，不阻断）；
- 子 Agent 扇出/并发硬上限；
- 父任务收件箱审批归属（requester=child）；
- stdio 事件/attach 显示；
- 模型 `delegate` 工具（P2，需模式/预算闸）；
- A/B 收益评测。

## 测试

`tests/test_subagents.py`：只读探索结构化结果、write_file/bash 被 subset 拒绝、结果 schema 校验、
子 TaskRecord 生命周期与 subagent-result.json、usage 写入父 ledger、父取消级联、depth 拒绝。
