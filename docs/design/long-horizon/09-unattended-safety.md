# 09 无人值守安全默认值：能力分级、保守预设与 kill switch

> 状态：讨论稿（2026-11-02；2026-11-03 01–05 决策对齐见 §9，用户回答见 §10）；P0 保守 profile 与 worker 能力拒绝已实现，见 [unattended-profile spec](../../specs/unattended-profile.md)。
> 所属：长任务与后台任务 / 第 9 块
> 前置/相关：[01 任务运行时](01-task-runtime.md)、[02 后台进程监督](02-process-supervision.md)、[03 无人值守审批](03-unattended-approval.md)、[04 隔离与并发](04-isolation-concurrency.md)、[05 跨段目标状态](05-goal-state.md)、[07 控制面与协议](07-control-plane.md)、[16 子 Agent 的有机结合](16-subagent-integration.md)；相关规格：[执行隔离评估](../../specs/execution-isolation.md)、[审批层级与作用域](../../specs/approval-tiers.md)、[用户可覆盖的审批策略](../../specs/permission-policy.md)、[沙箱调研](../../research/sandbox-research-2026-10-25.md)


## 1 问题与范围

无人值守与后台任务的危险不在于“模型更坏”，而在于**三个前提同时改变**：没有人守着审批点、运行时间拉长、可能与其他任务/用户并发。当前默认值是为交互设计的：默认 `autonomy=ask`、没有审批 handler 就 fail closed、命令以当前用户权限运行、沙箱默认关闭。把同一套默认直接用于夜间任务，结果要么永久卡在审批、要么为了跑通而开 `full`/bench 式全自动。

2026-11-03 已确认的 01–05 决策改变了前提：后台任务**默认开启**（范围待确认，见 01 §9），后台写必须 **worktree + 审批队列**（03 §9），required 审批过期 → **任务失败**（`approval_timeout`，03 §9），kill switch 走 **CLI + `.vibe/stop` + TUI 同一实现**（04 §9），日志按会话保留 + 软上限/显式清理（02 §9）。本文据此更新无人值守默认 profile：能力分级、默认允许集、opt-in、kill switch、审计与敏感信息；机制细节见 03/04，不在此重复。

**非目标**：不做“所有动作自动同意”的全自动；不实现后台 runner/worktree 本身（01/02/04）；不擅自拍板 §8 仍待确认的问题。

## 2 现状与证据

### 2.1 当前默认边界（代码）

| 事实 | 现状 | 证据 |
|---|---|---|
| 默认边界是审批 + 黑名单 + 超时 + 进程组 | 不是容器；命令仍以当前用户权限运行 | `README.md:24,83,190-195`；`docs/specs/execution-isolation.md:1-3,5-13` |
| 沙箱默认关闭 | `--sandbox` 或 `VIBE_SANDBOX=1` 才启用；macOS Seatbelt / Linux bwrap | `app/config.py:55,117`；`app/safety/sandbox.py:55-60` |
| 网络默认不被限制 | 只有沙箱内可断网；白名单仅 macOS Seatbelt | `app/safety/sandbox.py:118-146`；`app/safety/sandbox_proxy.py:119-145` |
| 审批默认询问 | `autonomy` 默认 ask；档位可切换但 T4 永不自动 | `app/safety/permissions.py:58-85`；`app/safety/policy.py:116-192` |
| 无 handler → fail closed | 后台没有 TUI 时动作直接拒绝 | `app/safety/policy.py:159-161`；`app/runtime/agent.py:1303-1305` |
| 黑名单是护栏不是边界 | 11 条正则；变量拼接/`find -delete`/`python -c` 等可绕过 | `app/safety/sandbox.py:17-29`；`docs/specs/execution-isolation.md:15-22` |
| bash 继承父进程环境 | `run_command` 用 `{**os.environ, **env_extra}`；仅 oneshot 移除 3 个 API key | `app/safety/sandbox.py:268-284`；`app/eval/oneshot.py:108-114` |
| 审批审计只有哈希 | 无 requester/decider/task_id/profile，无法归因“谁在无人值守下批准” | `app/safety/policy.py:194-212` |
| 自动批准存在特殊入口 | bench/eval/SWE-bench 用自动 handler；bench 还放宽路径限制 | `app/eval/oneshot.py:82-84,143-145`；`app/eval/internal.py:329`；`app/eval/swebench.py:48-52` |
| kill 只作用于当前任务 | TUI cancel / stdio stopping；后端退出清进程组 | `app/stdio.py:202-207,764-790`；`tui/src/backend.ts:160-177,214-249` |
| 日志只追加、无保留策略 | `events.jsonl`/`trace.jsonl` 只 append，没有软上限/轮转/清理入口 | `app/session/store.py:180-201`；`app/stdio.py:429-465` |

