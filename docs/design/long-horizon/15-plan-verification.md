# 15 计划验收与完成验证

> 状态：讨论稿（2026-11-02；2026-11-03 01–05 决策对齐见 §9，06–10 对齐见 §10，用户回答见 §11）；P0 验收映射与三态已实现，见 [plan-acceptance spec](../../specs/plan-acceptance.md)。
> 所属：进阶模式 / 第 6 块
> 前置/相关：**前置切片：完成验证门**（上一轮提出的独立切片，不在本目录；01–05 决策记录中也称其为“15 块的完成验证门”，指同一件事）、[14 计划执行绑定](14-plan-execution-binding.md)、[13 计划审批门](13-plan-approval-gate.md)、[05 目标状态](05-goal-state.md)、[16 子 Agent 的有机结合](16-subagent-integration.md)；相关规格：[完成守卫与审计](../../specs/verified-completion.md)、[goal-mode](../../specs/goal-mode.md)、[审批层级与作用域](../../specs/approval-tiers.md)

## 1 问题与范围

现在的 `verified` 回答的是“**这次 run 里有没有成功验证覆盖最后一次文件改动**”，证据来自模型自己选择的 `bash(verification=true)`。它比模型自述强，但仍回答不了“**计划里承诺的验收标准是否逐条被满足**”：模型可以自选一条最容易过的命令，可以漏掉计划里的关键项，也可以把“测试通过”当成“需求做完”。

**前置边界**：上一轮已提出“完成验证门”作为独立切片——验证命令来自可信配置、由 harness 主动执行、走 `ApprovalGate`、失败回灌、有界重试。本文不重写它，只处理它之上的差异：**计划验收（acceptance criteria）如何从计划带到执行，且标准本身不能被执行者临时改写**。完成验证门管“一次收尾有没有可信验证”，本文管“计划级验收是否被逐条覆盖”。

**验收标准来源也不由本文新造**：按 [05 目标状态](05-goal-state.md) §9 的已确认决策，验收标准是“模型提议 → TUI 选择框（可编辑/勾选/删除）→ 用户确认后写入 `goal-record.json`”；未确认候选不参与完成判定，改标准需重新确认；CLI/后台任务必须带已确认 criteria 文件。计划本身是 `kind=plan` 的 task（[01 任务运行时](01-task-runtime.md) §9）；goal 段内创建时它是 goal 的 child task、父 goal 的 mode 不变（[10 模式状态机](10-mode-state-machine.md) D10 方案 A）。本文只定义“**已确认 criteria → verification plan → harness 验证门**”的映射、执行与证据落账。

| 层 | 回答的问题 | 证据形态 | 归属 |
|---|---|---|---|
| 步骤验证 | 这一步做了且验过吗 | step 的 `required_evidence`（命令成功/文件变化） | [14 计划执行绑定](14-plan-execution-binding.md) |
| 计划验收 | 计划承诺的 acceptance criteria 逐条满足了吗 | 已确认 criterion ↔ verification plan 的执行结果 | 本文 |

一个 step 可以只覆盖一条 criterion，也可以多条 step 共同支撑一条 criterion；反之一个 criterion 可能只需要一条命令。两层都通过才允许计划级 `verified=true`。

标记约定：**现状**＝已实现且有代码/测试；**可选**＝开关默认关；**未实现**＝规划；**建议**＝本文观点。

## 2 现状与证据

