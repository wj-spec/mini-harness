# P3 设计：计划身份、启动凭据与失败恢复

状态：实施中。runtime 准确查询/失效拒绝/全文分页、共享写锁/快照冲突/原子保存与 steering 接线已实现；预留任务身份、启动凭据/恢复、审阅协议及 Ink 卡片已实现。受控真实模型写入/验收和原会话离线重开通过，Ghostty 真人与学习 Review 待完成。当前事实、探针及增量验证见 [verification](verification.md)。

2026-11-08 验收补充：真实样本在读取后因预算暂停，完整旅程未通过。新运行的 trace 起止记录包含 task_id/request_id，`/resume` 依据该身份续接；缺失/损坏/终态/禁用运行时不退回普通 run。creation 所带准确 Ref 在续做及模型调用前复核；显式 start_plan_step 可继续 in_progress 步骤，工具不自动重放。新审阅卡任务恢复时台账缺失或损坏不重建。只有失败证据不能完成步骤；最终计划回执在 harness 验收后生成，并同步相同 run 的 round record。旧记录无绑定时需显式技术接入，暂无 Ink 迁移入口。公开 fake 计划演示及真人待验收项见验证记录。

## 1. 先分清三种身份

| 身份 | 用途 | 不承担的职责 |
|---|---|---|
| JSONL request id | 关联某一次请求/响应/流 | 不能证明跨连接重复请求是同一个业务意图 |
| ReviewRef | 标识用户实际审阅的计划内容 | 不靠 mtime 猜当前计划，也不自动指向新版 |
| launch key 与绑定任务 | 标识该内容版本的初始启动操作与结果 | 不能保证任意工具副作用恰好一次；中断后的续做仍需显式操作 |

ReviewRef 固定字段：`session_id`、`plan_task_id`、`plan_id`、`plan_version`、`plan_hash`。
`plan_hash` 复用 `content_hash(plan)`，不能用包含 approval/status 的 `Plan.plan_hash()`；批准不应把用户已审阅的内容身份改掉。
旧产物缺少 `created_at` 时，load 使用空字符串作为确定的缺省值，不能每次读入当前时间改变内容 hash；新建 Plan 仍由创建入口写时间，查询不落盘。已回归旧产物读/批/再读身份稳定。
数字版本必须是正整数且不是 bool，hash 为 64 位小写十六进制；ID 有长度/格式校验，拒绝未知字段。前端不传路径。
后端从当前 SessionStore/TaskStore 内定位原计划任务，校验文件内 task_id/plan_id/version 一致，拒绝 symlink、越界或损坏状态。

- A 与 B 是不同计划：显示 A 后新建 B，不得改批 B；A 自身仍有效时，准确批准 A。
- 同一计划已有 v2：v1 卡片不能走新执行路径，返回 superseded 和新版只读引用；不自动批准 v2，也不退回 v1。
- 同版本内容 hash 变化：返回 content_changed；重新审阅，不默默刷新动作目标。

## 2. 用户旅程与焦点

计划生成完成时，Ink 通过只读查询取得后端返回的准确 ReviewRef 摘要；卡片进入对话流，不抢输入焦点或清空草稿。恢复会话只加载卡片和操作结果，不自动执行。
卡片以目标和 vN 为主；摘要给出步骤/验收数量、待决定项与主要约束，完整身份放在详情中。主对话中的摘要卡采用固定行数，状态更新不挤动前面的阅读内容；长标题与正文在详情展开。

待审阅卡片存在时，页脚提供 `Ctrl+R 审阅计划`；工具审批优先沿 P1 的 Ctrl+R 路由。进入卡片动作区后：

- `↑↓/Tab` 选择；快捷字母只选择，Enter 确认；初始不预选执行。
- 主动作：批准并执行；次动作：查看正文、仅批准、关闭审阅。
- Esc 返回草稿，保留卡片和选择对象；关闭审阅不拒绝计划、不改变授权。
- 新工具审批到达时清除计划动作焦点；返回后重新选择，不沿用隐藏动作。
- 发送中禁用重复确认；超时显示“结果待确认”，提供查询；不能把按钮直接改成重新发送新的 run。
- 已批准未启动时显示“继续执行此计划”；已经启动时显示绑定任务与其状态，不再显示“重试启动”。