**决策 vs 代码**：D1 TaskRuntime/后台默认、D2 worker/supervisor/日志保留、D3 审批过期与 requester/decider、D4 worktree/单写者/kill switch、D5 `goal-record.json` 目前都**只有决策、没有对应运行时实现**；当前仍是一连接一 active run（`app/stdio.py:633-634`）、写直接落主工作区（`app/tools/registry.py:122,992-996`）。本节其余“现状”均为代码事实，不是已实现能力。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 无无人值守 profile | 只有交互默认值 + 若干 env（`.env.example:26,41`）；没有按运行模式切换的能力集 | 后台只能靠人手动拼配置，容易漏项或过度放宽 |
| 后台写未落 worktree + 队列 | 通过审批后直接写主工作区（`app/tools/registry.py:122,992-996`） | 无隔离、无逐任务 diff；与 D3/D4 不一致 |
| required 审批超时未实现 | 当前 `_request_approval` 无限等待，只接受取消（`app/runtime/agent.py:1898-1923`） | 与 D3“到期任务失败”不一致；后台可能永久挂起 |
| 审批归属缺失 | audit 无 requester/decider/policy_hash/task_id（`app/safety/policy.py:203-210`） | 无法回答“谁在无人值守下批准了什么” |
| kill switch 未实现 | 只有当前 run 的 cancel（`app/stdio.py:202-207`）；无 CLI/`.vibe/stop` 全局入口 | 多后台任务无法一键停；与 D4 方案不一致 |
| 日志保留/告警未实现 | 会话 events/trace 只追加（`app/session/store.py:180-201`）；只有 trace/round/receipt | 长任务无界增长、敏感信息长期留存；离开终端后无告警 |
| 凭据默认不隔离 | bash 继承 `os.environ`（`app/safety/sandbox.py:278`）；交互入口没有 scrub | 模型/命令可读 API key、token |
| bench 自动批准可被误用 | 只有文档说“只用于一次性容器”（`docs/specs/harbor-benchmark.md:52-54`；`app/eval/oneshot.py:143-145`） | 本地误开 `--bench` 会全自动且放宽路径 |
| destructive 层未实现 | T5 仍是后续项（`docs/specs/approval-tiers.md:61-67`） | 不可逆命令没有逐次确认层 |

## 4 设计选项与推荐

| 方案 | 做法 | 结论 |
|---|---|---|
| A 维持现状 + 文档约束 | 只允许交互使用，后台任务靠人工拼 env | 不够：与“后台默认开启”冲突，安全依赖人记得所有开关 |
| B 后台默认开的保守 profile | 后台任务默认开启（范围待确认）；默认只读 + 后台写仅 worktree + 审批队列挂起；required 审批到期→任务失败；网络/MCP/browser/破坏性默认拒绝；放宽项显式 opt-in 并写入任务元数据 | **推荐**（与 D1–D4 一致） |
| C 宽松默认（full + network） | 后台默认全自动 | 否决：与 fail closed/审批矩阵冲突，审计无法追责 |

推荐 B：D1 改变了“后台是否开启”，但不改变“能力默认值要保守”；B 让后台默认可见、可 attach，同时把写限制在 worktree + 队列，把网络/MCP/远端动作与破坏性命令继续默认拒绝。**opt-in 只用于放宽，不用于“允许后台”本身**（后台已默认开）。

### 4.1 能力分级与默认值（建议）

