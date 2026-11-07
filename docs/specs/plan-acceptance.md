# 计划验收映射 P0（criteria → verification plan → 三态）

状态：2026-11-06 P0 纯模块实现（映射/校验/三态）；2026-11-07 已接入绑定计划完成门、可信命令执行（含重试/超时）、结果持久化与合并后重验。
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

- `run_verification_plan(acceptance, workspace, approval_gate, retries=0, timeout=300)`：
  - 只执行 `source ∈ project_profile/plan_approval` 的命令；
  - 每条命令走 ApprovalGate.request（risk=exec）；拒绝则不执行；
  - 通过 `run_command_async` 执行，记录 exit/timed_out/digest/output；
  - 汇总 `acceptance.status(results)` 三态与原因码；
  - 单命令超时可用 `VIBE_ACCEPTANCE_TIMEOUT_SECONDS`（默认 300，范围 1–3600）；
  - 整轮验收可用 `VIBE_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS`（默认单命令超时×尝试次数+30；
    超时 run 转 stopped/acceptance_timeout）。
- `trusted_commands()`、`command_digest()`。
- 测试：整轮验收超时 → stopped/acceptance_timeout。
- 测试：成功/失败、不可信命令不执行、审批拒绝不执行、digest。

## 集成

- 绑定 plan 的 run 完成条件：
  1. execution ledger 的 required 步骤全 done；
  2. 若 Plan 有 confirmed required criteria，则从 Plan 生成 PlanAcceptance，
     通过 `run_verification_plan` 执行可信命令；
  3. 验收通过时 run 标为 `verified=True`（harness 可信验证强于模型自证；避免 edit 计划任务因 `unverified_completion` 被暂停），任务按 completed 收口；
  4. 验收结果写入**计划所属任务目录** `acceptance.v<version>.json`（原子替换；后台 plan task 同样如此），并写本地通知
     `plan_acceptance`（task/plan/version/status/codes）；
     验收非 `True` → run 改为 `stopped/acceptance_<code>`，任务 paused；
     结果文本附失败命令 id/command/exit/输出摘要（最多 5 条），供回灌/排查。
- Plan schema 增加 `verification_plan` / `trusted_source`；submit_plan 可提交。
- 合并后重验：`./run_vibe.sh --merge-task` 时，若任务绑定 approved 且未 stale 的计划，
  自动取可信验收命令（显式 `--merge-verify` 优先）在合并后的主工作区重跑；
  命令会列在合并审批预览中；失败写 `merge_verification_failed` 通知并 exit 1。
- 验收失败自动修复（有界回灌）：`VIBE_ACCEPTANCE_REPAIR_ATTEMPTS`（0=关闭，最大 3）
  开启后，验收非 True 时把失败命令与输出作为新一轮用户消息回灌同一 Agent，
  并进入修复模式：允许只读工具、计划 edit 步骤范围内的写工具；若计划含 run/verify 步骤再允许 bash。
  修复后重新验收；通过则 run 为 done，仍失败且在次数内继续，用完则 `stopped/acceptance_<code>`。
  每轮写 `plan_repair` 通知（task/plan/attempt/stop_reason）；总 steps/usage/cost 汇总到最后结果。
  只对验收失败生效：`plan_incomplete`、`acceptance_timeout` 不触发修复。
- 真实模型证据：`scripts/real_p1_acceptance.py --repair` 连续 2/2 通过（修复创建 marker → 重新验收 acceptance=true），见 [真实 P1 记录](real-p1-plan-smoke-2026-11-07.md)。

## 未完成

- `/plan acceptance` 可查看 criteria 与 verification plan 清单（不执行）；
- 已执行结果可在任务目录 `acceptance.v<version>.json` 核对；
- `/plan acceptance` 若已有结果会追加 status/codes 与每条命令 ok/attempts。
- 从 GoalRecord 生成 PlanAcceptance（当前从 Plan 生成）；
- 合并后重验已接入 merge 门禁（见下文）；
- rounds/`/evidence` 展示验收结果；
- 三态结论写入 rounds/`/evidence` 与 goal 完成判定；
- 模型提议 criteria 的 `confirmed=false` 流程接 TUI plan.review。

## 测试

`tests/test_acceptance.py`：true/false/null、uncovered、manual、untrusted、非法 schema。
