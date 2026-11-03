# 11 模式契约：工具子集 / 审批 / 预算 / 提示词 / 完成策略 profile

> 状态：讨论稿（2026-11-02；2026-11-03 01–05 决策对齐见 §9，06–10 对齐见 §10，用户回答见 §11）；不是 spec，未承诺实现。
> 所属：进阶模式 / 第 2 块
> 前置/相关：[01 任务运行时](01-task-runtime.md)、[03 无人值守审批](03-unattended-approval.md)、[04 隔离与并发](04-isolation-concurrency.md)、[06 预算账本](06-budget-ledger.md)、[07 控制面](07-control-plane.md)、[09 无人值守安全](09-unattended-safety.md)、[10 模式状态机](10-mode-state-machine.md)、[12 结构化 Plan 产物](12-plan-artifact.md)、[16 子 Agent 的有机结合](16-subagent-integration.md)；相关规格：[approval-tiers](../../specs/approval-tiers.md)、[verified-completion](../../specs/verified-completion.md)、[multi-agent-computer-use](../../specs/multi-agent-computer-use.md)

## 1 问题与范围

[10](10-mode-state-machine.md) 只定义 mode 的身份与转换。本文回答下一个问题：**每个 mode 到底改变什么、在哪一层强制、模型如何看到一致的边界。**

核心原则：**能力边界必须在执行层强制；提示词只是告知，不是约束。** 一个 plan 模式如果只是系统提示写「你只能读」，它就不是模式。

对齐 D1（Python `TaskRuntime` 为权威）：profile 的解析与落位放在 `TaskRuntime` 创建 task 时——按 `TaskRecord.mode` 解析 profile，并把 `approval_policy`/`budget`/工具子集写进任务身份（[01](01-task-runtime.md) §5）；TUI 只是客户端，不解析 profile。

第二批（D6–D10）进一步收紧了 profile 的边界：预算采用三层模型（D6）；无人值守写采用「创建时预授权 + 运行时通知/审计」（D9）；控制面能力按类别声明、控制/审批缺失 fail closed（D7）；goal 段内的 plan 采用方案 A、不切换 `task.mode`（D10）。本文按这两批决策重写。

本文范围：mode profile 的字段、解析与校验、执行层如何保证「prompt 说的工具集 = 实际 registry」、chat/act/plan 三份 profile 草案、P0/P1 切片。不在本文范围：TaskRuntime 本体（[01](01-task-runtime.md)）、审批队列交互（[03](03-unattended-approval.md)）、worktree/单写者（[04](04-isolation-concurrency.md)）、预算账本实现（[06](06-budget-ledger.md)）、Plan schema（[12](12-plan-artifact.md)）、计划审批门（13 块）。

## 2 现状与证据

| 维度 | 今天怎么定 | 证据 | 今天能否按 run 变 |
|---|---|---|---|
| 任务级 profile 载体 | 没有 TaskRecord/TaskRuntime；profile 只能进程级 | [01](01-task-runtime.md) §2 | 否 |
| 工具集 | `ToolRegistry` 默认持有全部内置工具；`subset()` 只在审计子 Agent 显式创建时使用；`tools_enabled=False` 整体关闭 | `app/tools/registry.py:887-895`、`app/session/auditor.py:250`、`app/runtime/agent.py:307` | 只能进程级 |
| 审批 | `ApprovalGate` 单对象 + autonomy 档位 + T0–T4/作用域；无 handler fail closed；`request()` 是同步逐次回调 | `app/safety/policy.py:116-192`、`app/safety/policy.py:159-161`、`app/safety/permissions.py:58` | 可在 run 之间改 |
| 创建时预授权 | 不存在：没有创建时确认 capability 集，也没有自动放行的 notice/审计 | [03](03-unattended-approval.md) §9、[09](09-unattended-safety.md) §10 | 无 |
| 预算 | `Agent` 构造参数（steps/seconds/tokens/cost），来自 config env；一层 run 级 | `app/runtime/agent.py:267-306`、`app/runtime/agent.py:721-725`、`app/config.py:22-35` | 只能进程级 |
| 提示词 | 构造时按 `tools.names()` 拼一次；chat 版本另写一段写死 | `app/runtime/agent.py:343-410` | 只能进程级 |
| 完成策略 | `completion_guard` / `workspace_audit` / `llm_auditor` 三个 bool；无用户确认 criteria 接口 | `app/config.py:59-62`、`app/runtime/agent.py:338-341` | 只能进程级 |
| capability 声明 | `ready.capabilities` 是固定字符串列表；未知请求类型直接抛错，没有客户端声明/协商 | `app/stdio.py:725-726`、`app/stdio.py:598-604` | 无 |
| 委派策略 | 不存在：无 `delegate` 工具，无 task/mode 级委派规则 | `docs/specs/multi-agent-computer-use.md:47` | 无 |
| 只读子 Agent | `subset()` 已存在，审计子 Agent 手写一套受限构造参数 | `app/tools/registry.py:887-895`、`app/session/auditor.py:250-266` | 硬编码 |

