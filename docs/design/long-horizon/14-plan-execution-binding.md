# 14 计划执行绑定

> 状态：讨论稿（2026-11-02；2026-11-03 01–05 决策对齐见 §9，06–10 对齐见 §10，用户回答见 §11）；P0 step ledger 已实现，见 [plan-execution spec](../../specs/plan-execution.md)。
> 所属：进阶模式 / 第 5 块
> 前置/相关：[12 结构化 Plan 产物](12-plan-artifact.md)、[13 计划审批门](13-plan-approval-gate.md)、[15 计划验收](15-plan-verification.md)、[16 子 Agent 的有机结合](16-subagent-integration.md)；相关规格：[完成守卫与审计](../../specs/verified-completion.md)、[goal-mode](../../specs/goal-mode.md)

## 1 问题与范围

计划批准之后，harness 要能回答四个问题：现在走到第几步？这一步凭什么算完成？有没有偏离已批准的计划？重规划之后旧证据怎么办？如果回答不了，计划就只是“开场白”，长任务的进度仍然只存在于模型上下文里，跨段、压缩、崩溃恢复后都会失真。

按 [01 任务运行时](01-task-runtime.md) §9 的已确认决策，计划与执行都是 task（`kind=plan` 的任务产生计划，执行段仍挂同一 `task_id`/`goal_id`/`segment` 归属）；step ledger 属于任务，不是 run 的单例附件。按 [05 目标状态](05-goal-state.md) §9，步骤/计划的 `done` 只能由 harness 证据产生，模型与子 Agent 的报告只是 claim。

按 [10 模式状态机](10-mode-state-machine.md) 的 D10 方案 A，goal/plan task 生命周期内**不切换 `task.mode`**：goal 段内创建 `kind=plan` 的 child task，执行绑定挂在该 child task 上，父 goal 的 mode 不变。写任务按 [09 无人值守安全默认值](09-unattended-safety.md) 的 D9 在创建时预授权 capability 集（沙箱/worktree 写），运行时自动放行写 notice/审计；profile 变更需审批/审计。

本文设计 **Plan step ↔ TaskState/rounds/evidence 的绑定**：步骤标识与状态、只有证据能推进的状态机、偏离检测、重规划版本流转、每步预算，以及计划级完成条件。计划验收标准以 [05 目标状态](05-goal-state.md) 已确认的 `goal-record.json` 为准；它如何映射成可执行验证，见 [15 计划验收](15-plan-verification.md)，本文不展开。

标记约定：**现状**＝已实现且有代码/测试；**可选**＝开关默认关；**未实现**＝规划；**建议**＝本文观点。

## 2 现状与证据

| 事实 | 证据 |
|---|---|
| 计划/完成/待办是模型可写的字符串列表，无 step id、状态、证据引用 | `core/task/state.py:200-202`、`core/task/state.py:281-296` |
| `update_task` 只改状态，不算执行证据；停滞检测把它标成 `state_only` | `app/runtime/agent.py:1500`、`core/task/progress.py:89-116` |
| 工具事实带 agent loop 步号（`step`）但不带计划步骤 id | 文件变化 `core/task/state.py:298-345`；验证 `core/task/state.py:347-371`；命令结果 `core/task/state.py:384-390` |
| rounds 快照 plan/done/file_changes/verifications，但没有 step→evidence 映射，也没有 `task_id` | `core/task/rounds.py:21-63`、`core/task/rounds.py:64-81` |
| 计划执行没有任务归属：step 状态与证据只能按 run 记 | [01 任务运行时](01-task-runtime.md) §9、`core/task/rounds.py:50` |
| 完成守卫只按 run 级 `mutation_seq`/最后一次验证判定，不看计划步骤 | `core/task/state.py:53-86`、`app/runtime/agent.py:1148-1210` |
| 计划级完成与已确认验收标准未对接：未确认 criteria 也可能收尾 | `core/task/state.py:53-86`、[05 目标状态](05-goal-state.md) §9 |
| 进展检测只看重复调用/重复读/文件哈希/验证结果变化，不知道计划 | `core/task/progress.py:26-64`、`core/task/progress.py:66-116`、`app/runtime/agent.py:1405-1429` |
| 环境审计能发现未归因改动，但不归到计划步骤 | `app/session/audit.py:265-299` |
| 预算只有 run 级 `max_steps`/时间/token/cost；没有 task/goal 账本，也没有 per-request 成本归因 | `app/runtime/agent.py:721-725`、`app/config.py:22-23`、`core/budget/policy.py:7`、[06 预算账本](06-budget-ledger.md) §10 |
| 摘要/审计等辅助调用成本未归因到 step/task（D6 要求强制计入或标 `unaccounted`） | [06 预算账本](06-budget-ledger.md) §10、[07 控制面](07-control-plane.md) §10 |
| `/goal` 每段是普通 run，段间只注入提示，没有步骤台账 | `tui/src/goal.ts:57-73`、`docs/specs/goal-mode.md` §自动续段 |

