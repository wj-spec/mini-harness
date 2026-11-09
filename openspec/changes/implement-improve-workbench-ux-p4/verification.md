# P4 实施、评审与验证

状态：实施中，后端与 Ink 卡片反馈已接通，同进程修订/取消/续做及新版执行有自动与 PTY 证据；重开反馈恢复界面、真实模型修订质量和 Ghostty 完整旅程尚未验收。最新证据见末节。

## 基线与设计核对（2026-11-08）

- 已比对上轮 `.vibe/ux-p3-real-closeout-2026-11-08/closeout-manifest.json` 中全部文件哈希，当前工作树与已验证版本一致。新基线 `.vibe/ux-p4-2026-11-08/baseline/` 共 580 文件，保留此前 Plan、侧栏和 Agent 实验台改动；没有提交。
- 源码证明：request_changes 只调用 revise_plan 写复制版 draft，stdio 不启动模型；submit_plan 固定新 plan_id/v1；P3 卡片尚无反馈编辑。现有测试也明确断言该旧行为，不能将它当 P4 成功。
- 设计复核：坚持准确 ReviewRef、同族版本、没有占位 draft、硬只读、一次初始运行与只读恢复查询。补入旧 execute/background 的回退风险，避免只修新卡片却留下旧入口绕过。
- 测试 seam 延续 P3 的 ToolRegistry/Plan 产物、PlanReview/TaskRuntime、JSONL/FakeProvider、Ink App/真实后端；按纵向切片逐步增加行为测试。
- 主代理设计自审，未使用独立子代理。P2/P3 真人反馈仍待收到，不作为本阶段已验收依据。

实现与验证结果将在相应切片完成后追加；总清单见 [tasks](tasks.md)。

## 同族版本提交切片（2026-11-08）

`ToolRegistry.register_plan_tool()` 可由后端绑定原 Plan 快照、反馈和修订 ID；模型 schema 仍只包含业务内容。`propose_revision()` 在共享进程锁内校验来源、版本与 payload，原子发布 v+1 proposed。原版字节、审批、执行台账和验收记录不迁移；新版 approval 为空，省略的业务字段保留。当前没有产品调用方使用修订注册参数，旧 request-changes 仍只写 draft，不能据此宣称修订 run 已接通。

逐条失败回归与修复：

| 行为 | 红→绿证据 |
|---|---|
| 同 plan_id/v+1、保留原约束/验收、重新审阅 | `publish-red.log` → `publish-green.log` |
| 重新注册/回执丢失后复用原产物，不覆盖或追加版本 | `retry-red.log` → `retry-green.log` |
| 已有 v3、v2 缺失时不能用旧 v1 补发 v2 | `version-red.log` → `version-green.log` |
| executing/superseded/legacy 源状态不得直接修订 | `source-status-red.log` → `source-status-green.log` |
| 把源文件复制到另一 Task 后不得发布错误归属版本 | `source-owner-red.log` → `source-owner-green.log` |
| 产物已落盘、审计失败后重试补可追溯记录 | `audit-recovery-red.log` → 最终 `targeted.log` |

另外覆盖模型伪造 plan_id/task_id/version/approval、注册后来源变化、原子替换失败、另一修订占用版本与中文反馈超长拒绝。共新增 **16 项 Python**；最终相关回归 **201/201**，见 `targeted.xml/log`。故障只注入真实文件系统调用；测试通过公开 ToolRegistry/Plan 读写检查结果，没有 mock 领域内部函数。

主代理增量自审确认：普通 plan 注册未改变，修订专用身份不暴露给模型，Plan 加载/hash 与旧批准语义未修改，原有进程锁继续覆盖提交。也确认尚缺控制器对同族审批/执行的互斥、准确 Ref 查询和任务归属，必须在新入口开放前完成；没有将底层校验替代完整运行权限。

### 冻结后的全量结果

