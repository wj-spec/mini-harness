# 12 结构化 Plan 产物：schema / 生命周期 / 存储 / 渲染

> 状态：讨论稿（2026-11-02；2026-11-03 01–05 决策对齐见 §9，06–10 对齐见 §10，用户回答见 §11）；不是 spec，未承诺实现。
> 所属：进阶模式 / 第 3 块
> 前置/相关：[01 任务运行时](01-task-runtime.md)、[05 跨段目标状态](05-goal-state.md)、[06 预算账本](06-budget-ledger.md)、[07 控制面](07-control-plane.md)、[09 无人值守安全](09-unattended-safety.md)、[10 模式状态机](10-mode-state-machine.md)、[11 模式契约](11-mode-contracts.md)、[16 子 Agent 的有机结合](16-subagent-integration.md)；相关规格：[goal-mode](../../specs/goal-mode.md)、[verified-completion](../../specs/verified-completion.md)、[multi-agent-computer-use](../../specs/multi-agent-computer-use.md)

## 1 问题与范围

今天「计划」只是模型用 `update_task(plan=[...])` 随手写的一串字符串。用户要的 `/plan` 模式（[产品 UX 评审](../../research/product-ux-review-2026-10-31.md) 把它列为最缺的交互范式之一，见 `docs/research/product-ux-review-2026-10-31.md:317`）需要的是**可看、可审、可执行、可验收的计划产物**：计划生成后先给用户看，批准后才执行；步骤要能绑定实际改动与验证证据。

2026-11-03 已确认（D1/D5）：`plan` 是一种任务（`TaskRecord.kind=plan`），任务身份在 TaskRecord；Plan 产物是该任务的输出；有 goal 时 `GoalRecord.plan_id` 挂当前计划，而验收标准必须「模型提议 + 用户确认」，未确认候选不参与完成判定。

第二批（D6/D7/D9/D10）进一步确认：goal 内 plan 采用 **方案 A**——`TaskRecord.mode=goal` 在 goal task 生命周期内不变，goal 段内创建/批准 `kind=plan` 的子任务或 plan artifact（走 12/13），不切换 `task.mode`；`submit_plan` 需要独立 UI 确认 + 审计；Plan 的预算/审批字段对齐 D6 三层预算与 D9 预授权；`plan.review` 属于 D7 的能力类别。本文按这两批决策重写产物设计。

本文范围：Plan 的数据结构、生命周期、存储位置与格式、TUI/终端渲染、schema 校验与向后兼容。不在本文范围：TaskRuntime 本体（[01](01-task-runtime.md)）、计划审批交互（13 块：编辑、拒绝、重规划）、Plan→执行的绑定与偏离检测（14 块）、计划验收与完成验证门（15 块）、mode profile（[11](11-mode-contracts.md)）。

关键区分：**Plan 是产物（artifact），不是提示词里的一段文本。** 它有三个消费者——模型（执行时的任务状态）、用户（审查/编辑/确认）、harness（审批、验收、恢复、子任务派发）；任务身份与计划产物分离，经 `task_id` 关联。

## 2 现状与证据

| 能力 | 现状 | 证据 |
|---|---|---|
| 任务身份 | 不存在 TaskRecord/TaskRuntime；plan 只是 TaskState 字段，没有 task_id/goal_id/parent_task_id | [01](01-task-runtime.md) §2 |
| 数据结构 | 自由文本列表 `plan: list[str]` | `core/task/state.py:200` |
| 写入方式 | `update_task` 工具，`plan` 参数是字符串数组，整表覆盖 | `app/tools/registry.py:385-400`、`core/task/state.py:283-296` |
| 模型视图 | `state.render()` 只显示前 5 条、每条截断 120 字符 | `core/task/state.py:214-218`、`app/runtime/agent.py:631` |
| 持久化 | 随 `TaskState` 写进 `state.json`（`asdict`，无独立文件/版本） | `app/session/store.py:314-325` |
| 轮次快照 | rounds 的 `task_state.plan` 复制列表 | `core/task/rounds.py:64-66` |
| TUI 视图 | 只有计数「已记计划 done/planned」，不显示计划内容 | `app/stdio.py:472`、`tui/src/protocol.ts:123-133`、`tui/src/agent-status.tsx:179`、`tui/src/workbench.tsx:213` |
| 生命周期/审批 | 不存在：写入即生效，无 draft/proposed/approved、无 approval 引用、无提交确认 UI | 无对应状态字段 |