结论：**现状没有任何 plan-step 数据结构或证据推进机制**；步骤进度全靠模型在 `plan/done/open` 文本里自述，跨 run、跨任务都无法核对。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 步骤没有身份 | 计划是字符串数组，done 是另一组字符串 | 无法对应“第 3 步做完了”；改计划会错位 |
| 状态靠自述推进 | 模型写 `done` 即“完成”，harness 不校验 | 早停、虚报进度、重复做同一件事 |
| 无偏离检测 | 改到计划外文件、跳过验证只在收尾审计里事后暴露 | 长任务范围漂移发现太晚 |
| 无重规划协议 | 改计划=覆盖数组，无版本/无差异/无重批 | 批准过的计划可被静默替换 |
| 无预算分层 | 只有 run 级上限；task/goal 软预算与 global cap 未实现 | 单步烧掉全部预算；段边界被误当任务成本失败 |
| 计划执行无任务身份 | step 台账只能挂 run；证据与审批没有 `task_id` | 无法按任务恢复、调度单写者或归属成本 |
| 计划级完成证据不足 | 未确认 criteria 或 required 步骤无证据也能 done | 验收标准与完成判定脱节 |

## 4 设计选项与推荐

| 维度 | A 维持模型自述 + 提示词 | B harness 拥有的步骤台账（证据推进） | C 从工具轨迹自动推断步骤 | D 每步一个独立子 Agent，父只汇总 |
|---|---|---|---|---|
| 状态可信 | 否 | 是：状态转移只由 harness 事实触发 | 弱：启发式切分不稳 | 中：子报告仍需复核 |
| 与显式计划对应 | 无 | 强：step id 稳定 | 弱：推断出的步骤与计划对不上 | 强但成本高 |
| 改动范围 | 零 | 中：core/app/rounds/TUI | 大 | 很大 |
| 可恢复 | 否 | 是：台账随 task state/rounds 落盘 | 否 | 部分 |
| 失败模式 | 虚报完成 | 证据映射错会误判 | 步骤漂移 | 预算爆炸 |

**推荐 B：把步骤做成 harness 拥有、挂在任务下的台账，模型只能“请求开始/提交完成”，最终状态由证据决定。** 理由：项目已有可复用的确定性证据（`file_changes`、`verifications`、`command_results` 都带 `step`，`file_changes` 另带 `tool_call_id`），把它们按 step 切片映射到计划步骤成本可控；而“状态只能由证据推进”正是 [LongHorizon 调研](../../research/longhorizon-harness-2026-10-31.md) §10 与本仓完成守卫同一原则。

落到已确认决策上：台账属于 `kind=plan` task（01）；`done` 只由 harness 证据产生（05）；执行写走 04 的 worktree/单写者，合并默认用户最终批准、opt-in 自动合并需合并后重验；偏离/重规划涉及的 required 审批在任务级 deadline 内未决时按 03 让任务失败（`approval_timeout`），而不是静默继续。

