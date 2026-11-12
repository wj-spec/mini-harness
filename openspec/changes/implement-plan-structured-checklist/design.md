# 计划结构化子任务清单 · 设计

## 决策 1：单一事实源仍是 Plan JSON + StepLedger

- 审阅事件新增 `steps` 字段（无执行状态）：`step_id/title/kind/risk/files/depends_on/acceptance/executor`，由 `plan_review.read_review` 从 Plan 直接生成，最多 200 步；与 hash/版本绑定同源，不允许 TUI 猜测。
- 执行进度由新的 `plan_progress` 事件承载，来源是已绑定的 `StepLedger`：`plan{plan_id,task_id,version,status,objective}`、`steps[{step_id,title,kind,risk,files,status,evidence_count}]`、`done/total`。
- 发送时机：绑定成功（初始 pending）、`start_plan_step`、`complete_plan_step`、工具证据写入、运行结束前。事件 id 为当前 run id，TUI 只接受当前活动运行的事件。
- 协议校验：TUI 对 `steps` 与 `plan_progress` 做严格形状校验（状态枚举、计数一致、step 长度上限）；`steps` 为可选字段，旧后端/旧回执不受影响。

## 决策 2：审阅渲染是清单，正文是详情

- 计划卡：保留「计划 vN · 目标」「步骤/验收/待决定」摘要，随后最多 8 行步骤：`n. ○ step_id 标题 · kind[/risk] · files ← depends_on · 有验收`；超出提示 Ctrl+R 看正文。
- 审阅焦点（Ctrl+R 进入的动作视图）：动作选项后显示最多 6 行步骤；`b` 仍打开 Markdown 正文页，分页/复制/审阅动作不变。
- 步骤在审阅期一律显示 `○`（尚未执行）；执行状态只由 `plan_progress` 提供，避免审阅态伪造完成度。

## 决策 3：执行进度面板只读、可让位

- 组件 `PlanProgressPanel`：标题 `▌ 计划 vN · done/total 步 · 当前 sX`；行窗口围绕当前步骤（最多 4 行），图标：`✓ done / ◐ in_progress / ! blocked|deviated / – skipped / ○ pending`；行内容含 kind、risk、前两个文件、完成步骤的证据数。
- 显示条件：存在计划进度且运行处于 running/stopping/waiting_approval/有 activeId；同时不在审批、审阅焦点、修订、上下文/计划/详情覆盖层、历史结果、阅读焦点、退出确认中；终端 rows ≥ 18。
- 布局：固定高度（1 标题 + ≤4 步）并从主区高度扣除；不新增边框；32×12 与覆盖层路径保持不变。
- 面板不接收键盘/鼠标，不改变输入焦点与草稿；运行结束后隐藏，回执与侧栏保留最终结果。

## 决策 4：侧栏完成数来自 ledger

- 发布侧栏信号时，若 `plan_progress` 的 plan_id/version 与当前计划摘要一致，`doneSteps` 使用真实 done 数；否则保持 `null`（不推断）。
- `stepCount` 仍取计划声明值，`acceptance` 仍只来自验收证据。

## 决策 6（2026-11-11 UX 二轮）：计划审阅栏 + 一次 Enter 执行 + 进度常驻

触发：人工反馈「计划卡 → 执行链路太长（Ctrl+R → 选动作 → Enter），计划展示不够凸显，希望计划出现在输入框、Enter 执行，并能选中某一条审阅/修改」。

- 计划待决定且处于 idle、存在可执行动作时，用 `PlanReviewBar` **替代输入框**：标题（`▌ 计划 vN · 目标 · N 步`）、选中步骤高亮、步骤窗口、选中步详情（类型/风险/文件/依赖/验收）、操作提示；背景使用焦点色提高显著度。
- `Enter` = 直接 `approve-execute`（写入/命令仍逐个走工具审批）；`↑↓/PgUp/PgDn/Home/End/1-9` 选步骤；`b` 正文（进入详细审阅正文）、`r` 修订反馈、`a` 仅批准、`d` 关闭、`m/k/f/p` 分别预填 `/plan edit-step|kind|files|depends <step_id> ` 后回到普通草稿继续补全；`Esc` 稍后处理（隐藏审阅栏，`Ctrl+R` 仍打开详细审阅）。
- 普通草稿不丢失：审阅栏提示「草稿已保留（N 字）」，`Esc` 后原草稿可继续编辑；审阅栏只在 idle 且非审批/修订/覆盖层/阅读/退出时出现，运行中不抢占输入。
- 执行进度从「仅 live run 可见」改为**常驻**：只要当前计划有 `plan_progress` 且无覆盖层就显示，结束后保留 `已完成` 清单（最多 6 步窗口 + 标题计数），不再需要按键或 `/plan` 才能看进度。
- 操作对比：进入执行 3 键（Ctrl+R→e→Enter）→ **1 键（Enter）**；看正文 2 键（Ctrl+R→b）→ **1 键（b）**；改某步从审阅焦点多步 → **↑↓ + m 预填**。

## 决策 7（2026-11-12）：审批单键与 attach 进度重放

- 审批是阻塞态：**草稿为空时**按 `y` 直接允许本次、`n` 直接拒绝（不再需要 Ctrl+R/Tab/Enter 多步）；有草稿时 `y/n` 仍属于草稿输入，避免误审批。Tab/Shift+Tab+Enter 与鼠标点击保留；快捷字母若被输入框先收到会立即清除，不污染草稿。
- attach 一个任务时，若任务绑定了已批准计划且有 segment/ledger，bootstrap 在 `task_snapshot` 后追加 `plan_progress`（id 取最近 segment run_id，来自 `StepLedger`），TUI 直接显示只读步骤进度，不需要等下一次运行。
- 计划跑完/结束后进度面板继续常驻；attach 离线和在线路径都使用同一份 ledger 快照。

## 决策 8（2026-11-12）：长计划折叠、自适应与 Ctrl+L 全屏清单

- 常驻面板不再固定窗口：已完成的步骤折叠成一行 `✓ 已完成 N 步（含跳过 M）`；面板只展示当前及未完成步骤，行数按终端高度自适应（最多 5–9 行），当前步骤额外展开 1–2 行完整标题与文件/依赖/验收/验证标记。
- 未完成步骤超过预算时显示 `… 还有 N 步 · Ctrl+L 查看全部`；面板标题在足够宽时提示 `Ctrl+L 全部`。
- `Ctrl+L` 打开只读全屏清单：所有步骤、状态、完整标题（换行）、文件、依赖、验收、验证标记与证据数；`↑↓/PgUp/PgDn/Home/End` 滚动，`Esc` 返回，执行中实时更新；不调用模型、不改变执行。
- 紧凑面板不接收输入，不抢草稿；全屏清单通过既有 overlayKey/overlayScroll 通道，不新增输入焦点。
- 计划进度 payload 增补 `depends_on/acceptance/verification`，供全屏清单与当前步详情使用；仍限 200 步。

## 决策 5：兼容与限制

- `plan_review.steps` 可选；旧 TUI 忽略额外字段，新 TUI 对缺失 `steps` 的 review 仍显示原摘要。
- `plan_progress` 只在活动运行内发送；attach/resume 一个正在执行的计划任务时，本增量不重放历史进度（后续可在 task_state 快照中补充）。
- 执行中直接编辑步骤不在本增量范围，仍通过 P4 修订/重新审阅修改计划。
