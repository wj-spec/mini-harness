# 计划审批门 P0（状态 / hash / 审计）

状态：2026-11-06 P0 基础实现（plan 状态机 + 审批记录 + 审计）；执行门禁/工具拒绝/TUI 审批面板待后续。
设计来源：[13 计划审批门](../design/long-horizon/13-plan-approval-gate.md)。

## 已实现

`app/runtime/plan.py`：

- `propose_plan(task_dir, plan)`：
  - draft → proposed，原子保存；
  - 写 `audit.jsonl` 事件 `plan_proposed`（task_id/plan_id/version/content hash/requester）。
- `approve_plan(task_dir, plan, approved_by=..., capability="plan.review", approval_id=...)`：
  - 仅 proposed 可批准；
  - 缺 `plan.review` capability fail closed；
  - 写 approval 字段：approved_by/approved_at/approval_id/plan_hash/capability/requester/decider；
  - 写 `plan_approved` 审计事件。
- `reject_plan(...)`：回到 draft，记录 rejected_by/reason，写 `plan_rejected`。
- `content_hash(plan)`：绑定计划内容（approval/status 归一化），批准后内容变化则 hash 不匹配；
- `can_execute(plan)`：status=approved + capability=plan.review + plan_hash 匹配 + approved_by 非空。

## 未完成（P0 剩余）

- 工具执行前门禁：未批准 plan 的写/执行 deny(`plan_not_approved`)，当前只有 `can_execute` 纯函数；
- 审批队列/任务级 deadline → `failed(approval_timeout)` 接线；
- steering/偏离后 plan 变 stale 与重批；
- `submit_plan` 工具与 `kind=plan` task 接线；
- stdio 帧/`plan.review` capability 协商；
- TUI 计划卡片、折叠/分页、批准/拒绝/编辑；
- 验收标准确认与 GoalRecord 同步。

## 测试

- 未 propose 不可批准；
- 批准需 `plan.review`；
- propose → approve 后 `can_execute` 为 true，audit 两条；
- reject 回 draft；
- 批准后改内容 `can_execute` false。
