# 外置状态与完成审计（P0–P2）

日期：2026-10-31。状态：工程实现与确定性回归完成；真实模型、Ink `/goal` 体验与 benchmark 未验收。

## 用户目标

长程任务不再以“模型说做完了”作为完成依据：

1. harness 能判断“有文件改动但没有覆盖最后一次改动的验证”，并在收尾前给出一次可执行纠偏；
2. 文件改动有可追溯的元数据（路径、前后 hash、步骤、工具调用），但不复制文件正文；
3. 每次 run 结束写一轮不可变记录，可回看、可恢复、可审计；
4. 收尾时做一次只读环境审计，给出 `verified` 与完整性结论。

本切片只做确定性、本地、只读的判定。LLM 审计者、契约反查、fresh-context 执行器与 undo 仍后置。

## 行为

### 1. 完成守卫（P0）

- 仅对 `classify_task == action` 且有 `TaskState.file_changes` 的任务生效。
- 只有“成功的验证命令（`bash(verification=true)`、exit=0）覆盖最后一次文件改动”才允许 `verified=True`。
- 失败/缺失时：
  - 第一次模型声称完成：拒绝收尾，注入一条 `completion_guard` system 消息，要求运行验证或说明阻塞；
  - 第二次仍无证据：以 `status="done"` 收尾，但 `verified=False`，`receipt` 写明未验证原因。
- QA 任务、没有实际改动的任务不启用守卫，避免误伤只读任务；此时 `verified` 为 `null`，不宣称“已验证”。
- 可通过 `VIBE_COMPLETION_GUARD=0` 关闭。

### 2. 文件变动记录（P1）

- 对 `write_file` / `edit_file` / `apply_patch`：审批通过后、执行前记 before 元数据；执行成功后记 after 元数据。
- 每条记录：`path`、`kind`（create/modify/delete）、`before_sha256`、`after_sha256`、`before_status`、`after_status`、`size`、`step`、`tool`、`tool_call_id`、`approval_scope`、`at`。
- 只存 hash 与元数据，不存文件正文；内容仍由 git 与 `.vibe/backups/` 持有。
- 相同内容重写不产生记录、不增加 `mutation_seq`。
- bash 造成的文件变化不做逐条归因；由环境审计在 run 结束时按 git 基线发现，标记为未记录改动。

### 3. 本轮增量与验证记录

- 完成守卫、环境审计、交付回执与轮次记录都只统计**本次 run 的增量**（run 起点保存 `mutation_seq / file_changes / verifications / files_changed / command_results` 计数并切片）；上一轮的改动不会让本轮被判 `verified=true`，也不会让本轮纯问答触发守卫。
- 验证记录：

- `TaskState.verifications` 记录每次 `verification=true` 的命令：command、status、exit、output digest、step、记录时的 `mutation_seq`、时间。
- `TaskState.mutation_seq` 每次有实际文件变化 +1；`last_verification_seq` / `last_verification_ok` 供守卫判定。
- 守卫原因码：`no_verification`、`mutation_after_verification`、`last_verification_failed`。
- `TaskState.render()` 在状态消息里显示最近文件变化与验证尝试；有阻塞时显示 `Completion guard: blocked (...)`，不把模型自述当作证据。

### 4. 轮次账本（P1）

- `Agent` 可选 `on_round` 回调；交互入口传 `SessionStore.save_round`。
- 每次 run 结束写 `.vibe/sessions/<id>/rounds/round_XXX.json`，轮次编号递增、原子写、不可变。
- 记录含：schema version、run_id、goal、task_kind、status、stop_reason、steps、usage、cost、开始/结束时间、verified、completion、audit、紧凑 task_state、receipt。
- `task_state` 只含证据引用；不复制 command output 与文件正文。
- `state.json` 增加 `state_version: 2`；加载时兼容旧格式。

### 5. 只读环境审计（P2）

- `workspace_audit` 开启时，run 开始记录 git 基线（git 状态中的脏/未跟踪文件路径 + 当时 hash）。
- run 结束、模型声称完成时执行只读审计：
  - 记录的每个 `after_sha256` 必须与当前文件一致；删除状态必须仍然缺失；
  - 当前 git 脏/未跟踪文件若不在记录内、且相对基线发生变化，列为 `unattributed`，完整性记为 `suspect`；
  - 审计期间（前后两次快照）工作区变化记为 `violation`；
  - 非 git 工作区给出 `coverage="limited"`，只能核对已记录 hash。
- 审计结论：`status=complete/incomplete/blocked`、`integrity=clean/suspect/violation`、`verified`。
- 审计只读：不写文件、不执行命令；运行验证命令仍由模型工具 + `ApprovalGate` 决定。
- 可通过 `VIBE_WORKSPACE_AUDIT=0` 关闭。

## 数据结构

```text
CompletionDecision { allowed, codes[], messages[] }
TaskState.file_changes[] / verifications[] / mutation_seq / last_verification_seq / last_verification_ok
AuditReport { status, integrity, verified, findings[], unattributed[], changed_files, coverage, contract_audit }
RoundRecord { schema_version, run_id, round_index, status, task_kind, verified, completion, audit, task_state, ... }
```

`contract_audit` 当前固定 `unknown`，因为本切片没有实现契约反查；不能用它宣称“需求已被审计”。

## 验收

- [x] 核心守卫：无验证、验证后改动、验证失败、覆盖验证四种判定。
- [x] Agent：第一次未验证完成被拒绝，第二次以 `verified=False` 收尾。
- [x] Agent：写文件 + 成功验证后 `verified=True`，receipt 区分已验证/未验证。
- [x] 文件变动：只记录 hash/元数据，不含正文；相同内容重写不算改动。
- [x] 轮次：递增编号、原子写、损坏记录跳过、旧 `state.json` 兼容。
- [x] 审计：hash 一致为 clean；记录后外部改动为 suspect；未归因新文件为 suspect；非 git 为 limited；Agent 端到端可识别未记录 bash 改动。
- [x] 全量 Python 回归 `./run_tests.sh -q` 退出 0。
- [x] Ink `/goal` 逻辑回归：`verified=false` 时暂停而不是标记完成（`node --test tui/dist/tests/goal.test.js` 6 项通过）。
- [x] eval 入口未改：Agent 默认不启用守卫/审计，全量回归覆盖既有 eval 用例。
- [ ] 真实模型任务验证守卫纠偏是否可执行、是否引入过多额外轮次。
- [ ] 真实终端 `/goal` 下未验证完成提示的体验验收。

## 工程证据

- `tests/test_completion_guard.py`
- `tests/test_file_changes.py`
- `tests/test_rounds.py`
- `tests/test_audit.py`
- 全量回归：`./run_tests.sh -q`（2026-10-31，退出 0）。

## 边界与未做

- 第二次仍无证据时只标 `verified=False`，不强制阻止结束；是否改为 `stopped` 需真实使用反馈。
- bash 归因不精确：只记命令结果，不逐文件归因；审计靠 run 前后 git 基线兜底。
- 非 git 工作区只能验证已记录 hash，不能发现未记录改动。
- 8MB 以上文件、符号链接、目录变化标记为无法核验，可能导致 `suspect`。
- 未实现 LLM 只读 auditor、契约反查、fresh-context 执行器、undo/rollback，也不存文件正文。
- 交互入口默认开启；eval/bench 保持旧行为；`oneshot` 非 bench 模式开启。
- 所有写文件/执行命令仍经 `ApprovalGate`；审计只读，不绕过审批。
