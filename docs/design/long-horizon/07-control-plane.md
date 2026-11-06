# 07 控制面与协议：从单连接单任务到可 attach 的任务生命周期

> 状态：讨论稿（2026-11-02；2026-11-03 01–05 决策对齐见 §9，用户回答见 §10）；不是 spec，未承诺实现。
> 实现：P0 TUI attach + controller/observer + 心跳租约 + capability 协商 + replay/live 锁 + attach v2 帧 + `/attach`/`/attach now` 已实现；人工验收 TC-01–09 通过（[记录](../../specs/manual-acceptance-07-results.md)）；完整 v2 stdio 双栈后续。
> 实现 spec：[task-runtime-integration](../../specs/task-runtime-integration.md)（P0 已实现：run.task / task_state / TUI `/goal`；attach/v2 在后续切片）。
> 所属：长任务与后台任务 / 第 7 块（控制面与协议）
> 前置/相关：[01 任务运行时](01-task-runtime.md)、[02 后台进程监督](02-process-supervision.md)、[03 无人值守审批](03-unattended-approval.md)、[08 长任务评测](08-long-horizon-eval.md)、[16 子 Agent 的有机结合](16-subagent-integration.md)；相关规格：[goal-mode](../../specs/goal-mode.md)、[ink-parity](../../specs/ink-parity.md)、[Harness 技术方案](../../HARNESS.md)

## 1 问题与范围

长任务的第一道门槛不是模型能跑多久，而是**客户端能不能离开**：现在关掉 TUI 等于取消任务（§2），所以「后台任务」在协议层没有表达方式。本文讨论控制面协议：任务如何被列出、观察、接管、取消和审批，以及客户端/后端如何演进。

**已确认前提（2026-11-03）**：Python `TaskRuntime` 为权威、TUI 退化为客户端/视图；后台任务默认开启、范围待确认（[01 §9](01-task-runtime.md)）；每 task 一个 worker + per-workspace supervisor，attach 为「一个 controller + 多个只读 observer」（[02 §9](02-process-supervision.md)）；审批收件箱与过期清理归 TaskRuntime（[03 §9](03-unattended-approval.md)）。本文按这些前提设计；对应实现尚未落地，§2 列的是现状。

- 在范围内：任务级帧与生命周期事件、attach/detach 与 `control|observe` 模式、状态快照、日志与证据引用、审批收件箱帧、capabilities 协商、幂等与顺序、控制 CLI 入口。
- 不在范围内：任务模型与状态机（01）、进程监督与崩溃恢复（02）、审批语义（03）、预算账本（06）、子 Agent 调度（16）。本文只规定「怎么传」，不规定「任务怎么活」。
- 判断标准：客户端离开后任务不丢；重连/接管后看到的状态与证据可核对；旧 v1 客户端不被破坏。

## 2 现状与证据

