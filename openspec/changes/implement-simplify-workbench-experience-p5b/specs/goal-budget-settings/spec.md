## ADDED Requirements
### Requirement: Editable future goal defaults
The workbench SHALL allow viewing, editing, approval-gated saving and idle application of future goal limits while preserving environment precedence and existing task snapshots.
#### Scenario: Apply defaults
- **WHEN** the user applies valid goal defaults while idle
- **THEN** future goals use them and existing goals retain their snapshots
#### Scenario: Save rejected or stale
- **WHEN** approval is denied or the configuration file changes
- **THEN** the file and active defaults remain unchanged and the draft is retained
### Requirement: Shared continuation boundary
The backend SHALL check immutable per-task segment, elapsed-time, cumulative-token and estimated-cost limits before starting any further goal segment, including manual resume and worker continuation.
#### Scenario: Exhausted budget
- **WHEN** any recorded goal limit is reached
- **THEN** no new segment or model request starts and the user sees the reason
#### Scenario: Handoff or restore
- **WHEN** an existing goal is continued in another process
- **THEN** its original budget, start time and cumulative usage remain authoritative
#### Scenario: Legacy or corrupt record
- **WHEN** a legacy goal lacks a snapshot
- **THEN** matching historical values or startup defaults are frozen with a compatibility notice
- **WHEN** a present snapshot is corrupt
- **THEN** continuation is rejected without resetting limits
### Requirement: Accessible grouped settings
The settings panel SHALL keep per-group drafts, clear units and scope feedback, keyboard access and compact layout.
#### Scenario: Return from editing
- **WHEN** the user changes groups or visits a child panel and returns
- **THEN** edits are retained and leaving warns about unsaved changes