结论：这些开关分散在 CLI、环境变量、`Agent` 构造参数和 TUI 客户端四处，没有单一事实源；「模式」只能靠启动命令组合出来（D1 之前）；确认后由 TaskRuntime 按 TaskRecord 统一解析。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| profile 无单一事实源 | 一个 profile 的差异要同时改 CLI、env、构造参数、提示词 | 组合爆炸、易漂移、无法测试矩阵 |
| 无任务级 profile 快照 | 没有 TaskRecord；profile 解析结果不落盘 | 无法解释「这个 task 为什么只有这些工具/预算」 |
| prompt 与 registry 无自动一致性 | 默认提示词按工具名拼（好），但 chat 版写死、审计版手写；未来自定义 system 可能说错 | 模型按提示词调用不存在的工具 |
| 审批不随 mode 变 | plan 模式如果仍挂写工具，就会弹写审批而不是根本不给 | 「只读」靠自觉，审批负担也没降 |
| 预算只有 run 一层 | 只有 `max_run_*` 段边界与 env 上限，没有 global safety cap 与 task 软/硬预算 | 长任务要么 runaway，要么一刀切失败（D6） |
| 无人值守写无预授权载体 | 审批逐次回调；创建任务时无法确认 capability 集，自动放行不留 notice/审计 | 「谁授权了什么」无法回答（D9） |
| capability 无类别声明 | 固定字符串、无客户端声明；控制/审批能力缺失时可能静默降级 | 新旧前端行为不一致，危险操作可能被弱化（D7） |
| 完成策略不随 mode/criteria 变 | plan/goal 与 act 共用守卫；没有「用户确认 criteria」这一层 | 未确认标准可能被当成完成依据（D5/D10） |
| 委派策略缺失 | 无法表达「plan 允许只读子 Agent、act 禁止模型自发委派」 | 子 Agent 接入没有准入闸（[16](16-subagent-integration.md)） |

## 4 设计选项与推荐

### 4.1 候选

| 方案 | profile 事实源 | 优点 | 风险 | 结论 |
|---|---|---|---|---|
| A 硬编码 if/else | 写在 `Agent.__init__` 里 | 改动最小 | 组合爆炸、不可测、文档必然漂移 | 不选 |
| B 纯配置文件 | `modes.json`，项目可覆盖 | 用户可调、无需发版 | 过早产品化；项目可放安全边界；schema/迁移成本 | P2 再评估只读覆盖 |
| C 代码 registry + 项目收紧 | `app/runtime/modes.py` 常量；project overlay 只能收紧 | 可测、可 review、硬边界不可降 | 改 profile 要发版 | **推荐** |

### 4.2 profile 字段

| 字段 | 语义 | 是否可被项目覆盖 |
|---|---|---|
| `tools` | 工具名 tuple，或 `"*"` 表示全部 | 只能删，不能加 |
| `approval` | 审批策略上限：`autonomy`、requester/decider 归属、required 审批超时→任务失败；写/执行能力需创建时预授权（D3/D9） | 只能收紧 |
| `isolation` | 能力上限：`shared_readonly` / `sandbox_exec` / `worktree_write`；后台写必须 worktree + 预授权（D4/D9） | 不可放宽 |
| `budget` | 三层预算引用（D6）：task/goal 软预算、`hard_task_budget` 开关、run/segment 边界；global safety cap 不在 profile 内 | 只能降低/收紧 |
| `system` | `"default"` / `"chat_only"` / `"plan_only"`；内容由实际工具名生成 | 不可覆盖 |
| `completion` | 完成策略：P0 `done+verified`；criteria 就绪后切「用户确认 criteria + 验证门」，用 feature flag（D10） | 只能加严，不能关闭 |
| `evidence` | 是否要求验证命令、是否要求 Plan 产物 | 不可放宽 |
| `delegation` | 模型能否自发委派、允许 role、深度上限；子审批归父 task 收件箱（D3） | 不可放宽 |
| `capability` | 流程需要的控制面能力类别（D7）：`task.*`、`approval.decide`、`plan.review`、`attach.observe`；控制/审批缺失 fail closed | 不可放宽 |
| `output_contract` | `free_text` / `plan_v1`（见 [12](12-plan-artifact.md)） | 不可覆盖 |