| 事实 | 证据 |
|---|---|
| 验证证据只来自模型自选 `bash(verification=true)`；带管道时 harness 只额外加 pipefail，仍不决定跑什么 | `app/tools/registry.py:311-312`、`app/tools/registry.py:412-416`、`app/tools/registry.py:418`、`core/task/state.py:347-371` |
| 完成守卫按 run 级 `mutation_seq` 判定，最多拒绝一次，第二次以 `verified=False` 收尾 | `core/task/state.py:53-86`、`app/runtime/agent.py:1148-1210`、[完成守卫规格](../../specs/verified-completion.md) §1 |
| 环境审计只读，不执行命令；运行验证仍由模型工具 + 审批决定 | `app/session/audit.py:227-240`、[完成守卫规格](../../specs/verified-completion.md) §5 |
| 任务契约只有自由文本 goal；没有 `GoalRecord`/`goal-record.json`，也没有已确认 criteria 载体（本轮工作树尚未实现） | `core/task/state.py:21-28`、`core/task/state.py:195-213`、[05 目标状态](05-goal-state.md) §9 |
| 审批等待没有任务级 deadline；验证命令由模型发起，超时/挂起无任务级语义 | `app/safety/policy.py:116-192`、[03 无人值守审批](03-unattended-approval.md) §9 |
| 契约反查是 LLM 只读审计者的一段文本检查，不能执行命令 | `app/session/auditor.py:58-86`、`app/session/auditor.py:247-293`、[完成守卫规格](../../specs/verified-completion.md) §6 |
| `verified` 三态（true/false/null）已进回执、rounds 与 `/evidence` | `core/task/state.py:94-174`、`app/session/evidence.py:50-62`、`app/stdio.py:342-343` |
| `/goal` 只在 `verified === false` 时暂停，null 视为完成 | `tui/src/goal.ts:80-92` |
| config 只有守卫/审计开关，没有“可信验证命令清单” | `app/config.py:121-124` |
| 验证/审计自身成本没有进 task/goal 账本，也无 per-request 归因（D6 要求计入或标 `unaccounted`） | [06 预算账本](06-budget-ledger.md) §10 |
| 验收相关交互没有 capability 声明：`ready.capabilities` 只有功能列表，缺 `plan.review`/`approval.list/decide` 语义 | `app/stdio.py:725-726`、[07 控制面](07-control-plane.md) §10 |

结论：**现状没有计划验收概念**；验收标准载体、可信验证命令清单、计划级结论、验收证据落账全部为**未实现**。完成验证门本身也是未实现的前置切片，本文只引用其边界。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 验收标准不可执行 | 只有自由文本 goal；`goal-record.json` 未实现，`verified` 不引用任何 criterion | “验证通过”与“需求满足”之间无映射 |
| 验证命令来源不可信 | 模型选哪条命令、是否 `verification=true`；harness 只补 pipefail | 可挑软柿子；漏测计划关键项不会被发现 |
| 步骤验证与计划验收未分层 | 守卫是 run 级，不区分步骤与已确认 criteria | 未确认/未覆盖项也可能收尾 |
| harness 不主动执行验证 | 审计只读，命令只能由模型工具发起 | 无外部动作时无人验证；失败回灌不可控 |
| 失败重试无 harness 边界 | 守卫只注入一次纠偏消息 | 重度失败时行为不可预测，成本不可控 |
| 审批超时无任务级语义 | 验证命令 approval 挂起时没有任务级 deadline | 后台任务可能永远挂着，或语义不明的“继续” |
| 合并后重验未纳入计划验收 | 合并是 04 的话题，15 只按 run 级 verified | worktree 执行的任务合并后可能没重验就标完成 |
| 计划级结论不落账 | `verified` 只描述单次 run，rounds 也只有 `run_id` | 跨段/重规划无法回答“计划验收到哪一步、属于哪个 task” |

## 4 设计选项与推荐

| 维度 | A 扩展现有守卫（模型选命令） | B 完成验证门 + 计划验收映射（推荐） | C 纯 LLM 契约反查做主闸 | D 外部 CI 作为唯一验收 |
|---|---|---|---|---|
| 证据来源可信 | 否：模型自选 | 是：用户确认 criteria + 可信配置 + 计划审批版本 | 否：只读文本推理 | 是 |
| 能否自动阻断 | 能但依据弱 | 能，按 criterion | 能，但只作用于文本 | 延迟高，不适合每轮 |
| 与计划绑定 | 无 | 强：criterion ↔ required 步骤 | 弱：只对原始任务文本 | 中：按分支/PR |
| 成本/时延 | 低 | 中：harness 执行验证命令 | 低但多一次模型调用 | 高 |
| 失败模式 | 挑软柿子/漏测 | 标准未确认或命令不全 | 误判/幻觉 | 反馈慢 |

**推荐 B：验收标准来自 05 已确认的用户确认流程，映射为可执行 verification plan；harness 通过完成验证门执行，执行者不能临时改标准。** 理由：现有确定性守卫已证明“harness 只认环境事实”可行（`core/task/state.py:53-86`）；05 决策又把“标准是什么、谁确认”定在 `goal-record.json` 上；缺的只是把“验什么、谁执行、证据记到哪”从模型手里收回，并挂到 task/plan 的证据链上。