结论：plan 只是「模型随手更新的状态数组」，不是可审批、可绑定证据的产物；用户看不到计划内容，harness 也没有判定计划完成的依据。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 无结构 | 步骤没有 id/依赖/涉及文件/验收标准/验证方式 | 无法绑定执行证据，无法判断哪一步真正完成 |
| 无任务身份与审批引用 | 计划没有 task_id/goal_id/parent_task_id/approval_id；批准动作无对象 | 挂不到 TaskRecord/GoalRecord，无法审计谁批准 |
| goal 内 plan 无任务通道 | goal 只有单一 mode 的普通 run；无法在 goal 段内创建 plan 子任务/产物 | goal+plan 只能靠提示词，方案 A 无法落地（D10） |
| 无状态与版本 | 无法区分草案/已提议/已批准；模型可随时覆盖已批准计划 | 计划审批没有对象可审，批准形同虚设 |
| 验收标准未经确认 | `acceptance_criteria` 是模型写的字符串，无 required/verify/source/confirmed | 未确认候选可能被当作完成依据（D5） |
| submit_plan 无确认与审计 | 写计划只是工具调用，无独立 UI 确认、无 capability 检查、无审计事件 | 「谁批准了计划」不可回答（D10/D7） |
| 无独立存储 | 只活在 `state.json`，每轮被覆盖；rounds 里是快照 | 跨 run/跨段/子任务无法稳定引用同一份计划 |
| 渲染截断 | 模型只看前 5×120 字符；TUI 只有计数 | 「先看计划再执行」名存实亡，用户被迫离开终端 |
| 无预算/审批字段 | 计划没有 D6 三层预算引用、没有 plan.review 审批与 requester/decider | 计划级预算与审批无法对齐 06/13 |

## 4 设计选项与推荐

### 4.1 候选

| 方案 | 事实源 | 优点 | 风险 | 结论 |
|---|---|---|---|---|
| A 保持自由文本 | `TaskState.plan` | 零改动 | 永远不可审批/绑定/验收 | 不选 |
| B Markdown 文件 | `plan.md` | 人可读可编辑，接近产品形态 | 无强 schema；解析脆弱；机器绑定弱 | 只作渲染视图 |
| C JSON + Markdown 渲染 | `plan.json`（任务目录）+ 渲染层 | schema 可校验；可版本化/绑定/恢复；人读视图另出 | 需要迁移与存储约定 | **推荐** |

### 4.2 Plan schema 草案

```jsonc
{
  "schema_version": 1,
  "plan_id": "pl-20261102-a1",
  "task_id": "t-20261102-a1",        // kind=plan 的任务身份（TaskRecord）
  "parent_task_id": null,             // goal 段内 plan 子任务指向 goal task（D10 方案 A）
  "goal_id": "g-20261102-a1",        // 可为 null；GoalRecord.plan_id 反向引用
  "version": 1,
  "status": "draft|proposed|approved|executing|superseded",
  "objective": "一句话目标",
  "source": {"session_id": "...", "run_id": "...", "mode": "plan", "task": "原始需求摘要"},
  "created_at": "2026-11-02T23:00:00+08:00",
  "acceptance_criteria": [
    {"id": "ac1", "text": "全量测试退出 0", "verify": "bash:python -m pytest -q",
     "required": true, "source": "model_proposed", "confirmed": false}
  ],
  "budget": {                          // D6：task 软预算 + hard 开关 + run 段边界
    "task_soft": {"max_tokens": 0, "max_cost_usd": 0},
    "hard_task_budget": false,
    "run_segment": {"max_run_tokens": 60000, "max_run_seconds": 300, "max_steps": 20}
  },
  "approval": {                        // D7/D9/D10：plan.review 独立确认 + 审计
    "approved_by": null, "approved_at": null, "approval_id": null,
    "plan_hash": null, "capability": "plan.review",
    "requester": "harness", "decider": "user"
  },
  "constraints": ["AGENTS.md / 用户约束"],
  "out_of_scope": ["明确不做"],
  "open_questions": ["需要用户拍板的问题"],
  "steps": [
    {"step_id": "s1", "title": "读代码定位", "kind": "read", "depends_on": [],
     "files": ["app/x.py"], "acceptance": "找到实际调用点",
     "verification": {"command": "grep -n foo app/x.py", "expect": "非空"},
     "risk": "read", "executor": "self|subagent:explore|subagent:review"}
  ]
}
```

