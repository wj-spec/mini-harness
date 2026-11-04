# 03 无人值守审批：收件箱、归属与过期

> 状态：讨论稿（2026-11-02；2026-11-03 部分决策已确认，见 §9）；不是 spec，未承诺实现。
> 实现：P0 收件箱 + P1 worker 接线（request→inbox、attach→resolve、超时 expire + task failed(approval_timeout)）+ stdio/TUI `/approvals` 只读列表已实现，见 [approval-inbox spec](../../specs/approval-inbox.md)；TUI 决定 UI 后续。
> 所属：长任务与后台任务 / 第 3 块
> 前置/相关：[01 任务运行时](01-task-runtime.md)、[02 后台进程监督](02-process-supervision.md)、[04 隔离与并发](04-isolation-concurrency.md)、[07 控制面与协议](07-control-plane.md)、[09 无人值守安全默认值](09-unattended-safety.md)、[16 子 Agent 的有机结合](16-subagent-integration.md)；相关规格：[审批层级与作用域](../../specs/approval-tiers.md)、[用户可覆盖的审批策略](../../specs/permission-policy.md)、[目标模式](../../specs/goal-mode.md)、[多 Agent 与 Computer Use](../../specs/multi-agent-computer-use.md)


## 1 问题与范围

长任务一旦脱离前台 TUI（例如后台跑一段 `/goal`、夜间批处理、或用户离开终端），动作到达审批点时没有人可以点 `y/s/p/n`。当前审批是一个**同步回调**：请求只存在于 Python 进程内存和一条活的 JSONL 连接里；连接断开就按拒绝处理，恢复会话也不会重放待批动作。结果是“无人值守”目前只能在“全自动”和“卡住”之间二选一。

本文只讨论审批生命周期：请求如何排队与持久化、审批归属如何记录、无人值守策略如何只自动放行配置过的能力、过期与恢复如何处理。后台 runner/调度器本身、工作区隔离（04）、默认安全 profile（09）不在本文范围；T0–T4 与 once/session/workspace 的语义保持不变，本文只在它们之上加“队列”。

**非目标**：不做“所有动作自动同意”的全自动模式；不允许后台任务自行放宽层级或作用域；不实现子 Agent 独立审批权（见 §7）。

## 2 现状与证据

当前审批链路的形状是：`Agent` 对非只读工具生成预览并调用 `_request_approval`（`app/runtime/agent.py:1300-1314`）→ `ApprovalGate.request` 做策略/档位/已授权检查后调用 handler（`app/safety/policy.py:116-192`）→ 交互模式下 handler 是 stdio 的 `_Connection.approve`（`app/stdio.py:178-200,710-712`）。

| 事实 | 现状 | 证据 |
|---|---|---|
| 无 handler 时 fail closed | 记 `deny_no_handler` 并拒绝；Agent 侧无 gate 时返回 `ERROR approval_required` | `app/safety/policy.py:159-161`；`app/runtime/agent.py:1303-1305` |
| 交互审批依赖活的 TUI | stdio 的 `serve` 把 `connection.approve` 装进 gate；TUI 才有 `y/s/p/n` 面板 | `app/stdio.py:710-712`；`tui/src/app.tsx:38,701-705` |
| 待批请求只在内存 | `_Connection.pending` 是 `(approval_id, Future, decisions)`；TUI 侧也只是内存 Map | `app/stdio.py:127,178-200`；`tui/src/backend.ts:52,88-93` |
| 单连接只允许一个 run | 第二条 run 直接回 `busy` | `app/stdio.py:633-634` |
| 授权只在内存 | `session_allowed/workspace_allowed` 是进程内集合，重启/换工作区不继承 | `app/safety/policy.py:36-38,110-114` |
| 审计只存哈希，无归属 | 每行只有 ts/tool/workspace/args_hash/preview_hash/decision | `app/safety/policy.py:194-212` |
| 审批等待不计运行时间 | `_active_seconds` 减去 `approval_wait_seconds` | `app/runtime/agent.py:1895-1896,1898-1923` |
| 取消会拒绝待批动作 | `stopping()` 把 pending future 置 `deny`；Agent 给未执行调用补 `ERROR cancelled` | `app/stdio.py:202-207`；`app/runtime/agent.py:1925-1935` |
| 恢复只显示历史，不恢复待批 | `restore()` 发历史与 notice，明确“未自动续跑” | `app/stdio.py:209-237` |
| 已有自动批准先例 | bench/eval/SWE-bench 用各自的 handler 全量自动批准 | `app/eval/oneshot.py:82-84,143-145`；`app/eval/internal.py:329`；`app/eval/swebench.py:48-52` |
| 长任务分段仍走交互审批 | `/goal` 只在资源停止时自动开下一段，每段审批不变 | `tui/src/goal.ts:25,75-130`；`docs/specs/goal-mode.md:24-27` |

