# 计划结构化子任务清单 · 任务

- [x] 盘点 Plan/StepLedger/审阅 payload 与 TUI 渲染边界，确认改造只动展示与只读进度。
- [x] 后端：`plan_review.read_review` 增加结构化 `steps`；新增 `plan_progress` 事件（绑定、开始/完成步骤、证据、结束）。
- [x] TUI 协议：`PlanStepInfo`/`PlanProgress` 类型与严格校验，`plan_review.steps` 可选字段。
- [x] TUI 状态：`ViewState.planProgress`，事件归约，begin/ready 重置。
- [x] 审阅：计划卡与审阅焦点渲染结构化步骤清单（保留 Ctrl+R Markdown 正文）。
- [x] 执行：只读 `PlanProgressPanel`（状态图标、当前步骤、文件、证据数），窄屏/覆盖层/阅读态隐藏。
- [x] 侧栏：`doneSteps` 使用真实 ledger 完成数。
- [x] 回归：Python review/progress payload、Ink 协议/状态/计划卡/进度面板，更新旧「doneSteps 恒为 null」契约。
- [x] 文档：P3 验收暂停说明、roadmap/MILESTONES/task-log/USAGE/验收清单同步。
- [x] 真实 PTY 冒烟与证据归档：计划卡含步骤行、批准后出现只读进度面板与 ✓/◐/○。
- [x] 全量 Python/Ink 回归（Ink 742/742、Python 1953 passed / 4 skipped）与提交。
