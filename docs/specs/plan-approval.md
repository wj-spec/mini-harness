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
- steering：绑定 plan 的 run 收到 steer 时把 approval 标 stale、写 `plan_stale` 事件，
  后续写/执行门禁返回 `plan_stale`（需重新批准计划）。
- `reject_plan(...)`：回到 draft，记录 rejected_by/reason，写 `plan_rejected`。
- `content_hash(plan)`：绑定计划内容（approval/status 归一化），批准后内容变化则 hash 不匹配；
- `can_execute(plan)`：status=approved + capability=plan.review + plan_hash 匹配 + approved_by 非空
  + approval 未标记 stale。
- `revise_plan(task_dir, plan, ...)`：
  - 生成 version+1 的 draft，旧版本只读保留；
  - 清空 approval（批准失效）；
  - 写 `plan_revised` 审计；
  - 支持改 objective/steps/acceptance_criteria/verification_plan。

## 已接线的入口

- stdio command `plan`（args `{action: show|approve|reject}`）：读取当前 session 最近 plan 产物，展示/调用 approve/reject；
- TUI `/plan approve`、`/plan reject` 发送该 command；
- TUI 计划覆盖层中 proposed 计划按 `y`/`n` 也可直接批准/拒绝；
- TUI `/plan revise <objective>` 与 backend `plan revise` 生成 version+1 draft；
- backend `plan list` / TUI `/plan list` 列出最近计划版本。
- 通过 `plan.review` capability 语义（当前 stdio 本地用户命令视为 user 决定）。

## 执行门禁

- `TaskRecord` 支持 `plan_id`/`plan_version` 绑定；`TaskRuntime.run.task` 接受并持久化；
- run 启动时若绑定 plan：查找最新版本并检查 `can_execute`；
  - 未找到/未批准 → `ApprovalGate.execution_gate` 对所有需审批的写/执行返回 `plan_not_approved`；
  - 已批准但内容 hash 变化 → `plan_stale`；
  - approved 且 hash 匹配 → 放行到原 ApprovalGate（逐次审批照旧）。
- TUI `/plan execute` 读取最近 approved 计划，创建带 `plan_id/plan_version` 的 act run。

## 未完成（P0 剩余）

- 工具执行前门禁已接 ApprovalGate；仍需 trace/可见原因码与 stale 重批 UI；
- 审批队列/任务级 deadline → `failed(approval_timeout)` 接线；
- TUI 编辑/重规划入口已有 objective 修订；步骤级编辑 UI 待；steering 后 stale 标记已接；
- `submit_plan` 工具已接 plan profile；`kind=plan` task 的 TUI 创建/展示未接；
- stdio 帧/`plan.review` capability 协商；
- TUI 计划卡片、折叠/分页、编辑（已支持 `/plan approve|reject` 与只读展示）；
- 验收标准确认与 GoalRecord 同步。

## 测试

- 未 propose 不可批准；
- 批准需 `plan.review`；
- propose → approve 后 `can_execute` 为 true，audit 两条；
- reject 回 draft；
- 批准后改内容 `can_execute` false。
