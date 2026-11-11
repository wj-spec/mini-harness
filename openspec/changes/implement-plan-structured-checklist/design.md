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

## 决策 5：兼容与限制

- `plan_review.steps` 可选；旧 TUI 忽略额外字段，新 TUI 对缺失 `steps` 的 review 仍显示原摘要。
- `plan_progress` 只在活动运行内发送；attach/resume 一个正在执行的计划任务时，本增量不重放历史进度（后续可在 task_state 快照中补充）。
- 执行中直接编辑步骤不在本增量范围，仍通过 P4 修订/重新审阅修改计划。
