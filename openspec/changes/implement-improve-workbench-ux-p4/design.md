# P4 设计：所见版本、反馈与修订产物

状态：同族版本提交、持久操作、JSONL/capability、只读 Agent、Ink 卡片反馈与重开修订记录已接通；后台准备持锁复核/创建已接线。确定性取消/重开/续做与内容冲突已有跨层证据，受控真实模型与真人完整旅程待完成。

## 启动时事实与接口选择

`request_changes()` 调用 `revise_plan()`，直接写复制版 draft；stdio 只返回文本，没有 Provider 请求。`register_plan_tool()` 固定产生新 plan_id/v1。P3 已有准确 ReviewRef、共享 Plan 锁、启动创建凭据、只读查询和显式运行交接，不另建通用事务框架。

继续使用已建立的测试 seam：ToolRegistry 公共工具调用与 Plan 公共产物读取；PlanReview/TaskRuntime 查询与决策；JSONL/FakeProvider；Ink App 键盘及真实 Node→Python 接线。磁盘文件、TaskRecord、模型/审批调用和真实文件内容用于核对持久行为，不以内部 helper 调用次数代替结果。

## 1. 同族版本提交

- 后端注册 submit_plan 时绑定原 Plan 快照、反馈和修订操作 ID；这些字段由控制器决定，不放入模型 schema。模型不能选择 plan_id/task_id/version/approval。
- 模型仍提交 objective/steps，其他业务字段（criteria、verification、constraints、out_of_scope、open_questions）省略时保留原值；显式字段经 Plan 校验。风险提示沿用 P3 提交规范化。
- 发布时持共享进程锁，核对原快照及同族最新版本；以一次原子保存发布同 task_id/plan_id 的 v+1 proposed，不预先暴露可批准的占位 draft。
- 新版保留原 goal/parent/budget 等绑定，清空 approval，刷新 created_at；source 记录修订 ID、原版/hash 与反馈；review_feedback 追加本次反馈。原版、执行台账与验收结果不修改。
- 同一修订 ID 已发布时返回原产物，不能覆盖或另建版本；不同操作/原快照已变/同族已有新版返回冲突。结果未知以磁盘产物为准，不能盲重放模型调用。
- 已发布产物的复用追加 `plan_revision_reused`，携带版本/hash/修订 ID；可补齐“产物已保存、proposed 审计追加失败”的追溯记录，不重写产物或刷新审批时间。只读查询不得调用此提交入口。
- 本切片只建立提交能力；完整控制器须限制发布任务归属、并发与续接。未接线前普通 `/plan` 行为保持。

## 2. 请求、任务与恢复

增加独立 capability `plan-revision`，旧后端不显示可执行修订动作。JSONL 仍走 command=plan，准确 request-changes 携带 target、feedback；query-revision 仅查已有操作。旧文本命令在新后端先解析为准确对象，再走同一控制器，不继续创建占位 draft。

修订操作由 session/ReviewRef/完整规范化反馈决定，最多 4000 字符，不静默截断；换传输请求 ID 不产生第二任务。持久凭据先预留 task_id，再建立 mode=plan 的 TaskRecord，然后只交接一次初始 run。首份 TaskRecord 保存修订来源凭据，复用现有任务锁、segment 和预算。查询不建任务、不追加段。

同族一次只允许一个活动修订；不同反馈在已有活动任务时返回待处理状态并保留输入，不能静默替换。开始前与提交前再次复核准确版本；源内容变化、存在更新版本、任务身份不符、存储损坏均停止。已过审批 TTL 的原计划仍可被修订为新的 proposed，但不能借修订重用旧批准。

准备中崩溃按凭据与 TaskStore 恢复：有运行段时只返回原任务；无运行段且能证明启动者死亡或 prepare 失败时，显式继续原任务。存活/不明状态不判死。取消、预算暂停、Provider 错误、未 submit_plan 均保留反馈和来源；显式续做复核来源并复用该修订操作。新版本已发布却回执/任务收尾失败时，查询返回新版本，不再请求模型。

已实现的 runtime 契约：`PlanRevision.request/read/resume/context`。操作凭据保存在原任务 `plan/review-revisions/`，新 Task 的 creation 同时绑定 operation/source/feedback；`resume` 必须携带已观察的 segment，旧确认不新增段。已完成却未提交时回执为 `incomplete/revision_not_submitted`。查询以 TaskRecord 与原目录 v+1 的来源凭据核对结果，不写回执、不重放提交。公开 context 再检查原版本、任务范围、活动执行和其他修订；P3 准确审阅入口已有修订互斥，旧命令/后台入口待统一。

准备失败的恢复在 busy/来源/范围/运行时校验之后进行；有段的查询只还原已有结果，无段时仅在确认准备失败或同机启动进程已退出后复用预留任务。TaskStore 判定旧进程退出时一并关闭遗留 running 段，续做增加新段并保留历史。

