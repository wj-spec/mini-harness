# 13 计划审批门

> 状态：讨论稿（2026-11-02；2026-11-03 01–05 决策对齐见 §9，06–10 对齐见 §10，用户回答见 §11）；P0 plan 状态机/hash/审计已实现，见 [plan-approval spec](../../specs/plan-approval.md)。
> 所属：进阶模式 / 第 4 块
> 前置/相关：[12 结构化 Plan 产物](12-plan-artifact.md)、[11 模式契约](11-mode-contracts.md)、[14 计划执行绑定](14-plan-execution-binding.md)、[16 子 Agent 的有机结合](16-subagent-integration.md)；相关规格：[审批层级与作用域](../../specs/approval-tiers.md)、[完成守卫与审计](../../specs/verified-completion.md)

## 1 问题与范围

`/plan` 的缺口不在“模型会不会列计划”，而在**计划没有审批语义**：模型把计划写进 `TaskState.plan`，harness 就把它当作后续行动的依据；写操作只在工具层逐次审批。结果是用户没法“先看整体计划再放行”，事后也回答不了“哪一版计划、谁批的、批准范围是什么、执行还是不是这一版”。

按 [01 任务运行时](01-task-runtime.md) §9 的已确认决策，计划是 `kind=plan` 的 `TaskRecord`：本文的展示、审批、执行门禁、审计都挂 `task_id` + `plan_id`，不是任务体系之外的特例。审批决定复用 [03 无人值守审批](03-unattended-approval.md) 的收件箱、归属与过期语义；验收标准确认复用 [05 目标状态](05-goal-state.md) 的“模型提议 → 用户确认”流程——本文不另造第二套确认。

goal 模式内也走同一套：按 [10 模式状态机](10-mode-state-machine.md) 的 D10 方案 A，goal task 生命周期内**不切换 `task.mode`**；goal 段内创建/批准的是 `kind=plan` 的子任务或 plan artifact，plan 审批不改写父 goal 的 mode。`submit_plan` 落盘计划产物需要独立的 UI 确认与审计事件（D10 已确认），工作区源码保持只读。

本文只讨论四件事：计划展示与编辑、批准/拒绝、重规划、审批记录与执行门禁。结构化 Plan 的 schema/存储/渲染属于 [12 结构化 Plan 产物](12-plan-artifact.md)，模式切换属于 [10 模式状态机](10-mode-state-machine.md)，本文不重复。

标记约定：**现状**＝已实现且有代码/测试；**可选**＝开关默认关；**未实现**＝规划；**建议**＝本文观点。

## 2 现状与证据

| 事实 | 证据 |
|---|---|
| 计划不是任务：没有 `kind=plan` 的 `TaskRecord`，计划状态混在 `TaskState` 里 | `core/task/state.py:195-213`、[01 任务运行时](01-task-runtime.md) §9 |
| 计划是模型可写的字符串列表，没有 step id、状态、版本 | `core/task/state.py:200`、`core/task/state.py:281-296` |
| 模型通过 `update_task` 自由改写 plan；该工具 `risk` 默认 `read`，不经过 `ApprovalGate` | `app/tools/registry.py:386-403`、`app/tools/registry.py:876-885`、`app/tools/registry.py:63-72`、`app/runtime/agent.py:1300` |
| 审批只发生在写/执行工具调用上，与“计划是否被批准”无关；也没有任务级 deadline | `app/safety/policy.py:24-44`、`app/safety/policy.py:116-192` |
| 审批审计记录 tool/args_hash/preview_hash/decision，没有 requester/decider、task_id、plan 字段 | `app/safety/policy.py:194-212` |
| 运行中纠偏写成 user 消息并进 constraints，不会让计划失效或触发重审 | `app/runtime/agent.py:1613-1623`、[运行中纠偏](../../specs/run-time-correction.md) |
| TUI 没有 `/plan` 命令，未知命令直接提示 | `tui/src/app.tsx:34-37`、`tui/src/app.tsx:812` |
| 产品复盘把 plan mode 列为三大缺失交互范式之一，建议“只读探索 → 计划展示 → 批准执行” | [产品体验复盘](../../research/product-ux-review-2026-10-31.md) §5.2、§6.1、§7 P1 |
| `/goal` 只按资源停止原因续段，不涉及计划批准 | [goal-mode 规格](../../specs/goal-mode.md) §自动续段 |
| 审批事件没有 capability 声明与 fail closed：现有 `ready.capabilities` 只是功能列表，没有 `plan.review`/`approval.list/decide` 等能力边界 | `app/stdio.py:725-726`、[07 控制面](07-control-plane.md) §10 |

