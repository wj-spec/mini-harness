# 05 跨段目标状态

> 状态：讨论稿（2026-11-02；2026-11-03 部分决策已确认，见 §9）；不是 spec，未承诺实现。
> 所属：长任务与后台任务 / 第 5 块（目标、计划与段账本）
> 前置/相关：[01 任务运行时](01-task-runtime.md)、[02 后台进程监督](02-process-supervision.md)、[06 预算账本](06-budget-ledger.md)；相关规格：[goal-mode](../../specs/goal-mode.md)、[verified-completion](../../specs/verified-completion.md)、[w2-w3-acceptance](../../specs/w2-w3-acceptance.md)

## 1 问题与范围

`/goal` 目前把“目标”当成一段自由文本：TUI 在每次续段时把同一 objective 重新拼进提示词（tui/src/goal.ts:57-73），Python 侧只有 `TaskState.goal` 这一字符串（core/task/state.py:196-212）。也就是说，跨段延续依赖“同一会话历史 + 重放目标文本”，而不是一份可核对的目标状态。

本文讨论：**目标（goal）应该存什么、由谁维护、如何跨段/重启恢复**。范围：

- GoalRecord 数据模型：objective、验收标准、约束、段账本、未完成项、阻塞、验证计划、来源；
- 目标状态机与 segment 生命周期；
- 与 `TaskState`、rounds、`goal.json` 的关系与迁移；
- 跨段摘要/压缩时的目标保真。

不在本文：任务创建/编排（01）、进程托管（02）、预算数字与执行（06）、结构化计划产物（12-plan-artifact 块）、计划审批门（13 块）。

## 2 现状与证据

**目标只有一个字符串（现状）**

- `TaskState` 字段：`goal`、`plan`、`done`、`open`、`files_changed`、`tests_run`、`notes`、`command_results`、`constraints`、`file_changes`、`verifications` 与 `mutation_seq` 等（core/task/state.py:196-212）；其中 `goal` 是自由文本，`plan/done/open` 由模型通过 `update_task` 写（app/tools/registry.py:370-387；core/task/state.py:281-296）。
- `TaskState.render()` 被注入 system 消息（app/runtime/agent.py:631-634），包含 Goal/Plan/Done/Open/Files/Commands/Constraints（core/task/state.py:214-266）；`constraints` 目前只由 steer 追加（app/runtime/agent.py:1613-1618；core/task/state.py:268-273）。
- 首次 run 时 `TaskState(goal=task)`，之后 `goal` 不再变化（app/runtime/agent.py:617-619）；会话恢复时从 `state.json` 载入（app/session/store.py:314-337）。
- 完成守卫只看“本轮文件改动 + 验证覆盖”，不看“目标是否达成”（core/task/state.py:53-86；docs/specs/verified-completion.md:18-46）；`verified=false` 时 TUI 暂停目标（tui/src/goal.ts:80-93；docs/USAGE.md:264）。

**goal 状态只在 TUI（现状）**

- `GoalMode` 字段：objective/active/completed/segment/maxSegments/startedAt/maxSeconds/totalTokens/totalCostUsd/maxTotal*/pausedReason（tui/src/goal.ts:3-16）；段决策是纯函数（tui/src/goal.ts:75-130），`/goal resume` 会消耗一段（132-137）。
- 持久化文件 `<session>/goal.json`：temp+rename 原子写（tui/src/goal-store.ts:15-23）；读取校验严格但**忽略未知字段**（25-40）；active 恢复后强制 paused(`session_restored`)（42-54）。
- Python 不读写 `goal.json`：grep 全仓只有 TUI 的 writer/reader（tui/src/goal-store.ts:5-13,42-54）；规格也写明目标编排只在 Ink（docs/specs/goal-mode.md:9,36-42）。
- 段与 run 的关联只存在于内存：TUI 在 `done` 后决定下一段（tui/src/app.tsx:196-212,234-259）；Python 的 round record 只有 goal 文本、没有 goal_id/segment（core/task/rounds.py:48-81），`/evidence` 只读最近一轮（app/stdio.py:342-343）。