| 事实 | 现状 | 证据 |
|---|---|---|
| 传输 | JSONL v1，一行一帧、单帧 ≤1 MiB；TUI 与 Python 双向各做字段级校验 | `tui/src/protocol.ts:1,173-177`；`tui/src/backend.ts:183-196,256-267`；`app/stdio.py:28,88-112` |
| 请求/事件 | 6 类客户端请求（run/cancel/steer/command/approval/shutdown）；17 类服务端事件；`done`/`command_result` 终结一次请求 | `tui/src/protocol.ts:179-207`；`app/stdio.py:566-569,272-279` |
| 并发 | 一个后端进程一条连接；`serve()` 只维护一个 `_Connection`；`active` 只有一个任务槽，忙时返回 `busy`；命令与 run 共用该槽 | `app/stdio.py:689-753,123,633-640` |
| 状态词汇 | 只有 `starting/idle/running/waiting_approval/stopping` 五个阶段；没有 queued/paused/segment/task 标识 | `tui/src/protocol.ts:187` |
| 进程关系 | 后端是 TUI 的子进程（`run_vibe.sh --stdio`）；TUI 退出发 `shutdown`、取消 active、收 `closed`；Python 收到 SIGINT/SIGTERM/SIGHUP 即 stop | `tui/src/backend.ts:214-278,299-300,340-397`；`app/stdio.py:715-718,760-790` |
| attach | `ready` 每个后端进程只发一次；同进程内新订阅者能拿到内存回放（上限 16 MiB），跨进程没有 attach 语义 | `app/stdio.py:735`；`tui/src/backend.ts:29,282-297` |
| capabilities | 后端已发送能力列表（run/cancel/steer/approval_once/approval_scopes/session_recording/metrics/history_restore/commands/resume/native_tools），但 TUI 只校验类型，不消费 | `app/stdio.py:725-728`；`tui/src/protocol.ts:342` |
| 幂等 | 仅连接内 `seen` 去重（重复 request id → `duplicate_id`）；没有跨进程去重、没有 seq/ack 序号 | `app/stdio.py:593-596` |
| 会话/证据 | 已有只读会话索引（`--list-sessions`）与最近一轮 `/evidence`；没有「运行中任务」查询入口 | `app/__main__.py:87,163-171`；`app/session/store.py:122-141`；`app/session/evidence.py:149-188` |
| 长任务编排 | `/goal` 分段在 TUI 内存与 `goal.json`：资源停止自动续段、其余暂停、重启强制 paused；真实长任务验收仍待做 | `tui/src/goal.ts:25,31-34,75-137`；`tui/src/goal-store.ts:5,15-23,43-55`；`docs/specs/goal-mode.md:3,26-31` |
| 排队 | 只有 TUI 内存的 `/followup`：本轮结束后自动发送；连接 `closed` 即清空，不落盘 | `tui/src/app.tsx:70,84-99,195-199,758-772` |
| 命令扩展 | 后端命令是固定白名单（resume/permissions/cost/tools/diff/mcp/skills/evidence/autonomy），新增控制命令要同时改协议与两端校验 | `tui/src/protocol.ts:19,32`；`app/stdio.py:30-33` |

结论：v1 把「任务」表达成「一次请求」、把「观察」表达成「一次连接」。2026-11-03 已确认的目标——TaskRuntime 权威、后台默认开启、controller/observer、审批收件箱、`goal-record.json` 单写——尚未实现（`01-task-runtime.md:152-166`；`02-process-supervision.md:167-183`；`03-unattended-approval.md:134-146`；`05-goal-state.md:159-165`）。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 任务不是一等对象 | 只有 request id；done 即终结；无 task_id/父子/排序字段 | 无法「列出正在跑什么」「按任务取消」；已确认由 TaskRuntime 补（01 §9） |
| 无 attach/detach | TUI 关闭即取消；新进程只能 `--continue` 会话后 `/resume` 开新 run | 长任务要求终端常开；TUI 崩溃=任务中断，恢复靠人工 |
| 后台默认开启后缺常态路径 | TUI 关闭视为取消、启动不检测运行中任务、无任务列表 | 默认开启后 list/attach/detach 与重启提示必须是常规路径（01/02 §9） |
| 无状态快照查询 | 重连只能重读 session/trace 自行重建；界面回放语义在 Node 内存 | 多客户端状态不一致；恢复逻辑分散在 UI |
| 审批只有 push、没有收件箱 | `approval_request` 绑定 active run；断线/pending 时 fail closed；无过期语义 | 无人值守无法「稍后处理」；required 过期会永久卡住（03 §9） |
| capabilities 形同虚设 | 已发送但没人消费；新帧类型会被旧客户端判协议错误 | 协议增量只能大版本重写 |
| 无跨进程幂等与顺序 | `seen` 只活在一个连接；重放靠内存 history | attach 重放可能重复/乱序，无法判断缺口 |
| 缺任务级日志/证据引用 | 只有 session 路径与最近一轮证据 | 控制面拿不到 trace/rounds/审计摘要 |

## 4 设计选项与推荐

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A 扩 v1 | 在现有事件里加可选字段与 `task_*` 事件 | 改动最小 | 请求侧 Python 做字段集合精确匹配（`app/stdio.py:598-609`）；TUI 对未识别事件类型直接判协议错误（`tui/src/protocol.ts:333-397`），新增事件会让旧客户端断连 | 不选 |
| B 新增 v2 + capabilities | 新帧类型与协商；v1 双栈一个发布周期；任务生命周期建模成帧 | 向后兼容；可渐进迁移 TUI/CLI；capabilities 从「声明」变「协商」 | 需要双栈维护窗口；任务状态权威来自 TaskRuntime（依赖 01/02） | **推荐** |
| C 独立 HTTP/Unix socket | supervisor 暴露本地 API，stdio 只管流式事件 | 天然多客户端/跨语言 | 多一套服务与鉴权；与现有流式事件重复；当前单用户属过度设计 | 推迟（02 的本地 socket 可承载 attach 通道，帧仍用本文 v2） |