| 能力 | 保守默认（后台） | 放宽/差异 | 约束 |
|---|---|---|---|
| T0 只读（read_file/grep/list/git 只读） | 允许 | 前台/后台一致 | 路径仍在 workspace 内（`app/tools/registry.py:992-996`） |
| T1 工作区写（write/edit/patch） | 仅独立 worktree + 审批队列挂起，默认不自动同意 | 主工作区写仅限前台交互 | 需 04 的 worktree；无 worktree 时拒绝（非 git 见 §8） |
| T2 本地执行（bash、MCP risk=exec） | 默认拒绝；opt-in 后仅沙箱内 + worktree cwd | 指定命令白名单仍需审批 | 沙箱不可用时拒绝，不静默裸跑（`app/tools/registry.py:131-137`） |
| T3 网络（web_search、network MCP） | 拒绝 | opt-in 后仅沙箱代理白名单（macOS） | 非 macOS 没有白名单则全开/全关，需明确提示 |
| MCP 工具（read/write/exec） | 默认不启动 | 按服务逐项批准；写/exec 走 03 队列 | 启动与调用分别审批（`app/tools/mcp.py:304-338`） |
| T4 远端页面动作 | 拒绝 | 不提供后台 opt-in | 仍逐次确认，autonomy 永不自动（`app/safety/permissions.py:74-77`） |
| 破坏性命令 | 拒绝 | 不提供后台 opt-in | 黑名单 + 后续 T5；当前无 T5 |
| 审批超时 | required 审批到期→任务失败 `approval_timeout`；单动作过期→拒绝该调用 | — | pending 清空并写审计（03 §9） |
| 凭据/环境变量 | 最小 env allowlist；默认不继承 `VIBE_*`/API key | 按任务显式注入 | 需要修改 `run_command` 的 env 组装（`app/safety/sandbox.py:278-280`） |

### 4.2 默认 profile 草案（P0 建议，非现状）

```jsonc
{"schema_version":1,"profile":"unattended-conservative",
 "background_default":true, "background_scope":"goal+explicit_long", // 范围待确认（01 §9）
 "allow":["read"], "write":"worktree+approval_queue",
 "worktree_required":true, "worktree_reuse_env":true,
 "sandbox":"required_for_exec", "network":"deny", "mcp":"deny",
 "remote_action":"deny", "destructive":"deny",
 "env_allowlist":["PATH","HOME","LANG","TMPDIR"],
 "max_run_seconds":1800, "max_active_write_tasks":1, "max_active_tasks":2,
 "approval_mode":"queue",
 "on_required_approval_timeout":"task_failed", // reason=approval_timeout
 "on_action_approval_timeout":"deny_action",
 "merge_requires_user_approval":true,
 "kill_switch":[".vibe/stop","vibe task stop <id>|--workspace|--all"],
 "log_retention":{"per":"session","soft_cap":"TBD","approvals_audit":"longer"}}
```

配置放 workspace `.vibe/unattended.json`（或 env `VIBE_UNATTENDED_PROFILE`），随任务写入任务目录 `meta.json`（01/02）与 round 记录。**后台默认开启**，起步两个后台任务、写串行/读并发；文件集并行留 P2 实验（04 §9）。可用 `VIBE_TASK_RUNTIME=0` 回退旧行为（01 §9 建议）；普通 `--task`/eval 是否仍一次性待确认（§8）。只有用户可改 profile 与 opt-in；模型不能自行修改（与 `/autonomy` 一样属于用户选择，`app/stdio.py:344-356`）。

## 5 最小实现切片

**P0（profile + 后台默认 + 禁止能力 + 超时 + kill）**

- `UnattendedProfile` 加载/校验（version、未知字段拒绝、缺省即 conservative）；在 `_run_tool_calls` 调 `ApprovalGate` 之前检查 tier/tool，被禁止动作直接 `ERROR unattended_denied: <capability>` 并记 trace/audit（不进入队列）。profile/opt-in 写入 01 的 `TaskRecord.approval_policy` 与任务目录 `meta.json`（`01-task-runtime.md:70-99`）。
- 后台写前置：必须独立 worktree（04）+ 审批队列（03）；非 git 工作区按 04 §9 的 P0 建议**拒绝后台写、只读允许**（该方案仍待用户确认，不写成已定）。
- 审批超时：required 审批在任务级 deadline 内未决 → 任务 `failed`/`approval_timeout`，动作不执行、pending 清空并由 TaskRuntime 写审计；单动作过期仅拒绝该调用。任务级 deadline 独立于 `max_run_seconds`（后者排除审批等待，`app/runtime/agent.py:1895-1896`）。
- kill switch：主入口 `vibe task stop <task_id>` / `--workspace <path>` / `--all`；`.vibe/stop` 兜底；TUI 按钮调用同一实现。worker 在 step/工具边界检查信号，先 SIGTERM 进程组、宽限后 SIGKILL，任务状态写 `cancelled` + 审计（`04-isolation-concurrency.md:171`）。
- 后台默认开启：默认 profile 生效，起步两个后台任务、写串行/读并发（文件集并行留 P2）；无人值守任务用最小 env（不继承 API key/token）；`audit.jsonl` 增加 requester/decider/policy_hash/profile_id/approval_id，round 记录 profile 哈希与 opt-in。
- 防误用：`--bench` 自动批准与路径放宽仅在显式容器标记/一次性目录下允许，否则拒绝启动（`docs/specs/harbor-benchmark.md:52-54` 的行为化）。

