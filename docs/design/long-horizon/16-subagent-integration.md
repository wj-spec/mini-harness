# 16 子 Agent 的有机结合：任务节点，而不是第二套 loop

> 状态：讨论稿（2026-11-02；2026-11-03 01–05 决策对齐见 §9，06–10 对齐见 §10，用户回答见 §11）；不是 spec，未承诺实现。
> 所属：跨主题（长任务与后台任务 × 进阶模式 × 子 Agent）
> 前置/相关：[01 任务运行时](01-task-runtime.md)、[03 无人值守审批](03-unattended-approval.md)、[04 隔离与并发](04-isolation-concurrency.md)、[05 跨段目标状态](05-goal-state.md)、[06 预算账本](06-budget-ledger.md)、[10 模式状态机](10-mode-state-machine.md)、[11 模式契约](11-mode-contracts.md)、[14 计划执行绑定](14-plan-execution-binding.md)、[15 计划验收](15-plan-verification.md)；相关规格：[multi-agent-computer-use](../../specs/multi-agent-computer-use.md)、[verified-completion](../../specs/verified-completion.md)

## 1 问题与范围

用户接下来要做 subAgent，同时在做长任务/后台任务与 `/plan` 等进阶模式。核心问题：**这三件事是三个独立项目，还是同一套基础设施的三个消费者？**

本文的结论：**把子 Agent 定义成「任务节点（task node）」——由任务运行时统一调度、预算、审批归属、隔离和证据管理；它不是 Agent loop 里的特殊分支，也不是第二套运行时。** 本文只定架构关系与准备顺序，不复述 M1/M2/M3 的功能规格（已有 [multi-agent-computer-use](../../specs/multi-agent-computer-use.md) §3.5）。

## 2 现状与证据

| 能力 | 现状 | 证据 |
|---|---|---|
| 模型可见子 Agent | **没有**。没有 `delegate` 工具，也不存在父子 run 协议 | `app/tools/registry.py`（工具表）、`tui/src/protocol.ts` |
| harness 内部只读子 Agent | **已有原型**：LLM 审计在全新上下文里用一个只读子 Agent 跑，独立 history/state、只读工具、无审批、结构化解析 | `app/session/auditor.py:247-293`（`audit()`）、`_SYSTEM`、`AuditRequest` |
| 工具能力收窄 | `ToolRegistry.subset(names)` 已实现，供只读审计使用；只读集合已具名 | `app/tools/registry.py:59`、`app/tools/registry.py:881-886`；`app/session/auditor.py:250` |
| 审批隔离 | `ApprovalGate` 是单对象，session/workspace 授权存在对象内；没有 parent/child 归属 | `app/safety/policy.py:24-44,116-192` |
| 预算聚合 | 子 Agent 的 usage 有记录（`audit.llm.usage`），但**不计入父 run 的 tokens/cost** | `app/session/auditor.py:271`；`app/runtime/agent.py:1211-1256` |
| 父子 trace | 只有单层 `run_id`；没有 `parent_run_id` / `delegation_id`，也没有 child trace 目录 | `app/session/trace.py`；`app/session/store.py:339-389` |
| 协议与 UI | 单连接、单 active run；忙时 `busy`；没有子任务事件 | `app/stdio.py:633-635`；`tui/src/protocol.ts` |
| 证据处理 | 已有 verified 三态、环境审计、轮次账本；LLM 审计的输入明确写着 “Executor text is a claim, not evidence” | [verified-completion](../../specs/verified-completion.md)；`app/session/auditor.py:58-86` |

结论：**M1 的“只读子 Agent”在实现上已经有一个可运行原型**（审计子 Agent），但它是“硬编码的内部角色”，不是可复用的运行时能力；M1 规格里的 G0 前置（预算聚合、父子 trace、审批归属、子结果 schema）仍未补齐。长任务/后台任务要补的 9 块，恰好大部分就是 G0 的延伸。

## 3 缺口与归属