`/plan` 打开新卡片的准确对象；历史卡片保留自己的 Ref。已有 `/plan approve|reject|execute|request-changes|dismiss` 保留兼容，不重命名为另一套命令菜单。
当前 `request-changes` 只保存 draft，P4 再闭合自动修订，不在 P3 卡片伪装成已支持自动修订。

窄屏遵循 P1：32×12 仍显示目标版本、当前动作/确认/返回；容纳不下进入小屏安全状态。80×24 和 120×40 覆盖长标题、长正文、草稿与审批叠加。
完整正文查询支持分页，同一 Ref 的页不能混入新版本；查看/复制沿用 P2 阅读锚点。

## 3. Module 落位与协议

外部 seam 放在“审阅对象 + 用户意图”处。计划审阅 Module 的 Interface 只有查询和决策两种能力：

- `read_review(ref=None, cursor=0)`：ref 缺省时只负责发现最近可审阅对象；返回后所有动作都携带 Ref。只读，不批准/建任务/开段/续跑。
- `decide_review(ref, decision, request_id)`：decision 为 `approve-execute | approve-only | dismiss`；返回结构化回执，以及仅供 stdio 交接的一次性 RunTask（如本调用新启动）。

`app/runtime/plan_review.py` 实现此 Module；现有 `render_markdown`、`review_intent` 和反馈记录继续复用。
TaskRuntime/TaskStore 负责原有任务生命周期，新增的预留任务身份/创建凭据属于内部接线；不把 task_id 的创建权限直接开放给协议客户端。
不创建可注入但没有实际变体的 generic executor/store Interface。测试从同一审阅 Interface 和 JSONL/键盘 seam 进入，使用真实临时存储与 FakeProvider。

### 3.1 JSONL v1 的增量扩展

新增 capability `plan-review`。保留 `type=command, command=plan`，扩展 action：

| action | 入参 | 语义 |
|---|---|---|
| `review` | target 可选，cursor 可选 | 只读发现、重开、分页或查结果 |
| `approve-execute` | target 必填 | 对准确对象批准并启动，或返回/恢复它的既有启动操作 |
| `approve` | target 可选 | 有 target 时为准确对象仅批准；缺省继续兼容旧命令 |
| `dismiss` | target 可选 | 有 target 时关闭准确对象；不改变计划状态 |

新路径返回 `type=plan_review` 的结构化事件：`id`、`target`、`outcome`、`code`、`message`、`launch`、`summary`、`body_page`、`next_cursor`、`available_actions`。
类型必须是严格可判别联合；例如只有启动/已有任务结果含有效 binding，拒绝结果不能携带 `run_started=true`。

- `run_started=true` 只表示**本调用**首次交接成功；后续 `task_state/status/流式/done` 使用该请求 id，取消也指向它。
- 重复请求返回原绑定的 run_id/task_id，但 `run_started=false`；UI 不能把重复请求 id 当成运行中的 run id。
- query 与未启动的决策结果由 `plan_review` 事件完成，不再伪造一段 assistant 成功文本；旧命令仍用 command_result。
- 新事件应关联 pending decision 后再改变焦点/运行状态；重放的历史回执只更新对应卡片，不能误启动当前输入。
- query、以及只返回已有操作结果的重复请求可在 run 活跃时处理，不占用/清空 active_id。需要新批准/启动时仍执行 busy 检查。
- 客户端无 capability 时继续旧查看/管理入口，不尝试从中文 command_result 中猜“批准成功”再发 run。

正文每页最多 16000 个 Unicode 字符，编码后按 JSONL 1 MiB 上限再次约束；cursor 是该 Ref 渲染正文的确定性偏移，越界拒绝。摘要不携带整份 Plan。
计划 JSON 读取上限固定为 4 MiB；超出上限或损坏产物返回可解释错误，不把截断正文标为完整。实施时补精确边界回归，不能直接把任意大 JSON 放入单帧。

