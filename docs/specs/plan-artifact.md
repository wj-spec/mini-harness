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
- `submit_plan` 工具（仅 plan profile 的 run 注册）：
  - 模型提交 objective/steps/criteria 等，校验后写任务目录并置 proposed；
  - 返回 `plan proposed: <plan_id> v1`，不执行工作区写；
  - run 结束注销，act/chat 不可见。
- 测试：`tests/test_plan_artifact.py`（roundtrip/hash/原子写、6 类非法输入、环检测、
  unknown schema、渲染、路径布局、submit_plan 工具写 proposed）。
- TUI `/plan`：
  - `/plan <任务>` 发送 `kind=run, mode=plan`；
  - `/plan` 打开计划覆盖层（默认折叠：目标/状态/验收标准 + 前 3 步，提示共 N 步）；
  - `/plan all` 打开完整步骤覆盖层；
  - 覆盖层支持 ↑↓ 选择、s 查看选中步骤、a 查看验收标准、e 预填修订命令、滚轮/PgUp/PgDn 翻阅、Esc 返回；
  - proposed 计划可用 `y` 批准 / `n` 拒绝（发 backend plan command）；
  - approved 计划按 Enter 直接执行（`/plan execute` 等价）；执行时选择**最新可执行**的 approved 非 stale 版本，
    最新版本不可执行会提示回退版本；无可执行版本时提示批准/修订；
  - `/plan list` 列出最近版本/状态；`/plan step <id>` 或覆盖层 `s` 查看单步定义与 execution 证据；
  - `/plan diff <a> <b>` 比较两个版本的目标/状态/步骤/审批差异；
  - `/plan acceptance` 查看验收标准与可信验证命令清单（不执行）；
  - `/plan edit-step <id> <title>` 修改步骤标题并生成 version+1 draft；
  - 完整审批卡片与右侧 sidebar 仍待。
  - `/plan approve`、`/plan reject` 通过后端 `plan` command 处理最近计划（stdio 本地 user 决定）。
  - `/plan execute` 读取最近 approved 计划，提交带 `plan_id/plan_version` 的 act run。

## 未完成（P0 剩余）

- `submit_plan` 已接 plan profile；TUI `/plan` 展示/审批未接；
- plan.review 审批门（13）与 `plan_proposed/plan_approved` 审计事件；
- 执行绑定/步骤证据（14）、计划验收（15）；
- TUI `/plan` 覆盖层已能折叠/展开、方向键选择、y/n 审批；打开计划时把 plan 摘要写入 sidebar signal，
  侧栏 header 显示 `计划 vN status done/total步`，有验收结果时追加 ✓/✗；
  按 `p` 打开计划步骤面板（↑↓/j/k 选择、p 返回变更）；
- legacy `TaskState.plan` 合成 legacy 计划；
- 预算/审批字段的执行层校验（D6/D9）。

## 存储约定

```text
.vibe/sessions/<session>/tasks/<task_id>/plan/plan.v1.json
```

每版本不可变；审批通过后只新增版本，不回写旧版本；`revise_plan` 生成 version+1 draft 并保留旧版。