结论：**现状无计划审批门**。`/plan`、计划版本、审批记录、执行门禁、任务级审批 deadline 均为**未实现**；`autonomy ask/edits/workspace/full` 是工具审批档位，不是计划批准，不能替代。

[11 模式契约](11-mode-contracts.md) §5 P1 建议 plan 模式新增 `submit_plan` 工具产出计划；本文审批的对象就是这个产物（写入 `kind=plan` task 的 plan 载荷），而不是模型随手写下的自由文本。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 无计划审批 | 计划由模型写入即生效，用户只在工具层逐个点批 | 无法在改动前认可整体方案；审批次数多但信息少 |
| 计划无版本、不可编辑 | `update_task(plan=[...])` 整体覆盖 | 审批对象不稳定，无法回答“批准的是哪一版” |
| 无审批记录 | audit 只记到工具调用，无 plan hash、requester/decider、task_id | 事后无法归因；审计链在计划层断开 |
| 无执行门禁 | 未批准的计划不阻断写/执行工具 | plan 模式只能靠提示词“只读”，不可验证 |
| 纠偏不使计划失效 | steering 只追加 constraint | 批准范围可能被悄悄扩大或替换 |
| 与验收标准确认脱节 | 05 已确认验收标准需用户确认；13 若另造确认框就是第三套确认 | 用户重复确认、标准两处漂移 |
| 无审批 deadline | 审批等待没有任务级截止；后台长任务只能一直挂着 | 超时语义未定，容易“永远等”或静默继续 |
| 审批事件未按 capability 协商 | 旧前端/缺能力客户端可能静默忽略计划审批事件 | 计划门在后台或跨协议版本时不可靠 |

## 4 设计选项与推荐

| 维度 | A 提示词确认 | B 计划工件 + 审批门 | C 给 `update_task` 加 `approved` 字段 | D 默认自动批准 |
|---|---|---|---|---|
| 证据可信 | 否：模型自述 | 是：harness 记录版本/hash/决定 | 部分是：仍由模型写状态 | 否 |
| 可审计 | 否 | 是：audit.jsonl 带 task_id/plan/requester/decider | 弱 | 否 |
| 执行门禁 | 不可实现 | 可实现（工具执行前校验） | 可实现但易被模型绕 | 无门禁 |
| 改动范围 | 零代码 | 中：core/app/stdio/TUI 各一处 | 小 | 小 |
| 失败模式 | 模型声称已确认 | 审批挂起，超任务级 deadline → 任务失败 | 模型可自写 approved | 静默执行 |

**推荐 B：计划作为 `kind=plan` task 的一等载荷，审批门是 `ApprovalGate` 的上游前置，不合并两层。** 理由：计划和单个工具调用的生命周期不同——计划是“范围与路线”，工具调用是“这一次副作用”；前者需要版本、hash、重审规则，后者已有成熟的 T0–T4/作用域矩阵。两层分开才能做到“批了计划也不放宽任何一次写审批”。

审批语义跟 [03 无人值守审批](03-unattended-approval.md) §9 的已确认决策走：计划审批是 **required 审批**，默认挂起等待用户；任务级 deadline 内未决 → 任务 `failed`（reason=`approval_timeout`），审批动作不执行、pending 清空并写审计——不是“静默拒绝后继续”。审批等待与 run 的主动时长分开计。