两批决策后的硬规则：

- **budget 三层**：`global safety cap`（不可关闭，触发即暂停/取消并审计）> `task/goal` 软预算（记录 + 80% 预警，默认不直接失败；显式 `hard_task_budget` 才失败/暂停）> `run/segment` 边界（`max_run_*` 触发段边界，由 TaskRuntime 决定下一段，不是任务成本失败）。profile 只能收紧 task 软/硬预算与段边界，不能抬 global；上限声明落 `goal-record.json.budget`（goal）或 `TaskRecord.budget`（D6）。
- **mode 记忆与不可变**：入口按「项目级最近 mode + 会话覆盖」选默认，创建时显式传给 TaskRuntime；`TaskRecord.mode` 在一个 task 生命周期内不可变，跨段不允许切换（D10）。profile 不参与 mode 记忆。
- **预授权**：任务创建时用户显式确认 capability 集（沙箱执行、worktree 写等）；自动放行写 notice/审计；profile 变更走审批/审计；opt-in 只有本机 OS 用户可改，项目配置只能收紧（D9）。

### 4.3 三份 profile 草案

| 字段 | chat | act | plan（P1） |
|---|---|---|---|
| tools | —（`tools_enabled=False`） | `"*"` | `READ_ONLY_TOOL_NAMES` + `submit_plan` |
| approval | 无工具即无审批 | 随用户档位；后台写创建时预授权 + notice/审计 | 只读；`submit_plan` 走独立 plan.review 确认；required 超时→任务失败 |
| isolation | `shared_readonly` | `shared_readonly`；后台写 → `worktree_write`（预授权） | `shared_readonly` |
| budget | 沿用 config | task 软预算（80% 预警）+ run 段边界；`hard_task_budget` 默认关 | task 软预算 + run 段 `300s/20 步/60k`（D6） |
| system | `chat_only` | `default` | `plan_only`（只读 + 产出 Plan） |
| completion | 全关，`verified=null` | 守卫+审计（P0 `done+verified`） | 候选 criteria 需用户确认；P0 不参与判定，flag 后严格 |
| evidence | 无 | 常规验证记录 | 计划必须引用文件/行号证据 |
| delegation | 禁止 | `model_initiated=false` | P1 允许 harness 内部只读 explore（`requester=harness`） |
| capability | `task.list`（只读） | `task.*`、`approval.decide`、`attach.observe` | `plan.review`、`task.*` |
| output_contract | `free_text` | `free_text` | `plan_v1` |

### 4.4 执行层如何保证「prompt 工具集 = 实际 registry」

```python
# TaskRuntime 创建 task 时解析；profile 是解析结果，不是第二事实源
profile = resolve_profile(task.mode)                 # 未知 mode -> ValueError
missing = set(profile.tools) - set(registry.names())  # 拼错的工具名
if missing: raise ValueError(f"profile tools missing: {missing}")
tools = registry if profile.tools == "*" else registry.subset(profile.tools)
system = build_system(tools.names(), profile.system)  # 复用 app/runtime/agent.py:343 的逻辑
task.approval_policy = clamp_approval(profile.approval, user_policy, pre_auth)
task.budget = declare_budget(profile.budget, goal_record.budget, config)
require_capabilities(profile.capability, client_caps)  # 控制/审批缺失 -> fail closed（D7）
```