## 3. 执行与上下文

修订 prompt 包含完整原计划与本次反馈，并明确逐条回应、仅只读探索、最后 submit_plan。使用结构化边界标明来源，不把反馈当作改变 tool profile 的系统指令。超大原计划无法安全放入上下文时明确拒绝，不用截断正文冒充完整输入；上限在接线切片按现有上下文预算核定。

沿用 plan profile 的硬工具目录和执行 gate，禁止模型自发委派、bash、写入、MCP 副作用；恶意工具调用也必须被拒绝。模型未提交就结束时返回 revision_not_submitted，不发布复制版。原计划与反馈、模型调用、提交产物和修订 task/run 的关联保留在证据中。

活动执行任务不能同时修订其计划。修订启动到发布期间禁止同族新审批/执行；完成发布后旧 Ref 被 superseded。接线时一并检查旧 `/plan execute`、prepare-background 与绑定 run 路径，不能从新的待审版回退到旧 approved。旧产物本身不改写，独立查看仍可用。

## 4. Ink 旅程

当前后端接线：`request-changes`、`query-revision`、`resume-revision` 使用准确 target 与 feedback；续做另带正整数 expected_segment。`plan_revision` 帧区分 absent/pending/running/paused/incomplete/submitted/unavailable，返回原任务、是否实际启动及准确新版 Ref。仅启用任务运行时、原生工具和进程锁时公布 `plan-revision` capability；新准确动作遇到旧后端时拒绝发送。

旧文本反馈命令在发送时解析最近计划；`/resume` 从上轮 trace 的 request_id 找已观察段，先复核再追加，不能通过普通 `run.task` 绕开修订控制器。Agent 的固定 system part 存完整原计划/反馈 JSON，并标明内容不能授予权限；它与 plan policy 一同在 run 结束后还原。超窗时沿用 ContextBuilder 的硬停止，不截断来源继续请求。模型未提交时，最终结果、Task 与轮次账本统一为暂停/未完成。

下列卡片内编辑已实现，并有确定性跨层与三尺寸 PTY 证据：

审阅焦点选择 r→Enter 进入反馈编辑，页面显示原版本；普通 composer 草稿保留。反馈输入后显式确认才启动；Esc 返回保留未发送反馈。开始/未知/失败保留准确对象，操作回执丢失时只提供查询，不能自动重发。

运行中显示“修订 vN”，Ctrl+C 停止当前修订。收到权威成功提交后显示 vN+1 的新卡片，原卡标示新版可用；不抢占用户正在阅读的另一张卡。只有新的准确 Ref 可以批准新版，工具审批仍需另外确认。80×24、120×40、32×12 分别检验反馈、确认、返回和状态，保留 P1/P2 输入与阅读行为。

实现使用按 ReviewRef 保存的反馈记录；普通输入框持续挂载，反馈输入单独持有焦点和原生光标。编辑→确认→状态三个视图共用可点击动作按钮；回执按请求/来源/反馈/序号匹配，未知结果只提供查询。暂停或中断的已观察段才提供续做，后端再次复核。

已实现重开后的反馈恢复界面：未发送反馈只保存在界面进程内，已发送来源/反馈由后端持久化。修订发现只读保存的操作，不重新发送反馈或猜测最近计划；已提交记录打开准确新版，暂停记录显式续做时再次复核原来源。

重开切片的接口：新增独立 `plan-revision-history` capability，卡片 `h` 进入该计划的“修订记录”。`list-revisions` 绑定所见 Ref，在同一 session/plan_task/plan_id 内读取已保存操作；每页最多 10 条，按不可变操作标识排序，游标为上一页末标识，不按会变化的更新时间分页。记录仍携带各自完整原 Ref、反馈和只读回执，不因当前所见版本更新而改写来源。损坏/越界记录明确报错，不冒充空历史；查询不创建 Task、运行段或模型调用。

界面按记录显式查看，↑↓选记录、PgUp/PgDn翻页，按钮进入该条反馈状态或返回。不同已发送记录与本地未发送草稿分开保存。恢复后由用户显式查询/续做，提交成功的记录只提供新版审阅；来源变更/过期仍由原准确续做入口拒绝。未发送草稿的跨进程自动保存不属于已发送操作恢复，当前继续保留其进程内语义，并在使用说明中明示。

## 5. 纵向交付和风险复核

先做同族版本提交，再做持久修订操作与 JSONL 交接，最后接反馈编辑和完整旅程。每条切片先失败回归再实现，测试通过后审查该切片的对象漂移、部分写入和权限边界；不为未实现接口一次堆积所有测试。

风险重点：复制版被误批准、模型选择身份、旧批准回退、反馈丢失/截断、不同对象的迟到回执、重复模型费用、取消后误显示完成、修订来源不完整。P3 真人待验收不等于 P4 验收通过；后续按实际反馈调整共同 UI 行为。