runtime 查询切片约定：`ready` 仅代表内容可读，不代表已获执行授权；`unavailable` 带明确 code。失效响应保留原 `target`，新版只放在独立的 `replacement_target`，需重新查看才能采用。分页正文排除 status/approval 等可变元数据，动态状态放 summary；步骤验证字典稳定排序，反馈完整保留。同一 content hash 的分页不因批准或 JSON 对象键顺序变化而漂移。非首页必须携带 Ref，不能重新发现最新计划。runtime 查询起步切片使用 `available_actions=[]`、`launch=null`。当前启动存储切片已能返回有证据的 launch；当前 JSONL 已按状态、忙碌与能力提供 available_actions；summary 增加 executable。只有首次交接的 launched 帧 run_started=true 且 run_id 等于本帧 id；approved_not_started 的 run_id=null、segment=0、run_started=false。TypeScript 用可判别联合解析，旧后端没有 capability 时拒绝发送新动作。

## 4. 持久化启动操作

默认一份 ReviewRef 只有一次**初始启动**：launch key 为规范化 Ref 加操作种类 `approve-execute` 的 SHA-256。
因此关闭重开、客户端重启或换 request id 都会找到同一记录，无需前端持久化另一份批准权威。
P3 不提供“把重试当作再次执行”；再次执行必须是另一种显式意图，现有任务续做也不归为本操作的首次启动。

记录放在原计划任务的 `plan/review-launch/<key>.json`，包括：schema、Ref、launch key、预留 execution_task_id、stage、启动 request/run id、最近明确失败原因与时间。
动作路径下拒绝 symlink；原子写必须使用独占临时文件、replace 与必要的 fsync，不能共享固定 `.tmp` 名称。
这是计划领域的恢复记录，不复制 task 状态、模型输出和工具日志；查询时以 TaskStore/segment/锁为权威复核。

### 4.1 单次操作顺序

1. 获得原计划的短临界区锁，校验会话/对象/hash/同族最新版本/状态；拟启动时确认 tasks 已启用且连接不 busy；仅批准/关闭不依赖任务运行时。只查询或已有启动的重复请求不产生批准副作用。
2. 记录 Ref 与预留的 execution_task_id，再执行准确批准。`approval_id` 写 launch key，使“批准已写入、操作记录尚未更新”的崩溃窗口可核对。
3. 若已经批准，只复核 can_execute 和身份，不重写 approved_at，不刷新 TTL。TTL 沿既有提案审阅窗口规则，在新批准时检查；不偷偷增加新的批准后有效期策略。
4. 使用预留 ID 创建绑定任务，元数据从首次写入就含 launch key、原计划任务/版本/hash。相同 key 的既有任务可复用；不匹配、半成品或损坏目录均拒绝覆盖，不换新随机 ID。
5. 在调用 TaskRuntime.prepare_run 前记录 `starting` 与 run id；prepare_run 必须 attach 上一步的任务。它会先持久化 running/segment，再交给既有 Agent.run。
6. 交接成功记录绑定并发布 plan_review/task_state，之后沿现有运行、停止、Gate 与 StepLedger 路径；锁不跨模型调用持有。

TaskStore 已最小扩展“已预留 ID + 创建凭据”能力；正常 create 调用不变。
不能先 create 随机任务、再把 task_id 补写到操作记录，否则进程在两次写之间退出后无法证明未创建任务。

### 4.2 锁与并发的具体约束

P3 本机目标为当前 macOS/Linux。采用 stdlib `fcntl.flock` 的非阻塞排他锁，锁文件为原计划任务内固定 `review.lock`，不 unlink；进程退出由内核释放，避免凭时间或 PID 文件误抢活锁。
平台无此能力时，新的启动动作返回不支持，不静默退化为仅内存互斥；旧只读/管理入口仍可用。实现需增加两进程竞争同 Ref 的回归。
这是该 Module 的内部 Implementation，不推广成仓库通用锁抽象。