不选 A：等于把长任务进度继续托付给模型自述，跨段必然失真。不选 C：没有显式计划时省事，但有显式计划时自动切分与用户批准的对象对不上，无法审批。不选 D：独立子 Agent 属于后置阶段（[multi-agent 规格](../../specs/multi-agent-computer-use.md) §3.5），M1 只读、不写，且当前无子 run 展示；先用台账把单 Agent 做对。

### 4.1 数据结构草案

```jsonc
// PlanStep：plan 工件的一部分；step_id/files/verification 字段名对齐 [12](12-plan-artifact.md)
// 归属由 TaskRuntime 提供（task_id / plan_id / plan_version）；本文新增状态、证据引用、预算与 attempts
{
  "task_id": "task-...", "parent_task_id": "task-goal-...",   // goal 段内 plan 为 child task（D10 方案 A）
  "plan_id": "plan_01", "plan_version": 2,
  "step_id": "s2",
  "title": "把计划门接到写工具执行前",
  "depends_on": ["s1"],
  "status": "pending|in_progress|blocked|done|deviated|skipped",
  "required_evidence": ["command_ok"],        // file_change | command_ok | manual
  "files": ["app/runtime/agent.py", "core/task/plan.py"],  // 12 的 files，同时作为偏离检测锚点
  "evidence": [{"kind": "verification", "ref": "verifications[12]", "round_ref": "rounds/round_003.json"}],
  "budget": {"task_soft_ratio": 0.8, "max_steps": 6, "max_seconds": 300},  // D6：软预算/预警，不单独硬失败
  "started_seq": 14, "attempts": 1
}
```

### 4.2 状态推进规则（伪代码，harness 侧）

```text
finalize_step(task, step_id):
  step = task.plan.step(step_id); refs = evidence_since(task, step.started_seq)
  if "command_ok" in step.required_evidence and no verification(ok=True) since start:
      reject("step_evidence_missing")   # 模型/subAgent 自述与 update_task(done) 都不算证据
  if step.files:
      outside = changed_paths_since(start) - step.files
      if outside: step.status = "deviated"; require replan/approval
  step.status = "done"; step.evidence = refs
  ledger.append(task.task_id, step_id, refs, at=now)   # 进 task state + rounds
```

## 5 最小实现切片

### P0：step id + 证据映射 + 计划级完成条件

- `core/task/plan.py`（新建，纯 stdlib）：`PlanStep`/`StepLedger`（挂 `task_id`/`plan_id`）、状态机、`evidence_since` 映射、`plan_complete(task)`（required 步骤都有证据）。
- `core/task/state.py`：`TaskState` 增加 `step_ledger`（只存引用，不存输出正文）与兼容序列化。
- `app/runtime/agent.py`：工具执行后按当前 step 切分证据；`_complete_run` 前增加计划级检查，未满足时返回原因码（复用完成守卫的拒绝/纠偏模式 `app/runtime/agent.py:1148-1210`）。
- `app/tools/registry.py`：`update_task` 不再直接写 `done` 推进步骤（保留自由文本仅作展示），新增 `complete_step(step_id)` 元数据调用，最终由 harness 证据校验；模型不能把步骤标 `done`（05 决策）。
- `core/task/rounds.py`：`task_state` 增加 `step_ledger` 摘要（step id/status/evidence refs/round_ref）与 `task_id`/`plan_id`，格式版本 +1。
- TUI/`/evidence`：显示当前步骤与证据引用（复用 receipt 与 [evidence](../../specs/verified-completion.md) 口径）。
- 测试点：模型声称完成但无验证 → 步骤不推进；有覆盖验证 → 推进并落 rounds；计划外文件 → 不自动标 done；未确认 criteria 不参与完成判定；旧 `state.json`/rounds 兼容；重复 `complete_step` 幂等。
- 明确不做：不做步骤自动发现；不并行步骤；不做每步独立模型调用（fresh-context 执行器后置）；不改工具审批语义。

### P1：偏离检测 / 重规划 / 步级预算

- 偏离检测表：

