# 外置状态与完成审计（P0–P2 + 可选 LLM 审计）

日期：2026-11-01。状态：工程实现与确定性回归完成；真实模型、LLM 审计质量、Ink `/goal` 体验与 benchmark 未验收。

## 用户目标

长程任务不再以“模型说做完了”作为完成依据：

1. harness 能判断“有文件改动但没有覆盖最后一次改动的验证”，并在收尾前给出一次可执行纠偏；
2. 文件改动有可追溯的元数据（路径、前后 hash、步骤、工具调用），但不复制文件正文；
3. 每次 run 结束写一轮不可变记录，可回看、可恢复、可审计；
4. 收尾时做一次只读环境审计，给出 `verified` 与完整性结论。

P0–P2 只做确定性、本地、只读的判定；P3 前置的可选 LLM 只读审计默认关闭。契约反查、fresh-context 执行器与 undo 仍后置。

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

### 6. 可选 LLM 只读审计（P3 前置）

- `VIBE_LLM_AUDITOR=1` 且确定性守卫通过后，启动一个**全新上下文**的子 Agent 审计本轮完成声明；默认关闭，会增加一次模型调用。
- 子 Agent 只拿到本轮任务/声明/状态/证据摘要与确定性审计结论，不接收父 Agent 的对话轨迹、工具轨迹或内部推理。
- 工具层收窄为只读集合 `read_file` / `grep` / `list_files` / `git_status` / `git_diff`；没有写文件、`bash`、MCP、Web 工具，因此不是靠提示词约束。
- 子 Agent 输出前三行 `Status: complete|incomplete|blocked`、`Integrity: clean|suspect|violation`、`Contract audit: aligned|unknown|needs_revision|invalid`；缺少合法控制头时 fail closed。
- **契约反查**：审计者必须先从原始任务独立重建验收约束清单（最终消费者/状态载体、权威输入、候选选择、字段/文件/路径、保存/提交/持久化边界、格式/精确文本、不能改/漏/多做、非目标保持、禁止捷径），再输出 `Acceptance-constraint backcheck:` 段：`Contract conclusion`、约束清单、覆盖检查、逐条回查、`Blocking constraints`、建议修订。第三行 `Contract audit` 必须与 `Contract conclusion` 一致。
- 严格模式（`VIBE_CONTRACT_BACKCHECK=1`，默认）：必须 `Contract conclusion=aligned`、无 blocking、且控制行一致；否则即使 `Status=complete` 也降级为 `incomplete`，不保留 `verified=true`。`VIBE_CONTRACT_BACKCHECK=0` 时允许 `unknown`（仍不允许 `needs_revision/invalid`）。
- 只有 `Status=complete + Integrity=clean + 契约审计通过` 且确定性审计通过时，保留 `verified=true`；拒绝、非法报告或调用异常会把本轮标为 `verified=false`，并在回执与 `/evidence` 中显示 `LLM 审计`。
- 确定性审计不通过时跳过 LLM 审计；eval/bench 固定关闭。
- `contract_audit` 由 LLM 审计者给出；确定性审计自身仍固定 `unknown`，不能用它宣称“需求已被审计”。

## 数据结构

```text
CompletionDecision { allowed, codes[], messages[] }
TaskState.file_changes[] / verifications[] / mutation_seq / last_verification_seq / last_verification_ok
AuditReport { status, integrity, verified, findings[], unattributed[], changed_files, coverage, contract_audit, llm? }
AuditRequest { task, claim, state_summary, file_changes[], verifications[], deterministic_audit? }
RoundRecord { schema_version, run_id, round_index, status, task_kind, verified, completion, audit, task_state, ... }
```

确定性审计的 `contract_audit` 固定 `unknown`；可选 LLM 审计会在 `audit["llm"]` 中返回自己的控制头与结论。不能用任一字段宣称“需求已被审计”。

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
- [x] LLM 只读审计：工具子集只读、全新上下文、三行控制头解析、确定性审计失败降级、拒绝/异常标记未验证。
- [x] 契约反查：验收约束段解析、结论/控制行一致性、blocking 检测、严格模式降级、可关闭严格模式。
- [x] 全量 Python 回归与 TUI 定向测试通过（2026-11-01）。
- [ ] 真实模型任务验证守卫纠偏是否可执行、是否引入过多额外轮次。
- [ ] 真实模型下 LLM 审计的误杀率、额外 token 成本与延迟。
- [ ] 真实终端 `/goal` 下未验证完成提示的体验验收。

## 工程证据

- `tests/test_completion_guard.py`
- `tests/test_file_changes.py`
- `tests/test_rounds.py`
- `tests/test_audit.py`
- `tests/test_auditor.py`
- 全量回归：`./run_tests.sh -q`（2026-11-01，LLM 审计切片后退出 0）。

## 边界与未做

- 第二次仍无证据时只标 `verified=False`，不强制阻止结束；是否改为 `stopped` 需真实使用反馈。
- bash 归因不精确：只记命令结果，不逐文件归因；审计靠 run 前后 git 基线兜底。
- 非 git 工作区只能验证已记录 hash，不能发现未记录改动。
- 8MB 以上文件、符号链接、目录变化标记为无法核验，可能导致 `suspect`。
- LLM 只读 auditor 已实现但默认关闭，且只做一次、不参与重规划循环；其 token/时间消耗目前不计入父 run 预算，后续需要聚合。契约反查已实现为审计者的一段严格检查，但“从自然语言需求恢复完整验收清单”的质量仍依赖模型与真实任务验收；fresh-context 执行器、undo/rollback 仍未实现，也不存文件正文。
- 交互入口默认开启守卫/审计；LLM 审计需显式 `VIBE_LLM_AUDITOR=1`；eval/bench 固定关闭。
- 所有写文件/执行命令仍经 `ApprovalGate`；审计只读，不绕过审批。