含义：`ApprovalGate` 本身是可靠的 fail closed 边界，但它把“等待谁来批准”外包给了当前连接；没有任何机制能在无人连接时把请求留存下来，也没有字段能回答“这是哪个任务、哪个 Agent、以什么身份请求/批准”。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 无持久化 pending | 断开/退出即 deny，重启后无从恢复（`app/stdio.py:127,202-207`） | 后台任务在第一次写文件/执行时停死，或被迫全自动 |
| 无收件箱与查询 | TUI 只显示当前 `waiting_approval`，没有待办列表（`app/stdio.py:185-200`；`tui/src/backend.ts:52`） | 用户无法在恢复会话后看到“有哪些动作在等我” |
| 无过期与超时策略 | 人工等待没有截止时间（`docs/USAGE.md:302`） | 无人值守时任务永久挂起，或只能靠外部 kill |
| 无审批归属 | audit 无 task_id/origin/agent_id/approval_id（`app/safety/policy.py:203-210`） | 无法回答“谁在无人值守下批准了什么” |
| 无通知 | 只有同一条连接上的 JSONL 事件（`app/stdio.py:132-157`） | 用户离开终端后不知道有动作待批 |
| 无人值守策略只有两极 | 要么 ask/fail closed（`app/safety/policy.py:159-161`），要么 benchmark 式全自动 handler（`app/eval/oneshot.py:143-145`） | 无法表达“只自动放行配置过的能力，其余挂起” |
| 审批等待与后台时限冲突 | 等待不计入 `max_run_seconds`（`app/runtime/agent.py:1895-1896`）是有意设计 | 后台总时限会被无限审批等待架空，需要独立的任务级 deadline |

## 4 设计选项与推荐

| 方案 | 自动放行范围 | 无人值守时行为 | 风险 | 结论 |
|---|---|---|---|---|
| A 全自动 | T0–T4 全部 once | 不挂起 | 违背后台的无人确认前提；T4 floor 与网络/远端动作没有人工闸门；审计无法归因 | 否决 |
| B 策略自动 + 队列 | 仅用户/项目策略显式允许的能力（复用 `permissions.autonomy_allows` 与策略规则，默认空） | 其余请求进持久化队列；有 TUI 时弹出，无 TUI 时等待/过期；默认超时拒绝 | 需要队列、通知、恢复三件套；配置错误可能把危险动作列为自动 | **推荐** |
| C 任务创建时批量预批 | 任务声明的一组 scope | 不挂起 | 审批时还没有预览；批量 scope（尤其 workspace）把“逐动作核对”变成“一次授权长期生效” | 不推荐 |

推荐 B 的理由：它保持现有 `ApprovalGate` 作为唯一执行闸门（`app/safety/policy.py:116-192`），把“自动”限定在已有策略/档位能表达的范围内（`app/safety/permissions.py:74-85`；`app/safety/permission_policy.py:177-194`），其余动作一律“挂起而不是同意”。队列只改变**等待的位置**，不改变一次批准的作用域与矩阵校验。

## 5 最小实现切片

**P0（持久化队列 + 查询/恢复）**

- 审批记录放**任务目录**（01/02 定义的 `.vibe/sessions/<session>/tasks/<task_id>/approvals.jsonl`，见 `02-process-supervision.md:64-73`；任务体系未落地前暂放 session 根作过渡），只记录审批事件，不改现有 `audit.jsonl` 语义（审计仍由 `ApprovalGate._audit` 写 `app/safety/policy.py:194-212`）。
- 新增 `ApprovalInbox`：`submit(call, preview, tier, decisions, origin, task_id, expires_at)`、`list()`、`resolve(approval_id, decision, scope, decided_by)`、`expire()`；`_Connection.approve` 在“无前台连接/后台模式”下改为写入收件箱并等待同一 `Future`。
- 协议帧不另造：交互 v1 继续用 `approval_request/approval`；无人值守/多客户端用 07 的 v2 `approval_list`/`approval_decide` 帧与 `task_snapshot.approval_pending`（`07-control-plane.md:63,73,82,94`），本文只定义其字段语义：`task_id/origin/expires_at/decided_by`。恢复时不自动重放（`app/stdio.py:209-237,666-681`）。
- 过期：按 §9 决策，required 审批在任务级 deadline 内未决 → 任务 `failed`（动作未执行、pending 清空并写审计）；单动作过期先拒绝该调用。任务级 deadline 独立于 `max_run_seconds`（避免踩 `app/runtime/agent.py:1895-1896` 的等待不计时设计）。
- 归属：按 §9 拆成 `requester: parent|child|harness` 与 `decider: user|policy|autonomy|auto`，并记录 `task_id`、`agent_id/subagent_id`、`delegation_id`、`session_id`、`tier`、`decisions`、`decided_by`、`decided_at`、`policy_hash`/`project_policy_hash`；旧 `origin` 五值保留为兼容汇总值。

