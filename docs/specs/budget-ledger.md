# 预算账本（BudgetLedger，P0）

日期：2026-11-03。状态：P0 实现完成 + P1 `check_start` 硬上限已接 TaskRuntime（`VIBE_TASK_BUDGET_HARD=1` 时按 task budget 拒绝启动）；记账/终态；预留、per-child、soft-budget 体验仍待后续。
对应讨论稿：[06 预算账本](../design/long-horizon/06-budget-ledger.md)（§9/§10 已确认）。
依赖：[task-runtime](task-runtime.md)、[goal-record](goal-record.md)。

## 0 第一性原理与不变量

1. **花费发生在 run 内，归属在 task**：per-run 预算已有，但“这个目标/任务花了多少”必须按 `task_id/goal_id/segment` 持久化。
2. **事件优先于退出汇总**：进程可能被 kill；账本条目在 trace 事件到达时追加，不能用“进程退出时求和”作为唯一来源。
3. **声明与事实分离**：`goal-record.json.budget` 是上限声明；`budget.jsonl` 是实际发生。账本不复制/修改声明。
4. **账本只报告，不裁决**：硬上限由 TaskRuntime 在开始下一段/任务前检查；06 提供数字与解释（P1）。
5. **终态必须结账**：`completed/failed/cancelled` 都写 `task_close`；`failed(approval_timeout)` 不能留下悬空账目。
6. **不记正文**：只记 token/cost/时间/工具次数与引用；不存命令输出、模型文本、审批预览。
7. **时间双口径**：active time 与 approval wait 分开累计；对外报表分别展示，不合并成一个数。

不变量：

| # | 不变量 |
|---|---|
| M1 | 账本位于 `<session>/budget.jsonl`，append-only；损坏行跳过，不重写历史 |
| M2 | 每条有 `task_id`，可按 task/goal/segment 聚合；`parent_id` 预留子任务归并 |
| M3 | `llm` 条目只来自 `llm_end`；`tool` 条目只来自 `tool_end`；重复事件按事件条数记录（P0 不重试去重） |
| M4 | segment 结束写 `segment_close`；task 进入终态写 `task_close`（幂等追加，不修改旧条目） |
| M5 | 账本不改变 Agent 预算行为；per-run 上限继续由 Agent 执行 |
| M6 | 普通 run（无 task）不写账本；`VIBE_TASK_RUNTIME` 关闭路径零变化 |
| M7 | 摘要/审计成本 P0 若未接线，必须在字段/文档里显式标为未计，不假装已计 |

## 1 范围

**P0（本切片）**

- `app/runtime/budget.py`：BudgetLedger 追加、容错读取、`on_trace`、`close_segment`、`close_task`、`totals`、`task_terminal`；
- TaskRuntime：创建 ledger；stdout trace → `llm/tool/approval_wait/run_close` 条目；段/任务关闭写账；
- stdio/worker 共用（worker 子进程的 stdio 同样接账本）；
- 测试与查询为 Python API；CLI/协议查询留给 07。

**P1（后续）**

- TaskRuntime 开始下一段前 `check_start`/`reserve`；超上限暂停/失败；
- per-child 汇总（`kind=subagent` 的 `parent_id` 归并）；摘要/审计成本强制归集；
- verify time 聚合、控制面查询帧、崩溃后 totals 重建校验。

## 2 条目 schema（`budget.jsonl`，schema_version=1）

```jsonc
{
  "schema_version": 1,
  "entry_id": "b-…",
  "task_id": "t-…",
  "goal_id": "g-…",                 // 可空
  "parent_id": null,
  "node_kind": "goal|run|plan|subagent",
  "segment": 1,
  "run_id": "…",
  "entry_kind": "llm|tool|approval_wait|run_close|segment_close|task_close",
  "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
  "active_seconds": 0.0, "approval_wait_seconds": 0.0,
  "tool_calls": 0,
  "estimated": false,
  "status": null, "stop_reason": null,
  "terminal_status": null, "terminal_reason": null,
  "round_ref": null, "verified": null,
  "source": "trace|runtime",
  "recorded_at": "…"
}
```