| 子 Agent 需要的能力 | 当前缺什么 | 应由哪一块补 |
|---|---|---|
| 可调度的任务单元 | 没有 Task 对象/生命周期/父子关系 | [01 任务运行时](01-task-runtime.md) |
| 后台/异步执行 | 没有 worker、队列、attach、崩溃恢复 | [02 进程与生命周期](02-process-supervision.md) |
| 无审批权与审批归属 | 审批对象单层、无 task/agent 归属、无收件箱 | [03 无人值守审批](03-unattended-approval.md) |
| 写隔离 | 无 worktree/锁；子 Agent 只能“不写”或“共享工作区写” | [04 隔离与并发](04-isolation-concurrency.md) |
| 结果入账 | 目标/段账本没有 child 贡献与 provenance | [05 目标状态](05-goal-state.md) |
| 成本可控 | 子 usage 不聚合、无深度/扇出上限 | [06 预算账本](06-budget-ledger.md) |
| 可观测 | 子任务事件、状态、取消不可见 | [07 控制面](07-control-plane.md) |
| 收益可验收 | 没有“单 Agent vs 委派”的 A/B 指标 | [08 长任务评测](08-long-horizon-eval.md) |
| 默认安全 | 没有能力分级和无人值守默认值 | [09 无人值守安全](09-unattended-safety.md) |
| 何时允许委派 | 模式没有委派策略 | [10 模式状态机](10-mode-state-machine.md)、[11 模式契约](11-mode-contracts.md) |
| 计划步骤的 executor | Plan step 没有 executor/证据绑定 | [12 Plan 产物](12-plan-artifact.md)、[14 执行绑定](14-plan-execution-binding.md) |
| 子结果可信度 | 子报告不是 verified；父侧 verify 门还没有 | [15 计划验收](15-plan-verification.md)（前置：完成验证门） |

## 4 设计选项与推荐

### 4.1 三个必须先定的架构问题

| 维度 | 选项 | 影响 |
|---|---|---|
| A 谁发起 | harness 内部 / 用户显式 / 模型工具调用 | 决定信任边界：模型能自行放大成本与权限吗 |
| B 能力边界 | 只读 / 独立 worktree 写 / 共享工作区写 | 决定隔离成本与冲突风险 |
| C 结果可信度 | 子报告视为事实 / 视为 claim 由父侧复核 | 决定是否会把错误自评写进任务状态 |

### 4.2 推荐形态：任务节点

```jsonc
// TaskRecord（01 的任务模型）与子 Agent 共用；子 Agent 只是 kind 的一种
{
  "task_id": "t-...", "kind": "subagent",        // run | goal | plan | subagent（Plan 见 12）
  "parent_id": "t-parent", "parent_run_id": "run-...", "delegation_id": "d-...", "depth": 1,
  "mode": "act | plan",                          // 来自 TaskRecord，用于模式契约（10/11）
  "role": "explore",                              // explorer / reviewer / executor（未来）
  "capability_profile": "read_only",              // read_only | worktree_write（未来）
  "model": "same|cheap", "budget": {"max_run_tokens": 40000, "max_run_seconds": 120, "max_steps": 8},
  "workspace": {"mode": "shared_readonly|worktree", "ref": "..."},
  "approval": {"policy": "deny_all"},             // 子 Agent 默认无审批权
  "result_contract": "subagent_result_v1",         // 结构化结果 schema
  "trace_ref": ".vibe/tasks/t-.../trace.jsonl",
  "status": "queued|running|done|failed|cancelled|budget_exceeded|timeout"
}
```

关键约束：**子节点不拥有第二套循环**。它复用 `Agent.run()`，只是构造参数不同（工具子集、state、history=[]、approval=None、预算上限）；取消、预算、审批归属、证据落盘由任务运行时统一负责。

### 4.3 发起方式对比（推荐分阶段，而不是一开始就全开）

| 方案 | 形态 | 优点 | 风险 | 结论 |
|---|---|---|---|---|
| A 模型工具 `delegate` | 父 Agent 调工具，子 Agent 在进程内跑 | 模型自主性强，接近 M1 规格 | 模型可放大成本/扇出；需模式与预算闸 | P2 再做 |
| B harness 内部子 Agent | 运行时/验证/审计内部调用，模型不可见 | 边界最清晰，已有 auditor 原型 | 能力受限，需人工/模式触发 | **P0 先做** |
| C 用户显式命令 | `/explore`、CLI 子命令 | 人可控，适合验证收益 | 交互成本 | P1 |
| D 外部进程 / A2A | 独立 Agent 进程或远程 | 隔离强 | 协议/部署/审计成本高 | 推迟，非首版 |

推荐顺序：**B（泛化 auditor 原型）→ C（用户显式）→ A（模型工具，M1）**。先证明“委派有收益”，再交给模型自主决定。

### 4.4 与长任务 / 后台任务的关系

