# 计划验收映射 P0（criteria → verification plan → 三态）

状态：2026-11-06 P0 纯模块实现（映射/校验/三态）；harness 执行验证命令与完成门待后续。
设计来源：[15 计划验收](../design/long-horizon/15-plan-verification.md)。

## 已实现

`app/runtime/acceptance.py`：

- `AcceptanceCriterion`：
  - id/text/required/confirmed/manual/source；
  - 只有 `required && confirmed` 参与判定。
- `VerificationCommand`：
  - id/covers/command/kind/expect/source；
  - covers 必须非空且引用 criterion。
- `PlanAcceptance.from_dict` 校验：
  - criteria/verification id 唯一；
  - command 非空；
  - trusted_source ∈ project_profile/plan_approval，未知 fail closed。
- `status(results)` 返回 `(True|False|None, codes)`：
  - 无已确认 required → `None/no_confirmed_criteria`；
  - 未覆盖 criterion → `False/acceptance_uncovered`；
  - 任一覆盖命令失败 → `False/acceptance_failed`；
  - manual 项 → `None/manual_pending`（不虚假宣称 verified）；
  - 全部通过 → `True`。
- 成本/执行/完成门未接。

## 已实现（执行）

`app/runtime/verification.py`：

- `run_verification_plan(acceptance, workspace, approval_gate)`：
  - 只执行 `source ∈ project_profile/plan_approval` 的命令；
  - 每条命令走 ApprovalGate.request（risk=exec）；拒绝则不执行；
  - 通过 `run_command_async` 执行，记录 exit/timed_out/digest/output；
  - 汇总 `acceptance.status(results)` 三态与原因码。
- `trusted_commands()`、`command_digest()`。
- 测试：成功/失败、不可信命令不执行、审批拒绝不执行、digest。

## 未完成

- 从 `goal-record.json`/Plan 加载并生成 PlanAcceptance；
- 失败回灌、有界重试、合并后重验；
- 完成判定与 rounds/`/evidence` 接线；
- 三态结论写入 rounds/`/evidence` 与 goal 完成判定；
- 模型提议 criteria 的 `confirmed=false` 流程接 TUI plan.review。

## 测试

`tests/test_acceptance.py`：true/false/null、uncovered、manual、untrusted、非法 schema。