用户可见的批准选项建议沿用成熟产品的最小三选一（[产品体验复盘](../../research/product-ux-review-2026-10-31.md) §6.1）：**批准执行**（由 TaskRuntime 创建绑定该 plan 的执行任务；D10：不改已有 task 的 mode，工具审批按现有档位）/ **逐步确认**（每个写或执行前继续逐次审批，但计划的步骤先被认可）/ **继续规划**（回到只读探索，计划不生效）。“批准执行”的同一次交互里合并确认验收标准：TUI 用选择框展示模型提议的 `acceptance_criteria[]`（可编辑/勾选/删除），确认后写 `goal-record.json`，未确认候选不参与完成判定（[05 目标状态](05-goal-state.md) §9）——不另造第三个确认界面。

不选 A：跨 run、压缩和恢复后“用户确认过”没有落盘事实，无法审计。不选 C：`update_task` 是模型工具，允许它写 `approved` 等于让执行者自己批准自己。不选 D：与 `AGENTS.md`“写文件、执行命令必须经过 ApprovalGate”的 fail closed 原则冲突。

## 5 最小实现切片

### P0：展示 / 批准 / 拒绝 / 记录 / 门禁

```jsonc
// 计划审批事件（写入 task session + audit.jsonl；schema 由第 12 块扩展）
{
  "event": "plan_approval",
  "task_id": "task-...", "plan_id": "plan_01", "version": 3, "plan_hash": "sha256:...",
  "status": "pending|approved|rejected|stale",
  "approved_scope": "plan_once",          // 只覆盖本计划，不等同工具级 once/session
  "approved_epoch": 12,                   // 对应 steering/约束序号，用于失效判断
  "requester": "harness",                 // 谁请求：plan 审批由 harness 发起
  "decider": "user|policy",               // 谁批准；旧 origin 五值只作兼容汇总
  "decided_by": "...", "policy_hash": "...",
  "at": "2026-11-03T10:00:00+08:00"
}
```

```text
on_write_or_exec(call, task):
  if task.mode not in {act}: return ApprovalGate.request(call)     # 现状路径
  plan = runtime.plan_of(task.task_id)                             # plan 是 task 载荷
  if plan.status != "approved": deny("plan_not_approved")
  if plan.approved_epoch < task.constraints_epoch: deny("plan_stale")
  return ApprovalGate.request(call)   # 逐次审批照旧；写任务仍受 04 的 worktree/单写者约束
```