- **上下文隔离**：长任务最大的成本是父上下文膨胀；子 Agent 用全新上下文读代码/跑检查，只回摘要 + 证据引用，父上下文不装子轨迹。
- **并行单元**：后台任务可以同时跑多个只读子 Agent（探索/审查），但**共享工作区写仍只允许一个写者**（[04](04-isolation-concurrency.md)）。
- **生命周期**：子节点是任务节点；父任务由 per-task worker 执行时，P0–P2 的只读子 Agent 在父 worker 进程内运行（复用单 run 语义），P3 的 worktree 写子 Agent 才需要自己的 worker（[02](02-process-supervision.md)）。父任务取消时按 `parent_id`/`parent_run_id` 级联取消子节点；子崩溃不能杀死父。
- **预算**：子 usage 必须计入父/目标账本，深度/扇出/并发都是硬上限（[06](06-budget-ledger.md)、[09](09-unattended-safety.md)）。
- **审批**：子节点默认 `deny_all`；必须人审的动作回到父任务的审批收件箱并带 `subagent_id` 归属（[03](03-unattended-approval.md)）。
- **证据**：父的 goal/segment 账本只记“某子节点报告了什么 + 证据引用”，不把子报告升级为 verified（[05](05-goal-state.md)、[15](15-plan-verification.md)）。

### 4.5 与 `/plan` 等模式的关系

- **模式契约定义委派策略**：哪些模式允许委派（`plan` 的只读探索可以，`act` 默认不允许模型自行委派）、允许哪些 role、哪种子 Agent（只读/worktree）、模型与预算上限（[11](11-mode-contracts.md)）。
- **计划步骤可指定 executor**：`PlanStep.executor ∈ {self, subagent:explore, subagent:review}`；已批准的计划才能派发；子节点结果只作为步骤候选证据（[12](12-plan-artifact.md)、[14](14-plan-execution-binding.md)）。
- **计划验收由父侧完成**：子 Agent 的“我改好了/我验证过了”是 claim；父侧用完成验证门（可信命令）复核后才计入 `verified`（[15](15-plan-verification.md)）。
- **`/plan` 的探索阶段是子 Agent 的第一批真实用例**：并行读代码、列出候选方案、产出证据引用——只读、可取消、低风险，正好验证上下文隔离收益。

### 4.6 明确不做（首版）

- 不做嵌套子 Agent、子 Agent 自由派生（depth=1、串行/受控并发）；后台任务默认开启不等于允许无限扇出，扇出仍受 06/09 上限约束；
- 不给子 Agent 任何审批权，不继承父的 session/workspace 授权；
- 不做共享工作区写；写子 Agent 只能用独立 worktree，且合并需父或人工审批；
- 不把子 Agent 报告直接当 verified，不写进任务状态作为“已完成事实”；
- 不引入 A2A/远程 Agent、不做跨机调度；
- 不为子 Agent 新建第二条 Agent loop 或第二套 session 语义。

## 5 最小实现切片

| 阶段 | 内容 | 前置 | 验收 |
|---|---|---|---|
| P0 | 把 auditor 原型泛化为 `SubagentRunner`：只读、父 worker 进程内、独立 history、独立 trace 目录、结构化 `subagent_result_v1`、预算上限、取消传播、无审批、depth=1、串行；仅供 harness 内部调用；子节点写 `TaskRecord(kind=subagent, parent_id=...)` | 无（审计原型已存在） | fake 场景：子只读、父收到结构化结果；写/执行被 execute 层拒绝；取消能终止；usage 计入父 task 账本 |
| P1 | 用户显式入口（TUI `/explore` 或 CLI），只读；结果以摘要 + 证据引用进入父上下文 | P0 + [07](07-control-plane.md) 最小事件 | 真实任务对照：父上下文 token/纠正次数是否下降 |
| P2 | 模型工具 `delegate`（M1 规格：read_only、不嵌套、串行、无审批、schema 校验、provenance header） | P1 + [10/11](10-mode-state-machine.md) 委派策略 + [06](06-budget-ledger.md) 聚合 | M1 验收清单 + A/B 指标（[08](08-long-horizon-eval.md)） |
| P3 | worktree 写子 Agent（M2）：独立 `TaskRecord` + 自己的 worker + worktree；计划步骤 executor、后台并行（M3） | P2 + [04](04-isolation-concurrency.md) + [02](02-process-supervision.md) | 冲突/合并/回滚测试；合并默认用户批准 + 合并后重验；无收益则停在 P1/P2 |

P0 的修改点（草案）：新增 `app/runtime/subagents.py`（`SubagentRunner`、结果 schema、trace 落盘）；`app/runtime/agent.py` 抽出可复用的“构造受限 Agent”辅助（auditor 已在做同样的事，避免两份）；`app/session/store.py` 增加 child trace 目录约定；`core/task/rounds.py`/`rounds` 记录 `subagent_start/end`；测试 `tests/test_subagents.py`、扩展 `tests/test_auditor.py`。

## 6 与其它块的关系

