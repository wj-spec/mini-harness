# 建设方向

**P3 计划审阅栏与常驻进度（2026-11-11，实施中）**：针对「计划卡 → 执行操作太多、计划展示不够凸显」的反馈，计划待决定时在输入框位置显示审阅栏：`Enter` 一次直接执行（写入/命令仍逐个审批），`↑↓` 选步骤后 `m/k/f/p` 预填步骤修改，`b` 正文、`r` 修订、`Esc` 稍后处理并保留草稿；执行进度清单改为常驻（结束后保留 `已完成`）。操作从「Ctrl+R → e → Enter」降为「Enter」。完整 Ink **743/743**、Python **1957**（**1953 passed / 4 skipped**），真实 PTY 冒烟通过；P3 人工验收仍暂停至改造完成。见 [验证记录](../openspec/changes/implement-plan-structured-checklist/verification.md#ux-二轮计划审阅栏与常驻进度2026-11-11)。

**P3 计划结构化子任务清单（2026-11-11，实施中）**：人工验收指出 DSH 等 coding 应用的 plan 会制定结构化子任务，而本工作台此前把 plan 呈现为文本产物。本增量保留 Plan JSON/版本/审批/验收/StepLedger 证据底座，把审阅卡与审阅焦点改为结构化步骤清单（step_id/标题/类型/风险/文件/依赖/验收），执行中主区显示只读进度（✓/◐/○、当前步骤、证据数），侧栏完成数来自真实 ledger；文档正文仍可 `Ctrl+R` 查看。完整 Ink **742/742**、Python **1957**（**1953 passed / 4 skipped**），真实 PTY 冒烟通过。改造期间 **P3 人工验收暂停**。见 [实施包](../openspec/changes/implement-plan-structured-checklist/proposal.md) 与 [验证记录](../openspec/changes/implement-plan-structured-checklist/verification.md)。

**P3 计划结构化子任务清单（2026-11-11，实施中）**：人工验收指出 DSH 等 coding 应用的 plan 会制定结构化子任务，而本工作台此前把 plan 呈现为文本产物。本增量保留 Plan JSON/版本/审批/验收/StepLedger 证据底座，把审阅卡与审阅焦点改为结构化步骤清单（step_id/标题/类型/风险/文件/依赖/验收），执行中主区显示只读进度（✓/◐/○、当前步骤、证据数），侧栏完成数来自真实 ledger；文档正文仍可 `Ctrl+R` 查看。完整 Ink **742/742**、Python **1957**（**1953 passed / 4 skipped**），真实 PTY 冒烟通过。改造期间 **P3 人工验收暂停**。见 [实施包](../openspec/changes/implement-plan-structured-checklist/proposal.md) 与 [验证记录](../openspec/changes/implement-plan-structured-checklist/verification.md)。

**P3 启动期反馈与计划卡复核（2026-11-11，实施中）**：人工 P3 验收反馈「Enter 未展示计划卡」。真实 PTY（80×24、100×40、200×84，含模拟 kitty 协议）复核当前构建计划卡正常出现并可 Ctrl+R 审阅；修复启动阶段 Enter 被忽略且无反馈、计划卡提示被最新记录行覆盖两处问题。完整 Ink **738/738** 通过、Python **1957** 项（**1953 passed / 4 skipped**，本次沙箱允许 PTY）。真实用户屏幕仍待确认；P3 整体不标验收。见 [P3 验证记录](../openspec/changes/implement-improve-workbench-ux-p3/verification.md#启动期反馈与计划卡复核2026-11-11)。

**P2 Shift+Enter 换行（2026-11-10，实施中）**：输入框开启 Ink 的 kitty 键盘协议自动探测（`kittyKeyboard: auto`）。Ghostty 等支持协议的终端自动启用后，`Shift+Enter` 以 CSI-u 上报并插入换行；`Enter` 仍发送。不支持协议或非交互 TTY 时自动回退到原有行为（`Ctrl+J` 换行、`Enter` 发送），不报错。新增 Composer 原始序列与 App 端到端回归；完整 Ink **737**（735 passed、2 项沙箱 PTY 环境失败）、Python **1957**（1944 passed / 13 skipped）。真实 Ghostty 人工确认待做，P2 不标整体验收。见 [P2 验证记录](../openspec/changes/implement-improve-workbench-ux-p2/verification.md#shiftenter-换行kitty-键盘协议2026-11-10)。

**P6 侧栏生命周期与启动噪声（2026-11-10，实施中）**：手动验收发现的「主区退出后侧栏未关闭、重开再按 Ctrl+G 出现第二个侧栏、重启后左下角提示找不到计划产物」已修复。主区退出（含崩溃/终止）一律联动结束侧栏，不再因有选区保留；启动或再次打开侧栏前按 link 登记回收父进程已退出的遗留面板，已有存活侧栏按 PID 聚焦，标题失配也不重复分屏；resumed 计划自动发现改为静默，无产物时不再显示提示，显式 `/plan` 仍给出原因。完整 Ink **735**（733 passed、2 项沙箱 PTY 环境失败）、Python **1957**（1944 passed / 13 skipped）。真实 Ghostty GUI 与真人仍待验收，P6 不标整体验收。见 [P6 验证记录](../openspec/changes/implement-improve-workbench-ux-p6/verification.md#侧栏生命周期与启动噪声2026-11-10)。

**P2 附回占位与输入换行（2026-11-10，实施中）**：侧栏选区「附回」不再把整段多行引用塞进输入框，而是一行占位 `[附回] 工作区 vs HEAD · 文件 · 行/字符范围 · N 字符`；完整引用保留在附回缓冲，提交/纠偏/排队后续/模式任务时展开给模型，草稿与对话卡片保留占位。外部写入草稿（附回、命令预填、拒单恢复）统一把光标移到末尾，附回后可继续换行与续写；来源版本复核、去重与错误回执不变。完整 Ink **731**（729 passed、2 项沙箱 PTY 环境失败）、Python **1957** 项（1944 passed / 13 skipped）。真人再验收仍待完成，P2/P6 不标整体验收。见 [P2 验证记录](../openspec/changes/implement-improve-workbench-ux-p2/verification.md#侧栏附回占位与输入换行修复2026-11-10)。

**P2 记录与对话主区分离（2026-11-10，实施中）**：手动验收反馈工作台记录插入对话专注区打断心流。普通提示（复制/定位反馈、命令结果、操作异常、后台提示）改为独立记录缓冲，不再成为对话条目、也不把阅读位置拉回末尾；对话主区只保留任务/回复/工具/审批与运行回执、审阅卡、历史结果卡。底部提示行显示最新一条记录与 `/logs` 入口，`/logs` 只读面板展示完整多行记录，返回保留草稿与光标。新增 4 项 App 旅程并更新相关断言；完整 Ink **731**（729 passed、2 项沙箱 PTY 环境失败）、Python **1957** 项（1944 passed / 13 skipped）。真人 Ghostty 固定阅读/zoom 验收仍待完成，P2 不标整体验收。见 [本批验证](../openspec/changes/implement-improve-workbench-ux-p2/verification.md#工作台记录与对话主区分离2026-11-10)。

**P9 持久用户策略规则撤销（2026-11-09，实施中）**：`/permissions` 面板的持久策略条目带 `ur-`/`pr-` 稳定 id 与可撤销性：用户级规则可二次确认后撤销，后端原子写回用户策略文件（保留 version/autonomy/其它规则）并立即重载；项目级规则保持只读并说明「只能收紧、撤销会放宽」；未知/变化 id、文件不可用明确拒绝且不改文件。新增 4 项 Python 与 1 项 TUI 回归（含实际 Python 分发），完整 Ink **727**（725 passed、2 项沙箱 PTY 环境失败）、Python **1956** 项（1943 passed / 13 skipped）。项目策略写回、撤销独立审计、跨进程同步与真人/Owner Review 待完成，P9 不标完成。见 [本批验证](../openspec/changes/implement-improve-workbench-ux-p9/verification.md#持久用户策略规则撤销2026-11-09)。

**P10 检索索引与高亮（2026-11-09，实施中）**：跨会话检索改为进程内索引 + 签名增量缓存：未变化会话复用、只重建变化会话、删除剔除、损坏计数、200k 消息上限截断；20 会话/4000 消息冷启动 10.7–11.5ms、warm p95 2.9–4.0ms。新增 `highlightSpans`，`/find`/`/history` 结果行与定位后的正文目标行均高亮。完整 Ink **726**（724 passed、2 项沙箱 PTY 环境失败）、Python **1952** 项（1939 passed / 13 skipped）。磁盘级持久索引/预热与真人/Owner Review 待完成，P10 不标完成。见 [本批验证](../openspec/changes/implement-improve-workbench-ux-p10/verification.md#检索索引增量缓存与高亮2026-11-09)。

**P10 跨会话检索与轮次账本深链（2026-11-09，实施中）**：`/history <关键词>` 跨会话检索已保存消息并打开所选会话，打开复用 P8 身份复核、不自动运行，打开后自动定位首个匹配行；`/rounds` 展示轮次账本（目标/状态/验证三态/步数费用）并按目标文本定位正文轮次，旧轮次不在正文时提示用 `/sessions` 核验，无能力回退正文轮次。后端新增 `search`/`rounds` 结构化命令与能力协商；完整 Ink **725**（723 passed、2 项沙箱 PTY 环境失败）、Python **1950** 项（1937 passed / 13 skipped）。跨会话索引/增量缓存、搜索高亮与真人/Owner Review 待完成，P10 不标完成。见 [本批验证](../openspec/changes/implement-improve-workbench-ux-p10/verification.md#跨会话检索与轮次账本深链2026-11-09)。

**P10 会话内定位与只读工具折叠（2026-11-09，实施中）**：`/find <关键词>` 搜索本次会话正文并跳到命中行；`/rounds` 按用户任务轮次（含终态）定位；`/errors` 定位工具错误与失败轮次；定位只改变回看位置，`Ctrl+End` 回最新，返回保留草稿与光标。连续 ≥2 个成功只读（tier=read）工具调用折叠为摘要，`Tab`/`Ctrl+O` 仍可查看原始调用与输出；旧后端无 tier 时不折叠。3000 条记录/8999 渲染行实测搜索 p95 **4.3–4.5ms**；完整 Ink **717**（715 passed、2 项沙箱 PTY 环境失败）、Python **1946** 项（1933 passed / 13 skipped）。跨会话全文检索/索引、rounds 账本深链、真实终端与 Owner Review 待完成，P10 不标完成。见 [本批验证](../openspec/changes/implement-improve-workbench-ux-p10/verification.md#会话内定位与只读工具折叠2026-11-09)。

**P9 权限来源与逐条撤销（2026-11-09，实施中）**：`/permissions` 打开结构化权限面板：显示当前档位与来源（本进程 CLI / 用户策略默认 / 默认 ask，含 project 上限）、进程内 workspace/session 授权的稳定 id、工具与目标摘要、有效期（本进程，退出失效）、持久 user/project 策略规则（来源文件、不可进程内撤销原因）与策略加载错误。面板可逐条撤销授权并显示撤销后的剩余权限；未知/已撤销/跨工作区 id 明确拒绝且不改变现有授权；`/permissions reset` 保留 P1 文案与语义，旧后端回退文本结果。新增 3 项 Python、8 项 TUI 回归（含实际 Python list/revoke 传输），完整 Ink **707**（705 passed、2 项沙箱 PTY 环境失败）、Python **1946** 项（1933 passed / 13 skipped）。持久策略文件写回/撤销与真实终端、Owner Review 待完成，P9 不标完成。见 [本批验证](../openspec/changes/implement-improve-workbench-ux-p9/verification.md#权限来源与逐条撤销2026-11-09)。

**P8 旧会话预览与显式打开（2026-11-09，实施中）**：`/sessions` 可按当前用途分页浏览旧会话标题、更新时间、消息数与最近内容，预览只读；点击「打开会话」先创建候选后端并核对 workspace/session/identity/resumed 回执，通过后才替换当前工作台并关闭旧后端，不自动运行（需 `/resume` 或输入新任务）。运行中、目标模式或待办未保存时拒绝切换并保留原会话；失败/超时/取消/迟到回执保留原页与输入。新增 9 项 App/BackendClient 回归（含实际 Python 目录→打开→历史回放、零运行），完整 Ink **699**（697 passed、2 项沙箱 PTY 环境失败）、Python **1943** 项（1930 passed / 13 skipped）。真实终端/PTY 旅程、Ghostty 真人与 Owner Review 待完成，P8 不标完成。见 [本批验证](../openspec/changes/implement-improve-workbench-ux-p8/verification.md#旧会话目录预览与显式打开2026-11-09)。

**P8 原会话上下文与执行互斥（2026-11-09，实施中）**：返回后下一次模型请求接入最新有效快照、消息和任务状态；同会话其他连接运行时拒绝抢写，普通草稿恢复，持久后续消息退回队列并暂停。启动恢复不改写活跃工具，正常结束或强退后可显式重试。完整 Ink **690/690**、Python **1932 passed / 4 skipped**，实际双进程与 32×12 worker/Ink/Python PTY 通过。旧会话导航和 Ghostty 真人/Owner Review 仍待完成，见 [本批验证](../openspec/changes/implement-improve-workbench-ux-p8/verification.md#原会话上下文与并行执行保护2026-11-09)。

**P8 工作台内接入与停止（2026-11-09，实施中）**：已接通任务目录中的接入/审批/停止/离线查看，返回保留两侧草稿与光标，运行中等待或停止后切换，连接失败可回原会话。完整 Ink **687/687**、Python **1923 passed / 4 skipped** 与实际 worker/Ink/Python PTY 通过；原会话模型上下文同步/同会话并发写入、旧会话导航和 Ghostty 真人/Owner Review 仍待完成，见 [第二批验证](../openspec/changes/implement-improve-workbench-ux-p8/verification.md#工作台内接入与停止2026-11-09)。

**P8 当前会话任务导航（2026-11-09，实施中）**：已接通可点击目录、分页选择与准确历史，刷新保留任务身份，返回保留草稿/光标。完整 Ink **679/679**、Python **1918 passed / 4 skipped** 与实际 Ink/Python PTY 通过。接入/停止、旧会话切换和 Ghostty 真人/Owner Review 仍待完成，见 [第一批验证](../openspec/changes/implement-improve-workbench-ux-p8/verification.md)。

**P7 持久待办（2026-11-09，实施中）**：后续消息已保存到所属会话，重开默认暂停；同会话单窗口管理，其他窗口只读并可显式接入。发送中断保留请求身份并显示“结果待核对”，不会自动重发；保存失败和未保存编辑保留输入并提示。完整 Ink **670/670**、Python **1915 passed / 4 skipped**，真实 Ink/Python 进程的退出重开、多窗口接入与中断恢复通过；Ghostty 真人完整旅程与 Owner Review 仍待完成。 见 [本批记录](../openspec/changes/implement-improve-workbench-ux-p7/verification.md#持久待办与未知发送恢复2026-11-09)。


长期目标是一个越用越了解我的个人助手工作台，同时作为深入学习全栈开发的长期项目。当前以单进程 Agent loop 发展 vibe coding 能力，在真实编码任务中积累使用反馈，同时理解 agent 的运行原理；界面开发、状态管理、前后端通信和交付也纳入学习范围。后续探索主 Agent 编排、子 Agent 执行的协作架构，以及展示用户偏好与画像的“关于你”。阶段顺序表达建设重点，不预设完成日期。

具体交付顺序、当前状态与完成条件见 [工作台里程碑 W0–W7](MILESTONES.md)；本页记录长期方向与取舍。

## 第一阶段：把单 Agent 编码流程用起来（当前）

对应 W0–W3：可复现起点、真实代码改动、运行中纠偏与停止、可靠续做同一任务。

已有基础包括模型流式调用、读写工具、执行命令、审批、上下文压缩、会话恢复、eval 和 stdio MCP 客户端（外部工具按配置挂载，走同一套审批）。功能概览见 [README](../README.md)，运行与配置见 [使用指南](USAGE.md)，验收状态见 [里程碑](MILESTONES.md)。

接下来围绕真实仓库任务，把需求、读代码、修改、验证和复盘串起来。每次只推进能够理解和验证的一小段，优先处理使用中反复出现的失败与摩擦。

这一阶段的判断标准是：能完成小型编码任务，能解释每一步由模型还是 harness 负责，并能通过测试和执行记录确认结果。新增能力应对应具体任务中的问题。

## 界面与全栈工程学习

2026-10-20 用户明确将界面开发和深入全栈作为长期学习目标，因此新 TUI 选用 **Node.js + TypeScript + React/Ink**，Python 继续负责 Agent loop、工具、审批、预算和会话。已新增可选的 `run_ink.sh`，接通 JSON Lines 与全屏工作台，并继续补齐恢复、MCP、权限和可信执行结果。工程证据见 [功能补齐](specs/ink-parity.md)，真实模型任务与体验仍待验收。选型依据见 [TUI 调研](research/tui-research-2026-10-20.md)。

迁移作为 W1–W3 内的工程切片推进。Python 保持执行与权限的权威，界面通过请求和事件交互；界面断开不能被视为批准动作。2026-10-23 用户明确 Textual 后续不再使用：Ink 成为唯一界面入口，Textual 代码、测试与依赖已删除，Python 仅保留 `--stdio` 后端与 eval 入口。具体切片见 [里程碑](MILESTONES.md#界面迁移与全栈学习切片)。

这一阶段学习 TypeScript 类型、React 组件、状态管理、异步事件和进程通信。后续根据真实使用需要增加浏览器入口，继续学习 DOM/CSS、浏览器交互、HTTP API、数据持久化，以及有部署需求时的鉴权和部署；这些能力单独实践和验收。React 的思路、类型与部分状态逻辑可复用，浏览器界面需要专门设计与实现。

2026-10-21 根据首版界面过于简陋的使用反馈，已补全屏布局、Markdown 阅读、多行编辑和历史、工具全文、审批分页与真实用量显示，范围见 [Ink 工作台](specs/ink-workbench.md)。后续同日已接通恢复、MCP 和授权管理；视觉质量、结果可信度、恢复与真实编码体验一并纳入验收。

后续按用户滚动与视觉分区反馈，补齐滚轮 / 键盘阅读、焦点与稳定回看，以及按实际 Agent 活动展示的任务、工具、审批、错误与预算区域，见 [阅读与状态展示](specs/ink-reading-state.md)。工程验证与用户实际终端体验分别记录。

## 第二阶段：从使用反馈中积累个人上下文

对应 W4–W6：新任务复用项目背景，通过“关于你”查看和管理个人偏好与画像，验证个性化是否带来实际收益。W4 项目背景（2026-10-23）与 W5 个人偏好（2026-10-24）均已实现并通过真实验收；W6 v2 三组对照未观察到重复说明/纠正减少，主假设未获支持。2026-10-26 决定停止 P0 扩张、不进入 P5，保留显式偏好管理能力。

会话恢复和任务摘要已经服务于当前任务。“关于你”计划在日常任务协作中逐步整理 Agent 对用户偏好、目标、习惯和工作方式的理解，并向用户展示这些内容及其来源。用户可以查看、确认、纠正或删除相关理解，再将适用的内容用于后续任务。跨任务的项目背景与个人画像分别管理。

以重复说明减少、后续任务更贴合实际意图作为效果依据，再选择实现方式。当前只保留带来源、可纠正的偏好条目与显式观察；P0 复测已结束且未证明收益，路线图停在 [P0 结果与停止决策](specs/memory-personalization-roadmap.md)，不进入 P5。

## 第三阶段：按实际需求扩展工作台

对应 W7：在出现明确的常用场景后，细化并交付第二种工作流。

在编码工作流稳定、个人上下文确有帮助之后，再根据反复出现的需求扩展到其他个人助手任务。TUI 是首个入口，长期界面由使用场景决定。

LSP、插件生态和其他界面根据具体使用需求评估。stdio MCP 已能接入外部工具，但还没有在真实任务中验证过；HTTP transport、资源 / prompt 能力、LSP 与多 Agent 仍未实现。运行隔离也根据实际执行风险单独评估；当前命令边界仍是审批、黑名单和超时，不是系统沙箱，现状、绕过点与选项见 [执行隔离评估](specs/execution-isolation.md)，主流实现选型见 [沙箱调研](research/sandbox-research-2026-10-25.md)。

## 多 Agent 与 Computer Use 架构探索

当前交互主 loop 仍是单进程单 Agent；2026-11-06 已补只读子 Agent runner（`/explore`、子 TaskRecord、父取消/depth/批量扇出）与 Plan 模式（`/plan`、审批/执行绑定/验收）P0 基础，见 [P0 收束报告](specs/p0-closure-report.md)。2026-11-07 已完成真实模型 P1 冒烟：Plan 生成、两步执行、审批帧、可信验收命令均通过；
只读 `/explore` 子 Agent 也成功。写子 Agent、模型自发委派与真实多 Agent 收益仍未验证。后续可能由主 Agent 负责理解需求、拆分与编排任务、汇总和核对结果，子 Agent 负责执行范围明确的子任务；Computer Use 则让 Agent 观察/操作本机 GUI。

2026-10-26 已完成两方向的方向调研与实施规格：[Multi-Agent 与 Computer Use](specs/multi-agent-computer-use.md)；Computer Use 已收窄为先做 [Browser Use](specs/browser-use.md)。当前顺序为 **G0 MCP 工具级权限 → B1 浏览器观察 → B2 本地受控浏览器动作 → 再评估 M1 只读子 Agent / 桌面 Computer Use**。2026-10-27 B2 已通过用户 CLI 真实模型与 Ink 本地功能验收：click/type/select、逐次审批、拒绝后保持草稿、零提交均成立；主观使用体验继续记录。当前范围保持本地且不提交，后续扩展按实际需求单独评估。第一版不并行写、不做视觉优先的通用桌面控制。

这是一条架构探索方向，与“关于你”的个人上下文能力分别演进。先根据实际任务确认委派/GUI 操作是否带来收益，再确定通信、执行和任务状态的设计；多 Agent 不预设为 W0–W3 的交付要求。

## 计划模式后续方向（P2，2026-11-08 记录）

2026-11-08 已完成 P0/P1：可配置 plan 提示词、`submit_plan` 最终调用门禁、审阅三态
（批准/要求修改/关闭）、Markdown 审阅稿，以及会话级 plan mode
（`/plan on|off|status` + `plan-mode.json` + TUI 状态）。同日复盘发现交互层债务：
`/plan on` 等命令把内部状态暴露给用户、`request-changes` 不驱动模型修订、批准与执行分离；
下一轮优先做交互重设计（一步入口、Plan review 卡、request-changes 闭环、批准即执行），
缺陷、目标模型与 P0/P1/P2 方案见 [计划模式 UX 复盘与改进方案](specs/plan-ux.md)。
以下为交互重设计之外的后续项：

- **工具目录稳定/渐进披露**：若未来支持 turn 内切换 plan mode（当前只在 task/run 边界切换），
  参考 DSH 的做法保持工具列表稳定、首次进入只加 `submit_plan`，并评估 prompt cache 影响；
  当前不实现。
- **事件日志投影与多端 pending**：`plan-mode.json` 是单文件简单状态；多标签、attach、
  重启后的统一投影与 pending 回放后续再评估（当前 TUI 单连接够用）。
- **子 Agent/fork 的 plan 语义**：fork 是否继承计划模式、新 spawn 是否从非激活开始、
  子级不得自行批准计划；当前 plan profile 禁止模型自发委派，等实际需求再定。
- **模式抽象**：plan 协作状态保持 plan 专用；出现第二种具体协作模式后再抽通用 seam，
  避免提前做模式注册表。
- **通用审批/question seam**：当前 plan 审阅已有 `review_intent` 并集中到 `plan_review`；
  跨 plan/goal/子 Agent 的统一审阅请求与多端渲染，等出现第二个实际使用者后再抽，
  遵循“第二个使用者再复用”的取舍。

## 工作台 UX 落地规划（2026-11-08，P1 核心交互已验收）

最新顺序调整：P3 核心演示已获用户确认，但快捷键审批体验不佳，因此先交付[审批按钮增量](../openspec/changes/implement-improve-workbench-ux-p1/verification.md#审批按钮增量2026-11-08)：可点击的本次允许/拒绝、更多授权与常规键盘导航。自动与三种尺寸工具 PTY 通过，新按钮真人体验待验收。P3 原生重开/恢复、P2 真人和学习 Review 未获确认，P3 计划结构化清单与输入框审阅栏改造中、人工验收暂停，以下历史进度保留；当前推进 P6 对象范围与检查区，P4/P5 剩余验收单列，进度见本节末。

结合 [Ink UX 审计](research/ink-ux-audit-2026-11-08.md)、Plan UX 复盘与 Ghostty 侧栏走查，
新增 [工作台 UX 实施路线图](../openspec/roadmaps/improve-workbench-ux/roadmap.md)。近期先做输入/审批控制与稳定阅读，
再推进 Plan 审阅修订、操作入口及带轮次范围的任务检查区；每阶段分别记录自动验证和真人终端验收。
默认入口保持 Ink，OpenTUI 通过独立、限时的侧栏与输入对照试验评估；规划不等同于里程碑完成或迁移决定。
P1 的 [输入/审批控制](../openspec/changes/implement-improve-workbench-ux-p1/verification.md) 已完成工程验证，用户确认核心交互符合预期；新增可点击按钮的真人体验仍待反馈。P2 的 [稳定阅读](../openspec/changes/implement-improve-workbench-ux-p2/verification.md) 通过自动与工具 PTY；记录与对话分离、附回占位与输入换行修复、Shift+Enter 换行（kitty 协议）通过工程验证，真人待验收。P3 的 [审阅与执行](../openspec/changes/implement-improve-workbench-ux-p3/verification.md) 通过受控真实模型与用户核心演示，原生重开/恢复及学习 Review 待确认。P4 已接通修订和重开恢复，当前证据见下；P5 入口、模式恢复及紧凑布局通过工程验证，真人验收待完成；P6 实例隔离与基础运行/计划投影、侧栏生命周期与遗留实例回收通过工程验证、完整检查区实施中；P7 持久恢复通过工程验证、真人待验收；P8 当前会话导航、接入/停止、上下文同步/执行互斥与旧会话预览/显式打开通过工程验证，真实终端与真人待做；P9 权限面板与用户级持久规则撤销通过工程验证、真人待做；P10 会话内定位、跨会话检索与索引、账本深链与高亮通过工程验证、真人待做。

P3 验收补充：首个真实样本预算暂停后，已修复任务续接、拒绝后的完成判定与最终回执，并提供 `VIBE_FAKE_DEMO=plan` 演示。第二个受控真实样本已完成 message.txt 修改、两次逐次审批和验收 exit=0；离线重开未重复运行。样本暴露的新提案风险提示已修复，最终 Python 1817 passed / 4 skipped、TUI 497/497。Ghostty 真人与学习 Review 仍待完成；详见 [P3 验证记录](../openspec/changes/implement-improve-workbench-ux-p3/verification.md)。

P4 当前进度：修订记录与重开恢复已接通，[回执丢失与新版竞争](../openspec/changes/implement-improve-workbench-ux-p4/verification.md#实际进程故障恢复补验2026-11-08) 也通过实际进程验证；读取与冲突拒绝不增加模型调用或运行段。该轮 TUI 540/540，Python 1885 passed / 4 skipped。未发送反馈仍只保存在进程内；受控真实模型、Ghostty 真人与 Owner Review 待完成，P4 尚未整体验收。

P5 当前进度：[模式与恢复](../openspec/changes/implement-improve-workbench-ux-p5/verification.md#紧凑布局与原生点击2026-11-08) 已在命令入口与可用原因基础上接通：当前任务与下次默认值分别显示，pending 可选择/撤回，重开无自动运行，保存失败保留输入。全量 TUI 561/561、Python 1888 passed / 4 skipped，三尺寸实际 PTY 通过；早期跨语言检查遗漏已更正。80×24 固定样本正文从 8 行增至 12 行，三尺寸原生鼠标拒绝通过；下一步细化 P6 对象范围与检查区；真人与 Owner Review 单列，P6 实例隔离与基础运行/计划投影通过工程验证、完整检查区实施中；P7 持久恢复通过工程验证、真人待验收；P8 当前会话导航、接入/停止及上下文同步/执行互斥通过工程验证，旧会话导航实施中；P9 权限面板与用户级持久规则撤销通过工程验证、真人待做；P10 会话内定位、跨会话检索与索引、账本深链与高亮通过工程验证、真人待做。

P6 启动稳定性增量：侧栏新增启动与实例占用：同一工作台的多模式启动、不同会话的 quick 启动不会同时进入宿主操作；重复 CLI 拒绝覆盖登记，正常退出和强制结束后可重开。完整 Ink 643/643、Python 1915 passed / 4 skipped、八组并发压力与实际 PTY 复验通过；评审补齐路径别名竞争，Ghostty 原生 GUI 因工具安全限制未能核验，真人完整旅程与 Owner Review 仍待完成。见 [验证记录](../openspec/changes/implement-improve-workbench-ux-p6/verification.md#侧栏启动竞争与重复登记2026-11-09)。

P6 当前进度：主区新增可点击的专注检查，与独立侧栏共用变更、准确计划和验证阅读；返回保留草稿/光标、页码与选区。无法自动分屏时，结果入口进入内嵌视图；新审批只提示返回处理，停止仍指向当前运行。全量 Ink 638/638、Python 1915 passed / 4 skipped 与实际终端复验通过；Ghostty 原生、完整真人旅程与 Owner Review 仍待完成，P6 保持实施中。见 [专注检查证据](../openspec/changes/implement-improve-workbench-ux-p6/verification.md#内嵌专注检查2026-11-09)。

P6 来源路由增量：检查记录已保存任务实际文件目录，attach 和历史选择据此读取 worktree 的当前 diff；项目与任务中的同名文件不会混用。目录失效或旧记录缺少来源时明确说明，附回选区同时复核来源与版本。实际后台写入、当前/重开侧栏和目录替换拒绝通过；内嵌专注检查、完整真人旅程和 Owner Review 仍待完成，P6 保持实施中。完整 Python 1915 passed / 4 skipped，Ink **633/633** 全量通过；见 [来源路由证据](../openspec/changes/implement-improve-workbench-ux-p6/verification.md#worktree-文件来源与-diff-路由2026-11-09)。

## Agent 实验台规划（2026-11-08，未实施）

为持续改善 coding Agent，新增 [Agent 实验台路线图](../openspec/roadmaps/build-agent-lab/roadmap.md)
与 [整体设计](../openspec/roadmaps/build-agent-lab/design.md)：以真实任务证据、实际运行版本、固定协议评测和失败回归形成优化闭环。
近期先做可核查运行证据包与 SWE-bench 对照，Terminal-Bench 为紧随其后的必需主基准；内部 eval 保留快速自检。
后续根据实际案例推进本地配套工作台、基线管理与可选观测集成。不同数据集版本、原生评分与本地诊断统计分开；本地跑分不等同于官方接纳上榜。
Ink 仍是 Agent 操作入口，实验台承担跨运行分析；本地证据为事实源，OTel 为可选派生出口。
本规划服务于 W0 可复现起点与已有评测扩题，不改变 W0–W7 的验收状态；已完成文档规划与本地流程原型，
首版原型已获“业务覆盖不足”的反馈；用户明确优先定位真实任务的失败、反复与高消耗，
已用三个真实历史样本重做诊断入口，支持请求/上下文、工具结果及带证据的改进草案，双基准对比保留，
见 [业务旅程](../openspec/roadmaps/build-agent-lab/business-workflows.md) 与 [原型核验](../openspec/roadmaps/build-agent-lab/prototype-review.md)。
新版用户体验反馈待收集，P1–P7 的生产能力仍未实施。

## 持续取舍

- 先用真实任务发现问题，再决定功能与抽象；组件出现第二个实际使用者时再评估复用。
- 当前保持一个清楚的 Agent loop；后续探索多 Agent 时，任务委派、审批和结果仍需能够被解释与验证。
- 每次更新本文件时区分已有能力、当前工作和后续方向，避免把愿景写成已交付功能。