推荐 B，并把 v2 帧定义为 **TaskRuntime 状态的投影**：服务端决定任务生命周期，客户端只渲染与发起受控动作。attach 增加 `mode=control|observe`：controller 通过租约/心跳持有，只有它能审批、steer、cancel；observer 只读；接管需显式 `--take-control` 或用户确认，并写 `control_taken` 审计（02 §9）。审批只由 controller 决定，`approval_id` 一次性，过期/终态由服务端清理并发事件（03 §9）。

```jsonc
// 协商与查询
{"v":2,"type":"hello","id":"h-1","protocol":2,"client":"ink","capabilities":["task_list","task_attach","approval_inbox","control_lease"]}
{"v":2,"type":"ready","id":null,"protocol":2,"capabilities":["task_list","task_events","task_attach","approval_inbox","control_lease"],"tasks":["t-01","t-02"]}
{"v":2,"type":"task_list","id":"q-1","filter":{"status":["running","paused"]}}
{"v":2,"type":"task_snapshot","id":"q-1","task":{"task_id":"t-01","status":"running","segment":2,"last_seq":42,"approval_pending":null,"trace_ref":".vibe/tasks/t-01/trace.jsonl"}}

// attach：observer 只读；controller 需要租约，接管要显式确认
{"v":2,"type":"task_attach","id":"q-2","task_id":"t-01","mode":"observe","last_seq":41}
{"v":2,"type":"task_attach","id":"q-3","task_id":"t-01","mode":"control","last_seq":41}
{"v":2,"type":"task_lease","id":"q-4","task_id":"t-01","ttl_seconds":30}
{"v":2,"type":"task_take_control","id":"q-5","task_id":"t-01","confirm":true}
{"v":2,"type":"approval_list","id":"q-6","task_ids":["t-01"]}
{"v":2,"type":"approval_decide","id":"q-7","approval_id":"a-9","decision":"once"}
{"v":2,"type":"task_detach","id":"q-8","task_id":"t-01"}
{"v":2,"type":"task_event","id":null,"task_id":"t-01","seq":42,"type":"segment_started","payload":{"segment":2}}
```

任务事件集合：`task_created/queued/started/progress/segment_started/segment_ended/waiting_approval/paused/resumed/done/failed/cancelled`，控制面事件 `control_taken`、`approval_expired`；终态后不再发进度事件。固定约定：request id 是本次调用的关联键、`task_id` 是长期身份；`seq` 每任务单调递增；attach 先快照后增量，回放上限沿用 16 MiB；detach 不取消任务；observer 可看审批列表但不可决定；`approval_id` 一次性；capabilities 未声明的帧按 `invalid_request`。

**控制 CLI 草案**：`run_vibe.sh --list-tasks`、`--attach TASK_ID`（默认 observe）、`--attach TASK_ID --take-control`、`--logs TASK_ID`、`--cancel TASK_ID`；与 `--list-sessions` 区分：session 是对话记录，task 是执行实体（01 定义）。

## 5 最小实现切片

| 阶段 | 内容 | 修改点 | 验收 |
|---|---|---|---|
| P0 只读控制面 | v2 协商；`task_list/task_snapshot/task_attach(mode=observe)/task_detach`；启动时检测运行中任务并提示 attach/resume/cancel（不自动重跑）；快照来自 TaskRuntime | `tui/src/protocol.ts`（v2 类型+校验）、`tui/src/backend.ts`（协商/attach 回放）、`app/stdio.py`（连接能力协商与查询路由）；任务视图由 01 提供 | 旧 v1 回归全过；observe attach 不改变任务；TUI 关闭=detach 后任务继续；重连快照+增量无缺口 |
| P1 控制与审批 | `mode=control` 租约/心跳/`take_control` + `control_taken`；`task_event` 订阅；`approval_list/approval_decide`（仅 controller、一次性）；按 `task_id` 的 `task_cancel`、`approval_expired`、任务级 logs 引用 | `app/stdio.py` 拆「连接」与「任务执行」；trace 增加 task_id/seq；TUI `/tasks`、`/attach`、`/take-control` | 两个客户端 control+observe；observer 不能决定；接管有审计；重复/迟到审批返回 mismatch；慢消费者不阻塞任务 |