与 03/04 的衔接：验证命令属于动作，走 ApprovalGate risk=exec 并进审批队列；required 审批在任务级 deadline 内未决 → 任务 `failed(approval_timeout)`，不“静默拒绝后继续”。执行发生在 worktree 时，合并默认用户最终批准；opt-in 自动合并只允许 `verified=true` + 只读审阅通过 + diff 无冲突 + **合并后重验通过**，合并后重验结果进入计划验收证据。

不选 A：等于把“验收范围”继续交给执行者。不选 C：`app/session/auditor.py` 只读、一次性模型调用，无法产出可执行证据；契约反查的职责是“审标准是否被弱化/漏项”，不是“执行标准”。不选 D：CI 是可选补充（分支/合并阶段），不能替代本地长任务每一步的验收回归；合并后作为最终校验可作 P2。

### 4.1 验收映射草案（数据）

```jsonc
// PlanAcceptance：验收标准的权威是 05 的 <session>/goal-record.json（用户确认后生效）
// 12 的 acceptance_criteria 先为字符串草案，确认时固化为对象；本文补 verification_plan 与证据归属
{
  "task_id": "task-...", "plan_id": "plan_01", "plan_version": 2,
  "authority": "goal-record.json",
  "acceptance_criteria": [
    {"id": "ac1", "text": "新命令在 --help 中可见", "required": true,
     "source": "user+model_proposed", "confirmed": true, "covers_steps": ["s2"]},
    {"id": "ac2", "text": "全量回归通过", "required": true,
     "source": "user+model_proposed", "confirmed": true, "covers_steps": ["s4"]}
  ],
  "verification_plan": [
    {"covers": ["ac1"], "kind": "command", "command": "python -m app --help",
     "expect": {"exit": 0, "stdout_contains": "plan"}},
    {"covers": ["ac2"], "kind": "command", "command": "./run_tests.sh -q", "expect": {"exit": 0}}
  ],
  "manual": [{"id": "ac3", "text": "文案语气需人工确认", "required": true, "confirmed": true}],
  "trusted_source": "project_profile"   // 模型不能新增/修改 required 命令
}
```

规则：只有 `confirmed=true` 的 criterion 参与完成判定（未确认候选按 05 不参与）；`verification_plan` 的 `command` 必须来自可信配置（项目 profile / 计划审批版本），模型只能**建议**；新增/修改 required criterion 或命令需要重新走审批+用户确认（[13 计划审批门](13-plan-approval-gate.md)）；`manual` 项永远不能让 `verified=true`，只能呈现“待人工确认”。

### 4.2 执行与结论（伪代码）

```text
plan_acceptance(task):
  record = load_goal_record(task.session)                    # 05 权威文件，Python 单写
  criteria = [c for c in record.acceptance_criteria if c.confirmed and c.required]
  if not criteria: return "not_applicable", codes=["no_confirmed_criteria"]
  if not trusted_commands(record): return "not_applicable", codes=["acceptance_untrusted"]
  results = {}
  for c in criteria:
      if c.manual: results[c.id] = "manual_pending"; continue
      for v in record.verification_plan where c.id in v.covers:
          results[c.id] = verify_gate.run(v, gate=ApprovalGate, task=task)  # 前置切片；required 审批按 03
  step_ok = plan_complete(task)                              # 14 的证据台账
  plan_verified = all(r.ok for r in results.values()) and step_ok and audit_clean
  if task.merged_from_worktree: plan_verified &= post_merge_verification(task)   # 04 决策
  record(task.task_id, task.plan_id, criteria, results, round_ref)
```

计划级三态建议沿用既有语义：`true`＝required criteria 全通过 + 步骤证据齐全 + 审计 clean（+ 合并后重验通过）；`false`＝任一 required 失败/证据缺失/审计 suspect（附原因码）；`null`＝不适用（qa、无改动、无已确认可执行 criterion）或存在未确认 `manual` 项，**不宣称已验证**。

**完成判定切换（D10）**：P0 保持 `done+verified`；过渡期只要存在 `confirmed=true` 的 criteria 就按严格判定，没有则走旧语义并标 warning；全面切换用 feature flag（如 `VIBE_COMPLETION_CRITERIA=1`），在本块前置切片（完成验证门）验收后默认开——15 不自行提前切换。