- Python **1833 passed / 4 skipped**，78.60 秒；TUI **497/497**，30.31 秒，均 exit=0；仅既有 tarfile 警告。见 `python-full.xml/log`、`tui-full.log`。测试前冻结两份源码与一份测试，结束后核对 SHA-256。
- 已有 Node→Python 计划允许/拒绝链路保持通过。P4 尚未接入口，不用普通 fake smoke 冒充 P4 修订旅程；未调用真实模型、未修改 Ghostty 配置。
- 保存新增文档、基线增量差异、链接检查与归属清单，见 `.vibe/ux-p4-2026-11-08/p4-manifest.json`。core、侧栏和 Agent 实验台规划保持原哈希，未提交。
- 后续按 tasks 顺序接持久修订操作及只读 run，再接 Ink；P4 不标整体验收，P2/P3 真人与学习 Review 状态不变。

## 持久修订 runtime 切片（2026-11-08）

基线 `.vibe/ux-p4-runtime-2026-11-08/baseline/` 保存 584 文件，已核对上轮验证清单。本轮新增 `app/runtime/plan_revision.py` 和 `tests/test_plan_revision.py`；既有代码仅调整 P3 准确审阅互斥及 TaskStore 恢复。`PlanRevision` 仍未接 stdio 或模型运行，下面是公共 runtime 能力的验证，不是完整修订 UI 验收。

### 交付行为

- 来源五元 ReviewRef 与规范化反馈决定操作身份，先保存预留 task_id，再创建 mode=plan 的 TaskRecord；creation 凭据与初始 run 均需匹配。
- `request` 的重复调用返回原任务，`read` 不创建任务、不追加段、不写文件。`resume` 携带已观察 segment，过期确认只返回已有结果；显式续做保留原任务和已关闭历史。
- `context` 在模型交接前复核来源快照、任务目标/工作区/凭据、活动执行及其他修订。P3 准确批准入口已阻止活动修订期间批准；旧文本/后台入口仍待接线，不能宣称全入口互斥完成。
- 已发布 v+1 的来源凭据匹配时，查询直接返回新版准确 Ref；没有新产物的终态返回 `incomplete/revision_not_submitted`。取消/错误/预算暂停保留 Task 与反馈，后续 stdio 必须把模型 no-submit 映射为可续做的暂停，不将其交给 TaskRuntime 标成 completed。

### 自审发现与回归

假定使用规模为个人工作台，但同一会话可能被两个本机进程打开。评审覆盖新增模块及其 PlanReview、TaskStore、TaskRuntime、ToolRegistry 调用关系；这是主代理自审，没有独立评审结论。

| 具体问题或行为 | 处理与证据（以下日志均在本轮证据目录） |
|---|---|
| 进程退出后 Task interrupted，旧 segment 仍 running，显式续做失败 | 判定 stale 后一并关闭旧段；真实退出子进程复现，`process-exit-red.log` → `process-exit-green.log` |
| 续做准备失败后，busy 请求先恢复写盘再拒绝 | 前置校验全部先于恢复写入；`preflight-red.log` → `preflight-green.log` |
| 初始运行已建立但回执保存失败，后续续做误判为未启动准备 | 有段依据 TaskRecord 恢复；`receipt-red.log` → `receipt-green.log`，首次仍只交接一次 |
| 任务目标/工作区被改、creation 不是对象时仍交接或抛 AttributeError | 复核任务范围，返回领域错误；`context-scope-red.log` → `context-scope-green.log` |
| 请求后旧入口创建执行任务，context 仍交接 | 交接前重查执行冲突；`handoff-red.log` → `handoff-green.log`；旧入口统一仍是后续项 |
| completed 但没提交新计划时显示 paused | 明确 incomplete/revision_not_submitted；`incomplete-red.log` → `incomplete-green.log` |
| 无效 goal 绑定先保存操作再抛 ValueError；symlink 抛出内部异常 | 写入前验证来源，context 统一领域异常；`source-binding-red/green.log`、`storage-red/green.log` |

另有真实双进程同时请求、准备阶段进程退出、任务创建审计失败、运行/续做准备的原子写失败、损坏操作记录、错误 hash/session、超长反馈、disabled/busy/newer-version 无副作用回归。故障注入位于真实文件系统边界，不 mock 领域内部函数；`process-race.log` 记录双进程与准备退出核验。新增 **28 项**，最终相关 **239/239**（`targeted.xml/log`）。

### 全量与边界

