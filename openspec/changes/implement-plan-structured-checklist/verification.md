# 计划结构化子任务清单 · 验证记录

状态：实施中（2026-11-11）。自动化、真实 PTY 冒烟与本机 p3 体验待再验收；P3 人工验收在改造完成前暂停。

## 本轮改动

- **审阅结构化**：`plan_review.read_review` 新增 `steps`（step_id/title/kind/risk/files/depends_on/acceptance/executor），计划卡与审阅焦点直接渲染步骤清单；`Ctrl+R` 的 Markdown 正文、分页、动作选择不变。
- **执行只读进度**：后端在计划绑定、`start_plan_step`、`complete_plan_step`、工具证据与运行结束前发送 `plan_progress`（plan 身份 + steps 状态/证据数 + done/total）；TUI 新增 `ViewState.planProgress` 与 `PlanProgressPanel`，仅运行活跃时显示，覆盖层/阅读/窄屏下让位。
- **侧栏真实完成数**：计划摘要 `doneSteps` 只在 `plan_progress` 与当前计划 id/version 匹配时取真实 done，不再恒为 null。
- **协议**：`PlanReviewResult.steps` 可选并严格校验；新增 `plan_progress` 事件校验（状态枚举、done/total 一致、长度上限）。
- **测试契约更新**：旧「review 不得提供完成步骤数」断言改为「执行 ledger 提供真实完成数」；新增协议、ViewState、计划卡、进度面板与 App 旅程回归。

## 自动验证

| 边界 | 证据与结果 |
|---|---|
| Python | 计划相关（plan review/artifact/execution/stdio plan mode/stdio tasks）全绿；完整 **1957 项 / 1953 passed / 4 skipped / 0 failed**（本沙箱启用 PTY） |
| Ink 协议/状态 | `plan_review.steps` 合法/非法形状、`plan_progress` 合法/非法形状、begin/ready 重置均覆盖 |
| Ink 审阅 | 计划卡步骤行 `n. ○ step_id 标题 · kind/risk · files ← 依赖 · 有验收`；审阅焦点步骤列表 |
| Ink 执行 | App 旅程：进度事件后主区出现 `计划 v1 · 1/2 步 · 当前 s2`、`✓/◐` 状态与步骤行 |
| 侧栏 | 真实后端集成：拒绝路径 `doneSteps=0`、批准路径 `doneSteps=1`，来自实际 ledger |
| 完整 Ink | **742/742 通过**（本沙箱 PTY 可用） |
| 布局 | 进度面板仅在 live run 且 rows≥18 显示，覆盖层/审批/阅读/32×12 路径保持既有断言 |

## 真实 PTY 冒烟

`VIBE_TASK_RUNTIME=1 VIBE_FAKE_DEMO=plan ./run_ink.sh --fake --autonomy ask`（100×40，真实 PTY）：

- 计划卡出现，并包含步骤行 `s1 写入演示文件`；
- `Ctrl+R → e → Enter` 批准执行后，主区出现 `▌ 计划 v1 · … 步` 进度面板与 `✓/◐/○` 状态；
- 进入写入审批（后续点击允许/拒绝路径由 P3 验收覆盖）。
- 证据：`.vibe/ux-plan-checklist-2026-11-11/pty-plan-checklist.txt`、`tui-full.log`、`python-full.log`。

## UX 二轮：计划审阅栏与常驻进度（2026-11-11）

反馈：计划卡 → 执行需要 `Ctrl+R → 选动作 → Enter`，计划展示不够凸显；希望计划显示在输入框、Enter 执行、可选中某一步审阅/修改，进度清单常驻。

实现与自动验证：

| 边界 | 证据与结果 |
|---|---|
| 一次 Enter 执行 | App 旅程：计划回执后输入框位置出现 `▌ 计划 v1 · 目标 · N 步`；按 Enter 后客户端收到 `plan {action:'approve-execute', target}`，无需 Ctrl+R |
| 步骤选择/修改 | ↑↓ 选步骤后 `m` 预填 `/plan edit-step s2 ` 并退出审阅栏；`k/f/p` 同理预填类型/文件/依赖 |
| 草稿保留 | 审阅栏提示「草稿已保留（N 字）」；Esc 稍后处理后返回普通输入框，草稿仍在；集成测试按新契约先 Esc 再输入草稿 |
| 正文/修订 | `b` 直接进入正文；`r` 进入修订反馈；`Ctrl+R` 仍打开详细审阅 |
| 进度常驻 | App 旅程：run 结束后进度清单仍显示 `✓ s1`；真实 PTY 最终帧为 `▌ 计划 v1 · 1/1 步 · 已完成` |
| 布局 | 审阅栏替代输入框高度并参与主区扣减；运行中/审批/覆盖层/阅读/窄屏不显示；32×12 沿用既有路径 |
| 完整 Ink | **743/743 通过** |
| 完整 Python | 1957 项（1953 passed / 4 skipped；本批仅 TUI/文档变化，复跑确认） |

真实 PTY（100×40）冒烟：计划栏出现且含步骤行 → **单次 Enter** 启动执行 → 两次审批后结束 → 进度面板保留 `已完成`。证据：`.vibe/ux-plan-checklist-2026-11-11/pty-plan-bar.txt`。

## 未做 / 未确认

- attach/resume 一个正在执行的计划任务时，历史 step 进度不会自动重放（需后续从 task_state 快照补充）；
- 执行中直接新增/重排/跳过步骤（本轮只读）；
- 真实用户对清单形态的主观验收，以及改造后的 P3 人工完整旅程。
