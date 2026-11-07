# 计划执行绑定 P0（step ledger）

状态：2026-11-06 P0 纯模块实现（台账/证据推进/原子存储）；工具与 agent 接线待后续。
设计来源：[14 计划执行绑定](../design/long-horizon/14-plan-execution-binding.md)。

## 已实现

`app/runtime/plan_execution.py`：

- `StepRecord` / `StepLedger`：task_id/plan_id/plan_version + 每步
  status/evidence/attempts；
- 状态机：pending / in_progress / blocked / done / deviated / skipped；
- `from_plan(plan)`：从结构化 Plan 建台账；
- `start_step`：只允许 pending/blocked，attempts+1；
- `add_evidence`：kind ∈ file_change/verification/command_ok/manual，含 ref/ok/时间；
- `complete_step(plan, step_id)`：
  - 有 `verification` 的步骤必须至少一条 ok 的 verification/command_ok 证据；
  - 其他步骤至少一条证据；
  - 无证据 → `step_evidence_missing`；
- `mark_deviated(step_id, paths)`；
- `plan_complete(plan)`：所有非 skipped 步骤 done；
- `can_execute_step`：依赖均已 done；
- 原子存储 `task_dir/plan/execution.v<version>.json`；
- 展示：stdio `plan show` 与 TUI `/plan` 会附带 step status/evidence 数；
- bound plan 的执行 run 注册 `start_plan_step` / `complete_plan_step` 工具：
  - start 校验依赖并置 in_progress；
  - 之后的 read/write/bash 工具结果自动写 evidence（read/file_change/command_ok，ok 标记）；
  - complete 只在该步证据满足时成功；
  - run 结束注销工具并持久化 execution.vN.json；
- 步骤工具：`complete_plan_step` 接受模型额外 evidence/evidence_refs 参数（忽略），
  证据不足返回带操作提示的错误；
- 步骤级工具门禁：read 步骤只允许只读工具，edit 允许写，run/verify 允许 bash；
  越界返回 `plan_step_tool_blocked`；
- edit 步骤声明 files 时，write_file/edit_file 只能写这些文件，越界返回
  `plan_step_file_scope`；apply_patch 无法校验范围，直接提示改用 write_file/edit_file；
- 完成一步返回下一步提示（start directly / do not re-read）；
- 全部步骤完成后 harness 设置 tool_gate，拒绝后续非计划工具并提示停止；
  连续 2 次 gate 命中后 Agent 直接以 done 收尾，避免模型无限探索；
- `/resume` 续做已暂停的 bound plan task 时保留 task_id/plan 绑定并开启新 segment；
- 计划级完成门：绑定 plan 的 run 返回 done 时，若 required 步骤仍有未 done，
  改写为 `stopped/plan_incomplete`，任务转 paused，等待继续执行/重规划；
  步骤完成后若 Plan 有 confirmed criteria，再执行可信验收命令，未通过则
  `stopped/acceptance_<code>`。
- `load_ledger` 未知 schema fail closed。

## 未完成（P0 剩余）

- 工具证据按 active step 记录已接；自动将工具调用映射到计划外/偏差检测未接；
- 模型 `complete_step` 请求由 harness 证据校验（当前只有模块 API）；
- 偏离检测（计划外文件）和重规划；
- 每步预算/成本归因；
- 计划级完成门已接 required 步骤；验收 criteria 三态接入完成判定待后续；
- rounds 展示 step ledger；TUI 已有基础状态展示，完整计划卡片待后续。

## 测试

`tests/test_plan_execution.py`：roundtrip、依赖顺序、无验证证据拒绝、非法 step/状态、
deviation、原子写。

## 验收修复模式（2026-11-07 补充）

`VIBE_ACCEPTANCE_REPAIR_ATTEMPTS>0` 时，验收失败后的下一轮 Agent 运行使用修复门禁：

- 只读工具始终允许；
- `write_file` / `edit_file` 仅允许计划 `edit` 步骤声明过的文件（全部 edit 步骤的并集）；
  `apply_patch` 无法校验范围，修复模式直接拒绝；
- 若计划存在 `run`/`verify` 步骤，允许 `bash`/`run_command`；
- 其余工具返回 `plan_repair_tool_blocked`，不会触发完成门的强制收敛计数。

## 计划上下文注入（2026-11-07 补充）

绑定 plan 的 run 在用户输入后追加 `[已批准计划]` 段：目标、全部步骤（`step_id: title (kind, files)`）
以及执行协议（先 `start_plan_step`、完成后 `complete_plan_step`、不要跳步、全部完成后停止工具并总结）。

原因：后台 worker 只把 task objective 作为 run 文本发给后端；若不给步骤与协议，模型可能只输出文字、
不调用 step 工具，导致 `stopped/plan_incomplete`（deepseek-chat 实测一次）。注入后后台 plan 任务
与前台 `/plan execute` 行为一致；修复后 deepseek-flash/chat suite 均通过。

## 纯分析步骤的 manual 证据（2026-11-07 补充）

`complete_plan_step` 现在接受模型传入的 `evidence` / `evidence_refs`（字符串或含 `ref/path/command/detail`
的对象），记为 `kind=manual` 的证据后再尝试完成步骤。用于“总结/分析”这类没有工具副作用、无法自动记录
read/file_change 证据的步骤；缺少证据的错误提示也会提示传 `evidence_refs`。最多取 10 条、每条截断 500 字符。

## 计划任务排队（2026-11-07 补充）

无人值守批量执行入口：

- `--prepare-plan-task --session SID --workspace WS`：取会话中最新可执行计划（`executable_plans()`：
  approved、hash 匹配、非 stale），创建 `kind=run/mode=act` + `plan_id/plan_version` 的待运行 task，不启动 worker；
- `--run-plan-queue --session SID --workspace WS`：按 `created_at` 顺序串行启动这些 task 的 `--worker`，
  全部依赖 workspace 锁自然串行；任一 task 未 `completed` 即停止并 exit 1（JSON 输出 ran/completed/stopped_early）；
- `--queue-task-timeout` 控制单个 worker 超时（默认 1800s，上限 24h）；
- 默认任一 task 未 completed 即停止；`--queue-continue-on-failure` 继续后续任务（输出 `failed` 计数，最终仍 exit 1）；
- 队列只处理 status=created 且带 plan 的 task，不会重跑已完成/失败任务；
- 执行前用 `find_plan` + `can_execute` 复核计划仍可执行（plan 被删/修订/过期时标记 `skipped_plan_unavailable` 并停止）；
- `--queue-dry-run` 只读输出队列与 `plan_executable`，便于预演；
- `--queue-all-sessions` 跨工作区内所有会话收集 created 计划 task（同一 workspace 锁自然串行），
  排序 `(created_at, workspace, session, task_id)`，输出含 `workspaces/sessions` 列表；
- `--queue-workspace PATH`（可重复）跨多个工作区串行调度，worker 在各自工作区执行；与 `--session` 互斥。