| 偏离 | 检测证据 | 建议处置 |
|---|---|---|
| 改到计划外文件 | `file_changes` 路径 vs `files`；`app/session/audit.py` 的 `unattributed` 交叉核对 | 标 `deviated`，暂停请求批准或重规划；required 审批超任务级 deadline → 任务 failed(`approval_timeout`) |
| 步骤未验证就完成 | `required_evidence` 映射为空或 `verification` 非 ok | 拒绝推进，回灌纠偏 |
| 预算超支 | 步级 `max_steps`/`max_seconds` vs 段内观测 | 80% 预警 → 暂停该步等确认（软预算，不硬失败）；显式 `hard_task_budget` 才失败/暂停 |
| 重复步骤 | 同一 step id attempts 超限 / 重复验证 digest | 复用 `ProgressMonitor` 的 `repeated_*` 信号 |
| 计划外命令（可选） | `command_results` 不在计划声明内 | 仅记录 trace，P1 不阻断 |

- 合并门禁（04 决策）：执行发生在 worktree 时，计划完成后的合并默认用户最终批准；opt-in 自动合并只允许 `verified=true` + 只读审阅通过 + diff 无冲突 + **合并后重验通过**，冲突不自动解决；合并后重验证据写入 plan ledger，失败则退回未完成、不得标验收通过。写任务在创建时按 D9 显式确认 capability（沙箱/worktree 写），运行时自动放行写 notice/审计；非 git 工作区 P0 拒绝后台写、只读允许（04 已确认）。
- 重规划：`replan(reason, ops)` → plan `version+1`；未变 step id 保留证据，变更步骤重置 `pending`；影响文件集/required 证据时触发 [13 计划审批门](13-plan-approval-gate.md) 重批；重规划次数设上限（建议 3），超限暂停等用户。
- 预算分层（D6）：`global safety cap`（不可关闭的 runaway 护栏）> `task/goal` 软预算（记录 + 80% 预警，默认不直接失败；显式 `hard_task_budget` 才失败/暂停）> `run/segment` 边界（现有 `max_run_*`，触发段边界而非任务成本失败）。冲突优先级 `global > task hard > run segment`，TaskRuntime 在段起点检查 task 级；步级预算只是软预算下的观测/预警维度，不新增一层硬失败。
- 成本归因（D6）：P0 用 `llm_end` 聚合；P1 增加 per-request compact usage ledger（约 200–500B/请求）把成本归到 step；摘要/审计成本优先强制计入，短期难实现时标 `unaccounted` 并计数；每步同时记录 active time 与 wall clock。
- 与 `/goal`：续段决策由 [01 任务运行时](01-task-runtime.md) 的 TaskRuntime 触发，段开始时读 `step_ledger`，段结束要求 required 步骤均有证据；现有 `goalDecision` 只在 `verified === false` 暂停（`tui/src/goal.ts:80-92`），P1 增加 `plan_incomplete` 暂停原因并把判定迁到运行时。
- 测试点：四类偏离各一例；合并后重验失败 → 退回未完成；重规划保/清证据；重规划超限暂停；审批超时 → 任务 failed；`/goal` 两段之间 step 状态不丢；确定性回归 `./run_tests.sh -q` 不回归。

## 6 与其它块的关系

| 关系 | 块 | 交互 |
|---|---|---|
| 依赖 | [01 任务运行时](01-task-runtime.md) | `kind=plan` task、`task_id` 归属、worker/单写者调度 |
| 依赖 | [12 结构化 Plan 产物](12-plan-artifact.md) | step schema、版本、存储 |
| 依赖 | [13 计划审批门](13-plan-approval-gate.md) | 执行门禁、偏离重批 |
| 依赖 | [03 无人值守审批](03-unattended-approval.md) | required 审批队列/超时→任务失败 |
| 依赖 | [04 隔离与并发](04-isolation-concurrency.md) | worktree/单写者、合并门禁与合并后重验 |
| 依赖 | [05 目标状态](05-goal-state.md) | 已确认 criteria；`done` 只由 harness 证据产生 |
| 依赖 | [09 无人值守安全默认值](09-unattended-safety.md) | D9 创建时预授权 capability；运行时通知/审计 |
| 依赖 | [10 模式状态机](10-mode-state-machine.md) | D10 方案 A：mode 不可变，goal 内 plan 为 child task |
| 依赖 | [06 预算账本](06-budget-ledger.md) | D6 三层预算（global cap / task 软预算 / run 段边界）与 per-request 成本归因 |
| 被依赖 | [15 计划验收](15-plan-verification.md) | 验收映射到 required 步骤的证据 |
| 相关 | [08 长任务评测](08-long-horizon-eval.md) | D8 长任务定义（≥2 段/压缩/30min）与 blocking 门禁；恢复一致性用 step ledger 作证据 |

