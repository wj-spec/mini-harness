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

## 未完成

- 从 `goal-record.json`/Plan 加载并生成 PlanAcceptance；
- harness 通过 ApprovalGate 执行可信验证命令；
- 失败回灌、有界重试、合并后重验；
- 三态结论写入 rounds/`/evidence` 与 goal 完成判定；
- 模型提议 criteria 的 `confirmed=false` 流程接 TUI plan.review。

## 测试

`tests/test_acceptance.py`：true/false/null、uncovered、manual、untrusted、非法 schema。