**跨段恢复已有基础（现状）**

- 会话历史跨 run 保留：Agent 在 `finally` 写回 history（app/runtime/agent.py:690-695），stdio 通过 `on_history` 存快照（app/session/store.py:270-280），恢复时载入（app/__main__.py:234-244；app/stdio.py:209-237）。
- 段提示仅“继续目标 + 先核对工作区 + 不要重复已完成”（tui/src/goal.ts:57-73）；没有验收标准、阻塞项、验证计划。
- W3 验收记录证明“目标/约束/改动/验证/未完成项”在恢复后可被总结出来（docs/specs/w2-w3-acceptance.md:136-146,164-170），但这是模型+历史的效果，不是结构化目标状态。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 无验收标准 | objective 一段话；完成判定只看本轮验证覆盖（core/task/state.py:53-86） | 跨段的“做完了吗”只能靠模型自述与人工读回执，无法逐条核对 |
| 无段账本（Python 侧） | 段信息只在 TUI 内存与 goal.json；rounds 无 goal_id/segment | 崩溃/换客户端后无法重建“第几段、每段做了什么、为何停” |
| 无约束/阻塞结构 | `constraints` 仅 steer；`open` 由模型写（core/task/state.py:207,268-273） | 用户的验收要求、已知障碍不是 harness 事实，下一段容易漏掉 |
| 无验证计划载体 | 每段重复发现“该跑什么测试” | 验证标准不稳定，易出现“上次通过、这次没跑” |
| Python 读不到目标 | goal.json 只有 TS reader（tui/src/goal-store.ts:42-54） | 01 的 worker、02 的重启、06 的 goal 预算都拿不到目标语义 |
| 无 plan_id 预留 | plan 是字符串列表（core/task/state.py:200） | 未来结构化计划（12 块）无处挂接 |

## 4 设计选项与推荐

| 方案 | 目标状态存在哪 | 兼容/迁移 | 改动面 | 风险 |
|---|---|---|---|---|
| A 最小增量 | `TaskState` 加 goal_id/acceptance_criteria/segment 字段，Python 读 goal.json | 直接改 state schema | 小 | TaskState 变成“目标+执行”混合体；goal.json 仍是 TUI 权威，Python 只读 |
| **B 独立 GoalRecord（推荐）** | `<session>/goal.json` 由 Python 写，GoalMode 变成其 TUI 视图 | 先双写/兼容读取，后切权威 | 中：schema、状态机、迁移 | 需要定义 v1 兼容窗口与 TUI 降级路径 |
| C 独立存储/服务 | SQLite/HTTP 服务 | 需要数据迁移 | 大 | 单用户本地场景过度设计 |

**推荐 B**：目标语义独立于 run 执行状态（TaskState 仍是“本轮工作台”），GoalRecord 可按 task/goal 聚合、可被 01/02/06 读取；TUI 的 `GoalMode` 退化为 `GoalRecord` 的展示与命令入口。

迁移策略（按 §9 决策）：权威文件改为 `<session>/goal-record.json`（schema_version=2），Python 单写；`goal.json` 只作旧 TUI 的兼容读取/迁移来源，P0 可短暂双写但以 record 为准；TUI v2 直接读 record 后删除旧文件。`goal.json` v1 校验器与 spread 行为不变，保证回退窗口。

## 5 最小实现切片

### GoalRecord schema（草案）