- 交互入口：`/plan` 创建 `kind=plan` task（经 [01 任务运行时](01-task-runtime.md) 的 task API；TUI 只是客户端）；计划卡片展示后由 harness 发 `plan_approval_request`，stdio 帧 `plan_approval`。capability 按 [07 控制面](07-control-plane.md) D7 声明 `plan.review`、`approval.list/decide`、`attach.observe`；控制/审批/写能力缺失 fail closed（`capability_required`），未知字段只在已协商 version/capability 内忽略。
- 修改点：`core/task/plan.py`（新建，纯 stdlib：版本、hash、状态机、`can_execute`）；`core/task/state.py` 引入 `plan_artifact` 与审批记录；`app/safety/policy.py` 的 `_audit` 增加 `event`/`task_id`/`requester`/`decider`（缺省兼容旧行）；`app/runtime/` 在写/执行工具前调用门禁并写 trace；`app/stdio.py` 增加审批请求/决定帧与 `capabilities` 声明；`tui/src/app.tsx`、`tui/src/protocol.ts` 展示计划卡片与三选一。
- 时间成本：计划等待属于“等人”，不计入 run 的 active 秒数（参考现有审批等待口径 `app/runtime/agent.py:1898-1922`）；计划最长展示行数设上限，超出折叠。
- 无人值守：默认人工、动作进审批队列；required 计划审批在任务级 deadline 内未决 → 任务失败（`approval_timeout`），不自动批准也不静默继续。`policy` 只允许显式配置且限于预置计划模板，未命中即挂起/失败。执行计划的 act task 创建时按 [09 无人值守安全默认值](09-unattended-safety.md) D9 显式确认 capability 集（沙箱/worktree 写、后台范围）；运行时自动放行写 notice/审计，profile 变更必须走审批/审计，不能静默放宽。
- 验收标准确认：批准动作与 05 的确认流程合并；确认结果写 `goal-record.json`（schema v2，Python 单写），未确认候选不参与后续完成判定；改标准需重新确认。
- 测试点：未批准时写/执行 100% deny 且原因码可读；批准后仍走原审批；拒绝后计划不生效；steering 后旧批准变 `stale` 并重审；`audit.jsonl` 行含 `event=plan_approval`、`task_id`、plan hash、`requester/decider`；审批超时 → 任务 `failed(approval_timeout)`；旧 `state.json` 兼容。
- 明确不做：不做工具级审批语义改造；不做计划自动生成质量评估；不做多用户/多审批人；不新建第二套验收标准确认界面；不改完成判定语义（P0 仍 `done+verified`，切换条件见 [15 计划验收](15-plan-verification.md)/D10）。

### P1：编辑 / 重规划 / 后台策略

- 用户编辑计划（或让模型按反馈重新生成）→ `version+1`、状态回 `pending`，旧版本只读保留；若验收标准变化，需重新走确认。
- 拒绝时可附带理由，理由作为约束进下一轮规划（参考 `add_constraint` 来源机制 `core/task/state.py:268-273`）。
- 偏离或预算变化触发“重规划请求”，重规划同样走审批；重规划次数有上限，超限暂停等用户。
- 后台/无人值守：显式 `--plan-policy`（例如只允许只读+跑测试的计划），记录 `decider=policy` 与 `policy_hash`；无策略时挂起，deadline 后任务失败。
- 测试点：编辑后必须重批；重规划保留历史版本与 hash；策略未覆盖时挂起/失败；审批记录的 `requester/decider` 可被 `/evidence` 或审计查询。

## 6 与其它块的关系

| 关系 | 块 | 交互 |
|---|---|---|
| 依赖 | [01 任务运行时](01-task-runtime.md) | `kind=plan` 的 TaskRecord、`task_id`、审批发起方与 deadline |
| 依赖 | [12 结构化 Plan 产物](12-plan-artifact.md) | 提供 schema/版本/存储，本文只加审批字段与状态机 |
| 依赖 | [11 模式契约](11-mode-contracts.md) | plan 模式的只读工具子集、审批 profile |
| 依赖 | [10 模式状态机](10-mode-state-machine.md) | goal+plan 方案 A：不切 task.mode，goal 内 plan 为 child task/artifact |
| 依赖 | [07 控制面](07-control-plane.md) | `plan.review`/`approval.list/decide` capability 与任务事件；缺控制/审批能力 fail closed |
| 依赖 | [03 无人值守审批](03-unattended-approval.md)、[09 无人值守安全默认值](09-unattended-safety.md) | 审批队列/归属/过期、后台 policy 能力集 |
| 依赖 | [05 目标状态](05-goal-state.md) | 验收标准经用户确认写 `goal-record.json`，本文批准动作与其合并 |
| 依赖 | [04 隔离与并发](04-isolation-concurrency.md) | 批准计划不解除 worktree/单写者；合并默认用户最终批准 |
| 被依赖 | [14 计划执行绑定](14-plan-execution-binding.md) | 执行门禁的 `plan.status/epoch` 来源 |
| 被依赖 | [15 计划验收](15-plan-verification.md) | 验收标准随计划版本一起被批准 |
| 相关 | [06 预算账本](06-budget-ledger.md) | 计划级预算与等待成本 |