## 5 最小实现切片

### P0：概念 + 映射草案（不执行）

- `core/task/acceptance.py`（新建，纯 stdlib）：`AcceptanceCriterion`/`PlanAcceptance`、`confirmed` 校验（未确认候选不参与）、可信来源校验、`required` 覆盖检查、`plan_acceptance_status` 纯函数；读取按 05 的 `goal-record.json` schema v2。
- `core/task/state.py`：`TaskState.acceptance`（引用 + 结果，不存输出正文）；状态版本兼容。
- `app/tools/registry.py`/`app/runtime/agent.py`：计划模式允许模型**提议** criteria，但标 `source=model_proposal`、`confirmed=false`，未确认前不得作为 required（05 决策）。
- `app/config.py`：可信验证配置的加载（P0 只解析 + 校验，不执行）；未知/重复/覆盖不全的命令直接 fail closed。
- 成本与能力：验收/审计自身的 usage 按 D6 计入 task/goal 账本（短期标 `unaccounted`）并同时记 active time 与 wall clock；TUI 侧按 D7 声明 `plan.review`/`approval.list/decide` capability，缺审批/控制能力 fail closed。
- `core/task/rounds.py` + `app/session/evidence.py`：rounds 记录 `task_id`/`plan_id`、criteria 与结果引用；`/evidence` 渲染“计划验收：通过/未通过/不适用 + criterion 列表”。
- 测试点：无已确认 criterion 时 `null`；模型自写 required 被拒；manual 不让 `verified=true`；旧 state/rounds 兼容；`core/` 只依赖 stdlib。
- 明确不做：不执行验证命令（P1）；不自动从自然语言生成验收标准；不替代 eval/bench。

### P1：执行（接前置完成验证门）

- 通过 ApprovalGate（risk=exec）执行 verification plan，动作进审批队列；可选沙箱沿用现有执行边界（[审批层级](../../specs/approval-tiers.md)、[执行隔离](../../specs/execution-isolation.md)）；required 审批超任务级 deadline → 任务 `failed(approval_timeout)`（03 决策）。
- 后台/无人值守按 D9：验证命令的 exec/沙箱能力在任务创建时预授权，运行时自动放行写 notice/审计，profile 变更走审批/审计；验证与审计成本按 D6 计入 task/goal 账本。
- 失败回灌：把失败 criterion、命令、exit、output digest 作为 system 纠偏注入（复用 `core/task/state.py:53-86` 的拒绝模式），**有界重试**（建议每 criterion ≤2 次），重试计入 run/task 预算。
- 合并后重验：执行发生在 worktree 时，按 04 决策合并（默认用户最终批准，opt-in 自动合并需合并后重验），重验结果写入 plan 验收证据；失败退回未完成。
- 与 LLM 契约反查组合：反查负责“标准是否被弱化/漏项”，验证门负责“标准是否被执行”，两者都产出可审计原因码；任一 blocking 即不得 `verified=true`。
- 测试点：可信命令执行成功/失败/超时；命令来源非法被拒；审批超时 → 任务 failed；重试上限；manual 项导致 `null`；计划级结论进 rounds 与 `/evidence`；`/goal` 在计划验收 false 时暂停（P1 扩展到运行时判定）。

## 6 与其它块的关系

| 关系 | 块 | 交互 |
|---|---|---|
| 前置（不在本目录） | 完成验证门 | 提供“可信命令 + harness 执行 + 失败回灌 + 有界重试”；本文在其上加计划验收映射 |
| 依赖 | [01 任务运行时](01-task-runtime.md) | `task_id`/worker、审批归属与任务级 deadline |
| 依赖 | [05 目标状态](05-goal-state.md) | 已确认 criteria 权威 `goal-record.json`；`done` 只由 harness 证据产生 |
| 依赖 | [12 结构化 Plan 产物](12-plan-artifact.md)、[13 计划审批门](13-plan-approval-gate.md) | criteria 随计划版本批准、随用户确认生效 |
| 依赖 | [14 计划执行绑定](14-plan-execution-binding.md) | required 步骤证据是计划级结论的一部分 |
| 依赖 | [03 无人值守审批](03-unattended-approval.md) | 验证命令的 required 审批队列；超时→任务失败 |
| 依赖 | [04 隔离与并发](04-isolation-concurrency.md) | worktree 合并门禁与合并后重验 |
| 依赖 | [06 预算账本](06-budget-ledger.md) | D6 成本口径：验证/审计成本计入 task/goal、active 与 wall 分记 |
| 依赖 | [07 控制面](07-control-plane.md) | D7 capability：`plan.review`/`approval.list/decide`；缺审批能力 fail closed |
| 依赖 | [10 模式状态机](10-mode-state-machine.md) | D10 完成切换 feature flag 与 goal+plan 方案 A |
| 相关 | [08 长任务评测](08-long-horizon-eval.md) | D8 长任务定义与 blocking 门禁；“验收通过率/误完成率”指标 |

