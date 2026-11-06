# 结构化 Plan 产物 P0（schema / 存储 / 渲染）

状态：2026-11-06 P0 实现（纯模块 + 测试）；submit_plan/审批/执行绑定待后续。
设计来源：[12 结构化 Plan 产物](../design/long-horizon/12-plan-artifact.md)。

## 已实现

- `app/runtime/plan.py`
  - `Plan` / `PlanStep` dataclass（plan_id/task_id/version/status/objective/source/
    acceptance_criteria/budget/approval/steps 等）；
  - `Plan.from_dict` 严格校验：
    - 必填 objective/plan_id/task_id；
    - step_id 唯一、depends_on 必须存在且无环；
    - files 必须是相对路径（拒绝绝对路径/`..`）；
    - step kind ∈ read/edit/run/verify，risk ∈ read/write/exec/network；
    - acceptance_criteria id 唯一、confirmed 必须 bool；
    - 未知 schema_version fail closed。
  - `save_plan` 原子写 `task_dir/plan/plan.v<version>.json`；
  - `load_plan` / `load_plan_ref`；
  - `plan_hash()`（canonical JSON sha256）；
  - `render_plan()`：目标、状态、验收标准（候选/已确认）、全部步骤（不截断前 5 条）。
- 测试：`tests/test_plan_artifact.py`（roundtrip/hash/原子写、6 类非法输入、环检测、
  unknown schema、渲染、路径布局）。

## 未完成（P0 剩余）

- `submit_plan` 工具与 plan task 接线（仅 plan profile 可见）；
- plan.review 审批门（13）与 `plan_proposed/plan_approved` 审计事件；
- 执行绑定/步骤证据（14）、计划验收（15）；
- TUI `/plan` 渲染与编辑入口；
- legacy `TaskState.plan` 合成 legacy 计划；
- 预算/审批字段的执行层校验（D6/D9）。

## 存储约定

```text
.vibe/sessions/<session>/tasks/<task_id>/plan/plan.v1.json
```

每版本不可变；审批通过后只新增版本，不回写旧版本。
