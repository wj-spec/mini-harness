# Workbench settings
## Purpose
让用户按任务意图找到模型、权限、运行限制和高级状态，明确哪些更改作用于下一项任务、哪些需要重启。编辑过程保留对话草稿，任务恢复采用原有限制快照，配置写入沿用现有审批与私有原子保存。
## ADDED Requirements
### Requirement: Explicit limit changes
The system SHALL expose four validated per-run limits with configuration sources and separate save and apply actions.
#### Scenario: Override
- **WHEN** an environment or CLI value overrides the saved limit
- **THEN** the effective source is shown and application respects that override
#### Scenario: Denied or stale save
- **WHEN** approval is denied or the file changes while editing
- **THEN** the original file and current defaults are preserved
### Requirement: Stable execution limits
The system SHALL freeze per-run limits when execution begins and reuse the original snapshot for continuation.
#### Scenario: Resume after applying new defaults
- **WHEN** the user resumes a recorded task
- **THEN** the original limits govern that run and a later new task uses the updated defaults
### Requirement: Contextual settings navigation
The system SHALL preserve drafts, prioritize approval, support keyboard operation at 32 by 12 and expose only existing advanced actions.
#### Scenario: Advanced state
- **WHEN** the advanced section is opened
- **THEN** it shows sanitized status and restart requirements without changing permissions or launching external tools