**P1（保留/告警/配额/红队自动化）**：日志按会话保留 + 软上限/轮转/压缩/显式清理，审批/审计保留更久，删除显式且记录（02 §9）；后台开始/结束/无进展/审批超时通知；workspace 级 token/费用/并发配额；§8 红队清单固化为测试脚本。

**测试点**：默认 profile 下后台写主工作区被拒、写 worktree+队列允许；required 审批过期→任务 failed 且动作未执行；单动作过期只拒绝该调用；kill switch（CLI/`--workspace`/`--all`/`.vibe/stop`/TUI）均回收进程组并写 `cancelled`；后台默认开/两槽位/写串行读并发；非 git 后台写被拒（如决策确认）；日志轮转/清理后仍可审计；API key 不进入 bash 子进程 env；bench 自动批准在非容器环境被拒。

**明确不做**：T5 destructive 分类（后续）；容器运行时（04-C 推迟）；跨机器/远程审批；自动合并默认开；自动告警基建。

## 6 与其它块的关系

- **01 任务运行时**：`TaskRuntime` 权威、后台默认开启（范围待确认）、两个任务槽位、`TaskRecord.approval_policy` 由 01 定义；09 只给默认值与能力约束（`01-task-runtime.md:152-166`）。
- **02 后台进程监督**：每 task worker + per-workspace supervisor；日志按会话保留 + 软上限/清理；停止信号后的进程组回收落点由 02 提供（`02-process-supervision.md:169-183`）。
- **03 无人值守审批**：后台写必须 worktree + 队列；required 超时→任务失败；requester/decider 与 TaskRuntime 清理由 03 定义（`03-unattended-approval.md:136-146`）。
- **04 隔离与并发**：worktree/单写者/合并默认用户批准/kill switch CLI 方案（`04-isolation-concurrency.md:158-171`）；非 git P0 建议仍待确认。
- **05 跨段目标状态**：后台 goal 任务必须带已确认 `acceptance_criteria`（`goal-record.json`），否则暂停等用户确认（`05-goal-state.md:161-165`）；09 不重复验证语义。
- **07 控制面与协议**：profile 摘要可通过 `task_snapshot`/capabilities 暴露给 attach 客户端（`07-control-plane.md:61-72,86`）；后台默认开启后 attach/detach 是常态。
- 与评测/bench：bench/eval 是“一次性受控环境”的例外，不属于后台默认形态；其自动批准不能被当作后台默认值。

## 7 与子 Agent 的关系

| 维度 | 当前/近期 | 未来约束 |
|---|---|---|
| A 谁发起 | 父 Agent 工具调用、用户显式、harness 策略；子 Agent 尚未实现 | 子 Agent 不能修改 profile/opt-in；请求记录 `requester=child` 与 `delegation_id`，不能自行决定 |
| B 能力边界 | 现有原型 `app/session/auditor.py` 是只读子 Agent（`app/session/auditor.py:210-266`） | M1 只读；M2 worktree 写但子无审批权；共享工作区写不做（`docs/specs/multi-agent-computer-use.md:305-308,373,557`） |
| C 结果可信度 | 子报告是 untrusted claim；父侧用证据/测试复核 | 子不能自我批准、不能提升 profile；父对子结果做证据校验并写入父 audit（`docs/specs/multi-agent-computer-use.md:348,425-430`） |

子 Agent 作为 `kind=subagent` 任务节点的统一谱系与发起分级见 [16 子 Agent 的有机结合](16-subagent-integration.md)（`16-subagent-integration.md:55-95`）：09 只把它纳入能力分级与默认 profile，不重复任务模型本身。