- 四份源码/测试冻结后，Python **1861 passed / 4 skipped**（1865 项，78.61 秒）、TUI **497/497**（30.22 秒），均 exit=0；仅既有 tarfile 弃用警告。日志为 `python-full.xml/log`、`tui-full.log`，结束后源码 SHA-256 与 `verified-source.json` 一致。
- 现有 Node→Python 回归通过；本轮没有新增 P4 产品入口、没有模型调用或 Ghostty 真人测试。P1 已收到的人体验收保持通过，P2/P3 真人与 Owner 学习 Review 仍待收到。
- 文档同步模块索引、Plan UX/审计、roadmap/MILESTONES 与阶段执行清单；没有新增用户命令，因此 USAGE/DEMO 不提前描述不可用的反馈功能。文档链接、基线增量和受保护文件哈希见 `runtime-manifest.json`、`runtime-increment.patch`。
- 下一步：先接准确 JSONL/capability 和只读 Agent，避免修订 creation 被误识别为 P3 执行任务；mode=plan 不绑定执行 StepLedger；原计划/反馈完整进入预算约束的上下文。再统一旧批准/执行/后台入口，最后接 Ink 反馈/取消/重开旅程。P4 收尾清单仍保持未完成。

## JSONL 与只读 Agent 接线（2026-11-08）

以 `.vibe/ux-p4-runtime-2026-11-08/runtime-manifest.json` 核对上轮哈希，保存本轮 586 文件基线于 `.vibe/ux-p4-stdio-2026-11-08/baseline/`。本轮修改 12 份源码/测试，未添加新模块、未修改 core 或侧栏，也未调用真实 provider。

### 已实现的后端旅程

1. `plan-revision` capability 与 `request-changes/query-revision/resume-revision` 精确请求、`plan_revision` 回执已接通 Python/TypeScript。续做需要已观察段号；没有 capability 的后端拒绝新准确动作，禁用原生工具时不创建修订任务。
2. 新 stdio 按 creation.operation 区分修订与 P3 执行；修订不绑定执行台账，submit_plan 注册到原计划目录并固定身份。完整来源/反馈 JSON 作为独立固定上下文加入，带权限边界说明，run 结束后移除；plan profile 只提供五个只读工具与 submit_plan。
3. 只有可核对的同族 v+1 产物才算提交。模型只回复文字时，最终结果、Task 和轮次账本都记录 paused/stopped + revision_not_submitted；重复请求不调用模型，显式续做保持原 Task。
4. 旧文本反馈命令改走同一控制器；`/resume` 从最近 trace 找原请求段，来源变化或工具不可用时在新增段之前拒绝。普通 run.task 不得绕过该续做入口。
5. 旧批准进入 P3 准确控制器；TaskRuntime 在共享 Plan 锁内复核修订占用与最新版本再启动执行，后台候选过滤修订中和已被替代的旧批准版本。候选读取到排队创建之间的并发窗口、实际后台 worker 旅程仍保留为未完成项；没有以启动 guard 的回归替代这些验证。

### 自审发现与证据

按个人工作台、可能多进程打开同一会话的范围，主代理审查 stdio 分发、profile/注册/还原、TaskRuntime、后台候选及 TypeScript 传输；没有独立子代理评审。

| 具体问题/行为 | 证据（本轮目录） |
|---|---|
| 修订请求原来没有实际 run，普通提交会生成另一 plan_id | `request-red.log` → `request-green.log`：真实 Agent loop 发布同族 v2，旧版不变、无执行台账 |
| 普通文字结束被记为 done，没有新计划 | `no-submit-red.log` → `no-submit-green.log`：结果/Task/轮次一致未完成，显式续做提交 |
| 旧反馈命令仍只复制 draft | `legacy-red.log` → `legacy-green.log`：旧命令通过模型提交 proposed；原审阅/关闭/批准兼容 |
| 新准确修订被旧协议拒绝，客户端未检查独立 capability | `protocol-red/green.log`、`capability-red/green.log`：校验来源/段号/新版身份，拒绝旧后端 |
| 旧执行可以在活动修订期间或新版发布后从旧批准启动 | `execution-guard-red/green.log`：共用启动边界拒绝，不创建额外 Task |
| 旧批准/后台准备绕开修订互斥 | `legacy-guard-red/green.log`：旧批准使用控制器，后台候选过滤，无计划改写/额外任务 |
| `/resume` 先新增段再发现来源变化；raw run.task 可绕过控制器 | `resume-red/green.log`、`raw-resume-red.log`：复核发生在准备前；无效续做不加段、不调用模型 |
| 禁用工具仍能启动或续做；超长反馈回显破坏协议上限 | `invalid-red/green.log`、`resume-disabled-red/green.log`：拒绝不建任务，非法反馈不截短接受或超限回显 |