```jsonc
{
  "schema_version": 2,
  "goal_id": "g-20261102-143000",
  "task_id": "t-20261102-143000",
  "objective": "完成跨文件重构并跑通全量测试",
  "state": "created | running | paused | completed | failed | cancelled",
  "acceptance_criteria": [
    {"id": "ac1", "text": "全量 pytest 退出 0", "verify": "bash:python -m pytest -q", "source": "user"}
  ],
  "constraints": [{"text": "不要改测试文件", "source": "steering|user|project"}],
  "segments": [
    {"segment": 1, "run_id": "ab12cd34", "status": "stopped",
     "stop_reason": "max_run_seconds", "usage": {"total_tokens": 0},
     "verified": null, "round_ref": "rounds/round_003.json"}
  ],
  "open_items": [{"text": "…", "source": "model|harness"}],
  "blocking": [{"text": "…", "since_segment": 2}],
  "verification_plan": [{"command": "python -m pytest -q", "covers": ["ac1"]}],
  "budget": {"max_goal_tokens": 0, "max_goal_cost_usd": 0, "max_goal_seconds": 14400},
  "plan_id": null,
  "source": {"session_id": "20261102-143000", "created_by": "user"}
}
```

### 状态机（建议）

```text
created → running → (segment ends) → running
                     ↘ paused  （资源停止或 verified=false；等 resume/attach）
                     ↘ completed（所有验收标准有证据）
running → failed / cancelled
paused → running（显式 resume，开启下一段）
```

- 段结束规则直接复用现有 `/goal` 语义（tui/src/goal.ts:75-130）：自动续段的 stop_reason 集合不变；`no_progress/context_window/error/cancelled` 暂停。
- “完成”判定升级为：现有完成守卫 + 验收标准覆盖（逐条绑定证据）；P0 可先保留现有 done 语义，只把 `acceptance_criteria` 记录为待核对。

### 切片

- **P0**：新增 `app/runtime/goals.py`（GoalRecord 读写、v1 兼容、段账本追加）；stdio 在 run 前读 GoalRecord、run 后在 `on_round`/结束处追加 segment 与更新 state；按 §9 写 `goal-record.json`（P0 可双写 `goal.json` 供旧 TUI 读取）。
- **P1**：TUI 改为读 `record`（v2）；下一段提示词改由 Python 从 GoalRecord 生成（含验收标准/阻塞/未完成项摘要）；`/goal` 查看显示段账本；与 01 的 task 状态机合并（goal 是 task 的一种）。
- **修改点**：新增 `app/runtime/goals.py`；`app/stdio.py` 在 run 前后读写 GoalRecord；会话目录复用 `SessionStore.root`（不新增目录）；TUI 暂不改（P1 才改读 record）。
- **测试点**：schema 读写 roundtrip；v1→v2 兼容（TUI validator 不拒绝、TUI 保存不丢 record）；段账本 append-only 与崩溃后重建；损坏 goal.json 被忽略（沿用 tui 行为）；state 迁移（created→running→paused→running）；verified=false 暂停；`/goal resume` 段号 +1 语义不变。
- **明确不做**：LLM 单方面决定验收标准（允许模型提议、必须用户确认，见 §9）；多 goal 并行；跨 goal 合并；结构化计划实体（12 块）；完成验证门本体（15 块，属前置切片）。

## 6 与其它块的关系

- **01**：GoalRecord 是 `kind=goal` 任务的载荷；01 的 task_id/goal_id 与本文 schema 对齐。
- **02**：worker 重启后读 GoalRecord 决定“第几段、从哪继续”，而不是重放历史。
- **06**：`budget` 块是账本的声明侧；实际累计由 06 写入，GoalRecord 只保留上限与段引用。
- **03/04/13/15**：无人值守审批、隔离、计划审批门、完成验证门分别消费 `constraints`/`acceptance_criteria`/`verification_plan`；本文只定义字段与状态，不定义审批/验证流程。

## 7 与子 Agent 的关系

**A 谁发起**——子任务由父 task 派生，子任务不新建 goal（避免目标发散），只引用 `parent_goal_id`；用户可显式要求“委派一个只读调查”。**B 能力边界**——只读子 Agent 的结论可以写进父 GoalRecord 的 `open_items`/证据引用；写类子 Agent 先不做。**C 结果可信度**——子 Agent 对“目标已达成/找到原因”的报告是 claim：父侧要用 GoalRecord 的验收标准与证据逐条复核，子报告本身不能置 `completed`。