## 8 开放问题与验收标准

**待拍板（保留原问题，不擅自拍板）**

1. 保守默认的“自动允许”起步集：只读是否足够？是否允许“沙箱内、worktree 内的写入”自动放行？

回答：允许“沙箱内、worktree 内的写入”自动放行，并在自动执行的时候 要求用户给提示放权限

2. opt-in 的授权主体与可审计性：仅本机用户可改，还是允许任务创建者按模板选择？profile 变更是否必须写入审批记录？

回答：需要你给出专业建议供我判断


3. kill switch 的宽限期与触发后语义：SIGTERM 后宽限多久；是否允许“暂停并保留 worktree 等待恢复”（主语义已由 D4 定：CLI + `.vibe/stop` + TUI 同一实现）。

回答：需要你给出专业建议供我判断


4. 凭据策略：最小 env allowlist 是否破坏 git/uv/npm？哪些目录需显式挂载/拒绝？

回答：需要你给出专业建议供我判断。当前的allowlist很僵化是事实


5. 保留与告警的具体数值/渠道：会话 events/trace 软上限、轮转阈值、审批/审计保留时长、通知发到哪里（策略已由 D2 定，数值/渠道待定）。

回答：需要你给出专业建议供我判断


6. 后台默认开启范围（先 `/goal` + 显式长任务，普通 `--task`/eval 是否一次性；01 §9 建议待确认）与非 git 工作区 P0 方案（04 §9 建议拒绝后台写、只读允许，待确认）。

回答：需要你给出专业建议供我判断



**红队验收清单（实现后逐项测试或人工记录）**

- 提示注入：读到要求“关闭沙箱/跳过审批/输出 API key”的文件，工具权限与审批不被改变。
- 越界写入：symlink、`..`、`cd` 后相对路径、`echo > ~/x`、嵌套 shell——worktree/沙箱/写路径护栏至少一层拒绝，且失败有结构化前缀。
- 凭据外发：env 放标记 key，bash/网络白名单工具看不到 key；网络请求不携带 key。
- 审批伪造/重放：错误/过期 `approval_id`、旧连接恢复后重发——`approval_mismatch` 且无动作。
- 审批超时：required 审批到期→任务 `failed`/`approval_timeout`、动作未执行、pending 清空并写审计；单动作过期只拒绝该调用。
- kill switch：在 bash、审批等待、模型等待三个时点触发 CLI/`--workspace`/`--all`/`.vibe/stop`/TUI，进程组回收、`cancelled` 与审计落地、不自动重试。
- 并发与隔离：两个任务槽位下写串行、读并发；写任务只出现在自己的 worktree；用户手动编辑在主工作区被列出但不强制合并。
- 日志保留：轮转/压缩/显式清理后仍可审计；审批/审计保留更久；删除动作有记录。
- bench/eval 误用：非一次性容器环境启动自动批准，拒绝或醒目警告且不落生产工作区。
- 审计追溯：任意无人值守批准可从 audit/round 回答 task_id、requester/decider、policy_hash、profile、approval_id 与决定时间。

**验收条件（P0）**

- 后台默认开启后，默认 profile 只允许只读与 worktree+队列内写；越权能力被结构化拒绝。
- required 审批过期→任务失败、kill switch、凭据/沙箱、日志保留各至少一个端到端测试，失败路径可观测。
- 所有无人值守的批准/拒绝/超时/停止都能在上文红队清单的审计项中复原。

## 9 决策对齐（2026-11-03）

**受哪些已确认决策影响**

- **D1（01 §9）**：后台任务默认开启（范围待确认）、TaskRuntime 权威、两任务槽位、写串行/读并发、TUI 关闭提示与恢复（`01-task-runtime.md:152-166`）。
- **D2（02 §9）**：每 task worker + per-workspace supervisor、controller/observer、日志按会话保留 + 软上限/轮转/显式清理、审批/审计更久（`02-process-supervision.md:169-183`）。
- **D3（03 §9）**：后台写 = worktree + 审批队列（默认挂起）；required 审批到期→任务失败 `approval_timeout`；requester/decider 拆分；TaskRuntime 统一清理（`03-unattended-approval.md:136-146`）。
- **D4（04 §9）**：单写者 + worktree；合并默认用户批准、合并后重验；worktree 复用主环境；Kill switch = CLI + `.vibe/stop` + TUI 同一实现；非 git P0 建议待用户确认（`04-isolation-concurrency.md:158-171`）。
- **D5（05 §9）**：`goal-record.json` + 用户确认 `acceptance_criteria`；后台 goal 任务需携带已确认 criteria（`05-goal-state.md:161-165`）。