字段取舍：`kind ∈ {read, edit, run, verify}` 决定步骤需要什么能力（子 Agent 只能接 `read`）；`files` 是绑定证据的锚点；`verification` 让「步骤完成」有可执行判定；`executor` 为将来子 Agent 预留（[16](16-subagent-integration.md)），P0 固定 `self`；`acceptance_criteria` 对齐 [05](05-goal-state.md) 的 `{id,text,verify,required,source}`：确认前 `source=model_proposed, confirmed=false`，确认后 `source=user+model_proposed, confirmed=true`（D5）；`budget` 是 D6 三层预算的声明侧（global cap 不在这里、由 06/平台控制）；`approval` 记录 D10 的 `plan.review` 独立确认与 D9 的 requester/decider 归属，批准时写 `approved_by/approved_at/approval_id/plan_hash`（13 块）。

### 4.3 生命周期

| 状态 | 进入方式 | 允许操作 | 退出 |
|---|---|---|---|
| draft | `kind=plan` task 运行中；goal 段内子任务用 `parent_task_id` 指向 goal task（D10 方案 A，`goal.mode` 不变） | 模型修改、用户查看 | `submit_plan` 且 schema 校验通过 → proposed |
| proposed | 候选计划 + 候选验收标准已提交，等待用户 plan.review | 用户查看/编辑/勾选确认/拒绝（05 D5） | 用户确认并批准 → approved；拒绝 → draft |
| approved | 用户通过 plan.review 独立确认验收标准并批准计划（13 块，D10） | 只读引用；不可原地修改 | 开始执行 → executing；被新计划替换 → superseded |
| executing | goal 段或 `kind=run` 执行 task 绑定 approved plan（14 块） | 更新步骤状态（证据化） | 全部步骤完成 → 计划完成（15 块）；偏离 → 重规划 |
| superseded | 新版本/新计划替换 | 只读归档 | — |

不变式：approved 之前 `acceptance_criteria` 只是候选，不参与完成判定；批准时把确认结果写回 `Plan.acceptance_criteria`，有 goal 时同步 `GoalRecord.acceptance_criteria`（D5）。approved 之后**内容不可原地改**；修改 = 同 `plan_id` 升 `version` 并回到 `proposed`，旧版本保留为历史；状态变化必须写 session JSONL 事件，不允许只改文件。`submit_plan` 是 required 确认：任务级 deadline 内未决 → 任务 `failed(approval_timeout)`（D3/D9），不是静默通过。Plan 产物状态 ≠ task 状态：`kind=plan` task 用 01 的 `created/running/completed/failed`，两者通过 `task_id` 关联（[13](13-plan-approval-gate.md) 草案把待批态写作 `pending`，落地时统一为 `proposed`）。

### 4.4 存储

- 位置：`.vibe/sessions/<session>/tasks/<task_id>/plan/plan.v<version>.json`（每版本不可变，挂在 `kind=plan` 任务目录下；goal 段内 plan 子任务用 `parent_task_id` 指向 goal task，产物仍在 plan 子任务目录）；`plan_ref`（`plan_id`+`version`+hash）写入 `state.json` 与 rounds。
- 与目标状态的关系：`TaskRecord`（`kind=plan`）提供任务身份/状态/mode；Plan 产物提供 `plan_id`/版本/approval；有 goal 时 `<session>/goal-record.json` 的 `GoalRecord.plan_id` 指向最新 approved 版本，验收标准以 GoalRecord 中用户确认的集合为准（D5），Plan 里的同名字段是提议快照。
- 预算与审批：Plan 的 `budget` 字段是声明侧（D6：task 软预算 + hard 开关 + run 段边界，global safety cap 由 06/平台控制）；goal 的上限声明在 `goal-record.json.budget`，纯 plan task 在 `TaskRecord.budget`，approved 时校验不超过声明；`approval.capability=plan.review`、requester/decider 写入审计，自动放行须有创建时预授权（D9）。
- 证据链：session JSONL 追加 `plan_proposed` / `plan_review_confirmed` / `plan_approved` / `plan_superseded` 事件（带 `task_id`/`parent_task_id`/`goal_id`/`approval_id`）；rounds 记录 `plan_ref`，不复制计划全文——与 `verified-completion` 的「只存证据引用、不存正文」原则一致。
- 写入：先校验再原子写（临时文件 + `os.replace`，复用 `app/session/store.py:314-325` 模式）；校验失败不落盘半成品，错误回给模型重试；恢复时未知 `schema_version` **fail closed**（不猜测），旧会话无 plans 目录则从 `TaskState.plan` 合成 `legacy` 计划（只读、不可批准）。
- 向后兼容：`TaskState.plan` 保留为**渲染镜像**（由结构化计划派生），旧代码与旧 rounds 继续可读；新增字段全部带默认值（`load_state` 的 `TaskState(**data)` 依赖这一点，`app/session/store.py:327-339`）。

