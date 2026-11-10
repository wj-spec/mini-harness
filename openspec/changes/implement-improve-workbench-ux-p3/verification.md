# P3 实施证据与评审

日期：2026-11-08。状态：runtime 查询、共享写入保护与启动凭据/恢复已实现，既有 steering 已接线；新审阅 JSONL/TypeScript 协议、客户端与 Agent.run 交接已实现；Ink 准确审阅卡、焦点/草稿保护、正文分页与未知结果查询已接通，P3 整体未验收。
P2 实现、回归、性能复测、全量与工具 PTY 已完成，真人体验仍待反馈；以下先保留设计预检历史，再记录本轮实现，不增加产品验收完成项。

> 2026-11-10 增量：resumed 会话的自动计划发现改为静默查询；没有计划产物时不再把「找不到计划产物」显示到左下角，显式 `/plan` 仍提示原因。见 [P6 验证记录](../implement-improve-workbench-ux-p6/verification.md#侧栏生命周期与启动噪声2026-11-10)。

## 当前事实与探针

证据目录 `.vibe/ux-p3-preflight-2026-11-08/`：`probe.py`、`probe.log`、`results.json`。
使用真实临时 SessionStore/TaskStore、现有 JSONL serve、FakeProvider；未调用真实模型或产生工作区工具副作用。输入源码 SHA-256 保存在 results.json。

| 事实 | 实测结果 | 对 P3 的影响 |
|---|---|---|
| 旧 approve 按 mtime 重新选对象 | 已显示 A 后 B 更新，A 留在 proposed，B 变 approved | 卡片必须把准确 Ref 送到后端校验 |
| plan 参数没有身份/hash | 携带 plan_id/version/hash 得到 invalid_request | 需扩协议，不能承诺纯前端串接 |
| request id 是传输身份 | 同 Plan 两次不同 request id 得到两个不同 task_id，Provider 调用两次 | 需要持久化初始启动身份及结果查询 |
| TTL 失败被包装为文本 | 收到 command_result，text 含 review window expired | UI 不能把 command_result 到达当作批准成功 |
| 修订产生 draft 不使旧产物不可执行 | v2=draft，v1 的 can_execute 仍为 true | 新路径须检查同族最新版本，不自动回退旧 approved |
| 旧产物缺少 created_at | 同一文件连续 load 得到不同 content_hash | 缺省值须确定，不能把每次读取时间纳入 Ref |

这六项是**现状复现成功**，不是 P3 产品验收通过。进入实现后须转成期待目标行为的正式失败回归。

源码依据：

- [计划审阅](../../../app/runtime/plan_review.py)：review_intent / render_markdown / request_changes。
- [Plan](../../../app/runtime/plan.py)：content_hash、approve_plan、revise_plan、can_execute。
- [stdio](../../../app/stdio.py)：_plan_command、command、accept、run。
- [TaskRuntime](../../../app/runtime/task_runtime.py)：prepare_run、_create、_attach。
- [TaskStore](../../../app/runtime/tasks.py)：create、start_segment、acquire_lock、recover_stale。
- [Ink](../../../tui/src/app.tsx)、[客户端](../../../tui/src/backend.ts)、[协议](../../../tui/src/protocol.ts)：旧 Plan 覆盖层、latestExecutablePlanArtifact、随机请求 ID、参数校验。

## 设计自审与处置

应用 codebase-design 的 Module/Interface/Implementation/seam 原则，主代理结合现有调用方自审；未声称独立或跨模型评审。
负载范围：个人本机 macOS/Linux，现有单 Agent 执行路径；同一计划的重复连接/进程竞争须拒绝或去重，不扩为分布式执行系统。

| 发现 | 具体失败场景 | 设计处置 |
|---|---|---|
| 把“另一个计划更新”一概当 stale | 用户主动查看 A，但无关 B 更新导致 A 永远无法批准 | A/B 不同族不重定向；同族新版本才 superseded |
| 先随机建任务再补写操作记录 | 两次写之间崩溃，重试又创建第二个任务 | 先持久化预留身份，task meta 从首次写入就带创建凭据 |
| 只有 receipt 的 started 字段可去重 | 开段已落盘但 receipt 未落盘，重试重复执行 | 查询联合 TaskStore/segment/key；有段即只返回原任务 |
| 仅新 UI 使用锁 | 旧 approve/revise 或 CLI 同时写同一计划，校验后内容被改 | 同一计划的应用内写入口共享同步可嵌套锁，锁内重读/校验，禁止跨 await 持锁 |
| 普通 command busy 路由拦住查询 | 用户运行中丢回执，无法查询启动结果 | 只读 query/只返回既有绑定的重复请求不占 active_id；新启动仍检查 busy |
| 直接沿用不稳定的旧字段缺省 | 缺 created_at 的旧计划每次查询都是新 hash | load 使用确定缺省，新建仍写创建时间；补旧产物身份稳定回归 |
| 单帧传完整 Plan 不受控 | 大计划超过 JSONL 1 MiB，连接被关闭 | 有界读取、正文分页、摘要与正文分离，页携带同一 Ref |
| 自动重试被当成恢复 | 无法证明是否已调用模型/工具时再开一段 | 显示待核对，返回原任务；仅证明无任何运行段且原执行者退出时可显式继续准备 |

实现时仍须用任务矩阵验证这些设计，没有代码/故障注入证据前不能标已解决产品问题。

## 设计阶段的实施门槛与未完成项（历史）

- P2 Ghostty 真人固定/接入/zoom/比例反馈尚未收到；已有问题仍保留，不把工程结果代替真人验收。
- 本包的实现任务全部待做。P3 不继承 P2 的全量测试为自己的实现证据。
- 文件读取上限采用 4 MiB、正文单页 16000 字符并受 1 MiB 帧约束。当前 `.vibe` 排除本轮探针后未发现可用 Plan 产物样本；这属于工程限额，实施时用合成边界/大计划验证，不宣称已做真实负载测量。
- Windows 新动作的跨进程锁不在本轮支持范围；无 fcntl 时 capability/动作必须明确禁用，旧路径不得静默获得新的并发保证。
- 只完成设计准备：遵循路线图“先交付并验收一个阶段，再启动下一个”的顺序，P2 反馈纳入后进入 P3 产品实现。

设计结论：已有足够证据细化 P3，当前包可用于下一步测试设计；不能把“批准并执行”当成仅增加一个前端按钮。


## 设计阶段文档核验（历史）

8 份文档新增/更新；项目文档检查 **5 passed**，本包与进度索引 **112 处链接/锚点**通过，`git diff --check` 通过。
探针使用的 8 份源码 SHA-256 保持不变；P2 的 13 份产品/测试文件与上阶段最终快照一致。
文档增量及摘要分别保存为 `planning-increment.patch`、`planning-manifest.json`；并行的 Agent 实验台文档未纳入本包。
本轮没有产品改动，因此未重复全量产品测试；基线探针也不冒充新行为已通过。

## runtime 查询切片（上一轮）

依据用户持续开发授权，将原先顺序细化为：P2 工程已通过，真人反馈仍待验收；先实现与 Ghostty 原生体验无技术依赖的 P3 后端查询，接入 UI 前再核对反馈。没有更改 P2 通过状态，也没有改主 app/侧栏。

开工基线：`.vibe/ux-p3-2026-11-08/baseline/` 与 `baseline-manifest.json`，共 578 份文件，包含已有 Plan、P1/P2 和并行实验台文档。
本切片仅修改 2 份 runtime 源码、2 份已有测试及本包/进度文档；没有新增产品文件、协议字段或 capability，没有提交代码。

### 已实现边界

- `PlanReview.read_review(ref=None, cursor=0)`：发现最近计划后选该族最高版本；指定 Ref 时准确定位原任务/计划/version，拒绝会话或文件身份不一致。A/B 不同计划不会换对象。
- 同族新版与同版改内容分别返回 `superseded` / `content_changed`；保留请求 target，替代引用单独放 `replacement_target`。旧 approved 兼容执行路径尚未改，不能把本查询能力宣称为已阻止所有旧执行入口。
- 查询不写 Plan、不批准、不建任务、不开段。`ready` 表示可读；`launch=null`、`available_actions=[]`，尚无批准执行或恢复能力。
- 原计划路径逐层拒绝 symlink/非普通文件目录；损坏最新版本不静默退回旧版。缺失、损坏、非法 UTF-8/Unicode、身份错误均返回结构化结果。当前为读取前路径检查，未宣称可对抗外部进程在检查与打开之间替换目录；写入/启动阶段仍须实现设计中的共享锁与再次验证。
- Plan 读取最多 4 MiB，测试精确覆盖限额内及超出 1 字节。旧产物缺失/空 created_at 时保持确定身份，读取不回写，新建入口仍填写时间。
- 正文每页最多 16000 个 Unicode 字符，非首页必须携带 Ref；错误偏移拒绝。中文/emoji 长正文拼接完整，实际编码页小于 1 MiB。动态批准状态只出现在 summary，正文稳定；反馈完整展示。

### 红绿证据与自审

以下日志均在 `.vibe/ux-p3-2026-11-08/`，每组先保存目标行为失败，再实现并复跑：

| 日志前缀 | 失败场景与修复 |
|---|---|
| 01-legacy | 缺 created_at 的旧文件每次读取 hash 改变；批准后仍保持原内容身份 |
| 02-query | 新查询入口缺失；建立准确对象只读查询 |
| 03-stale | 新版本/改正文后仍返回 ready；改为明确失效及独立替代引用 |
| 04-identity | 会话、字段类型和文件内身份未验证；先校验再读取 |
| 05-bounds | 超 4 MiB 仍读入、非法 UTF-8 直接抛异常；限制读取并转 PlanError |
| 06-storage | 路径链接/缺失/损坏最新版仍读入或回退；返回明确失败并保留证据 |
| 07-discovery | 旧文件较新 mtime 压过 v2、最新损坏时偷偷回退；发现仍遵循同族最高版本 |
| 08-pages | 全文单次返回、偏移未验证；有界分页并固定 Ref |
| 09-review | 批准时插入状态文字导致分页漂移，反馈只剩末 10 条；动态状态与正文分开且反馈完整 |
| 10-canonical | 同 hash 的 JSON 对象换键顺序导致正文变化；步骤验证配置稳定排序 |
| 11-unicode | JSON 转义孤立代理字符通过解析后 hash 崩溃；查询返回 storage_error |

使用 TDD 与 ponytail-review，由主代理自审增量及 load/render 调用方，不宣称独立代理或跨模型评审。按个人本机会话负载判断；未增加通用存储/事务框架。
自审发现的分页漂移、反馈遗漏、字典顺序和 Unicode 异常均连接到先失败后通过的正式回归；这仍不是启动幂等/并发验收。

### 验证与待办

Plan 两份相关测试 **73 项通过**；首次全量 **1699 passed / 4 skipped**，最后补 Unicode 回归后的全量结果见下方收尾记录。
本轮没有 stdio/Ink 产品修改，未重复 TUI、工具 PTY 或 Ghostty 真人操作，也未运行无适配脚本的 fake eval。待接线后再做跨层验证。
R01/R02/R10/R14/R16 仅部分只读子行为有证据，不将这些端到端场景整行勾选；R03–R09/R11–R13/R15 的动作/运行语义未实现。
下一切片：持久化启动凭据、预留 Task 身份、共享短锁及故障恢复，再接 stdio 与 Ink；新 capability 等所承诺动作可用时发布。

### 本切片收尾核验

- 最终全量 `./run_tests.sh -q -o addopts='' --junitxml=...`：**1701 passed / 4 skipped / 1 warning，74.60 秒**；日志 `python-full-final.log`、机器报告 `python-full-final.xml`。唯一 warning 为既有 tarfile 弃用提示，未出现新增失败。
- 相比基线新增 **42 项用例**；Plan 两份测试合计 **73 passed**。文档检查 **5 passed**，8 份本包/索引文档 **114 处链接/锚点**通过；`git diff --check` 通过。
- 基线 578 文件中 12 份属本切片增量，566 份保持原 SHA-256。增量包含 2 份源码、2 份测试和 8 份文档；未修改 stdio/TaskStore/TaskRuntime/ApprovalGate、core、tui 或并行实验台文件。
- 增量归档 `query-increment.patch`、`query-manifest.json` 只以本轮工作树快照为基线，不把 HEAD 上原有 Plan/P1/P2 差异归入本次；P1/P2 既有证据快照不重算。
- 自审结论：本 runtime 只读切片可作为后续动作接线基础；不是 P3 阶段完成，也不承诺当前用户界面已具备“批准并执行”。后续锁/幂等/恢复需单独红绿与故障注入。

## Plan 共享写入保护（上一轮）

基线：`.vibe/ux-p3-storage-2026-11-08/baseline/`，578 份文件，包含上一轮通过的查询切片；不覆盖前两阶段或查询切片的历史快照。

### 实现与调用方核对

- `plan_lock` 使用 `<task_dir>/plan/review.lock` 的非阻塞 flock；同线程嵌套复用，其他线程/进程得到 lock_busy。锁文件保留，持锁进程结束由内核释放；不按超时抢锁，不跨 await 持锁。
- 保存、提案、批准、拒绝、修订、反馈、关闭与 stale 写入共享同一锁。锁内重读完整状态；过时对象拒绝覆盖，也不能覆盖已有新版本。`save_plan` 创建要求目标不存在，更新要求准确 expected 快照。
- 原子保存改为独占临时文件、内容 fsync、replace、POSIX 目录 fsync；不使用或删除已有固定名临时文件。保存也执行 4 MiB 限制，避免写出自身不能再读的产物。替换前 I/O 失败不修改原文件或调用者状态。
- Plan 审计追加拒绝 symlink/非普通文件/多链接 inode；内部审计同样处于短锁内。此处不承诺 Plan 与审计日志跨文件事务：Plan 写成而审计失败时仍可能部分成功，后续启动操作必须按磁盘 Plan/Task/receipt 核对。
- steering 改用 `mark_plan_stale`，磁盘冲突不再整份覆盖；本次运行的内存 Gate 仍保守阻止继续执行。先验证并受理正确的 active run，再标记 stale，错误目标不改计划。
- 核对 `app/` 和 `scripts/` 的所有实际 Plan 写入调用：工具 submit_plan、stdio 管理/反馈/steering、CLI 共用路径、真实验收脚本。脚本补写验收命令时携带修改前的深拷贝快照；未回退为无条件覆盖。

### 红绿与评审处置

主代理按 TDD 与 ponytail-review 检查增量和连接代码；未使用独立代理或宣称跨模型评审。日志均位于本轮证据目录。

| 证据 | 观察/处置 |
|---|---|
| 01-lock-red/green | 活进程持锁时仍被批准；改为明确 busy，释放后可批准 |
| 02-writers-red/green | 其余写入口绕开共享锁；统一覆盖，并检查嵌套异常不提前释放外层锁 |
| 03-stale-red/green | 7 种动作在内容/状态变化后仍覆盖或记录旧对象；改为锁内快照校验，后补 stale 动作同样覆盖 |
| 04-steer-red/green | 收紧 save 后旧 steering 静默写入失败；改专用动作，真实 JSONL/FakeProvider 验证正确目标标 stale、错误目标不标 |
| 05-atomic-red/green | 固定临时名跟随 symlink、超限文件仍能保存；独占临时文件和限额修复；replace 失败后可从原 proposed 状态重试 |
| 06-lock-lifecycle | 持锁进程被终止后可重新取得同一 inode 的锁；无 fcntl 时拒绝要求进程锁的操作，兼容旧创建；重复修订不能覆盖 v2 |
| 07-audit-red/green | dismiss 通过审计 symlink 写到无关文件；改为拒绝，保留文件与链接 |
| python-full / 08-acceptance-green | 首次全量 1 failed / 1734 passed / 4 skipped，发现真实验收脚本旧 save 调用；携带 expected 修复，脚本 9 项回归通过 |

原 `test_plan_approval_gate_and_audit` 用新的内存 Plan 覆盖同路径已批准 v1 来准备拒绝样本，违背本轮写入契约；将拒绝样本放入独立任务目录，保留“proposed 可拒绝”断言，未放宽冲突保护。
本机实测为 macOS；Linux flock 为目标兼容平台，未声称另跑 Linux。无 fcntl 用平台能力边界模拟，不冒充 Windows 原机验收。

### 本轮范围与下一步

Plan/查询/stdio-plan 三份相关测试 **111 passed**。最终全量和归属核验记录在本节后续收尾中；保留首次失败日志。
没有新审阅 capability、决策 API、预留 Task 身份、launch receipt 或 Agent.run 交接；共享锁通过不等于 R05 启动幂等通过，杀进程释放锁也不等于 R06–R09 恢复完成。
下一步必须落实预留 task_id、首份 task meta 的创建凭据及持久启动操作，再做批准/启动部分成功和崩溃窗口回归；随后接新协议与 Ink 卡片。P2 真人验收仍待反馈，P3 整体未验收。

### 写入保护收尾

- 最终全量 **1735 passed / 4 skipped / 1 warning，77.04 秒**；日志 `python-full-final.log`、报告 `python-full-final.xml`。唯一 warning 仍为既有 tarfile 弃用提示；首轮失败日志保留，未用它宣称通过。
- 相比本轮开工新增 **34 项用例**；三份相关测试 **111 passed**，验收脚本 **9 passed**。没有运行真实模型、TUI/PTY 或 Ghostty 人工操作，不将存储检查冒充界面验收。
- `TaskStore`、`TaskRuntime`、core、tui、ApprovalGate 及并行实验台等 **119 份受保护文件** SHA-256 不变；查询类、正文渲染、load/content_hash 的 AST 与开工一致。
- 本轮改动为 3 份产品源码、1 份验收脚本、3 份测试与 10 份文档；不新增产品文件、不提交。`storage-increment.patch` 与 `storage-manifest.json` 按本轮工作树快照记录归属。
- 自审结论：共享写入保护及兼容接线通过；预留任务、启动记录与故障恢复仍未实现，P3 保持实施中。

## 持久化启动与恢复（上一轮）

基线 `.vibe/ux-p3-launch-2026-11-08/baseline/` 保存 578 份文件；本轮基于既有查询/共享写入保护继续实现，没有改动 stdio 或 Ink，也没有启动模型调用。

### 实现边界

- `PlanReview.decide_review(ref, decision, request_id, busy=False)` 支持准确对象的 approve-execute、approve-only、dismiss；返回结构化 receipt，以及仅首次成功准备时存在的 RunTask。request_id 是本次传输/运行身份，Ref 的规范化 SHA-256 是持久操作身份。
- 先写预留 task_id，再批准，再用同一身份建立 act Task。首份 meta 即含 creation key/Ref；TaskStore 不删除失败的预留目录，不以随机新 ID 掩盖半成品。重复请求和重开查询均复核原 TaskRecord 的身份、Plan 绑定、creation 与首个 segment。
- `starting` 在 prepare_run 前写入。若已有匹配段，只返回原任务，绝不因换请求 ID 开第二段。若无段且能证明启动者已退出，或明确失败标记已落盘，且任务没有存活/异机/损坏锁，显式重试可继续同一预留任务。时间久不代表进程死亡，查询本身不恢复或启动。
- prepare_run 已成功返回 RunTask 而最终 receipt 写失败时，仍保留唯一交接；重试由原 starting 记录和已落盘 segment 查回原运行。prepare_run 自身抛错但已有段时只返回原绑定，不构造第二份交接对象。
- 批准已落盘但任务创建失败时返回 approved_not_started；继续不刷新批准时间。TTL、stale、busy、tasks disabled 与非法旧 goal_id 在相应批准/创建前拒绝。仅批准/关闭不建立任务或运行段。
- 旧任务已绑定同 Plan/version（或缺 version）却没有新启动凭据时，返回 needs_reconciliation；损坏、身份不匹配、链接与不完整任务保留现场，不另建。操作 JSON 最多 64 KiB；恢复锁最多 4 KiB，拒绝 FIFO 等非普通文件。
- Task 元数据升级为独占临时文件、fsync、replace 和 POSIX 目录 fsync。TaskRuntime attach 显式传入的 plan_id/version 必须与原记录完全一致，不能为无绑定的任务补认一个计划。

### 失败回归与自审

以下日志位于本轮证据目录。主代理应用 TDD/ponytail-review 核对模块及连接的 TaskRuntime/TaskStore；没有使用独立评审代理。10-concurrency 是已满足设计后的补充验证，其余编号均保留红绿日志。

| 日志前缀 | 失败或验证场景 |
|---|---|
| 01-launch / 02-replay | 首次准确绑定 act Task/segment；重开、换 request id 和忙碌重试返回原绑定 |
| 03-actions | TTL/stale/busy/disabled 拒绝；approve-only/dismiss 不开段 |
| 04-partial | 已批准但创建目录失败；继续复用预留身份且批准时间不变 |
| 05-crash | 子进程在 reserved、approved、task-created、starting、running、segment、started 七处退出；前五处可恢复准备，后两处仅查回原运行 |
| 06-meta | 固定临时路径链接会误写外部文件；改独占临时文件，fsync 失败保留原状态 |
| 07-handoff | 当前进程准备明确失败不能重试、最终回执写失败丢失交接；分别用明确失败记录与保留 RunTask 修复 |
| 08-identity | JSON bool 混同版本 1、类型损坏、task_id/creation 不匹配及旧执行任务仍可误通过；严格校验并拒绝另起 |
| 09-attach | 原 Task 无 Plan 或版本不同仍可 attach；显式绑定必须准确相等 |
| 10-concurrency | 两个实际进程争抢同 Ref，持锁者完成后另一请求仅查回；存活/异机 owner 即使时间久也不被回收 |
| 11-lock-file | FIFO 锁使恢复读取阻塞；改普通文件与有界读取，原 FIFO 保留 |
| 12-goal-binding | 旧 Plan 的非法 goal_id 在批准后才抛异常；改批准前结构化拒绝且不建记录 |

### 验证范围与下一步

四份相关测试 **185 passed**。首轮全量 **1778 passed / 4 skipped / 1 warning，76.37 秒**；随后自审补 goal_id 回归，最终结果记录在下方收尾。没有执行真实 Provider/工具，不能把 Task/segment 数量当作 Agent 或工具恰好执行一次的证明。

R03–R10、R16 的 runtime 子行为已有证据；R01/R02 的准确查询证据延续上一切片。R11–R15 的协议、焦点、卡片和 Gate 跨层旅程仍未完成，不将矩阵整行记为端到端通过。available_actions 仍为空，新 capability 尚未发布；当前 UI 仍走旧入口。

下一步接入 JSONL 决策/查询：已有结果查询不占 active_id，首次交接才调用 Agent.run；执行前再验 hash/可执行性，沿原 Gate 与 StepLedger 运行。之后实现 Ink 卡片与草稿/焦点保护，再做 FakeProvider、真实工具、PTY 和真人验收。P2 真人反馈与 Owner 学习笔记仍待补；P3 保持实施中。

### 启动存储切片收尾

- 最终 `./run_tests.sh -q -o addopts='' --junitxml=...`：**1779 passed / 4 skipped / 1 warning，76.30 秒**；本轮新增 **44 项用例**。日志 `python-full-final.log`、报告 `python-full-final.xml`。唯一 warning 是既有 tarfile 弃用提示。
- 四份相关测试 **185 passed**；文档检查 **5 passed**。未改 UI、未运行本轮 TUI/PTY/真人体验，也没有真实模型调用；不以历史 UI 测试冒充本轮闭环验收。
- 基线 578 文件中 **18 份本轮修改、560 份 SHA-256 不变**；本轮为 4 份 runtime、3 份测试及 11 份文档，没有新增产品文件。stdio、Agent/Gate、core、tui 与实验台等 **120 份指定保护文件**保持原 SHA-256。
- 归属记录 `launch-manifest.json` 与 `launch-increment.patch` 以本轮工作树快照为基线，不混入 HEAD 上既有 Plan/P1/P2/查询/写入保护差异；未提交。
- 自审结论：持久化准备/一次初始段绑定与恢复通过，P3 整体仍实施中。下一片验证 JSONL 与 Agent.run 交接，再接 Ink 卡片和跨层工具审批。

## JSONL、客户端协议与实际运行交接（本轮）

基线 `.vibe/ux-p3-stdio-2026-11-08/baseline/` 保存 578 份文件，包含此前全部 Plan/P1/P2 和 P3 存储增量。此次新增行为通过 JSONL 与真实 Node→Python 子进程验证；主界面和右侧栏没有修改。

### 已实现的协议与运行行为

- `command=plan` 新增 `review` / `approve-execute`；携带 target 的 `approve` / `dismiss` 走准确对象决策，旧无 target 命令保留。新路径回复 plan_review 帧，不用中文 command_result 判断成功。
- 有进程锁能力时公布 plan-review capability；查询、仅批准、关闭不依赖 TaskRuntime。任务禁用时不提供执行动作；无 fcntl 不公布新能力，直接发决策也明确拒绝。summary 的 executable 与 available_actions 供客户端展示，动作执行时仍由后端重查。
- 首次返回 RunTask 才调度 Agent.run，绑定准确原计划目录、版本与 hash。已有段的重放/重开只回复原 task/run，run_started=false；一个新的后端进程打开同会话后仍不自动续跑。
- 查询与既有结果重放在忙碌运行中可用；不占用 active_id、不重置取消目标。另一计划的新批准/启动返回 busy，原计划状态不变。
- 准备开始时、以及调用 Agent.run 的前一刻都复核准确 Plan。内容变化、stale 或同族新版本会停止已登记段并释放任务锁，Provider 未被调用。检查与模型/工具执行之间不持文件锁，不声称能对抗任意外部写者的跨文件事务。
- 沿用既有模式工具集、StepLedger、验收和 ApprovalGate。计划已批准后的写入仍出现工具审批；拒绝和输入连接关闭均不写目标文件。准备阶段 I/O 失败能结束原段、恢复 Gate/工具并接受下一条普通请求。
- 旧启动回执不会关闭后来开启的计划模式；模式文件保存失败会明确报告，已准备的 act run 仍按准确绑定交接，避免丢失任务所有者。
- TypeScript 增加 ReviewRef、严格结果联合和输入校验；未启动绑定不能含 run_started=true，有效新运行的 run_id 必须等于回执 id。旧后端无 capability 时不发送新动作；新运行开始后，客户端不再向晚订阅者重放启动时的上下文快照。

### 红绿证据与评审

主代理使用 TDD/ponytail-review 自审模块及 stdio、TaskRuntime、worker、Node 客户端的连接关系；未声称独立或跨模型评审。当前负载假设仍是个人本机单 Agent 会话。

| 证据 | 验证/修复 |
|---|---|
| 01-query-red/green | JSONL 准确查询与有界正文；不批准、不调用 Provider |
| 02-handoff-red/green | 新动作从未知请求变为一次运行交接；不同 request id 查回同一暂停任务 |
| 03-recheck-red/green | 回执发布后改内容/stale/新版本仍调用模型；启动前拒绝并结束原段 |
| 04-busy-gate | 同时存在 A/B；准确执行 A，忙碌 query/replay 不影响取消 A，B 未批准；工具拒绝保留原文件 |
| 05-mode-red/green | 旧回执误清新 plan mode；mode 文件写失败丢失已准备交接；分别修复 |
| 06-capabilities-red/green | 能力公布、非执行动作、disabled/unsupported 边界与 available_actions |
| 07-protocol-red/green | TS 输入/回执联合、目标格式、未启动与已启动、错误绑定拒绝 |
| 08-client-integration / 09-client-red/green | 真实 Node→Python 运行/重放；旧后端动作拒绝与启动上下文重放修复 |
| 10-cleanup / 10-reconnect | 审批时断开输入仍无写入；ledger I/O 失败结束段且连接可继续；新进程重开原会话查回原运行 |
| 11-final-check-red / 11-legacy-regression | 更晚的 metrics 发布期间改 Plan，复现仍调用模型；补模型调用前校验后，相关测试检出新参数与旧局部变量同名导致旧路径失败 |
| 12-legacy-sandbox / 12-legacy-approved | 修正独立参数后，普通环境剩余 worker socket PermissionError；核对 worker.log 后在完整权限环境复测，相关 **167 passed** |

前两次全量仍有失败（`python-full.log`：1 failed / 1791 passed；`python-full-intermediate.log`：29 failed / 1766 passed），均保留排错证据，不作为通过结果。其中首轮执行期间追加校验被 worker 子进程加载，因此该轮也不用于证明一个固定源码快照。修复后保存 `verified-source.json`，最终全量期间冻结这 8 份源码/测试，只更新文档。

### 尚未完成

本轮覆盖协议/运行层的 R01–R11、R15/R16 子行为；已有 storage 证据继续覆盖崩溃和并发，但不把它们直接当作整个 UI 旅程通过。R12–R14 的审阅卡回执归属、焦点、输入草稿与终端布局仍待实现；未做本轮手动 Ghostty 或真实模型验收。

下一步接 Ink 审阅卡：/plan 产物发现、摘要/全文分页、Ctrl+R 聚焦与选择后 Enter、工具审批优先、旧回执只更新对应卡片，以及未知结果查询。随后验证完整键盘旅程和真实工具闭环，再请求真人确认。P2 真人反馈仍待收到，P3 整体保持实施中。

### 协议与运行交接收尾

- 最终冻结源码全量 **1795 passed / 4 skipped / 1 warning，76.65 秒**：`python-full-verified.log/xml`；唯一 warning 为既有 tarfile 弃用提示。最终 TUI **473/473，30.09 秒**：`tui-full-verified.log`。两者退出 0；之前失败结果均保留且未冒充通过。
- 本轮新增 **16 项 Python、2 项 TUI 用例**；相关 Plan/stdio/worker **167 passed**，文档检查 **5 passed**。真实子进程重开/重放已验证，尚未进行审阅卡键盘、Ghostty 或真实模型验收。
- 全量开始前保存的 8 份源码/测试 SHA-256 与结束后一致；期间只更新文档。578 份基线中 **18 份本轮修改、560 份保持原 SHA-256**，范围为 4 份产品源码、4 份测试、10 份文档；无新产品文件。
- 主 Ink app、侧栏、Plan/Task 存储、Agent/Gate、core 与实验台等 **117 份指定保护文件**保持原 SHA-256；既有 Plan/P1/P2 和前几轮 P3 增量均保留。
- 增量与归属保存为 `stdio-increment.patch` / `stdio-manifest.json`，以本轮工作树快照而非 HEAD 为基线；没有提交。自审结论：协议/实际运行交接已通过，下一片为 Ink 审阅卡，P3 整体仍未验收。

## Ink 审阅卡与键盘闭环增量（2026-11-08）

证据目录 `.vibe/ux-p3-ink-2026-11-08/`。开工前保留 578 文件工作树快照；本轮增量只涉及 Ink 审阅视图/协议身份 helper、测试与进度文档。17 份既有文件修改、2 份新增；其余 561 份基线文件哈希不变，105 份 Python/core/侧栏/实验台受保护文件保持原内容。196 处本轮文档链接/锚点与 5 项文档测试通过。P2 真人反馈仍未收到，本次没有据此改动固定/接入、分栏比例或原生 IME。

### 已实现行为

- 支持 capability 时 `/plan` 查询准确卡片，`submit_plan` 完成与恢复会话只触发发现查询；查询不改当前运行和取消目标。
- transcript 摘要固定四行，显示目标/版本、步骤/验收/待决定数量与结果；约束和完整身份在正文查看。卡片到达不抢焦点、不清草稿。
- Ctrl+R 聚焦，↑↓/Tab 或 e/b/a/d/q 只选择，Enter 才发送；仅批准、关闭、批准并执行均携带原 Ref。工具审批优先，打断时清除隐藏选择。
- 正文前后页携带同一 Ref；页锚点包含对象与游标。Ctrl+Y 复制当前正文页，明确说明范围。历史回看与正文复制均保位。
- 决策发送中抑制重复确认；15 秒未收到回执、错误或断线标记结果待确认，先查原对象。已有启动结果显示绑定任务；不会转成新 run 或重试按钮。
- 新发现不会替换正在审阅的对象；失效页保留标注过时的原页，新版需 v → Enter 显式查询并重新审阅。
- 匹配 task_id/run_id 的状态事件更新原卡片；未知/重复回执不接管其他运行。旧后端能力缺省仍保留原面板。

### 验证与评审

最终冻结的 7 份源码/测试 SHA-256 见 `verified-source.json`；TUI **492/492**（30.05 秒），比本轮前增加 **19** 项用例。32×12、80×24、120×40 均覆盖目标版本、动作/确认/返回与长标题；更小窗口沿 P1 安全提示。全量 Python **1795 passed / 4 skipped**（原有 tarfile 警告 1 条）；文档链接与工作树归属检查收录在 `ink-manifest.json`。

| 证据 | 内容与结果 |
|---|---|
| `01-discovery-{red,green}.log` | 发现卡片、保留中文/a/d 草稿、运行中查询不改变取消目标 |
| `02-actions-{red,green}.log` | 选择→确认、重复 Enter 抑制、仅批准/关闭、旧启动回执隔离 |
| `03-body-focus-{red,green}.log` | 稳定分页/本页复制、工具审批打断、窄屏、提交后自动查询 |
| `04-recovery-{red,green}.log` | 请求错误待确认、新发现不换目标、过时页与显式新版 |
| `05-integration.log` | 30 项阶段相关用例，真实 Node→Python 初始运行/重放/重开查询 |
| `06-full-journey-verified.log` | Ink `/plan <需求>` → submit_plan → 准确卡 → 执行 → 工具审批拒绝；磁盘文件仍为 original，Plan approved、Task creation Ref 相同、仅一段 |
| `07-reading-connection-verified.log` | 历史行锚点/正文复制位置、断线保留草稿且不受理晚到启动回执 |
| `08-live-status-{red,green}.log` | 实际 PTY 发现并修复卡片仍显示 running 的状态遗漏 |
| `tui-full-verified.log` | 最终全量 492/492；`tui-full-before-status-fix.log` 是修复前的 491/491，不能代替最终版本 |
| `pty-before-status-fix.json` | 首次 80×24 实际入口：草稿、正文、仅批准、执行、原绑定查询；发现状态滞后 |
| `pty-verified.json` / `pty-disk-verified.json` | 最终入口重测：草稿保留、卡片随任务显示 paused、一个执行任务/一段运行、退出码 0 |

测试夹具早期两处断言错误已修正并保留日志：Task 元数据实际为 segments 数组，非 segment 单字段；新增卡片会改变滚动条长度，阅读保位应核对同一正文行而非滚动条符号位置。这些没有被记作产品缺陷。

本轮自审重点是异步对象归属与当前运行分离。修复了“审阅 A 时 B 的发现响应替换当前对象”和“任务终态不更新卡片”两处实际缺陷，均有失败回归。查询/决策不拼接旧 approve→run 命令；计划执行仍走既有 ApprovalGate。

### 仍待完成

P3 整体未验收。真实模型小计划、Ghostty 原生 IME/缩放/主侧栏共存、重连后的完整人工旅程与 Owner 学习 Review 待完成；P2 真人反馈继续单列。当前重开查询最近计划及其持久启动结果，并不重建整个会话所有历史卡片。正文复制范围为当前页。

默认 `--fake` 只产生占位回复，不会完成步骤；实际 PTY 中运行以 `plan_incomplete` 暂停，已如实显示，不能宣称计划执行成功。完整生成/拒绝链路采用测试内脚本化 FakeProvider；不是一次真实模型验收。此前浏览器 smoke 与通用 fake eval 限制仍见 P2 验证记录。

## 真实样本、恢复修复与公开演示（2026-11-08）

证据目录 `.vibe/ux-p3-acceptance-2026-11-08/`；开工前保存 580 文件工作树快照。P1 真人确认仍有效，P2 真人反馈尚未收到；本轮不修改侧栏与 core。

### 真实模型：部分通过，完整验收未通过

在独立 `/private/tmp/mini-harness-p3-real-6_d7rhk7` 工作区，通过实际 Ink 入口调用已配置的 `deepseek-flash`。仅有 README/AGENTS 样本；ask 档位，单 run 上限 24k tokens / 12 步 / 180 秒。没有读取个人画像或修改项目源文件。

- 会话 `20261108-194449-131809`；规划任务 `t-20261108-114506-0a5cf5` 提交 `pl-a41fbf75b03c` v1；卡片确认创建执行任务 `t-20261108-114637-4a45c0`，creation 精确绑定原 Plan/hash，初始运行仅一段。
- 模型把要求的单步拆成“读取”和“验证”两步，目标重复较长的基线事实；这是偏离要求的样本，不计作完全遵循指令。实际审阅后才确认执行。
- 规划 3 次模型请求 / 8,518 tokens；执行 5 次 / 22,520 tokens。读取步骤完成后因 `token_budget` 暂停；验证命令未执行，没有发生工具审批。README 哈希不变；输出中的命令文本不算实际执行。
- 退出重开仍是原 task/run、paused、一段，模型请求仍共 8 次。原始快照与核对见 `real-before-reopen*`、`real-pty.json`、`real-reopened-verified.json`。
- 没有追加真实模型重试或放宽预算。样本发生在本轮修复前；修复后的完整真实模型旅程仍待验收。

### 证据驱动的修复

| 问题 | 修复与回归 |
|---|---|
| `/resume` 重开丢失 Task，prepare 失败可能退回普通 run | run_start/run_end 保存 task_id/request_id；严格续接，失败停止。stdio 覆盖新进程、终态/缺失任务、禁用 runtime、旧记录；普通新 run 不误续旧计划 |
| in_progress 步骤不能再进入 | 显式 start_plan_step 可聚焦原步骤，不自动重放工具；`step-resume-red.log` → `recovery-green.log`，原任务增加一个显式段、实际读取一次 |
| 被拒绝的工具证据也能完成步骤 | 至少需要一条成功证据；`denied-evidence-red.log` → `denied-evidence-green.log`；拒绝后不进入可信验收 |
| 台账损坏被当成成功或重新初始化 | 验收读取失败停止；新审阅卡任务恢复时缺失/损坏台账不调用 Provider、保留现场。`ledger-read-red.log`、`ledger-reopen-red.log` → 最终 stdio 回归 |
| 暂停计划的回执仍显示完成通过 | harness 验收后生成最终回执，更新匹配 run 的 round；成功列实际命令 exit=0，拒绝列未运行。`receipt-red.log` → `receipt-green.log` |
| 长目标遮挡绑定状态、切换后留下旧确认提示 | 动作页目标一行、全文入口保留，切换清除旧选择；`ui-red.log` → `ui-green.log` |
| 续做的新 run 不更新原卡片 | 同 task_id 的新 segment 更新状态，忽略更早段，不接管当前输入任务；`resume-card-red.log` → `ui-green.log` |

主代理使用既有 TDD/ponytail-review 流程自审 Agent、TaskRuntime、StepLedger、SessionStore 与 Ink 接线；未声称独立评审。负载假设为个人本机单 Agent。新审阅卡创建的任务续做必须再次通过准确 Ref 检查；旧执行路径兼容边界另列，不声称所有旧记录都已迁移。

### 最终验证

- 新公开入口：`VIBE_TASK_RUNTIME=1 VIBE_FAKE_DEMO=plan ./run_ink.sh --fake --autonomy ask`。只预填输入，Enter 才开始；写入与可信验证均经原 ApprovalGate。默认 fake 和 `VIBE_FAKE_DEMO=1` 保留。
- 冻结 9 份源码/测试后，全量 TUI **497/497**（30.22 秒），Python **1807 passed / 4 skipped**（80.24 秒）；仅既有 tarfile 警告。结束后 SHA-256 相同。见 `python-full.xml/log`、`tui-full.log`；较早的 1803/497 全量不替代最终版本。
- 新增 **12 项 Python、5 项 TUI**；真实 Node→Python 的公开演示同时覆盖允许与拒绝，检查实际文件、verified/status 与审批次数。
- 本轮 20 份既有文件修改（5 源码、4 测试、11 文档），其余 560 份基线文件保持 SHA-256；32 份 core/侧栏/实验台保护文件不变。194 处文档链接/锚点、5 项文档测试与 `git diff --check` 通过；没有提交。
- 最终 80×24 PTY：`20261108-200414-624051` 两次批准，completed、s1 done、验收 exit=0；`20261108-200457-343552` 拒绝写入，paused/plan_incomplete、原文件未变、s1 in_progress、验收未运行。草稿保留，查询与重开仍指向原任务/一段，进程均退出 0。见 `demo-approved-pty.json`、`demo-denied-pty.json`、`demo-reopened-pty.json`、`demo-disk-verified.json`。
- 早期 `demo-pty.json` 保留旧回执矛盾，只作失败证据；最终以修复后记录为准。归属、文档链接和快照见 `acceptance-manifest.json`。

### 真人验收与边界

步骤见 [DEMO](../../../docs/DEMO.md) 和 [USAGE](../../../docs/USAGE.md)。已请求 P3 正文/动作切换、中文草稿、批准与拒绝反馈，尚未收到确认。工具 PTY 不代替 Ghostty 原生 IME/缩放/主侧栏共存、重连人工体验；Owner 学习 Review 待做。

旧会话有 Task 却缺少续做绑定时拒绝猜测；技术接入可用 JSONL run.task.task_id 明确指定，Ink 暂无旧记录迁移入口。新版本运行后重开可直接 `/resume`；台账丢失需核对，不自动重建。正文只复制当前页，重开只发现最近计划。P3 保持实施中，P4–P10 未实施。

## 真实模型闭环与风险提示修复（2026-11-08）

证据目录 `.vibe/ux-p3-real-closeout-2026-11-08/`；开始时保存 580 文件基线。P1 真人反馈保持通过，P2/P3 Ghostty 反馈尚未收到。

### 完整样本与证据范围

- 使用实际 Ink 入口与 `deepseek-flash`，独立工作区 `/private/tmp/mini-harness-p3-write-t6z439ql`。受控任务仅将 `message.txt` 从 `before\n` 改为 `after\n`；提前指定单个编辑步骤、验收标准和准确 Python 验收命令。它验证交互与执行接线，不代表开放任务规划能力。
- 本样本单 run 上限 **64k tokens / 16 步 / 180 秒**，ask 档位。与上轮 24k/12 步只读样本协议不同，不作性能或能力 A/B 比较；没有在失败后自动重复请求。
- 会话 `20261108-201101-977543`，规划任务 `t-20261108-121123-c0b659`，Plan `pl-ecafaeedbe09` v1，hash `6e94f10eba34c0b9abdcf2a05023b96672bb94dfa35fc064ae5f51f5f342e0c3`。实际审阅正文与动作后启动 `t-20261108-121235-682ff2`，creation 精确绑定该 Ref，最终 completed、仅一段、s1 done。
- `write_file` 和 harness 的 `bash` 验收各经一次 `once` 审批；不是 workspace 授权。`acceptance.v1.json` 记录 v1 一次执行、exit_code=0、未超时；最终 round 为 verified=true，回执列出实际命令和 exit=0。文件 SHA-256 为 `7b9a72466d3960eb2aacccfc848939453490db0678bd4725def3f789b891c919`，AGENTS 内容未变，未出现其他工作区文件。
- 规划 **3 次请求 / 8,087 tokens**，执行 **6 次 / 25,423 tokens**，共 **33,510 tokens**。非 Git 工作区的通用审计 coverage=limited；原 completion audit 保留 harness 验收前的 no_verification，最终计划验收单独保存，不把前一时点记录改写成通过。
- 工具驱动 100×24 PTY 保留中文草稿，最终显示“已验证”，进程退出 0。审批按聚焦、选择、Enter 分步操作；初次合并按键数据被当成草稿输入，没有导致批准。此证据不替代真人键盘/IME 体验。
- 使用真实 provider 配置重开时，自动审批审查以潜在上下文外发为由拒绝启动；改用明确的 `--fake` 离线恢复。重开显示原任务 completed、已有运行未重复启动；trace 仅新增启动上下文快照，模型请求仍为 9，工具调用仍为 9，审批/最终 round/TaskRecord 字节不变。未验证同 provider 的重开，也没有执行 `/resume` 已完成任务。
- 原始记录：`real-pty.json`、`real-before-reopen/`、`real-after-offline-reopen/`；独立核对脚本与结果：`verify_real.py`、`real-disk-verified.json`。离线 PTY 存档只包含退出帧；启动恢复画面见本轮工具记录。

### 样本发现与修复

真实模型遵守 `submit_plan` schema，但 schema 没有 `risk` 字段且禁止额外属性。模型把 `risk: write` 写入标题，结构化 edit 步骤仍缺省成 read，正文风险提示失真。实际文件写入仍经过 ApprovalGate，没有权限绕过。

先加公开 ToolRegistry→持久计划→渲染的失败回归，再补 schema 的可选 risk 与新提案规范化：read→read、edit→write、run/verify→exec；低于 kind 的 read/write 提示提升，显式 exec/network 保留。`submit_plan` 工具自身仍是 read，不把审阅信息用于工具授权。旧 Plan 加载默认值不变，已批准哈希保持稳定；旧样本原始 risk=read 也保留作为问题证据。

真实模型运行发生在上述小补丁之前；没有把它冒充补丁后的真实模型复跑。补丁由 **10 项新增 Python 回归**验证（7 种新提案风险、1 项 provider schema 契约、2 项旧 edit 产物身份），185 项相关回归通过。证据 `risk-red.log` → `risk-green.log`。

### 最终核验与后续

- 冻结源码后 Python **1817 passed / 4 skipped**（76.93 秒）、TUI **497/497**（30.25 秒），均 exit=0；仅既有 tarfile 警告。见 `python-full.xml/log`、`tui-full.log` 与 `verified-source.json`。
- 主代理按既有 TDD/ponytail-review 流程检查新增提案、历史哈希、准确 Ref 和工具 Gate 的职责，没有声称独立评审。未修改 core、侧栏或 Agent 实验台规划，未提交；增量差异、冻结哈希与文档链接结果见 `closeout-manifest.json`。
- P3 真实模型小样本项已完成；Ghostty 真人与 Owner 学习 Review 未完成，P3 整体仍实施中。P2 真人状态不变。下一步可据稳定的审阅身份契约细化 P4 反馈修订，P4–P10 尚无产品实现。

## 真人核心演示反馈（2026-11-08）

用户对公开 `VIBE_TASK_RUNTIME=1 VIBE_FAKE_DEMO=plan ./run_ink.sh --fake --autonomy ask` 演示最终确认“没有问题”，同时指出“当前审批是使用快捷键，这体验很差”。以这条后续澄清替代先前“遇到问题，我补充现象”的待核对状态。

确认范围为生成计划、中文草稿、正文/动作切换、批准执行、拒绝及“已验证/计划暂停”反馈。未收到真人重开/恢复与 Owner 学习 Review，不能外推为整个 P3 已验收。根据体验意见优先补工具审批按钮，实施和新验收边界见 [P1 按钮增量](../implement-improve-workbench-ux-p1/verification.md#审批按钮增量2026-11-08)；Plan 卡片动作导航保持当前实现。