同一计划的 save/propose/approve/reject/revise/request-changes/dismiss 应用内写入入口需要共享临界区，覆盖 stdio、CLI 和工具调用方。锁上下文仅支持同步嵌套：进程内同线程重入复用句柄与深度，最外层才取得/释放 flock；不得跨 await 或模型调用持锁。各修改入口在锁内重读并校验原对象，不用锁外旧对象覆盖新内容；初始创建与版本新增另验目标不存在。实现需逐一核对调用方和并发回归，不只在 UI 外包一层锁。
Agent 开始执行前再次检查绑定 hash/可执行性；外部直接改 `.vibe` 文件不受应用锁约束，检测到变化应 fail closed，不声称可对任意外部写者做跨文件事务。

已实现的写入规则：`plan_lock` 位于 `plan.py`，锁文件为 `<task_dir>/plan/review.lock`，不删除；`save_plan` 首次创建要求版本不存在，更新要求提供准确 `expected` 快照。上层动作在锁内重读，成功保存后才更新调用者内存。stale 由专用 `mark_plan_stale` 动作处理；steer 先验证并受理运行目标，再变更计划。文件写入采用独占临时文件、fsync、replace 与 POSIX 目录 fsync；审计追加拒绝符号链接与多链接文件。进程死亡释放锁、线程互斥、嵌套异常退出、写入失败/版本冲突已有回归；这不等于启动去重或故障恢复已实现。

## 5. 恢复判定表

| 持久证据 | 展示 | 同一“批准并执行”再到达 |
|---|---|---|
| 无批准、无启动记录 | 待审阅 | 正常校验后执行一次 |
| 计划带本 key 批准，但后续创建失败 | 已批准，尚未启动 + 原因 | 用户显式继续，同一 key/预留 task_id；不重复批准 |
| 已创建任务，状态 created、无段、无启动凭据 | 已批准，任务已准备 | 用户显式继续，attach 原任务 |
| starting，创建/段证据不足，原执行者仍存活 | 启动中或待核对 | 只查回；不抢锁、不新建 |
| starting，已证明原执行者退出，任务无任何运行段 | 尚未运行，可继续准备 | 先按既有恢复规则核对任务状态，再显式继续；不能从观察超时推断进程已死 |
| starting，prepare_failed 明确落盘、任务无段且没有存活锁 | 已批准尚未启动 | 显式继续同一任务；失败标记写不成时保持待核对 |
| 有匹配 run id 的 segment，且与操作记录一致 | 返回原 task/run；运行中/中断/已结束 | 只返回，不开第二段；中断续做走已有显式任务恢复 |
| 任务/操作文件损坏、对象不匹配、无法判定 | 结果待核对 + 原因 | 不重建/覆盖，不自动运行 |

“已批准但没有执行”可恢复；“可能已经执行”保留未知。重放回执不代表当前连接取得原运行控制权；另一连接的任务按现有 attach/查询规则处理。
首次执行的 task/segment 可能已建立但尚未产生模型输出；界面说“已启动”指 harness 已建立运行，不能说工具已执行或已完成。

## 6. 错误与兼容

启动存储切片补充：Task 元数据采用独占临时文件、fsync/replace 与目录 fsync。prepare_run 明确失败且磁盘无段时保存 prepare_failed；最终 receipt 写入失败但已收到 RunTask 时保留唯一交接，有段但没有成功交接对象时只查询原绑定。锁恢复拒绝存活/异机/损坏 owner，不按时间抢占；原 TaskRecord 的 task_id、session、plan/version、parent、mode 和首份 creation 均须匹配。旧绑定执行任务没有新 key 时进入 needs_reconciliation。

结构化 code 至少覆盖：`not_found`、`target_mismatch`、`content_changed`、`superseded`、`review_expired`、`plan_stale`、`busy`、`tasks_disabled`、`lock_busy`、`unsupported_platform`、`storage_error`、`needs_reconciliation`。
拒绝不能用 `command_result.text` 冒充成功。approved_not_started/已有任务是有证据的部分结果，不与完全失败混为一谈。
同一 Session 的历史产物无启动记录时可查询审阅；如已有旧流程绑定任务却无 hash/key，显示已有执行需核对，不静默再建新任务。
管理命令继续可用，但新卡片不借兼容路径逃过目标、同族最新版本或重复请求检查。

## 7. 实施取舍与验证