### 4.5 plan task 的输出、确认与只读工具子集

- `kind=plan` task（mode=plan，可作 goal 段内子任务）：工具 = `READ_ONLY_TOOL_NAMES` + `submit_plan`；`submit_plan` 写任务目录下的计划产物，是唯一允许的写入，且属于 harness 产物而非工作区源码（profile 见 [11](11-mode-contracts.md)）；工作区源码保持只读（D10）。
- 独立确认 + 审计（D10）：`submit_plan` 只把计划置为 proposed，必须经 plan.review UI 由用户确认并通过 13 的审批门才生效；缺 `plan.review` capability 的前端 **fail closed**（D7），审计记录 approver/time/capability/plan_hash。
- 预算（D6）：plan task 用 run 段边界（`max_run_*`）暂停/收尾，task 软预算只记录 + 预警；global safety cap 不可绕过；不因 plan 阶段烧掉执行预算——执行预算在后续执行 task/goal 段单独声明。
- 完成判定：plan task 本身不执行改动、不触发完成守卫；候选验收标准不参与任何完成判定，用户确认（05 D5）+ 执行 task 的证据才是后续（14/15）的判定依据；P0 goal 完成仍按 `done+verified`，criteria 严格判定用 feature flag 过渡（D10）。CLI/后台 plan task 未携带已确认标准时应暂停等确认（D5）。

### 4.6 渲染与编辑入口

- P0：`/plan` 由 TUI 作为客户端渲染 TaskRecord 的 plan 产物（Markdown：目标/验收标准/步骤，带「候选/已确认」标记）；后端走 `command_result` 或 TaskRuntime 查询接口，不新建事件类型。
- P1：计划面板（步骤树 + 依赖 + 文件 + 验收）+ `/plan edit`（编辑产生新 `draft`/`version`）；编辑走 TaskRuntime API 生成新版本，**渲染是投影，不回写事实源**；验收标准与计划确认用 plan.review UI（选择框编辑/勾选/删除后确认，05 D5）；客户端未声明 `plan.review` 时只读展示、不可确认（D7）。

## 5 最小实现切片

**P0：schema + 落盘 + 渲染**（前置：01 P0 的 `app/runtime/tasks.py` 任务目录；预算/审批字段按 D6/D9/D10 声明）

- 新增 `app/runtime/plan.py`：`Plan`/`PlanStep`/`PlanBudget`/`PlanApproval` dataclass、`validate_plan()`（必填、step_id 唯一、`depends_on` 引用存在且无环、files 为相对路径、kind/risk 枚举、criteria `confirmed` 字段、budget 三层结构）、`render_plan()`（终端 Markdown）、`from_legacy()`；纯函数优先，便于直接测试。
- 修改：`app/tools/registry.py` 注册 `submit_plan`（仅 plan profile 可见；只置 proposed，不直接生效）；`app/runtime/tasks.py` 创建 `kind=plan` task（goal 段内子任务写 `parent_task_id`）并在任务目录落盘 plan 版本；`app/session/store.py` 增加 `plan_*` 事件与 rounds `plan_ref`；`core/task/state.py` 增加 `plan_ref` 与渲染摘要（镜像）；`app/stdio.py`/TUI 增加 `/plan` 查询、plan.review 确认与审计展示。
- 接口：`submit_plan(plan: object) -> str`（返回校验错误或 `plan_id/version`）；`plan_ref = {"plan_id": "...", "version": 1, "sha256": "..."}`；`review_plan(plan_ref, decision, approved_by, capability) -> approval_id` 由 13 块提供，写 `plan_review_confirmed`/`plan_approved` 审计事件。