## 7 与子 Agent 的关系

| 维度 | 在本块的落实 |
|---|---|
| A 谁发起 | 验证由 **harness 主动发起**（完成验证门），不是模型工具调用；`requester=harness`，`decider=user`/`policy`/`autonomy`（D3 两根轴）。用户确认 criteria，执行者不得临时改；子 Agent 只能**建议** criterion 或提供证据，不能决定验收通过 |
| B 能力边界 | 验证命令在父的共享工作区经审批执行；只读子 Agent 无法执行命令；写子 Agent 只在独立 worktree 跑并在合并后由父重新验证（[04 隔离并发](04-isolation-concurrency.md)），共享工作区写先不做 |
| C 结果可信度 | 子 Agent 的“测试已通过”是 untrusted claim：父侧必须重新执行或核对 hash/digest；证据记录带 `delegation_id`，审计能区分“谁做的验证、谁复核的”（统一约定见 [16 子 Agent 的有机结合](16-subagent-integration.md)） |

## 8 开放问题与验收标准

待用户拍板：

1. 验收标准的长期权威来源：05/D10 已确认“模型提议→用户确认、写 goal-record”的流程；仍待拍板的是长期维护方式（项目 profile 长期维护 vs 每任务重新确认）与已有标准迁移。建议两者并存，计划可收紧不可放宽。

回答：按照你的建议。

2. 可信命令清单存在哪：项目 profile、`.vibe/` 会话文件，还是显式 CLI 参数？05 已确认 CLI/后台必须携带已确认 criteria 文件；命令清单的存放位置仍待定。建议项目 profile 为主，会话可覆盖但需批准。

回答：先按照你的建议。

3. 每个 criterion 的重试次数与时间上限：建议 2 次、单条 ≤5 分钟，超时按失败处理。

回答：这个主要是在防止什么场景？

4. `manual` 项如何处理：挡住 `verified=true` 但不阻断交付，还是强制人工确认后才能收尾？建议前者（不虚假宣称）。

回答：按照你的建议。

5. 全量套件成本：每个计划收尾都跑全量回归，还是 required 子集 + 最终全量？建议按计划声明，避免长任务成本失控。

回答：按照你的建议。

可验证验收条件：

- 未确认候选 criterion 不参与判定：无已确认 criterion 时计划级结论为 `null`；
- 模型自写 required criterion / 自选可信命令在 P0 即被拒（fail closed），错误码可读；
- 每个 required criterion 要么映射到可信 verify，要么显式 `manual`；覆盖不全时计划不能通过审批；
- P1 中 criterion 失败会回灌并计入有界重试，重试耗尽后 `verified=false` 且原因码可查；验证命令的 required 审批超时 → 任务 failed；
- 计划级结论进 rounds 与 `/evidence`，含 `task_id`/`plan_id`/criterion/结果/引用，且不包含命令输出正文；
- worktree 执行的任务必须合并后重验才可 `verified=true`；
- 现有 `verified` 三态行为不回归：`./run_tests.sh -q` 全绿，fake provider 覆盖“自选命令被拒 + 可信命令通过/失败”两场景。
- 与 [08 长任务评测](08-long-horizon-eval.md) D8 口径一致：本块相关场景进入 fake blocking 门禁（恢复一致、无重复副作用、`approval_timeout` 语义正确），真模型指标仅作 advisory、阈值由用户拍板。

## 9 决策对齐（2026-11-03）

受 D1（一切挂 task）、D3（审批队列/过期→任务失败）、D4（合并默认用户批准+合并后重验）、D5（验收标准用户确认、harness 强证据）影响。