测试点：协议版本协商与未知 capability 拒绝；v2 帧字段校验与 v1 降级；attach 重放去重与 seq 缺口检测；observe 只读不写 session；`duplicate_id` 仍生效；`take_control` 双重接管冲突；审批过期发 `approval_expired` 且任务按 03 语义失败。

明确不做：不新增独立 HTTP 服务；不定义任务状态机（跟随 01）；不定义并发调度（02/04 决定：起步两任务槽、写串行/读并发）；不在本切片删除 v1；不做远程/多用户 attach。

## 6 与其它块的关系

| 块 | 关系 |
|---|---|
| [01 任务运行时](01-task-runtime.md) | 本文传的是 TaskRecord 与生命周期；状态集合、task_id、父子关系归 01（§9 已确认 TaskRuntime 权威） |
| [02 后台进程监督](02-process-supervision.md) | 每 task 一个 worker + supervisor 提供 registry/健康检查/attach 入口；本文帧承载 worker 事件，attach 通道可复用其本地 socket |
| [03 无人值守审批](03-unattended-approval.md) | 收件箱字段、过期→任务失败、清理归 TaskRuntime；本文提供 `approval_*` 帧与 controller 限制 |
| [04 隔离与并发](04-isolation-concurrency.md) | 写串行/读并发、worktree 由 04 定义；任务列表与快照暴露 task kind/workspace，不授予路径能力 |
| [05 目标状态](05-goal-state.md) / [06 预算账本](06-budget-ledger.md) | 快照引用 `goal-record.json` 与账本数据，不复制；成本口径待 06 定稿 |
| [09 无人值守安全](09-unattended-safety.md) | capabilities 白名单；控制面默认只本机 attach，不监听网络；kill switch 走 CLI（04 §9） |
| [16 子 Agent](16-subagent-integration.md) | 子节点复用 task 模型（`kind=subagent`）；M1 可只表现为父任务的 `tool_result`，M3 才需要 `subagent_*` 事件 |

## 7 与子 Agent 的关系

| 维度 | 本文结论 |
|---|---|
| A 谁发起 | 三种发起方式（harness 内部 / 用户显式 / 模型工具调用）的分层由 16 定义；TaskRuntime 调度，控制面本身不发起。首版（M1）子 Agent 在父 run 内执行，控制面只看到父 task；升级为独立 task 时沿用 task_id/父指针/事件模型 |
| B 能力边界 | 仍是 16 的三档（只读 / 独立 worktree 写 / 共享工作区写，第三类首版不做）。`attach/detach/cancel` 只是控制动作，不授予工具、路径或审批权；observer 即使 attach 到等待审批的任务也无权批准 |
| C 结果可信度 | 子 Agent 报告是 untrusted claim。任务快照可带 `verified/completion/audit` 与 round 引用，但 `done ≠ verified`；claim 不因进入事件流而升级为事实，须父侧用证据/verify 复核 |

现有唯一子 Agent 原型是只读审计（`app/session/auditor.py`），规格见 [multi-agent-computer-use](../../specs/multi-agent-computer-use.md) §3.5/§5；控制面不复制其角色语义，只提供任务粒度的观察面。

## 8 开放问题与验收标准

待拍板：
1. v1 兼容策略：双栈一个发布周期、仅 TUI 迁移，还是后端按连接首帧选协议？建议双栈一个周期，先迁 TUI。

回答：按照你的建议

2. capabilities 粒度与强制：哪些查询/控制能力必须声明，旧 v2 客户端缺能力时降级还是拒绝？未拍板。

回答：这部分我需要你的建议

3. attach 到 paused 任务的语义：只读观察，还是允许「发送纠偏继续」？建议 P0 只 observe。

回答：发送纠偏继续，但是p0还是observe。