**P1**：审批门（13）、执行绑定与步骤状态（14）、验收门（15）、TUI 计划面板与编辑、`executor` 字段启用、per-request 预算明细（D6 P1）。

**测试点**

- schema：缺 objective、step_id 重复、`depends_on` 悬空、依赖成环、绝对路径、非法 kind → 全部拒绝且不落盘；
- 任务挂载：`TaskRecord.kind=plan` 与 `plan.task_id` 一致；goal 内 plan 的 `parent_task_id` 指向 goal task，且 goal `task.mode` 仍为 `goal`（D10 方案 A）；
- 用户确认：未确认候选 `acceptance_criteria` 不进入完成判定；`submit_plan` 后未 review 不生效；缺 `plan.review` capability → fail closed；批准后 `approval` 字段完整（含 capability/requester/decider）；
- 预算：plan 的 `run_segment` 触发只产生段边界；`hard_task_budget` 默认关不误判失败；不得绕过 global safety cap（D6）；
- 确认超时：required 计划审批超时 → plan task `failed(approval_timeout)`，动作不执行且有审计（D3/D9）；
- 落盘/恢复：计划文件原子写，`--continue` 后 `plan_ref` 可解析；未知 `schema_version` fail closed；
- 兼容：旧 `state.json`（无 plan_ref）读取为 legacy 计划，不可批准；旧 rounds 无 `plan_ref` 不报错；
- 渲染：`render_plan()` 对长计划/中文/超长字段截断稳定，模型视图不再出现前 5 条限制。

**明确不做**：不做图形化计划编辑器；不做多计划并行；不做计划自动执行（必须 13/14 先落地）；P0 不删除 `update_task`（兼容期共存，P1 再收敛到单一写入路径）；不把计划正文复制进 rounds/trace；不允许 `submit_plan` 直接修改 `GoalRecord` 的已确认标准；不新增第三套确认机制（计划确认只用 13 的审批门 + 05 的 criteria 确认）。

## 6 与其它块的关系

| 块 | 关系 | 方向 |
|---|---|---|
| [01 任务运行时](01-task-runtime.md) | `kind=plan` task 提供任务身份/parent_task_id；Plan 产物挂 task 目录 | 本文依赖 01 |
| [05 跨段目标状态](05-goal-state.md) | `GoalRecord.plan_id` 挂当前 approved 计划；用户确认的验收标准是完成判定权威 | 本文依赖 05 |
| [06 预算账本](06-budget-ledger.md) | Plan 的 budget 字段是 D6 三层声明侧；global cap/账本在 06 | 本文依赖 06 |
| [07 控制面](07-control-plane.md) | `plan.review`/`task.*` capability 类别与协商规则（D7） | 本文依赖 07 |
| [09 无人值守安全](09-unattended-safety.md) | 创建时预授权与 notice/审计（D9）；后台 plan task 的授权边界 | 本文依赖 09 |
| [10 模式状态机](10-mode-state-machine.md) | plan mode 的产出物；goal+plan 方案 A、`task.mode` 不变（D10） | 本文依赖 10 |
| [11 模式契约](11-mode-contracts.md) | plan profile 的工具集、预算/审批字段与 `output_contract=plan_v1` | 相互依赖 |
| [13 计划审批门](13-plan-approval-gate.md) | 计划的生命周期由 13 的 plan.review 审批驱动（独立 UI 确认 + 审计） | 本文依赖 13 |
| [14 计划执行绑定](14-plan-execution-binding.md) | 步骤状态与证据绑定消费本文的 `steps[].verification` | 本文被 14 依赖 |
| [15 计划验收](15-plan-verification.md) | 计划完成与「完成验证门」消费已确认 `acceptance_criteria` 与步骤证据 | 本文被 15 依赖 |
| [16 子 Agent](16-subagent-integration.md) | `steps[].executor` 是子任务节点入口；子结果只作步骤候选证据 | 相关 |

## 7 与子 Agent 的关系

