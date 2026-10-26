# /goal 目标模式与长任务自动续段

日期：2026-10-26。状态：TUI 编排实现与回归完成；真实长任务验收待跑。

## 目标

单次 run 的时间/预算限制不能支持跨小时的目标。`/goal` 把任务提升为“目标”，在每个运行段因**资源限制**结束时自动开启下一段；遇到需要人判断的停止原因则暂停。

本切片只做 Ink 工作台编排，不改变后端 Agent loop、工具审批或沙箱边界。

## 命令

| 命令 | 行为 |
|---|---|
| `/goal <目标>` | 启动目标模式，第 1 段开始 |
| `/goal` | 查看目标、段数与暂停原因 |
| `/goal stop` | 停止目标，取消进行中的段；不自动续段 |
| `/goal resume` | 从暂停/完成状态恢复下一段 |

## 自动续段

每段仍是普通 run，事件、审批、工具记录和会话历史不变。段结束后的判断：

- **自动继续**：`status=stopped` 且 `stop_reason ∈ {max_run_seconds, token_budget, cost_budget, max_steps}`；
- **暂停，不继续**：`no_progress`、`context_window`、`error`、`cancelled`，或正常 `done`（目标完成）；
- 继续时注入 `[GOAL MODE · 第 N/M 段]` 提示：先核对工作区与保存进度，再继续未完成部分；
- 完成时显示“目标完成”；暂停时给出 `/goal resume`。

## 上限

- `VIBE_GOAL_MAX_SEGMENTS`：默认 8 段；
- `VIBE_GOAL_SECONDS`：默认 4 小时；
- `VIBE_GOAL_MAX_TOKENS` / `VIBE_GOAL_MAX_COST_USD`：默认 0（不限）；
- 达到上限后暂停，需 `/goal resume` 或新目标；不会静默无限续段。

## 实现

- `tui/src/goal.ts`：纯函数目标状态、段决策、提示词、上限与恢复；
- `tui/src/app.tsx`：`/goal` 命令、done 事件后的 `goalDecision`、续段 run 启动；
- 目标状态保存在 App 进程内；重启 Ink 不自动恢复目标（历史会话可用 `/resume`）；
- 底部 hint/输入占位显示目标段；续段任务以新 task entry 呈现，便于阅读。

## 测试与边界

- `tui/tests/goal.test.ts`：段推进、完成、非资源暂停、段/总量上限、resume；
- `tui/tests/app.test.tsx`：`/goal` 启动、`max_run_seconds` 后自动发起第 2 段、done 停止自动续段、`/goal stop` 不再续段；
- 现有审批、preview、写文件、bash、sandbox、上下文压缩行为不变；
- 真实长任务与跨段成本待验收。
