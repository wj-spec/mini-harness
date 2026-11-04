# GoalRecord：跨段目标状态（P0 基础）

日期：2026-11-03。状态：P0 基础实现完成（GoalRecord schema/迁移/段同步 + TaskRuntime 接入）；P1a 的 worker 续段提示与继续决策（`goal_prompt`/`continue_decision`）已实现；TUI v2 读取与用户确认 UI 仍在 P1。
对应讨论稿：[05 跨段目标状态](../design/long-horizon/05-goal-state.md)（§9 已确认）。
依赖：[task-runtime](task-runtime.md)（TaskRecord）、[task-runtime-integration](task-runtime-integration.md)（goal task 接入）。

## 0 第一性原理与不变量

1. **目标不是提示词**：一段 objective 文本无法回答“验收标准是什么、哪一条已有证据、下一段该继续什么”。目标必须结构化、可核对。
2. **目标状态必须跨进程**：worker/attach/重启要读同一份目标；只存在 TUI 内存或 TS `goal.json` 里，Python 无法续段。
3. **真值分离**：段/任务状态由 harness 写；模型只能提议验收标准/未完成项，不能把目标标成完成。
4. **单一权威文件**：`goal-record.json`（schema v2）由 Python 单写；`goal.json` 只作旧 TUI 迁移来源。
5. **摘要而非全文**：段账本只存 `segment/run_id/status/stop_reason/usage/verified/round_ref`；证据通过 round_ref 回取。

不变量：

| # | 不变量 |
|---|---|
| L1 | goal-record.json 位于 `<session>/goal-record.json`；原子写；未知字段忽略 |
| L2 | Python 单写；TUI 在 P1 前仍写 goal.json，二者不互相覆盖 |
| L3 | 目标状态只由 TaskRecord/验证门推进；模型不能把 goal 标 completed |
| L4 | acceptance_criteria 只有 `confirmed=true` 才参与完成判定；未确认候选保留但不生效 |
| L5 | open_items 的 `done` 只能来自 harness 证据；模型只能写 open/blocked/描述 |
| L6 | 段账本 append-only/与 TaskRecord segments 对齐，不复制回执正文 |
| L7 | 旧 goal.json 迁移不删除原文件、不破坏旧 TUI 回退 |

## 1 范围

**P0（本切片）**

- `app/runtime/goals.py`：GoalRecord v2 schema、GoalStore 原子读写、v1 `goal.json` 迁移、段同步、状态映射；
- TaskRuntime 接入：goal task 创建/attach 时 ensure GoalRecord；segment 结束时 sync；
- 测试；不改 TUI、不改 stdio 协议。

**P1（后续）**

- TUI 读 `goal-record.json`（v2）替代 `goal.json`；下一段提示词由 Python 生成；
- `/goal` 展示 acceptance criteria/约束/未完成项/段账本；
- 验收标准模型提议 → TUI 选择框 → 用户确认；
- 目标状态机与 01 的 task 状态机合并。

**非目标**

- 完成验证门本体（15）；计划审批门（13）；预算账本（06）；worker 续段（02 P0c/05 P1）。

## 2 GoalRecord schema v2

```jsonc
{
  "schema_version": 2,
  "goal_id": "g-20261103-120000-abcdef",
  "task_id": "t-…",
  "objective": "完成跨文件重构并跑通全量测试",
  "state": "created | running | paused | completed | failed | cancelled",
  "acceptance_criteria": [
    {"id":"ac1","text":"全量 pytest 退出 0","verify":"bash:python -m pytest -q",
     "required":true,"source":"user+model_proposed","confirmed":true}
  ],
  "constraints": [{"text":"不要改测试文件","source":"user|steering|project"}],
  "segments": [
    {"segment":1,"run_id":"…","status":"stopped","stop_reason":"max_run_seconds",
     "usage":{"total_tokens":0},"verified":null,"round_ref":"rounds/round_003.json"}
  ],
  "open_items": [
    {"text":"…","status":"open|done|blocked","source":"harness|model",
     "evidence_refs":[],"updated_at":"…"}
  ],
  "blocking": [{"text":"…","since_segment":2}],
  "verification_plan": [{"command":"python -m pytest -q","covers":["ac1"]}],
  "budget": {"max_goal_tokens":0,"max_goal_cost_usd":0,"max_goal_seconds":0},
  "plan_id": null,
  "source": {"session_id":"…","created_by":"user|harness|model_proposed"},
  "created_at":"…","updated_at":"…"
}
```