`readonly-context.log` 验证反馈无法开放 write_file/bash/delegate，文件不变、没有工具审批；完整来源超窗时 provider 请求为零。`cancel-query-green.log` 验证真实 run 中查询不改变取消目标，停止后反馈/Task 保留，显式续做提交。该测试初版等待了无 ContextBuilder 配置下不存在的 snapshot 事件；已改为等待实际 FakeProvider 请求，不作为产品缺陷计数。

真实 Node→Python 测试通过现有 fake plan 脚本发布同族 v2，关闭进程、重开原会话后只查询；原 Task/segment 不变，trace 中 llm_start 保持 **1 次**，没有协议错误。见 `transport-green.log`；初版重开参数误将互斥的 --continue/--session 同传，修正测试调用后通过。该样本验证接线与恢复，不证明真实模型按反馈修订的质量，也不是卡片编辑/真人验收。

### 冻结后的验证与剩余工作

- 新增 **15 项 Python、3 项 TUI**；最终相关 **272/272**（`targeted.xml/log`）。12 份源码/测试冻结后，全量 Python **1876 passed / 4 skipped**（1880 项，79.95 秒），TUI **500/500**（30.29 秒），均 exit=0；仅既有 tarfile 弃用警告。见 `python-full.xml/log`、`tui-full.log` 与 `verified-source.json`。
- 文档已同步 USAGE/DEMO、计划审批/产物、UX 审计、roadmap/MILESTONES 与 P4 设计/任务；旧切片记录保留历史口径，当前状态以本节为准。文件归属、链接与受保护哈希见 `stdio-manifest.json`、`stdio-increment.patch`。
- 下一步先复核后台排队并发与失败回执，再实现 Ink 卡片内反馈编辑/确认、普通草稿保留、取消/未知结果查询、新版卡片与阅读对象保护。公开 P4 演示、受控真实模型、Ghostty 真人和 Owner Review 均待完成；P2/P3 真人状态不变，P5–P10 未实施。

## Ink 卡片反馈与后台复核（2026-11-08）

证据目录 `.vibe/ux-p4-card-2026-11-08/`；本切片从 587 文件基线继续，修改 11 份已有源码/测试，新增 `action-buttons.tsx` 与 `use-plan-revision.ts`。先前已存在的 `PlanReview.prepare_background` 及其单元回归不重复计为本轮新增；本轮完成 CLI/stdio 调用接线。`core/` 与侧栏源码保持基线哈希。期间发现实验台原型文档及其索引已有外部更新，保存 `external-observed.json` 单列并保留，不归入本轮交付。

### 用户行为与边界

- 所见卡片 `r` → Enter 打开独立反馈输入；显示来源标题/版本，普通草稿、光标和未发送反馈在同一进程内保留。下一步只展示确认，确认修订才发送准确来源/反馈；超过 4000 字不截断、不发送。
- 反馈编辑、确认和状态使用可点击按钮，也支持 Tab/方向键/Enter。等待回执时不重发，未知结果仅查询；取消只针对修订运行，暂停/中断的已观察段可显式续做。
- 成功提交只查询准确新版 Ref，旧卡显示新版可用；另一份已聚焦正文不被切走。工具审批优先，打断反馈输入不消耗普通草稿中的 y/s/p/n，返回后反馈仍在。
- 后台准备将准确复核和 Task 创建放在同一 Plan 进程锁内；共享 helper 复用已有待运行任务，并检查活动修订/执行和损坏记录。CLI 保留优先级与 reused 输出；stdio 保留原回执格式。
- 新 `VIBE_FAKE_DEMO=revision` 演示固定把 `plan demo` 改为 `revised demo`，先提交 v1，再修订为 v2，再由用户批准执行；两次工具审批仍经 ApprovalGate。它证明运行接线，不证明真实模型理解任意反馈。

### 主代理自审与红绿证据

按单用户工作台、同一会话可能跨本机进程使用的范围，检查 App 输入/事件、两个 hook、Composer、按钮测量、backend 协议及 PlanReview/CLI/stdio 调用链。这是主代理自审，没有独立代理评审。