| 块 | 关系 |
|---|---|
| 01 任务运行时 | 子 Agent 是 `TaskRecord.kind=subagent`；由运行时调度、取消、持久化 |
| 02 进程/生命周期 | per-task worker + supervisor；P0–P2 在父 worker 进程内，P3 写子 Agent 才独立 worker；attach 控制权为单 controller + observer，子事件只读 |
| 03 审批 | 子节点 `deny_all`；需要人审的动作回到父任务收件箱，`requester=child`、`decider` 由父/用户/策略记录；过期→任务失败 |
| 04 隔离 | 只读共享工作区；P3 写子 Agent 用独立 worktree，单写者；合并默认用户批准 + 合并后重验 |
| 05 目标状态 | 子报告记为 claim + 证据引用，不升级为 verified；`open_items` 模型只能标 open/blocked |
| 06 预算 | 子 usage 聚合进父/目标账本；深度/扇出/并发硬上限 |
| 07 控制面 | M1 可隐藏（普通 tool_result）；M3 加 `subagent_*` 事件与 `capabilities` |
| 08 评测 | 单 Agent vs 委派 A/B；无收益就停 |
| 09 安全 | 能力分级、后台默认开启（范围待确认）、预算/深度上限、红队清单 |
| 10/11 模式 | mode 是 TaskRecord 属性；委派策略属于 mode profile，决定是否允许、允许哪些 role |
| 12/14/15 计划 | Plan 是 `kind=plan` task；step executor、结果 claim、父侧验收；验收标准用户确认 |

## 7 与子 Agent 的关系（本文的三维结论）

| 维度 | 首版结论 | 演进条件 |
|---|---|---|
| A 谁发起 | harness 内部（P0）→ 用户显式（P1）→ 模型工具（P2） | 每级都要先有预算/证据/模式闸；不得跳级 |
| B 能力边界 | 只读（P0–P2）→ 独立 worktree 写（P3） | worktree 隔离、合并门禁、单写者先就位 |
| C 结果可信度 | **子报告 = untrusted claim**；父侧用证据/验证门复核后才进入目标状态 | 验证门先于模型可见 delegate；否则错误自评会传播 |

一句话：**子 Agent 提供的是“隔离的探索/执行能力”，不提供“可信事实”**。可信事实只能由父侧的确定性检查（文件哈希、审计、可信命令验证）产生。这与项目既有原则和 `docs/research/longhorizon-harness-2026-10-31.md` 的结论一致：执行者报告与可入账证据分离。

## 8 开放问题与验收标准

**待拍板**
1. 第一批子 Agent 是否严格只读，还是直接允许 worktree 写？建议先只读，直到 A/B 证明收益。

回答：按照你的建议。

2. 是否允许模型直接调用 `delegate`，还是先由用户显式触发？建议 P1 之前只允许用户/harness 触发。

回答： 按照你的建议

3. 子 Agent 用同模型还是便宜模型？建议先用同一 provider，成本对照后再路由。

回答：父Agent可以对子 Agent 分配任务时，给出任务难度+置信度，这样如果我们的LLM服务层 底座可以接入服务商多个模型确定 模型的优先级配置后，再考虑，这个设计应该是对LLM provider 接入层的能力开发。 当前只讨论这个太上层。

4. 子结果的默认形态：摘要 + 证据引用 + 结构化 findings，是否进入父上下文全文？建议只进摘要，全文落 child trace。

回答：按照你的建议。 摘要。 后期开发如果子Agent可写，那么就要验收结果也要提交给父Agent。

5. 是否允许子 Agent 拥有自己的 goal/plan（嵌套目标）？建议首版明确禁止。

回答：首版先不开发，后续可以通过测验是否添加。

**验收（P0）**
- 子 Agent 在 execute 层无法写文件/执行命令（不是靠提示词）；
- 子 Agent 无审批权，父的 session/workspace 授权不传给子；
- 子 usage 计入父 run 的 tokens/cost，且父预算硬上限有效；
- 父取消能终止子，子超时/超预算返回结构化失败；
- 子结果通过 `subagent_result_v1` schema 校验，父侧记录 provenance（untrusted report）；
- 至少一个真实任务的 A/B 记录：父上下文 token、总成本、纠正次数、完成质量；无收益则不进入 P2。

## 9 决策对齐（2026-11-03）

本节记录 01–05 已确认决策对 16 的影响；未改变 §8 中仍需用户 review 的开放问题。