- `subset()` 只改 `clone._tools`，而 `specs()`（`app/tools/registry.py:906`）和 `execute_async()`（`app/tools/registry.py:919`）读同一个字典，因此**两层同时生效**；模型即使幻觉出未暴露工具，执行时返回 `unknown_tool`（`app/tools/registry.py:924`），不弹审批。审批不能靠 subset 代替：只读工具走 T0 免审批，写/执行工具在 plan profile 里根本不存在，审批 profile 只是「档位上限」。
- 硬边界不能由 profile 放宽（D3/D4/D6/D9）：后台写必须在独立 worktree 且动作进审批队列（默认挂起）；required 审批任务级超时 → 任务失败（`reason=approval_timeout`）；合并默认用户最终批准；global safety cap 不可关闭。profile 只能更严，不能把这些改成自动放行。
- 预算按三层合并（D6）：profile 声明 task 软预算/`hard_task_budget`/run 段边界；实际执行由 [06](06-budget-ledger.md) 的 `BudgetLedger` 与 TaskRuntime 在「段起点检查 task 级、随时中断 global」处落地；run 级 `max_run_*` 只触发段边界，不把 task 判失败。P0 用 `llm_end` 聚合，P1 才有 per-request compact ledger。
- 预授权与 notice（D9）：创建时用户确认的 capability 集写入 TaskRecord/审计；自动放行写 notice 审计事件（谁授权、profile、task、时间）；profile/opt-in 变更必须写审批/审计，不能静默放宽。
- capability 协商（D7）：服务端在 `ready` 广告类别、客户端声明；只读能力缺失可降级 + 提示；控制/审批/写能力缺失 **fail closed**（`capability_required`）；未知字段只在已协商 capability/version 内忽略新增可选字段，影响控制语义的字段（`mode/task_id/approval_id`）必须显式支持。
- 完成语义按 D10 过渡：P0 保持 `done+verified`；有 `confirmed=true` criteria 时按严格判定，全面切换用 feature flag（15 前置切片验收后默认开）；plan/goal 的候选 criteria 在用户确认前不参与判定。
- autonomy 只降不升：`effective = strictest_autonomy(profile.approval, config.autonomy)`（`app/safety/permissions.py:68`），mode 不能替用户扩权。
- chat 保持 `tools_enabled=False` 而不是空 registry：provider 请求里不带 `tools` 字段，避免「声明了空工具集」的语义差异；`app/runtime/agent.py:307` 已有开关。
- 失败关闭：profile 缺字段、工具名拼错、`system` 变体未知、必需 capability 缺失 → 任务创建失败并给出 mode/profile 名，不静默降级。

## 5 最小实现切片

**P0：chat/act profile + 解析器**（前置：01 P0 的 `app/runtime/tasks.py`；profile 由 TaskRuntime 调用而非入口各自解析）

```python
# app/runtime/modes.py（草案）
@dataclass(frozen=True)
class ModeProfile:
    tools: tuple[str, ...] | str
    approval: str                 # ask|edits|workspace|full 上限 + 预授权要求
    isolation: str                # shared_readonly | sandbox_exec | worktree_write
    budget: dict                  # {"task_soft": {...}, "hard_task_budget": bool, "run_segment": {...}}
    system: str
    completion: dict[str, bool]   # legacy_done_verified / criteria_required / verification_gate
    delegation: dict
    capability: tuple[str, ...]   # 必需 capability 类别（D7）

MODE_PROFILES = {"chat": ..., "act": ...}
def resolve_profile(mode: str) -> ModeProfile: ...      # unknown -> ValueError
def build_system(tool_names: list[str], variant: str) -> str: ...  # 从 app/runtime/agent.py 抽出
```

- 新增 `app/runtime/modes.py`（枚举、profile、校验、`build_system`）；`app/runtime/tasks.py`（01 P0，前置）在 create/start 时解析 profile，把 `approval_policy`/`budget`/profile_id 写进 TaskRecord，worker 用其工具子集；`app/runtime/agent.py` 接受解析后的 system/tools/budget。
- budget P0：只声明 task 软预算 + `hard_task_budget` 开关 + run 段边界；global safety cap 由 TaskRuntime/06 配置读取，不由 profile 设置。
- capability P0：至少声明/校验 `task.list` 与 `approval.decide`；缺控制/审批能力时拒绝进入需要审批的流程（fail closed），只读界面可降级提示。
- mode 记忆 P0：入口（TUI/CLI）读「项目级最近 mode + 会话覆盖」决定默认并显式传入；TaskRuntime 不接受跨段改 mode。
- 兼容窗口：`app/stdio.py`/`app/__main__.py` 在 runtime 开关关闭时仍按 run 前应用 profile（不改成每 run 新建 Agent）；行为与现状一致。
- 记录：TaskRecord.meta 与 round 记录 `mode`/profile_id/tools/budget 摘要；预授权的 capability 集与 notice 审计事件落盘。