## 7 与子 Agent 的关系

统一约定（任务节点、父子归属、untrusted claim）见 [16 子 Agent 的有机结合](16-subagent-integration.md)；本块只列差异。

| 维度 | 在本块的落实 |
|---|---|
| A 谁发起 | 审批请求由 harness 发起（`requester=harness`）、用户裁决（`decider=user` 或 `decider=policy`）；子 Agent 是 `kind=subagent` task，**只能提议计划**，不能批准也不能替父批。审批记录带 `task_id`/`agent_id`/`subagent_id`/`delegation_id`，审计能回答“谁请求、谁批准” |
| B 能力边界 | plan 模式只读：父子都拿不到写/执行工具（工具子集在 registry 层限制，见 [11 模式契约](11-mode-contracts.md)）。子 Agent 的写计划能力要到“独立 worktree 写”阶段（[04 隔离并发](04-isolation-concurrency.md)），共享工作区写先不做 |
| C 结果可信度 | 子 Agent 的“计划已执行/已批准”是 **untrusted claim**；父侧只认审批记录与工具证据，子 Agent 报告不能推进 plan 状态（与 [multi-agent 规格](../../specs/multi-agent-computer-use.md) §5.4 的审批归属要求一致） |

## 8 开放问题与验收标准

待用户拍板：

1. 批准粒度：整份计划一次批准，还是 required 步骤逐条批准？建议默认整份 + 破坏性步骤单独确认。

回答：按照你的建议

2. 批准后范围变化：steering/偏离触发自动升版本重批，还是先暂停问用户？建议“影响文件集或验收标准才重批”。

回答：按照你的建议

3. 无人值守允许预批到什么程度：只读+跑测试，还是允许 T1 写？建议 P0 只到“只读+测试”。

回答：先实现p0， 后续探索 允许写
4. 计划等待是否计入 `/goal` 的总时长？审批等待已排除出 run 主动时长，是否进 goal 总时长仍待拍板。

回答：goal的总时长统计是包括的，更详细的时间统计区分Agent active 与 human 审批

5. TUI 呈现：计划卡片默认展开还是折叠？长计划是否允许在审批面板内编辑（客户端提交、服务端校验）？

回答：计划卡片默认折叠，展开后允许分页，可以使用 上下键进行选择， 另外当前tui 开发 在ghostty 上支持 右侧拓展屏，这个屏幕我觉得可以进一步利用。 请你给出好的建议供我判断。

可验证验收条件：

- 未批准状态下，写/执行工具 100% 被拒，原因码 `plan_not_approved`，且 trace 可见；
- `audit.jsonl` 每次计划决定都含 `event=plan_approval`、`task_id`、plan hash、版本、`requester/decider`；
- 任务级 deadline 内审批未决 → 任务 `failed(approval_timeout)`，动作未执行、pending 清空且有审计；
- steering 后旧批准变 `stale`，不重新批准则写操作被拒（确定性测试）；
- 拒绝/取消不产生任何写操作，计划文件保留旧版本；未确认验收标准不参与完成判定；
- `./run_tests.sh -q` 全绿；TUI fake provider 端到端覆盖“展示→批准→执行”与“拒绝→不执行”。

## 9 决策对齐（2026-11-03）

受 D1（任务运行时：plan 是 `kind=plan` task）、D3（审批队列/归属/过期→任务失败）、D4（worktree/单写者/合并默认用户批准）、D5（验收标准模型提议→用户确认、harness 强证据）影响。

已按决策就地修改：