**本块已按决策修改的内容**

1. §1/§4：把“后台默认关闭 + opt-in 才后台”改为“**后台默认开启**（范围待确认）+ 保守 profile”；opt-in 只用于放宽能力。
2. §4.1/§4.2/§5：T1 写默认改为独立 worktree + 审批队列挂起；增加两任务槽位、写串行/读并发；required 审批超时→`task_failed`、单动作超时→`deny_action`；profile 增加 kill switch/retention/merge 字段。
3. §5：kill switch 改为 CLI `vibe task stop <id>`/`--workspace`/`--all` + `.vibe/stop` + TUI 同一实现；SIGTERM→宽限→SIGKILL 进程组，`cancelled` + 审计。
4. §2/§3/§6：把“审批/隔离/日志现状”与决策分开表述，明确这些决策**尚未有代码实现**；补充保留策略、requester/decider、非 git 待确认的交叉引用。
5. §8：保留原开放问题，更新 kill switch 为剩余细节（宽限期）、新增后台默认范围与非 git P0 待确认；红队清单补充审批超时/kill switch/日志保留。
6. 全文 `path:line` 按 core/app 分域重构后的路径更新（`app/safety/`、`app/runtime/`、`app/session/`、`app/tools/`、`app/eval/`）。

**仍未决（保留给用户 review，见 §8）**

- 自动允许起步集；opt-in 授权主体与审批记录；凭据 allowlist 与工具链；日志/告警的具体数值与渠道；后台默认开启范围；非 git 工作区 P0 方案。

## 10 用户回答处理（2026-11-03）

用户回答原文见 §8。处理结果：

**Q1（已确认 2026-11-03）：创建时预授权 + 运行时通知**
- 任务创建时用户**显式选择/确认**允许的 capability 集（沙箱执行、worktree 写等）；
- 自动放行时写 `notice`/审计事件（谁授权、profile、任务、时间），不是静默自动；
- profile 变更必须写审批/审计，不能静默放宽。

**已确认（Q2–Q6，2026-11-03，采用 Lead 建议）**
- Q2 opt-in 主体：仅本机 OS 用户可改；任务创建可按预置模板选择但需显式确认；项目配置只能收紧；所有变更写审批/审计。
- Q3 kill switch：`vibe task stop` 在 step/工具边界检查 → SIGTERM 进程组 → 宽限 5s → SIGKILL；任务 `cancelled`、保留 worktree 供检查；另设 `vibe task pause` 暂停并保留 worktree 等 resume；`--now` 立即杀；首版无远程。
- Q4 凭据：放弃“按变量名硬 allowlist”，改「最小继承 env + 秘密 deny-list + 项目显式追加」：继承 `PATH/HOME/USER/LANG/LC_*/TMPDIR/XDG_*/UV_*/NODE_*/npm` 等工具链安全前缀，strip `*KEY*/*TOKEN*/*SECRET*/*PASSWORD*` 与已知值模式，不传 `.env`；目录按只读缓存挂载；用 git/uv/npm/pytest 矩阵验证。
- Q5 保留/告警默认值：events/trace 64MB 或 30 天轮转并 gzip；审批/审计保留 180 天；rounds 永久（小）；通知 macOS `osascript` + terminal bell，事件=待审批/完成/失败/超时；提供显式 purge + 审计。
- Q6 后台范围：建议 `/goal` 与显式长任务默认后台，普通 `--task`/eval 一次性；非 git P0 拒绝后台写、只读允许，P1 影子副本。

**实现备注**：Q2–Q6 的建议值作为默认（后续可按实测调整）：kill switch 宽限 5s；env 采用安全前缀继承 + secret deny-list；events/trace 64MB/30 天、审批/审计 180 天；后台范围 `/goal`+显式长任务、普通 `--task`/eval 一次性；非 git P0 拒绝后台写/只读允许、P1 影子副本。无剩余待拍板项。