```jsonc
// .vibe/sessions/<session>/tasks/<task_id>/approvals.jsonl（P0 草案，append-only）
{"type":"requested","approval_id":"a1","task_id":"goal-3","agent_id":"parent","origin":"parent",
 "tool":"bash","tier":"exec","decisions":["once","session","workspace","deny"],
 "preview_ref":"messages.jsonl#step=7","created_at":"…","expires_at":"…"}
{"type":"resolved","approval_id":"a1","decision":"once","scope":"once",
 "decided_by":"user","decided_at":"…"}
```

- **敏感信息**：`preview` 可能含代码/路径；P0 建议默认存 `preview_ref`（消息事件偏移）而不是复制正文，渲染时再取；若必须持久化预览，需在文档里写明会话目录本身已是敏感数据。

**P1（通知/过期/体验）**：终端 bell 或桌面通知、收件箱 UI、按 origin 的批量处理、审批统计写入回执/轮次、`/approvals` 命令。
**测试点**：无 handler 仍 fail closed；queue 模式请求跨进程重启后可列出并处理；超时后动作未执行且 audit 记录 `timeout`；错误/迟到 `approval_id` 返回 `approval_mismatch`；恢复时不自动重放；T4/网络动作不会被默认配置自动放行。
**明确不做**：全自动模式、后台 runner 本身、子 Agent 独立审批、跨机器审批。

## 6 与其它块的关系

- **01 任务运行时**：审批归属的 `task_id`、任务目录与生命周期由 01 定义；本文只在其任务目录里记录审批事件，不另建任务身份。
- **02 后台进程监督**：worker 等待审批时事件写 `waiting_approval`，无人 attach 时按本文策略处理（`02-process-supervision.md:99`）；断开 attach 不取消任务，审批留待收件箱处理。
- **07 控制面与协议**：`approval_list/approval_decide` 帧、`task_snapshot.approval_pending` 与 capability 协商由 07 定义（`07-control-plane.md:58-63,73,82,94`）；03 提供语义与字段。
- **04 隔离与并发**：worktree 的合并门禁、写锁获取都复用本文的审批队列；但队列不会让两个写任务并发——并发约束在 04。
- **09 无人值守安全默认值**：09 定义“默认 profile 允许哪些能力、超时怎么办、kill switch 在哪”；03 只提供把请求留下来并归属的记录机制。
- 与现有 `/goal`：目标分段继续是普通 run；只有段内的动作审批改用队列，段决策不变（`tui/src/goal.ts:75-130`）。

## 7 与子 Agent 的关系

| 维度 | 当前/近期 | 未来约束 |
|---|---|---|
| A 谁发起 | 父 Agent 工具调用、用户显式、harness 策略/自动；子 Agent 尚未实现（原型只有只读审计子 Agent） | 子 Agent 可发起审批请求，但**不能自己批准**；请求带 `parent_run_id/delegation_id`，父或用户决定 |
| B 能力边界 | 现有原型 `app/session/auditor.py` 只拿只读工具子集、`approval=None`（`app/session/auditor.py:210-266`） | M1 只读子 Agent 无审批权；M2 worktree 写仍由父/用户审批；共享工作区写不做 |
| C 结果可信度 | 子 Agent 报告是 untrusted claim，父侧用证据/测试复核 | 审批决定与拒绝原因进入子 trace 与父 audit，`origin=child` 可追溯 |

依据：M1 子 Agent「无 handler / deny-all、只读、不嵌套、串行」（`docs/specs/multi-agent-computer-use.md:284-359,425-430`）；M2 才引入 worktree 与父级串行合并（`docs/specs/multi-agent-computer-use.md:373`）；审批记录需带 origin/agent/purpose（`docs/specs/multi-agent-computer-use.md:430`）。子 Agent 作为任务节点时审批归属如何挂到 `task_id/parent_run_id`，见 [16 子 Agent 的有机结合](16-subagent-integration.md)（`16-subagent-integration.md:138-150`）。