已按决策就地修改：

- 明确验收标准的权威来自 05 的 `<session>/goal-record.json`（模型提议→TUI 选择→用户确认）；`confirmed=true` 才参与判定，未确认候选不参与、改标准需重新确认，与 05 一致（§1、§2、§3、§4.1、§5）。
- 计划级完成只认 harness 证据：模型/子 Agent 的“完成/测试通过”是 claim；步骤证据来自 14 的台账，验收证据来自 harness 验证门（§1、§4、§7）。
- 验证命令按 03 的审批队列执行，required 审批在任务级 deadline 未决 → 任务 `failed(approval_timeout)`，删除“静默继续”的默认假设（§3、§4、§5、§7）。
- 纳入 04 合并门禁：worktree 执行的任务合并默认用户最终批准，opt-in 自动合并需合并后重验通过，重验结果进计划验收证据（§3、§4.2、§5）。
- 全部证据挂 `task_id`/`plan_id`；代码路径同步到分域后布局（`core/task/`、`app/runtime/`、`app/tools/registry.py`、`app/safety/`、`app/session/`），行号按当前工作树重新核对（§2、§5）。

仍未决（§8 原问题保留，不擅自拍板）：验收标准的长期权威/维护方式、可信命令清单存放位置、重试与时间上限、`manual` 项处置、全量套件成本。

## 10 第二批决策对齐（2026-11-03）

受 D6（成本口径）、D7（capability/事件）、D8（长任务评测定义/门禁）、D9（预授权/审批队列/超时）、D10（完成语义切换、goal+plan 方案 A）影响。

已按决策就地修改：

- 验收标准口径补 D10：模型提议→用户确认写 `goal-record.json` 保持为唯一来源；P0 完成判定仍 `done+verified`，过渡期“有 `confirmed=true` criteria 即严格、否则旧语义 + warning”，全面切换用 `VIBE_COMPLETION_CRITERIA=1` 且等本块前置切片验收后默认开（§1、§4、§5）。
- 计划级结论挂 task/plan 证据链；goal 段内 plan 是 child task，父 goal mode 不变（D10 方案 A）（§1、§4.2、§6）。
- 验证命令按 D9 在创建任务时预授权 exec/沙箱能力，运行时自动放行写 notice/审计；required 审批超任务级 deadline → 任务 `failed(approval_timeout)`（§5、§6）。
- 成本按 D6：验证/审计自身 usage 计入 task/goal 账本（短期标 `unaccounted`），active time 与 wall clock 分别记录；P1 用 per-request compact ledger 归因（§2、§5、§6）。
- 验收/交互能力按 D7 声明 `plan.review`/`approval.list/decide`，缺审批/控制能力 fail closed（§2、§5、§6）。
- 块级验收与 D8 对齐：fake blocking 门禁（恢复一致、无重复副作用、`approval_timeout` 语义正确），真模型指标为 advisory、阈值用户拍板（§6、§8）。

仍未决（§8 原问题保留，§9 的问题继续有效，不擅自拍板）：验收标准的长期权威/维护方式、可信命令清单存放位置、重试与时间上限、`manual` 项处置、全量套件成本；`VIBE_COMPLETION_CRITERIA` 的默认开启时点以完成验证门切片验收为准。

## 11 用户回答处理（2026-11-03）

**已确认**
- Q1：长期标准“项目 profile 长期维护 + 每任务重新确认”并存；计划只能收紧、不能放宽。
- Q2：可信命令清单以项目 profile 为主；会话覆盖需批准。
- Q4：`manual` 项挡住 `verified=true`，但不阻断交付（不虚假宣称）。
- Q5：按计划声明的 required 子集 + 最终全量回归，避免每步全量。

**Q3（重试/时间上限防什么）说明与建议（待回执）**
- 防三类问题：flaky/慢命令无限重试、验证失败后模型反复改-验的循环、长任务成本失控。
- 建议：每个 criterion 最多 2 次重试；单条命令默认 ≤5 分钟（可按命令类型配置）；超时按失败处理并回灌证据；重试耗尽后 `verified=false`，原因码可查，不隐藏为成功。
- 这不是为了让失败消失，而是让失败有界、可解释。

**仍未决**：Q3 的“2 次 / 5 分钟”是否确认。