## 7 与子 Agent 的关系

统一约定（任务节点、父子归属、untrusted claim）见 [16 子 Agent 的有机结合](16-subagent-integration.md)；本块只列差异。

| 维度 | 在本块的落实 |
|---|---|
| A 谁发起 | 步骤开始/完成可由模型工具调用请求，但**状态转移由 harness 证据驱动**；用户可标记 `manual` 步骤。子 Agent 是 `kind=subagent` task，只能产生候选证据，不能直接改父的 step 状态，也不能自己标 `done` |
| B 能力边界 | 只读子 Agent 可做探索/审查并回传 `path:line` 证据；写子 Agent 只在独立 worktree（[04 隔离并发](04-isolation-concurrency.md)）里执行，父侧合并后再记证据；共享工作区写先不做 |
| C 结果可信度 | 子 Agent 的“步骤已完成”是 untrusted claim；其证据必须落在父可复核的事实上（父重新读取/复跑验证/审计核对 hash），证据记录带 `delegation_id` 以便归因 |

## 8 开放问题与验收标准

待用户拍板：

1. 步骤粒度：由模型提议、用户批准，还是 harness 按文件/验收项切分？建议模型提议 + 审批门裁决。

回答：按照你的建议

2. 每步最小证据：默认要求“一次成功的验证命令”，还是允许 `file_change` 就够？建议 required 按步骤类型声明。

回答：按照你的建议，我意识到你说的这个背后的问题，过多的验证会导致效率变慢。 这个需要探索。

3. 偏离处置默认值：warn 继续、暂停等用户、还是自动重规划？（审批过期语义已由 03 决策覆盖；这里问的是默认处置）建议“影响文件集/验收标准 → 暂停”。

回答：按照你的建议。

4. 步级预算默认值与重规划上限：建议 max_steps 6/步、重规划 3 次，均可配置。

回答：这个预算 是不是太少了， 建议值是否太过于保守？ 请你给我说明。

5. `/goal` 段结束时计划未完成怎么办：暂停等用户，还是允许收尾并标未完成？建议暂停，不静默完成。

回答：暂停等用户。

可验证验收条件：

- 无验证证据时 `complete_step` 100% 被拒，原因码可读；模型/子 Agent 报告不能推进 step 状态；
- 每次步骤状态变化都能在 rounds JSON 找到对应 evidence 引用与 `task_id`（只引用，不复制正文）；
- 计划外文件改动被标 `deviated` 并可触发重规划/重批；required 审批超时 → 任务 failed；
- worktree 执行的任务合并后必须重验，重验失败不得标验收通过；
- `/goal` 跨两段后 step ledger 与证据不丢；恢复后不重做已完成步骤，且恢复成功按 D8 = `done+verified` 且无重复副作用（人工 `/resume` 单独报告）；
- `./run_tests.sh -q` 全绿，TUI fake provider 覆盖“虚报完成被拒”场景。

## 9 决策对齐（2026-11-03）

受 D1（plan/run/goal 都是 task）、D3（审批过期→任务失败）、D4（worktree/单写者/合并默认用户批准+合并后重验）、D5（`done` 只由 harness 证据产生）影响。

已按决策就地修改：