**P1：plan profile + 预授权 UI**——`app/runtime/modes.py` 增加 plan；`app/tools/registry.py` 注册 `submit_plan`（仅 plan profile 可见，写 task 目录计划产物，待 [12](12-plan-artifact.md) 定 schema）；计划确认走 `plan.review` capability（13 块）；预授权 UI 与通知接 [09](09-unattended-safety.md)；完成判定接用户确认 criteria（D5/D10 feature flag）。

**测试点**

- profile 声明的工具名全部存在于 `registry.names()`（启动校验，拼错名即失败）；
- chat 的 provider 请求不含 `tools` 字段；act 的 `names()` 与 HEAD `02a6b6c` 一致；
- plan profile 下模型调用 `write_file`/`bash` → `unknown_tool`，不产生审批请求；
- 预算三层：global 触发随时中断；task hard 超限 → 失败/暂停；run 段触发只产生段边界、不判 task 失败；profile 不能抬 global；
- 预授权：创建时 capability 集落盘 + notice 审计；profile 变更需审批；项目配置尝试放宽被拒；
- capability：控制/审批能力缺失 → fail closed（`capability_required`）；只读缺失 → 降级提示；
- TaskRecord 快照：`approval_policy`/`budget`/`mode` 与 profile 一致，round 可离线解释；跨段 mode 不变。

**明确不做**：用户可编辑的 profile 文件（P2）；per-tool 细粒度覆盖；plan→act 审批门（13 块）；子 Agent profile 运行时（[16](16-subagent-integration.md)）；把 bench profile 泛化为 mode（bench 是执行环境，不是工作契约）；不重写 03/06/09 的队列、账本与预授权实现。

## 6 与其它块的关系

| 块 | 关系 | 方向 |
|---|---|---|
| [01 任务运行时](01-task-runtime.md) | profile 在 TaskRuntime 解析，结果写 TaskRecord.mode/approval_policy/budget | 本文依赖 01 |
| [10 模式状态机](10-mode-state-machine.md) | 10 定义 mode 身份、mode 记忆与跨段不可变；本文定义每个 mode 的契约 | 本文依赖 10 |
| [12 Plan 产物](12-plan-artifact.md) | plan profile 的 `tools` 与 `output_contract` 由 Plan schema 决定 | 相互依赖 |
| [03 无人值守审批](03-unattended-approval.md) | profile 只设审批上限；队列、挂起、超时→任务失败、requester/decider 是硬边界 | 本文依赖 03 |
| [04 隔离与并发](04-isolation-concurrency.md) | `isolation` 上限服从单写者/worktree/合并门禁（D4） | 本文依赖 04 |
| [06 预算账本](06-budget-ledger.md) | 三层预算与冲突优先级（D6）；profile 只是声明侧 | 本文依赖 06 |
| [07 控制面](07-control-plane.md) | capability 类别与协商规则（D7）；缺失时 fail closed | 本文依赖 07 |
| [09 无人值守安全](09-unattended-safety.md) | 创建时预授权、notice/审计、opt-in 权限（D9） | 本文依赖 09 |
| [16 子 Agent](16-subagent-integration.md) | `delegation` 字段是委派准入闸；子预算计入父 task（D6）、事件按 D7 | 本文依赖 16 的节点模型 |
| [13 计划审批门](13-plan-approval-gate.md) | plan→act 是否放行由 13 判定，profile 只声明「plan 不可写」 | 13 依赖本文 |

## 7 与子 Agent 的关系