对 05 的数据要求：段账本需能挂 `child_id`/`delegation_id`（预留字段），否则父目标无法解释“哪一段是子 Agent 做的”。规格见 docs/specs/multi-agent-computer-use.md:284-340。

## 8 开放问题与验收标准

待用户拍板：

1. 验收标准从哪来：只由用户显式给出，还是允许模型提议后由用户确认（类似偏好确认流程）？

回答：验收流程按照开发专业流程，验收标准模型提议后由用户确认，模型可以根据任务推断验收标准供用户选择（TUI展示选择框）。

2. GoalRecord 的权威文件：继续用 `goal.json` 双写，还是切到独立 `goal.json` v2/新文件名（如 `goal-record.json`）？

回答：goal-record.json

3. `open_items` 的来源：只信 harness 证据，还是允许模型写（现 `TaskState.open` 就是模型写）？

回答：harness 证据为强证据，模型写只是作为佐证，模型的completed要限制在verfiy里面的情况，就可以只使用harness作为强证据。

4. 完成语义：P0 是否继续“done + verified 即完成”，还是要等 15 块的验证门？

回答：p0 可以， 未来还是要完成15 块的验证门

5. 段账本保留粒度：只保留 state/status/usage 摘要，还是保留每段回执全文引用？

回答：只保留 state/status/usage 摘要

验收条件（建议）：

- 一个 3 段以上的 `/goal`：每段都有 GoalRecord segment 记录（run_id/stop_reason/round_ref），重启后能重建。
- 验收标准可逐条对照证据（P1）；未覆盖时不允许把 goal 标 completed。
- `goal.json` 迁移不破坏现有 TUI：旧 validator 可读、新字段不丢、`/goal resume` 段号语义不变。
- 压缩/长历史场景下，目标、约束、未完成项仍出现在下一段提示与 state 渲染中（w3-compaction 口径，docs/specs/w2-w3-acceptance.md:168-170）。

## 9 决策记录（2026-11-03）

1. **验收标准来源（Q1，已确认）**：模型提议、用户确认。流程：模型在 goal/plan 首段产出 `acceptance_criteria[]`（每条含 `text/verify/required`），TUI 用选择框展示（可编辑/勾选/删除），用户确认后写入 GoalRecord 并标 `source=user+model_proposed`；未确认的候选不参与完成判定。改验收标准需要重新确认；CLI/后台任务必须携带已确认的 criteria 文件，否则暂停等确认。
2. **权威文件（Q2，已确认）**：`<session>/goal-record.json`（schema_version=2，Python 单写）。`goal.json` 只作旧 TUI 兼容读取/迁移来源；P0 可短暂双写，但以 record 为准，TUI v2 迁移后删除旧文件。v1 校验器与 spread 行为保持不变，保证回退窗口。
3. **`open_items` 来源（Q3，已确认）**：harness 证据是强证据，模型写入只作佐证。字段建议 `{text, status: open|done|blocked, source: harness|model, evidence_refs[], updated_at}`；模型可以把条目标成 `open/blocked` 或描述进展，但**不能把条目标成 `done`**；`done` 只能由 harness 证据/验证门产生。子 Agent 的结论同样只是 claim，需父侧证据复核。
4. **完成语义（Q4，已确认）**：P0 继续“done + verified”作为完成；未来接入 15 块的完成验证门后，改为“验收标准逐条有证据 + 验证门通过”。`verified=false`/`null` 的语义不变，不用完成状态吞掉未验证。
5. **段账本粒度（Q5，已确认）**：只保留摘要——每段 `segment/run_id/status/stop_reason/usage/verified/round_ref`，不复制回执全文与命令输出；完整证据通过 `round_ref`/`/evidence` 回取。会话保留策略与 02 §9 一致（软上限 + 显式清理）。
**补充（2026-11-03，来自 13-Q4）**：`/goal` 总时长包含审批等待；报表同时分别统计 Agent active time 与 human approval time，不合并成一个数。