- step ledger 与证据从“run 附件”改为挂在 `kind=plan` task 的 `task_id`/`plan_id`/`plan_version` 下，rounds 增记任务归属（§1、§2、§4.1、§5）。
- 状态推进规则明确“模型/subAgent 自述、`update_task(done)` 都不算证据”，`done` 只由 harness 证据产生，与 05 决策一致（§4、§5）。
- 偏离/重规划涉及的 required 审批按 03 决策：任务级 deadline 未决 → 任务 `failed(approval_timeout)`，不静默继续（§4、§5）。
- 新增 04 合并门禁：worktree 执行的任务合并默认用户最终批准，opt-in 自动合并需合并后重验，失败退回未完成（§5、§6）。
- `/goal` 续段改由 TaskRuntime 驱动（01），段末 required 步骤无证据 → `plan_incomplete` 暂停；代码路径同步到分域后布局，行号按当前工作树重新核对（§2、§5）。

仍未决（§8 原问题保留，不擅自拍板）：步骤粒度与最小证据、偏离默认处置、步级预算/重规划上限、`/goal` 段末未完成的处置。

## 10 第二批决策对齐（2026-11-03）

受 D6（三层预算/成本口径）、D9（创建时预授权 + 运行时通知/审计、worktree 写）、D10（mode 不可变、goal+plan 方案 A、完成切换）影响；D8（长任务评测口径）用于验收条件。

已按决策就地修改：

- 预算从“只有 run 级上限”改为 D6 三层：global safety cap > task/goal 软预算（80% 预警，默认不直接失败，`hard_task_budget` 才失败/暂停）> run/segment 边界（段边界不是任务成本失败）；步级预算降为软预算内的观测/预警维度（§2、§3、§5）。
- 成本归因按 D6：P0 `llm_end` 聚合，P1 per-request compact ledger 把成本归到 step；摘要/审计成本优先强制计入（否则标 `unaccounted`），每步同时记 active time 与 wall clock（§2、§5）。
- 归属按 D10 方案 A：goal 段内 plan 是 child task，`step_ledger` 挂该 child task，父 goal 的 mode 不变；JSON 草案加 `parent_task_id`（§1、§4.1、§6）。
- 写任务按 D9 在创建时预授权 capability（沙箱/worktree 写），运行时自动放行写 notice/审计；非 git 工作区 P0 拒绝后台写（§5、§6）。
- 恢复/评测口径按 D8：恢复成功 = `done+verified` 且无重复副作用，人工 `/resume` 单独报告；块级验收沿用 D8 blocking（fake）门禁（§6、§8）。
- approval 超时→任务失败沿用第一批 D3（§5、§9）。

仍未决（§8 原问题保留，§9 的问题继续有效，不擅自拍板）：步骤粒度与每步最小证据、偏离默认处置、步级预算默认值与重规划上限、`/goal` 段末未完成处置。

## 11 用户回答处理（2026-11-03）

**已确认**
- Q1：步骤粒度模型提议 + 审批门裁决。
- Q3：偏离/重规划默认“影响文件集或验收标准 → 暂停等用户”。
- Q5：`/goal` 段末计划未完成 → 暂停等用户，不静默完成。

**Q2（最小证据与效率）建议（待回执）**
- 按步骤类型分层，不做“每步都全量验证”：常规编辑/低风险步骤要求 `file_change` + 轻量检查（语法/构建）；行为变更要求该步骤相关的目标测试/验证命令；跨层/集成步骤要求相关测试 + 计划收尾时一次全量回归。
- 这样既避免“每步全量套件拖慢”，又保证关键证据；全量套件只做一次（15 Q5）。
- 证据要求写进 PlanStep 的 `required_evidence`，审批时可见。

**Q4（步级预算）说明与建议（待回执）**
- 原建议 `max_steps=6/步` 偏保守：一个计划步骤常需要检查→改→测→修，6 个模型步容易在正常修复循环中耗尽。
- 建议默认 **10–12 个 agent steps/计划步骤**、重规划 **3 次**，均为可配置；真正的成本刹车是 06 的 global safety cap + task 软预算，而不是步数。
- 达到步数上限时**暂停并展示证据**，由用户决定继续/放宽，不直接判失败。

**仍未决**：Q2 分层证据方案、Q4 默认 10–12/3 是否确认。