约束：

- `goal_id` 若 task 未提供，由 task_id 派生（`g-<task_id 去掉 t- 后的部分>`），稳定可读；
- `acceptance_criteria` 缺省空；未确认候选允许存在；
- `segments` 由 TaskRecord segments 同步（P0）；
- `state` 由 TaskRecord.status 映射：running→running，paused/interrupted→paused，completed→completed，failed→failed，cancelled→cancelled，created→created；
- 不存命令输出/文件正文/审批预览。

## 3 GoalStore API

```python
class GoalStore:
    def __init__(self, session_root): ...
    def load(self, goal_id: str) -> GoalRecord | None
    def save(self, record: GoalRecord) -> None
    def ensure_for_task(self, task: TaskRecord) -> GoalRecord
    def sync_from_task(self, task: TaskRecord) -> GoalRecord
    def migrate_legacy(self) -> GoalRecord | None
```

- `ensure_for_task`：已有同 task_id 的 record → 复用；否则从 legacy goal.json 迁移（若存在）或从 task 创建；
- `sync_from_task`：更新 objective/state/segments（保留 criteria/constraints/open_items）；
- 原子写 temp+replace；损坏文件抛 `GoalCorrupt`；不存在返回 None；
- 多 goal 文件：P0 只保留一个 `goal-record.json`（当前会话目标）；后续多 goal 用目录另定。

## 4 TUI/goal.json 迁移

- legacy 字段：`objective/active/completed/segment/maxSegments/startedAt/...`；
- 映射：completed→completed；active→running；否则 paused；`source.created_by="tui_legacy"`；
- 迁移写入 `goal-record.json`，**不删除/不修改 goal.json**；TUI 继续按旧文件工作；
- 无效/损坏 goal.json 忽略，不阻断任务。

## 5 TaskRuntime 接入

`TaskRuntime` 内部持有 `GoalStore(store.root)`：

- `prepare_run` 成功后（goal task）：`ensure_for_task`；若失败记录 worker/task event，不阻断执行（P0 可观测优先）；
- `finish_run` 后：`sync_from_task(record)`；失败记录错误但不改写 Agent 结果；
- 只有 `kind=goal` 创建 GoalRecord；其它 kind 不创建。

## 6 测试计划

- `tests/test_goals.py`：
  - schema roundtrip/原子写/损坏；
  - task→record 创建、goal_id 派生；
  - 状态映射全表；
  - segments 同步（running/done/stopped/failed）；
  - 保留 criteria/open_items 不被 sync 覆盖；
  - legacy goal.json 迁移（active/completed/paused/损坏）；
  - 不删除 goal.json；
- `tests/test_task_runtime.py` 增加：goal task prepare 后有 GoalRecord；finish 后 state/segments 同步；
- 全量回归。

## 7 验收标准

- 一个 goal task 运行后，`<session>/goal-record.json` 存在且 objective/task_id/segments/state 正确；
- legacy goal.json 场景迁移后旧文件不变；
- 非 goal task 不产生 goal-record.json；
- 损坏 record 不阻断 run（记录诊断）；
- 全量回归通过；TUI 行为不变。

## 8 后续

1. TUI v2 读 record、删除 goal.json（P1）；
2. 下一段提示词从 GoalRecord 生成（P1）；
3. 验收标准模型提议 + 用户确认（TUI）；
4. P0c TUI detach 依赖本切片 + 02 worker 续段。

## 9 风险与未决

- **多 goal**：P0 单文件；并行 goal 需要目录/索引，后定。
- **goal_id 派生**：task.goal_id 为空时派生；TUI 传入后切换。
- **legacy 迁移时机**：ensure 时惰性迁移；如果 TUI 同时写 goal.json，可能产生分叉；P1 切权威后解决。
- **open_items 强证据**：P0 只存字段，不实现证据核验；15 门实现后生效。
