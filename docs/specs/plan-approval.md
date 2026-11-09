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
  - 写 `plan_approved` 审计事件；同时写本地通知 `plan_approved`/`plan_rejected`；
- submit_plan 成功后写 `plan_proposed` 本地通知。
- steering：绑定 plan 的 run 校验并受理指向该 run 的 steer 后，以锁内 `mark_plan_stale` 动作把 approval 标 stale、写 `plan_stale` 事件；错误目标不改计划，
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
- TUI `/plan execute` 与覆盖层 Enter 选择最新可执行 approved 非 stale 版本；stale/最新 draft 会提示修订或回退；
- TUI `/plan revise <objective>` 与 backend `plan revise` 生成 version+1 draft；
- backend `plan list` / TUI `/plan list` 列出最近计划版本；
- backend `plan step` / TUI `/plan step <id>` / 覆盖层 `s` 展示单步定义与 execution evidence；
- backend `plan diff` / TUI `/plan diff <a> <b>` 比较版本差异；
- backend `plan edit-step` / TUI `/plan edit-step <id> <title>` 生成步骤标题修订版本；协议也支持仅改 `kind`（read/edit/run/verify）；
- `plan action=card` / 覆盖层 `c`：完整审批卡（目标/约束/范围外/验收标准/验证命令/`executable`/批准人时间/hash 匹配）。
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
- 审批时限已接（可选 env）；TUI 编辑/重规划入口已有 objective 修订；步骤级编辑 UI 待；steering 后 stale 标记已接；
- `submit_plan` 工具已接 plan profile；`kind=plan` task 的 TUI 创建/展示未接；
- stdio 帧/`plan.review` capability 协商；
- TUI 计划卡片、折叠/分页、编辑（已支持 `/plan approve|reject` 与只读展示）；
- 验收标准确认与 GoalRecord 同步。

## 测试

- 未 propose 不可批准；
- 门禁拒绝原因写入 ApprovalDecision.reason，工具错误显示
  `denied_by_user: plan_not_approved/plan_stale`；
- 绑定未批准 plan 的 run 即使审批 handler 允许也无法写文件（ApprovalGate gate deny）；
- 批准需 `plan.review`；可选审批时限 `VIBE_PLAN_REVIEW_TIMEOUT_SECONDS`（默认 0=不限），
  超时后 approve 返回 `review window expired`，需修订后重新批准；
- propose → approve 后 `can_execute` 为 true，audit 两条；
- reject 回 draft；
- 批准后改内容 `can_execute` false。

## 审阅动作与会话 plan mode（2026-11-08 补充）

`app/runtime/plan_review.py` 与 stdio `plan` command 新增审阅动作：

- `request-changes`：P4 后端已改为持久修订任务，反馈限 1–4000 字；准确协议携带 ReviewRef，旧文本命令先解析当前最近计划。完整原计划与反馈进入只读 run，只有 submit_plan 成功才发布同族 v+1 proposed，反馈写入 `Plan.review_feedback`，清空 approval；会话 plan mode 保持开启。纯产物 helper 的旧 draft 语义保留，stdio 不再调用它。
- `dismiss`：只写 `plan_review_dismissed` 审计，不改计划状态；批准动作未发生。
- `markdown`：输出可审阅 Markdown 稿（目标与成功标准、步骤、验证命令、约束、反馈）；
  `plan.vN.json` 仍是机器契约与审批 hash 载体。
- 修订后的 proposed 必须重新批准；旧会话遗留 draft 仍可由用户显式 `approve`，先补 propose 再 approve。新旧 stdio 批准入口均进入准确审阅控制器，活动修订期间拒绝批准。模块级 `approve_plan` 仍只接受 `proposed`。
- `plan` 新增 `mode-on|mode-off|mode-status`：会话级状态存
  `.vibe/sessions/<id>/plan-mode.json`（`active`/`wanted`/`pending`）；
  任务运行时未启用时拒绝开启。
- `/plan <任务>` 同时开启会话 plan mode；批准计划后自动关闭；`/plan off` 手动关闭。
- 普通 run 请求在会话 plan mode 开启时自动创建 `mode=plan` 的 task；
  显式 `task.mode` 优先（`/act`、`/chat`、goal 不受影响）。
- 新增 JSONL 帧 `plan_mode`：`{mode:{active,pending,wanted}}`，在 ready、
  模式切换、批准计划、run 结束后发送。
- TUI：`/plan on|off|status`、`/plan request-changes <反馈>`、`/plan dismiss`、
  `/plan markdown` 仅在 idle 时发送；计划覆盖层 `r` 预填反馈、`m` 打开审阅稿；
  paneTitle/hint/placeholder 显示计划模式 active/pending。

## 准确审阅与启动恢复（2026-11-08，runtime）

`PlanReview` 已提供准确 ReviewRef 的查询和显式决策，使用共享 Plan 锁、预留 Task 身份与首份 meta 创建凭据。一次初始启动按 Ref 去重；已有运行段只返回原绑定；已批准但无段时，只有明确失败或原启动者已退出且任务可恢复，才在用户重试时继续同一任务。旧绑定任务没有新凭据、存储损坏或身份不一致均要求核对，不随机另起。批准时间不因重试刷新。

此能力已接新 JSONL/capability、Agent.run 和 TypeScript 客户端；Ink 卡片尚未接通，当前界面命令入口仍按上文兼容行为运行。工具 ApprovalGate 没有变化，计划批准不能代替工具授权。实现、故障注入与后续验收见 [P3 证据](../../openspec/changes/implement-improve-workbench-ux-p3/verification.md)。


JSONL 的 `plan-review` 扩展已实现：`review` 可选 target/cursor；`approve-execute` 必须携带准确 target；带 target 的 `approve` 仅批准，`dismiss` 仅关闭。target 包括 session_id、plan_task_id、plan_id、plan_version、plan_hash。新结果为结构化 plan_review，只有本次首次交接的 launched 帧具有 run_started=true；重试与重连返回原绑定，不再次调用模型。查询可在其他运行活跃时使用，新批准/启动仍受 busy 限制。执行准备和模型调用前复核内容及可执行性；失败结束原段并释放锁。此处是协议能力，Ink 卡片入口仍待实现。