| 维度 | 本文结论 | 落地约束 |
|---|---|---|
| A 谁发起 | plan task 的只读探索阶段是子 Agent 的第一批真实用例：harness 内部或用户显式触发，模型工具 `delegate` 暂不开放 | 见 [11](11-mode-contracts.md) `delegation` 字段 |
| B 能力边界 | P0 子 Agent 只读；`steps[].executor` 只接受 `subagent:explore` 一类只读角色；写步骤首版仍由父执行；写任务若启用需 worktree + D9 预授权 | 共享工作区写不开放 |
| C 结果可信度 | 子 Agent 的探索结果只是 Plan 的 `source`/证据引用，是 untrusted claim；不能把步骤标为完成，父侧用 `verification` 或验证门复核 | 复用 `app/session/auditor.py` 的结构化输出思路（`docs/specs/multi-agent-computer-use.md:317-325`）；子 usage 计入父 task（D6） |

Plan 同时是**父任务与子任务之间的契约**：子节点（`TaskRecord.kind=subagent`，带 `parent_id`/`parent_run_id`/`delegation_id`）拿到的应是某个 step 的目标、允许路径、验收与输出 schema，而不是父的完整计划；返回结果以 claim + 证据引用进入 `plan_ref` 绑定的步骤证据，再由父侧决定是否计入任务状态。**验收标准的确认不能委派给子 Agent**：它只能提供佐证，最终由用户 plan.review 确认或由已确认标准判定（05 D5/D10）。goal 内的 plan 子任务（方案 A）与子 Agent 是两种不同节点：前者有独立产物与审批门，后者是执行节点。

## 8 开放问题与验收标准

**待用户拍板 / 已确认标注**（2026-11-03 第二批决策见 §10）

1. 事实源选 JSON（推荐）还是 Markdown-first？Markdown 可读性好，但机器校验和版本化会更难。

回答：JSON。

2. （已确认 2026-11-03，见 §10）存储位置：Plan 产物放 `kind=plan` task 目录；跨 task 复用与展示方式仍待定。

回答：按照你的建议。

3. 是否允许用户直接编辑 `plan.json` 文件？建议不允许，编辑必须走 TaskRuntime API 生成新版本，否则证据链断裂。

回答：用户可以在plan一开始审批验证的时候进行编辑调整，后续不可以直接编辑文件。这个plan.json应该在项目内不对用户可见，可以考虑存放在本机的用户数据中。

4. （已确认 2026-11-03，见 §10）`submit_plan` 需要独立 UI 确认与审计（D10）；不再讨论「是否算审批动作」。

回答：需要。 

5. 验收标准在 Plan 与 GoalRecord 双写时谁是真权威？D5 已定 GoalRecord/用户确认为准，但无 goal 的纯 plan task 的权威载体仍待定。

回答：这部分我需要你给出专业建议供我判断。

**可验证验收条件（P0）**

- `validate_plan()` 对 6 类非法输入全部拒绝，且 workspace 无残留文件；
- `submit_plan` 成功后 `plan_ref` 可由 session 恢复，计划内容不被后续 run 覆盖；`TaskRecord.kind=plan` 与 `plan.task_id` 一致；
- 未确认候选标准不参与完成判定；批准后 `approval` 字段完整、`GoalRecord.plan_id` 可解析；
- `/plan` 能渲染目标、验收标准（候选/已确认标记）与全部步骤（长计划不被前 5 条截断）；
- 旧会话/旧 rounds 不报错，legacy 计划标记为不可批准；全量 Python 回归退出 0，`update_task` 旧路径行为不变。

## 9 决策对齐（2026-11-03）

本节记录 01–05 已确认决策（D1–D5）对本块的影响；§8 的未决问题保持不变。