- 把计划从“`TaskState` 里的自由文本”改为 `kind=plan` 的 `TaskRecord`，审批/执行/验收统一挂 `task_id`+`plan_id`（§1、§2、§5、§6、§7）。
- 审批记录字段改为 D3 的 `requester/decider` + `task_id`/`decided_by`/`policy_hash`，旧 `origin` 只作兼容汇总；审批语义改为“required 审批挂起，任务级 deadline 未决 → 任务失败（`approval_timeout`）”，删除“静默拒绝后继续”的表述（§2、§3、§4、§5）。
- 批准计划与 05 的验收标准确认合并为同一交互：模型提议 → TUI 选择框 → 用户确认 → 写 `goal-record.json`；未确认候选不参与完成判定（§4、§5、§8）。
- 补充 04 边界：批准计划不放宽 worktree/单写者，合并仍默认用户最终批准、opt-in 自动合并需合并后重验（§5、§6）。
- 代码路径同步到分域后布局（`core/task/`、`app/runtime/`、`app/tools/registry.py`、`app/safety/`、`app/session/`），行号按当前工作树重新核对。

仍未决（§8 原问题保留，不擅自拍板）：批准粒度、范围变化时的自动重批条件、无人值守预批程度、计划等待是否计入 goal 总时长、TUI 展示/编辑细节。

## 10 第二批决策对齐（2026-11-03）

受 D7（capability/事件）、D9（创建时预授权 + 运行时通知/审计）、D10（mode 不可变、goal+plan 方案 A、完成语义切换）影响；D6 的预算口径不影响本块审批语义。

已按决策就地修改：

- goal 内 plan 采用 D10 方案 A：goal task 生命周期内不改 `task.mode`，goal 段内创建/批准 `kind=plan` 子任务/artifact；“批准执行”由 TaskRuntime 创建绑定计划的新执行任务，而不是切换既有 task 的 mode（§1、§4）。
- `submit_plan` 增加独立 UI 确认与审计要求，工作区源码保持只读（§1、§5）。
- 计划审批事件按 D7 挂 capability：`plan.review`、`approval.list/decide` 等；控制/审批/写能力缺失 fail closed，旧前端/缺能力客户端不得静默忽略审批事件（§2、§3、§5、§6）。
- 无人值守按 D9：创建 act task 时显式确认 capability 集（沙箱/worktree 写、后台范围），运行时自动放行写 notice/审计，profile 变更必须走审批/审计（§5）。
- 完成判定不提前切换：P0 仍 `done+verified`，切换条件归 [15 计划验收](15-plan-verification.md) 与 D10（§5）。
- approval 超时→任务失败语义沿用第一批 D3（§5、§9），本批未改动。

仍未决（§8 原问题保留，§9 的问题继续有效，不擅自拍板）：批准粒度、范围变化时的重批条件、无人值守预批程度（D9 已确认“创建时预授权”，但 plan 审批本身仍默认人工）、计划等待是否计入 goal 总时长、TUI 展示/编辑细节。

## 11 用户回答处理（2026-11-03）

**已确认**
- Q1：默认**整份计划一次批准**；破坏性步骤单独确认。
- Q2：steering/偏离在**影响文件集或验收标准**时触发重批；其他情况不自动重批。
- Q3：P0 无人值守只到“只读 + 跑测试”；写能力后续再探索。
- Q4：`/goal` 总时长**包含**审批等待；报表分别统计 Agent active 与 human approval 时间。
- Q5：计划卡片默认折叠；展开后分页，↑↓ 选择。

**Q5（TUI/右侧屏）建议（待回执）**
- 主对话区保留计划卡片（折叠）：标题显示目标、版本、批准状态、步骤数、预计风险；展开后分页 + ↑↓ 选择。
- Ghostty 右侧侧栏定位为**只读评审面板**：展示当前 plan 树/验收标准/待审批项/证据与 diff；执行中作为观察面，不承载批准决定。
- 批准动作仍在主审批面板完成（安全边界：侧栏不持有决定权），侧栏提供“定位到主面板”提示；后续可加“并排对照”（对话 vs 计划/证据）。
- 编辑仅发生在首次计划审批阶段（12 Q3），执行阶段侧栏只读。

**仍未决**：TUI 具体布局/分页参数待实现时再定；Q5 侧栏方案是否确认。