否决前端盲串 approve/run：调用者必须处理 mtime、错误文本和丢响应，复杂度散在 UI，无法满足恢复要求。
否决通用审批/事务平台：当前只有此处需要该启动语义，会扩大 Interface 和迁移面。
选择计划专用 Module：删除它会让绑定、恢复与去重重新散回 stdio/UI，具有实际 Depth；可复用的任务生命周期继续留在 TaskRuntime/TaskStore。

实施顺序：准确查询/拒绝 → 持久化与故障注入 → stdio 启动交接 → 卡片焦点 → 跨层验证。每次先补对应失败回归。
所有明确验收场景、协议错误组合和崩溃窗口列在 [tasks](tasks.md)。最终以真实持久证据、FakeProvider 调用次数和实际 workspace 副作用核对，不能只看绿色按钮或某一条事件。


## 8. JSONL 接线落实（2026-11-08）

stdio 的准确审阅分支在普通 busy 命令路由前受理，但新决策将当前 busy 传入 PlanReview；已绑定重放不会改 active_id。首次交接才进入 run_review，核对原 Plan/Task 后沿既有 run 执行；模型调用前再次检查，不能只在任务登记前检查。准确原计划以独立 reviewed_plan 参数传入，避免旧 find_plan 路径按 plan_id/version 重新发现对象。

run_started=true 表示本请求已登记运行并交接，后续仍可能出现准备错误/停止；done 和 Task 状态才表示本段终态。模式状态文件与计划批准不是一份事务：模式写失败单独报告，不抹去已批准/已创建的事实，也不遗失已经返回的 RunTask。重放只读结果不清理后来开启的计划模式。

TypeScript 已解析 plan_review，但审阅卡和 pending decision 的视图归属尚未实现；因此 R12/R13 不能靠协议类型检查提前验收。当前客户端不缓存新回执供历史重放，重开应主动查询原 Ref；UI 必须按请求归属决定是否进入运行状态。


## 9. Ink 接线落实（2026-11-08）

`use-plan-review.ts` 集中持有准确卡片、请求 id 归属、决策发送、15 秒回执等待和正文分页；
`view-state` 只投影固定四行摘要到 transcript，`App` 协调工具审批优先、输入焦点和唯一运行交接。
查询不走 begin_command，不改 active_id；只有待决策请求的准确 launched 回执可接管当前空闲运行。
未知/重复回执不切换运行，失联或超时先查询原 Ref。收到匹配 task_id/run_id 的 task_state 时更新卡片状态，避免保留 running 的旧回执。

卡片出现保持草稿和阅读位置。Ctrl+R 进入动作，e/b/a/d/q 只选择，Enter 确认；正文 ←→ 分页、a 返回动作，Ctrl+Y 明确复制当前页。
动作页 ←→ 可切换已发现的历史计划，每次清除选择。新版引用仅在 v → Enter 后查询；返回后重新聚焦审阅。
正文页锚点包含计划身份与页偏移；一次只缓存每张卡的当前页，保留前页游标，不把不同版本拼成全文。
约束与完整身份在正文展开，摘要当前提供目标、版本和步骤/验收/待决定数量。

P2 真人反馈仍未收到。本次只扩展主工作台审阅行为，保留既有复制/回看语义；没有调整 Ghostty 侧栏比例、固定/接入或原生 IME。
工程证据不能替代 P2/P3 真人体验；P3 真实模型样本与工程回归分别记录。

## 新提案的风险提示（真实样本补充）

`submit_plan` schema 增加可选 `risk`。提交边界对缺省值按 kind 补齐：read→read、edit→write、run/verify→exec；显式 read 不得降低 edit/run/verify 的提示，run/verify 的 write 提升为 exec。保留显式 exec/network 等已有更强提示；这只是审阅信息，不授予工具权限。

不修改 `PlanStep.from_dict` 的历史默认值，不迁移已存计划，避免旧内容哈希与已批准 ReviewRef 漂移。`submit_plan` 自身仍是只读规划控制工具，实际写入和验收命令继续经过 ApprovalGate。此修复仅覆盖新提案提交，不将旧产物的风险元数据重新解释为权限判定。