1. **plan 是任务（D1）**：Plan 产物改述为 `TaskRecord.kind=plan` 任务的输出，任务身份/状态/mode 在 TaskRecord，产物只放 `plan_id/version/approval`；转换经 TaskRuntime（§1/§2/§4.3/§4.4）。
2. **Plan 与 GoalRecord 的关系（D5）**：新增 `task_id`/`goal_id` 字段与 `GoalRecord.plan_id` 反向引用；验收标准改为 `{id,text,verify,required,source,confirmed}` 并注明用户确认为准；Plan 里的同名字段是提议快照（§4.2/§4.3/§4.4）。
3. **审批引用（D3/D5）**：schema 预留 `approval{approved_by,approved_at,approval_id,plan_hash}`；批准动作由 13 块写、TaskRuntime 记录；未确认候选不参与完成判定（§4.2/§4.3/§5）。
4. **路径**：`core/state.py`→`core/task/state.py`、`app/tools.py`→`app/tools/registry.py`、`app/session.py`→`app/session/store.py`、`core/rounds.py`→`core/task/rounds.py`、`app/agent.py`→`app/runtime/agent.py`、`app/auditor.py`→`app/session/auditor.py`；新模块落位 `app/runtime/plan.py`（替代原 `core/plan.py` 设想，任务目录与存储属 app/runtime）（§5）。
5. **仍未决（保留）**：JSON vs Markdown（§8 Q1）、产物目录归属（§8 Q2）、是否允许直接编辑文件（§8 Q3）、`submit_plan` 是否审批（§8 Q4）、无 goal 时验收标准权威载体（§8 Q5）。

## 10 第二批决策对齐（2026-11-03）

本节记录 06–10 已确认决策（D6–D10）对本块的影响；§8 的未决问题与 §9 的第一批记录保持。

1. **goal 内 plan 采用方案 A（D10）**：`TaskRecord.mode=goal` 生命周期内不变；goal 段内创建/批准 `kind=plan` 子任务或 plan artifact（`parent_task_id` 指向 goal task），不切换 `task.mode`（§1/§4.2/§4.3/§4.4/§6）。
2. **submit_plan 独立确认 + 审计（D10/D7）**：从「普通工具写」改为 required plan.review 确认；批准走 13 的 UI + 审计事件（`plan_review_confirmed`/`plan_approved`）；缺 `plan.review` capability 的前端 fail closed；工作区源码保持只读（§3/§4.2/§4.3/§4.5/§5）。
3. **预算/审批字段（D6/D9）**：schema 新增 `budget`（task 软预算 + `hard_task_budget` + run 段边界；global cap 不在产物内）与扩展 `approval`（capability/requester/decider）；计划审批超时→任务失败；创建时预授权/notice 规则接 09（§4.2/§4.4/§4.5/§5）。
4. **完成语义（D10）**：P0 goal 完成仍 `done+verified`；有 `confirmed=true` criteria 时按严格判定，全面切换用 feature flag（15 前置切片验收后默认开）；Plan 的候选 criteria 在批准前不参与判定（§4.3/§4.5/§5）。
5. **仍未决（保留）**：JSON vs Markdown（§8 Q1）、跨 task 复用展示（§8 Q2）、是否允许直接编辑文件（§8 Q3）、无 goal 时验收标准权威载体（§8 Q5）；§8 Q4 已确认。

## 11 用户回答处理（2026-11-03）

**已确认**
- Q1：Plan 事实源用 **JSON**。
- Q2：存储位置沿用 `kind=plan` task 目录（跨 task 复用与展示方式后续再定）。
- Q4：`submit_plan` 需要独立 UI 确认 + 审计。

**Q3（编辑与本地存储）落实**
- 计划**首次审批时允许用户编辑调整**；批准后不可直接编辑文件；任何修改必须经 TaskRuntime API 生成新版本（旧版本与审批 hash 保留）。
- 按你的要求：Plan 产物**不进工作区源码、不在 TUI 文件列表展示**，存本机运行数据目录。当前设计仍是 `<session>/.vibe/.../tasks/<task_id>/plan/plan.v<version>.json`（gitignored、本地）；如果你指的是全局 `~/.vibe` 用户数据目录，请确认，我再改路径。
- 不把计划文件当作普通可编辑文件暴露；编辑走审批面板/受限表单。

**Q5（纯 plan task 的验收标准权威）建议（待回执）**
- 单一权威，不双写：有 goal 时，权威是 `GoalRecord` 中用户确认的 `acceptance_criteria`；Plan 只存引用/提议快照。
- 无 goal 的纯 plan task：权威是该 plan task 自己的 `acceptance` 块（用户批准计划时一并确认，`confirmed=true`）。
- 若之后挂到 goal：由 TaskRuntime 做一次带版本号的迁移/合并，重新确认，不并行维护两份。
- Plan 内任何未确认候选都不参与完成判定。

**仍未决**：Q3 的本地存储路径（项目 `.vibe` vs 全局用户数据）；Q5 单一权威方案是否确认。
