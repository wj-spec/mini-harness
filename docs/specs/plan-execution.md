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
- 全部步骤完成后 harness 设置 tool_gate，拒绝后续非计划工具并提示停止；
  连续 2 次 gate 命中后 Agent 直接以 done 收尾，避免模型无限探索；
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