| 维度 | 本文结论 | 落地约束 |
|---|---|---|
| A 谁发起 | profile 的 `delegation.model_initiated` 是模型自行发起的总闸；P0 全部 `false`，plan P1 只允许 harness 内部只读 explore，用户显式入口另算 | 模型工具 `delegate` 必须先有 profile 闸与预算闸 |
| B 能力边界 | 子 Agent 拿到的工具集 = 父 profile 的可见子集 ∩ 子角色 profile；写子 Agent 只能用独立 worktree（P3），且父任务创建时已预授权（D9）；共享工作区写不开放 | 用同一 `resolve_profile` + `subset()` 机制 |
| C 结果可信度 | 子 Agent 报告是 untrusted claim；父侧用确定性证据/验证门复核。profile 不能把「子报告」升级为 `verified` | 子结果写 trace + claim；子 usage 计入父 task（D6） |

现有原型 `app/session/auditor.py` 的审计子 Agent 就是一份**硬编码 profile**：只读工具、无审批、有限预算、结构化解析（`app/session/auditor.py:250-268`）。未来应先把它泛化成「任务节点（child task node）」：`TaskRecord.kind=subagent`，带 `parent_id`/`parent_run_id`/`delegation_id`、独立预算与工具子集、审批归属为 `requester=child`（请求进父 task 收件箱，D3）、独立 trace/session 记录；事件按 D7 capability 声明暴露；再由 mode profile 决定何时允许发起。

## 8 开放问题与验收标准

**待用户拍板**

1. 项目级覆盖是否 P1 就要（只读、只能收紧），还是 P2？建议 P2，先让内置 profile 跑真实任务。

回答：按照你的建议

2. plan profile 是否允许 `update_task`？建议不允许（Plan 用 `submit_plan` 单一写入路径），避免两份计划源。

回答：好的 按照你的建议。

3. CLI 显式预算参数与 profile 冲突时取 min 还是报错？D6 已定 global > task hard > run segment，但 CLI 与 profile 的合并/报错口径仍待定；建议取 min 并提示。

回答：这个显示预算在用户实际使用的场景会使用么？ 预算 上下文窗口 与LLM服务商提供的模型能力强相关。 需要你给我专业的建议供我判断

4. profile 是否进 `verified` 判定输入？建议不进：profile 是边界，不是完成证据。

回答：可以按照你的建议，但是你要说明完成证据目前如何判定。

5. 后台写任务的 profile 是否需要在 `TaskRecord` 里记录 worktree 路径与合并门禁状态（04）？建议记录，但字段细节待 04/07 收敛。

回答：按照你的建议。

**可验证验收条件（P0）**

- 任一 mode 的启动校验：profile 里每个工具名都存在于 registry（missing 即失败，`subset` 不会静默兜底）；
- chat 模型请求无 tools 字段；act 工具集与 HEAD 一致；
- plan profile（P1）下写/执行工具调用在执行层被拒、审批面板零弹出；autonomy ceiling 只能收紧（组合测试）；
- TaskRecord 与 round 能离线回答「这个 task 当时可见哪些工具、预算多少、授权了什么 capability」；
- 全量 Python 回归退出 0，现有 `--chat-only` / `--autonomy` 行为在 runtime 关闭时不变。

## 9 决策对齐（2026-11-03）

本节记录 01–05 已确认决策（D1–D5）对本块的影响；§8 的未决问题保持不变。

1. **任务运行时权威（D1）**：profile 的解析者从「入口各自解析」改为 Python `TaskRuntime` 在创建 task 时解析；结果写 `TaskRecord.mode`/`approval_policy`/`budget`，TUI/stdio 只提交请求（§1/§2/§4.4/§5）。
2. **审批硬边界（D3）**：新增「profile 不能放宽」约束——后台写必须 worktree + 审批队列、required 审批超时→任务失败、合并默认用户批准；`delegation` 子请求归父 task 收件箱（§4.2/§4.4/§7）。
3. **隔离与预算（D4/D5）**：新增 `isolation` 字段（`shared_readonly`/`worktree_write`，写 task 单写者）；`budget` 改为写入 TaskRecord、由 TaskRuntime 在 task/段边界执行；`completion` 对齐「用户确认的 acceptance_criteria 才是完成依据」（§4.2/§4.3）。
4. **路径**：`app/tools.py`→`app/tools/registry.py`（`subset` 890、`specs` 906、`execute_async` 919、`unknown_tool` 924）、`app/agent.py`→`app/runtime/agent.py`、`app/policy.py`→`app/safety/policy.py`、`app/permissions.py`→`app/safety/permissions.py`、`app/auditor.py`→`app/session/auditor.py`；新模块落位 `app/runtime/modes.py`（§5）。
5. **仍未决（保留）**：项目级 profile 覆盖范围（§8 Q1）、plan 是否允许 `update_task`（§8 Q2）、CLI 预算与 profile 冲突（§8 Q3）、profile 是否进 `verified`（§8 Q4）、worktree/合并门禁字段（§8 Q5）。