- `llm`：来自 `llm_end` 的 prompt/completion/cost/estimated；
- `tool`：来自 `tool_end`，`tool_calls=1`；
- `approval_wait`：来自 `approval_wait` 的等待秒数；
- `run_close`：来自 `run_end`，`active_seconds=duration_ms/1000`；**不重复累计 tokens/cost**；
- `segment_close`：段状态/stop_reason/round_ref/verified；
- `task_close`：终态 + reason。

## 3 API

```python
class BudgetLedger:
    def __init__(self, session_root): ...
    def append(self, entry) -> dict
    def entries(self, *, task_id=None) -> list[dict]
    def on_trace(self, *, task_id, goal_id, parent_id, node_kind, segment, run_id, event) -> dict | None
    def close_segment(self, *, task_id, ..., status, stop_reason=None, round_ref=None, verified=None) -> dict
    def close_task(self, *, task_id, ..., status, reason=None) -> dict
    def totals(self, *, task_id=None, goal_id=None, segment=None) -> dict
    def task_terminal(self, task_id) -> str | None
```

`totals` 返回 `tokens_in/tokens_out/tokens_total/cost_usd/active_seconds/approval_wait_seconds/tool_calls/entries`；只聚合 `llm/tool/approval_wait/run_close`，避免 close 条目重复计数。

## 4 TaskRuntime 接线

- `TaskRuntime` 持有 `BudgetLedger(store.root)`；
- stdio 的 `record_trace` 在事件到达时调用 `TaskRuntime.record_trace(run_task, event)`；
- `finish_run`：
  1. `end_segment` + task transition；
  2. `close_segment`（使用映射后的 segment status/reason）；
  3. 若 task 进入 `completed/failed/cancelled`，`close_task`；
  4. 再 `GoalStore.sync_from_task`；
- 账本错误（`BudgetError`）在 P0 被吞掉/记录，不改变 Agent 结果；后续可发诊断事件。

## 5 失败与边界

| 场景 | 行为 |
|---|---|
| 进程 kill -9 | 已写条目保留；缺少 `segment_close/task_close`；totals 仍可读已发生用量 |
| 损坏行 | 读取跳过；后续 append 不受影响 |
| 重复 trace 事件 | P0 按条记录（不保证幂等）；P1 用事件 id 去重 |
| 普通 run | 无 task 上下文 → 不写账本 |
| 摘要/审计 | P0 未接线；文档/报表明确“未计”，P1 强制归集 |
| 任务暂停 | 只写 `segment_close`，不写 `task_close`；resume 后新段继续累计 |

## 6 测试

- `tests/test_budget.py`：append/entries/损坏跳过、llm/tool/run_close 聚合、close_segment/task、终态校验、TaskRuntime hook；
- `tests/test_stdio_tasks.py`：真实 stdio goal run 产生 `llm` 和 `task_close(completed)`；
- 全量回归。

## 7 验收标准

- 一个 goal task 运行后 `budget.jsonl` 存在，`llm` 条目与 `task_close` 正确；
- totals 与 entries 一致，close 条目不被重复计数；
- 普通 run 不产生账本；
- 损坏 tail 行不阻断读取；
- 全量回归通过。

## 8 后续 P1

1. TaskRuntime 在段开始前调用 `check_start`，超 task/goal cap 由 01 暂停/失败并写 reason；
2. `reserve`/`release` 收尾预留（固定 output cap）；
3. 摘要器/审计器 usage 归集；子 Agent `parent_id` 归并；
4. 控制面只读查询（07）与评测成本口径（08）。

## 9 风险与未决

- **重复事件**：P0 无幂等键；重试可能导致重复计数，P1 需事件 id/run_id+step 去重。
- **崩溃结算**：kill -9 后无 task_close；是否在 TaskRuntime 恢复时补写，P1 定。
- **摘要/审计**：未接线前成本低估，报表需显式标记。
- **货币/价格**：沿用现有 cost_usd 计算，不做币种/价格表管理。