| 具体问题 | 处置与日志 |
|---|---|
| 后台候选读取后直接创建，期间另一进程可获得修订操作 | CLI/stdio 使用共享锁内准备；真实子进程在 act 元数据保存期间请求修订，被 `lock_busy` 拒绝；`background-red.log` → `background-verified.log`/`background-final.log` |
| 两个挂载的 Composer 争用原生光标，未聚焦框隐藏反馈框的光标 | 仅聚焦 Composer 挂载光标持有者；真实 Ink TTY 流验证显示/隐藏；`cursor-red.log` 与全量 terminal-input 回归 |
| Tab 同时由根输入和反馈 Composer 处理，第一次越过“下一步” | 反馈编辑状态由 Composer 独占首次 Tab；`tab-red.log` 与 App 回归 |
| 准确新版查询之后，通用 done 又发现最近计划，可能切到错误对象 | 跟踪已启动修订直到 done，抑制重复发现；`submitted-red.log` 与准确新版/另一份正文回归 |
| UI 允许终态 cancelled 续做，却未提供后端支持的 interrupted 续做 | 与后端 paused/interrupted 条件一致；`resume-status-red.log` → `resume-status-green.log` |
| 返回普通草稿后，修订待回执被解释成“等待审批” | 显示修订请求处理中，仍保留输入；`pending-label-red.log` → `cancel-resume.log` |
| 反馈输入缺少来源标题，复用输入框还显示“审批按钮” | 显示所见来源标题；通用按钮提示不混淆授权；`source-display-red/green.log`、`feedback-hint-red.log` → `tui-final.log` |

另外验证了准确 A/B 来源、重复确认、超长反馈、未知结果只查询、旧后端无入口、鼠标三尺寸确认及审批交错。新增 **1 项 Python、20 项 TUI**，不把修改已有断言另计为新测试。

测试夹具纠正单独记录：初版跨层测试未清空 fake 预填输入，把查询误变成新生成；空审批记录实际是 `{}`；审批事件需包含 call。以上均修正测试，不当作产品缺陷。PTY 初版在 32×12 主对话等待被裁掉的版本标题，改为进入实际审阅页核对 v2；`pty-first.log` 保留原失败。

### 运行证据与全量结果

- `background-final.log`：120 项相关 Python 通过，其中 CLI 真实 prepare→queue→worker 完成；首次正常沙箱运行被本地 Unix socket 权限阻止，正常本机权限下验证通过。
- `public-demo-green.log`：实际 App→Node→Python 允许/拒绝新版流程，原版字节不变、新版 proposed 且 approval 为空，未批准前工作文件不变；批准运行绑定 v2，拒绝后原文件保留。
- `cancel-resume.log`：实际 Ctrl+C 停止修订，反馈保留；显式续做复用原 Task、segment=2，最终提交同族 v2。普通成功只调用一次修订模型；取消后显式续做增加一次调用，查询不增加调用。
- `pty-verified.json`：80×24 与 120×40 鼠标确认修订、允许写入/命令并验证 `revised demo\n`；32×12 鼠标拒绝，无目标文件。三次均保留中文草稿、旧版不变，一个修订任务/一个执行任务、执行绑定 v2、exit=0。原生 TTY 光标显示序列存在，不等同于 Ghostty IME 候选框真人验收。
- 全量 Python **1878 passed / 4 skipped**（1882 项，81.53 秒，`python-full.xml/log`），只有既有 tarfile 弃用警告；TUI **529/529**（31.40 秒，`tui-final.log`）。`tui-full.log` 的首轮同为 529/529；其后仅修正反馈框的通用按钮文案并重跑 TUI，Python 源码未变。`verified-source.json` 固定最终源码，PTY 采于该文案修正前，布局/动作未再改。
- 未调用真实 provider、未提交代码。文档、链接和归属见 `card-manifest.json`、`card-increment.patch`。

### 尚未完成

未发送反馈目前只保存在界面进程内；已发送反馈由后端持久化，但重开后的准确修订发现/恢复界面仍待接线，已有文本 `/resume` 和后端重开查询不能替代它。接下来完成此旅程，再做版本冲突/未知回执的完整重开验证、受控真实模型修订和 Ghostty 真人体验。P3 核心演示已接受；新审批按钮真人体验、P2 真人、P3 原生恢复与 Owner Review 仍待反馈。P4 不标整体验收，P5–P10 未实施。