## 10 第二批决策对齐（2026-11-03）

本节记录 06–10 已确认决策（D6–D10）对本块的影响；§8 的未决问题与 §9 的第一批记录保持。

1. **预算三层（D6）**：`budget` 字段从「单 run 上限」改述为三层引用（global safety cap / task 软预算 + `hard_task_budget` / run 段边界）；补冲突优先级 `global > task hard > run segment`、段起点检查、P0 聚合与 P1 per-request ledger（§4.2/§4.3/§4.4/§5）。profile 不能设置或关闭 global cap。
2. **预授权 + 通知（D9）**：`approval` 字段增加「创建时用户确认 capability 集、自动放行写 notice/审计、profile 变更走审批/审计、opt-in 仅本机用户」；`isolation` 与 §4.4 硬边界同步（§2/§3/§4.2/§4.4/§5/§7）。
3. **capability 类别（D7）**：新增 `capability` profile 字段与协商规则（服务端广告、客户端声明、控制/审批/写缺失 fail closed、未知字段仅在协商内忽略）；§4.4/§5 增加 `require_capabilities` 与测试点（§4.2/§4.3/§4.4/§5/§6）。
4. **mode 记忆与不可变（D10）**：新增「项目级最近 mode + 会话覆盖」的入口默认与「`TaskRecord.mode` 跨段不可变」说明；profile 不参与记忆（§4.2/§5）。
5. **完成语义（D10）**：`completion` 改为 P0 保持 `done+verified`、criteria 就绪后切「用户确认 criteria + 验证门」、用 feature flag 过渡；plan profile 的完成判定前移到 13/15（§4.2/§4.3/§4.4/§5）。
6. **仍未决（保留）**：§8 Q1–Q5 全部保留（项目级覆盖范围、plan 是否允许 `update_task`、CLI 预算合并口径、profile 是否进 `verified`、worktree/合并门禁字段记录细节）。

## 11 用户回答处理（2026-11-03）

**已确认**
- Q1：项目级 profile 覆盖放 P2（先用内置 profile 跑真实任务）。
- Q2：plan profile **不允许** `update_task`；Plan 只用 `submit_plan` 单一写入路径。
- Q4：profile **不进入** `verified` 判定输入；完成证据判定说明见下。
- Q5：后台写任务在 `TaskRecord` 中记录 worktree 路径与合并门禁状态（字段细节由 04/07 收敛）。

**Q3（CLI 预算参数）说明与建议（待回执）**
- 预算在实际使用中主要来自 profile/配置与后台/评测入口；CLI 显式预算是给脚本/自动化/运维用的，不是每次交互都手填。
- CLI 与 profile 同时给上限时建议**取更严格者（min）并提示冲突来源**；profile 不能抬 CLI 的上限；global safety cap 始终最高优先。
- 预算与上下文窗口是两个轴：上下文窗口是模型/Provider 能力（1M 等），任务预算是对成本/资源的治理；不要用上下文窗口推导任务预算，也不要按模型价格表自动改预算。
- P0：CLI 预算参数可选；交互默认取 profile；`/status` 展示本次实际生效的预算与来源。

**Q4（完成证据如何判定）说明**
当前路径仍是运行级完成守卫：action 任务 + 本轮有文件改动时，要求本轮有一次成功 `bash(verification=true)` 且覆盖最后一次改动（`mutation_seq`/`last_verification_seq`），再做只读环境审计（git 基线/文件 hash），可选 LLM 审计做契约反查；`verified` 三态（true/false/null）只由这些环境证据得出。未来切换到 15 的完成验证门 + 用户确认 criteria 后，profile 仍只是边界，不是证据。

**仍未决**：Q3 的“取 min + 提示”是否确认；Q4 仅为说明，无决策项。