## 8 开放问题与验收标准

**待拍板**

1. 后台任务默认形态：只读执行 + 产出建议，还是允许写但必须先在 worktree/批准队列里？默认是“队列挂起”还是直接拒绝？

回答：允许写但必须先在 worktree/批准队列，队列挂起

2. 过期语义：单次动作拒绝、整个任务暂停、还是任务失败？过期时长按动作、按段还是按目标总时长？

回答：任务失败吧

3. 预览持久化：存引用（可能被压缩/清理）还是存正文（更可审但更敏感）？敏感信息如何脱敏？

回答：存索引，正文可召回。敏感信息通过规则过滤？

4. 归属粒度：`origin` 五值是否足够？是否记录批准人身份（本机用户/父 Agent/策略版本）与项目策略哈希？

回答：origin` 五值是？记录

5. 恢复对账：任务已取消/预算过期但队列仍有 pending 时，谁负责清理并写审计？

回答：你给出建议

**验收条件（P0）**

- 断开 TUI 后，后台任务在需要审批的动作处**挂起而非自动同意**；重新连接/重启后能列出 pending 并处理。
- 过期拒绝时动作未执行，`audit.jsonl` 与审批收件箱都能回答“何时、因何被拒”。
- 默认配置下 T3/T4、MCP 写、破坏性命令不会被自动放行（引用 09 的 profile）。
- 队列与恢复不引入重复执行：同一 `approval_id` 只能解析一次，迟到/错误 ID 不产生动作。
- 子 Agent 请求审批时记录 `origin=child` 与 `delegation_id`，且不能自行批准。

## 9 决策记录（2026-11-03）

1. **后台任务形态（Q1，已确认）**：允许写，但必须先在独立 worktree 中执行，动作进入审批队列；队列默认“挂起”，不自动同意。无 worktree 能力的非 git 工作区先拒绝后台写（见 04 §9）。
2. **过期语义（Q2，已确认）**：required 审批在任务级 deadline 内未决 → **任务失败**（`failed`，reason=`approval_timeout`）；该动作不执行，pending 清空并写审计。单动作过期先拒绝该调用；不自动把任务标完成。任务级 deadline 独立于 `max_run_seconds`（后者有意排除审批等待，`app/runtime/agent.py:1895-1896`）。
3. **预览持久化（Q3，已确认）**：存索引/引用，正文可召回；敏感信息用确定性规则过滤，不依赖模型判断。
   - 队列持久化：`approval_id`、`task_id`、`agent/subagent`、`requester/decider`、`tier`、`decisions`、`preview_hash`、`preview_ref`（messages/trace 偏移）、`expires_at`。
   - 正文：默认不复制全文；需要展示时从会话事件/消息读取；若为跨重启可读而落盘，落**脱敏后的正文**（≤8KB）并标 `redacted=true`，原文只留 hash。
   - 敏感过滤规则：已知 secret 键名（`VIBE_API_KEY`、`*_KEY/TOKEN/SECRET`、`AWS_*`、`GITHUB_TOKEN` 等）与模式（`sk-…`、`ghp_…`、`Bearer …`、私钥块）替换为 `[REDACTED]`；HOME 路径缩写为 `~`；`.env` 内容默认不进入持久化正文。规则必须可测试、只做确定性替换。
4. **`origin` 五值（Q4）**：原枚举 `parent | child | policy | auto | user` 把“谁请求”和“谁批准”混在一个字段里，建议拆成两根轴：
   - `requester: parent | child | harness`（顶层 run/父任务、子 Agent、harness 内部策略/段推进）；
   - `decider: user | policy | autonomy | auto`（本机用户、用户/项目策略规则、autonomy 档位、评测/bench 自动批准）。
   - 同时记录 `decided_by`（用户标识/策略 id/auto）、`policy_hash`、`project_policy_hash`、`approval_id`、`task_id`、`agent_id`、`subagent_id`、`delegation_id`。旧 `origin` 可作为兼容汇总值保留（`requester/decider`）。
5. **清理责任（Q5 建议）**：**TaskRuntime 是唯一清理者**。任务进入终态（completed/failed/cancelled）或预算过期时，由 task worker/运行时调用 `ApprovalInbox.expire(task_id, reason)` 把 pending 解析为 denied、写 `task_terminal` 审计并从收件箱移除；supervisor 在启动/attach 时做一次对账扫描（孤儿 pending、stale pid），只补处理并记录、不重放动作；TUI 只展示与决定，不负责清理。`approval_id` 一次性有效，重复/迟到决定返回 `approval_mismatch`。