## 修订记录与重开恢复（2026-11-08）

证据目录 `.vibe/ux-p4-reopen-2026-11-08/`；从 590 文件基线继续，修改 14 份已有源码/测试，无新增产品模块。新增 **7 项 Python、9 项 TUI**；修改已有 capability 断言不另计。期间 `CONTEXT.md` 与实验台规划、业务流程及审阅产物共 8 份文件发生外部更新，单列保留；core、侧栏及已有审批按钮未改。

### 当前行为

- 新独立 capability `plan-revision-history`；所见卡片 `h` 打开修订记录，按同一会话/任务/计划族读取已发送反馈。每页最多 10 条，游标使用不可变操作标识；各条保留原版本/hash/完整反馈。
- 读取不创建任务、运行段、模型调用或锁文件。损坏、外族、超大或符号链接记录明确报错；新版出现不会把旧反馈重新绑定到新版。
- 查看记录后，暂停/中断由用户显式续做；已提交结果打开准确新版，不再提供续做。新反馈与已保存反馈分别保存，普通草稿保留；未发送反馈仍仅在当前进程内保存。
- 原内容在恢复后被改动时，准确续做返回 content_changed；无新增模型请求、运行段或 v2。反馈仍可查看。
- `VIBE_FAKE_DEMO=revision-resume` 提供重开时的固定修订回复，配合原会话 ID 使用。直接体验审批仍用 DEMO 首节的 `VIBE_TASK_RUNTIME=0 VIBE_FAKE_DEMO=1`，等待就绪后 Enter 提交。

### 评审与红绿证据

主代理检查持久记录读取、协议校验、请求/来源/反馈匹配、分页重试、草稿独立性及焦点切换；没有独立代理评审。

| 问题或行为 | 证据 |
|---|---|
| 重开无法发现未知反馈，缺少协议/capability 和界面入口 | history、stdio、protocol、capability、ui 的 red/green 日志；history-pages-final/历史损坏回归 |
| 已发布新版但任务暂停时仍给出续做动作 | published-resume-red/green：改为打开已发布结果；键盘验证实际动作，避免只断言被裁掉的标签 |
| 迟到的历史查询回执清空另一份反馈的确认选择 | late-history-red/green：只在历史视图清空选择 |
| 第二页查询失败后重试回到首页 | paging-ui.log → paging-ui-green.log：记录请求游标，32 列验证重试与返回 |
| 用户点击历史结果的“新版”时被另一张卡挡住 | explicit-result-red/green：显式操作打开准确版本，自动回执仍不抢占其他计划 |
| 重开 fake 脚本无法提交预期修订内容 | resume-demo-red/green：增加 revision-resume 固定回复入口，实际检查 v2 验收内容 |

夹具问题另记：初版历史测试给 propose_revision 的参数位置错误，并误以为 superseded 失败回执仍携带任务；初版内容冲突核验把 Task 元数据路径写成 task.json，实际为 meta.json。修正夹具后通过，不计为产品缺陷。

### 实际运行与最终验证

- `reopen-conflict-green.log`：实际 App→Node→Python Ctrl+C 取消修订，关闭并重开原会话。只读发现/查看保留原反馈，llm_start 不增加；显式续做增加一次调用，复用原 Task、segment=2，发布 v2。另一分支在查看后修改原内容，续做明确拒绝，模型仍为原来的 1 次、Task 仍只有 1 段、无 v2，反馈与普通草稿保留。
- `pty-verified.json` 与 `pty-final.log`：80×24、32×12、120×40 的真实 `run_ink.sh --session` 从已保存暂停夹具恢复，鼠标查看时模型调用为 0，点击续做后为 1；原 Task 两段、旧版字节不变、v2 含 revised demo、中文草稿保留，三次 exit=0。PTY 的取消前状态由公开 runtime 创建；实际 Ctrl+C 和跨进程取消恢复由上述 Node→Python 用例验证，不混称同一现场。工具 PTY 不等同于 Ghostty IME 真人验收。
- 最终冻结 14 份源码后，Python **1885 passed / 4 skipped**（1889 项，79.09 秒，`python-final.xml/log`），TUI **538/538**（31.88 秒，`tui-final.log`），均 exit=0。仅既有 tarfile 弃用警告。首次全量为相同 Python 数量及 TUI 536/536；显式新版修复、冲突用例和演示入口加入后重新跑完整两套，首轮记录保留。
- 最终源码哈希在 `verified-source.json`，首次冻结在 `verified-source-first.json`；PTY 在最终源码上执行。文件归属、文档链接、受保护哈希和完整增量见 `reopen-manifest.json`、`reopen-increment.patch`。未调用真实 provider、未提交代码。