4. 事件回放上限与缺口策略：超 16 MiB 只给快照+提示，还是分页？建议先快照+提示。

回答：采纳你的建议

5. controller 租约参数（时长/心跳/失效判定）与接管 UX；远程/多用户 attach 何时考虑（首版不做）。

回答：首版不做。


验收条件（P0/P1）：
- 旧 v1 客户端全部回归通过；v2 完成 list/observe attach/detach 且不改变任务状态。
- TUI 关闭或 detach 后任务继续；重启检测到运行中任务并提示 attach/resume/cancel，不自动重跑已执行段。
- observer 不能审批/steer/cancel；controller 可审批；接管写 `control_taken` 审计，双重接管被拒绝。
- 审批列表跨客户端可见；`approval_id` 一次性，重复/迟到决定返回 mismatch；过期发 `approval_expired` 并按 03 语义使任务失败。
- 断线重连：seq 无重复、缺口可检测，重放不产生重复副作用。

## 9 决策对齐（2026-11-03）

**受影响的已确认决策**：D1 任务运行时（[01 §9](01-task-runtime.md)）、D2 进程监督与 attach（[02 §9](02-process-supervision.md)）、D3 审批收件箱（[03 §9](03-unattended-approval.md)）、D4 单写者/kill switch（[04 §9](04-isolation-concurrency.md)）、D5 目标状态（[05 §9](05-goal-state.md)）；06 预算账本仍未定稿。

**本块已按决策修改**
- 服务端权威：§1/§2/§4 明确 TaskRuntime 权威，v2 帧是 TaskRecord 的投影；客户端不再持有编排权。
- attach：§4/§5 增加 `mode=control|observe`、controller 租约/心跳、显式 `--take-control` 与 `control_taken` 审计；observer 只读。
- 后台默认开启：§1/§3/§5 把 list/attach/detach、重启检测当作常态路径，不再是 opt-in 例外；范围（`/goal` + 显式长任务、`VIBE_TASK_RUNTIME=0` 回退）待 01 确认。
- 审批：§4/§5 增加 `approval_list/approval_decide`、`approval_expired`；只允许 controller 决定、`approval_id` 一次性、清理归 TaskRuntime。
- 目标与路径：§6 引用 `goal-record.json`（05）与 TaskRuntime 任务目录；全部 `path:line` 已按分域重构核对（`app/runtime/agent.py`、`app/session/*`、`app/eval/*`）。

**仍需要用户 review**
- §8 的 v1/v2 兼容窗口、capabilities 粒度、paused attach 语义、回放上限、租约参数与远程 attach 均未拍板。
- “后台默认开启”的范围仍按 01 §9 待确认；本文按“默认开 + 可回退”设计，未写死普通 `--task`/eval 行为。

## 10 用户回答处理（2026-11-03）

用户回答原文见 §8。处理结果：

**已确认**
- Q1：v1/v2 双栈保留一个发布周期，先迁 TUI。
- Q3：P0 只允许 observe attach；后续再允许“发送纠偏/继续”，不在 P0。
- Q4：事件回放先“快照 + 提示”，不做分页。
- Q5：首版不做远程/多用户 attach。

**已确认（2026-11-03，采用 Lead 建议）**
- Q2 capability 粒度：按能力类别声明，至少 `task.list/snapshot/events`、`attach.observe`、`attach.control`、`approval.list/decide`、`plan.review`、`subagent.events`。服务端在 `ready` 广告，客户端声明支持。
  - 只读能力缺失：降级并提示（例如无 `task.list` 就不显示列表）；
  - 控制/审批/写能力缺失：**fail closed**（`capability_required`），不得静默忽略；
  - 未知字段：只在已协商 capability/version 内忽略“新增可选字段”；影响控制语义的字段（`mode/task_id/approval_id` 等）必须显式支持，否则拒绝。
  - capability 版本随协议走，v1/v2 双栈期要有兼容矩阵测试。
- controller 租约参数建议：heartbeat 5s、TTL 15s、失联后观察者提示但不自动接管；接管必须显式 `--take-control`/用户确认（与 02 §9 一致）。首版无远程。

**实现备注**：capability 模型采用建议；controller 租约采用 heartbeat 5s / TTL 15s；无剩余待拍板项。