1. **任务节点（D1）**：`TaskRecord` 是权威；子 Agent 是 `kind=subagent` 的任务节点，带 `parent_id`/`parent_run_id`/`delegation_id`/`depth`；后台任务默认开启（范围待确认），子节点也在 TaskRuntime 里可见/可取消。
2. **worker 与 attach（D2）**：每 task 一个 worker；P0–P2 的只读子 Agent 在父 worker 进程内运行（不需要独立进程），P3 的 worktree 写子 Agent 使用自己的 worker；attach 采用“单 controller + 多 observer”，子 Agent 的实时事件（M3）只作为观察数据，不能由 observer 控制。
3. **审批（D3）**：子 Agent 无审批权；需要人审的请求进父任务收件箱，归因为 `requester=child`，`decider` 记录 user/policy/autonomy/auto；required 审批过期→任务失败，父任务取消/失败时子节点级联终止并写审计。
4. **隔离与合并（D4）**：P0–P2 只读；P3 写子 Agent 使用独立 worktree + 单写者；写入主工作区前默认用户最终批准，策略自动合并仅 opt-in，且合并后必须重验；子 Agent 不能自行合并。
5. **目标状态与证据（D5）**：子报告是 untrusted claim；父侧写入 GoalRecord/`open_items` 时只能作为佐证或证据引用，不能把子报告标成 `done`/verified；完成判定仍以 harness 证据与用户确认的验收标准为准。
6. **预算（06 未定稿）**：子 usage 计入父 task 账本；每 task worker 的启动/运行成本计入 task；深度/扇出/并发上限仍待 06/09 定稿，本文不擅自给数值。

**未决（保留给用户 06–16 review）**：第一批是否只读、模型何时可调用 `delegate`、同模型/便宜模型、子结果进父上下文形态、是否允许子 Agent 拥有嵌套目标。

## 10 第二批决策对齐（2026-11-03，06–10 已确认）

1. **后台范围（01 已确认）**：P0 后台默认只覆盖 `/goal` 与显式长任务；普通 `--task`/eval 一次性。子 Agent 作为任务节点，若父任务在后台 worker 中运行，P0–P2 只读子 Agent 仍在同一 worker 进程内执行；P3 写子 Agent 才用独立 worker。
2. **非 git 工作区（04 已确认）**：P0 只读子 Agent 可用（不写工作区）；P3 worktree 写子 Agent 需要 git 仓库；非 git 下后台写任务拒绝，P1 影子副本 + 自动 git 仅用于产出 diff。
3. **预算（06）**：子 usage 计入父 task 的 task/goal 账本；三层预算（global safety / task soft+hard 开关 / run segment 边界）同样约束子节点；深度/扇出/并发上限是独立硬约束；per-request 明细 P1。
4. **控制面（07）**：子 Agent 事件（M3）按 capability 声明，observer 只读；controller 才可对父任务审批/steer/cancel；首版无远程。
5. **长任务评测（08）**：单 Agent vs 委派 A/B 沿用“多段/压缩/30min”长任务定义与 blocking/advisory 门禁；子 Agent 收益必须用同一套指标测量，无收益不进入下一阶段。
6. **安全（09）**：P3 worktree 写子 Agent 采用“创建时预授权 + 运行时通知/审计”（用户显式确认子 Agent 可在 worktree 写）；子 Agent 无审批权；kill switch/凭据/保留策略统一走父任务与 09 默认值。
7. **模式与 Plan（10）**：子 Agent 不新增 mode；委派策略属于 mode profile；goal 内 plan 采用方案 A（`kind=plan` 子任务/产物，不切 `task.mode`）；子 Agent 可作为 plan step executor，但完成判定仍由父侧 harness 证据与用户确认 criteria 决定。
8. **保留的开放问题**：§8 仍有效（首批只读、模型何时可调用 `delegate`、同模型/便宜模型、子结果进父上下文形态、是否允许嵌套目标）；这些待用户 review 16 时拍板。

## 11 用户回答处理（2026-11-03）

**已确认**
- Q1：第一批子 Agent 严格只读，直到 A/B 证明收益。
- Q2：P1 之前只允许用户/harness 触发 `delegate`，模型不能自行调用。
- Q4：子结果默认只进**摘要 + 证据引用 + 结构化 findings**，全文落 child trace；若未来子 Agent 可写，执行结果/验收证据也要提交给父 Agent。
- Q5：首版禁止子 Agent 拥有自己的 goal/plan；后续按实际测试再评估。

**Q3（模型路由）记录**
- 你提出的“父 Agent 分配任务时给难度/置信度，由 Provider 层做多模型优先级配置”属于 LLM Provider 接入层能力，当前不展开；首版仍用同一 provider，成本对照后再评估路由。

**仍未决**：无（§8 五个开放问题均已回答；后续子 Agent 可写阶段的验收契约另开切片）。