### 剩余验收

重开反馈界面已实现；仍需补齐“发布后回执丢失”的实际进程重开及“同族新版竞争”的界面组合，不能把既有后端恢复测试和受控界面帧当作完整跨层样本。受控真实模型按反馈修订、Ghostty 真人修订/取消/重开和 Owner Review 待做。新审批按钮真人体验、P2 真人、P3 原生恢复状态保持，P4 不标整体验收，P5–P10 未实施。

## 实际进程故障恢复补验（2026-11-08）

证据目录 `.vibe/ux-p4-faults-2026-11-08/`，592 文件基线。新增 `tui/tests/revision-recovery-integration.test.tsx` 的两项实际 App→Node→Python 回归，生产代码未改。前轮列出的两项完整故障组合现有如下直接证据。

1. **成功回执丢失**：公开 `revision` 脚本生成 v1，再按反馈提交 v2。测试传输代理收到 submitted 帧时将其保留为故障证据、丢弃该帧并 kill 后端，原 App 从未收到 submitted。重新启动普通后端和 App，历史记录返回原反馈、原 v1 Ref、原修订 Task/segment=1 与准确 v2；用户打开新版成功，草稿保留。trace 共 2 次模型请求（初次规划+修订），重开查询不增加；仅原规划/修订两个 Task，无 v3、无执行任务、工作文件未写入，v1/v2 字节保持。
2. **同族新版竞争**：首次规划后开始反馈 A，实际 Ctrl+C 取消；关闭并重开 App，查看已恢复的 A。第二个真实后端在同一会话以反馈 B 提交 v2；第一界面仍持旧 A，点击续做返回 superseded，反馈和普通草稿保留，不提供继续启动。显式查询旧计划并打开新版后，历史同时保留 A 的原来源/冲突状态和 B 的准确产物。模型累计 3 次（规划、被取消的 A、B），拒绝与查询均无新增；三个 Task，A/B 各一段，旧版与新产物字节不变，无 v3。

`lost-receipt-first.log` 与 `newer-version-first.log` 分别记录首个完整样本通过。前者在成功帧进入传输代理时才切断，因此证明“产物和任务已保存、客户端成功回执丢失”的恢复，不冒充“原子保存中崩溃”；后者是在用户已查看旧反馈之后真实出现新版，不用受控界面帧替代后端竞争。更早的持久层部分保存/进程退出样本仍保留在前文。

本轮沿用已确认的 App 输入、JSONL、实际子进程和持久产物边界；没有 mock 内部控制器，没有产品专用故障开关。主代理评审检查传输代理的子进程回收、环境变量还原、临时目录清理，以及来源/反馈/Task/产物关联；没有独立代理评审。现有实现通过，无产品修复。初次 TypeScript 编译发现闭包内 session_path 窄化丢失，测试改为保存已确认的局部常量；这属于测试实现问题。

冻结新测试后，TUI 全量 **540/540**，31.85 秒、exit=0，见 `tui-full.log`。生产代码、既有 Python/TUI 测试与入口均保持本轮基线哈希，Python **1885 passed / 4 skipped** 与三尺寸 PTY 沿用前轮证据，本轮未重跑 Python 或 PTY。哈希、改动归属、文档链接与差异见 `faults-manifest.json` 和 `faults-increment.patch`。未调用真实 provider、未提交代码。

P4 的确定性恢复工程范围现已通过；受控真实模型按反馈修订、Ghostty 真人与 Owner Review 仍待完成，P4 不标整体验收。总路线图与设计首页的过期状态已校正。P5 的命令目录、中文查找、参数补齐和模式可见性是下一开发范围，P5–P10 当前仍未实施。
