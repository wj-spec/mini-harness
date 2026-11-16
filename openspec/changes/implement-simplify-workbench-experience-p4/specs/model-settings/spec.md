# Model settings
## Purpose
让用户在工作台内核对当前模型、配置来源和保存位置，编辑连接信息并显式检测、保存或应用。保护凭据和正在运行的任务，避免将保存成功混同于当前生效，保持原有启动入口和环境覆盖规则。
## ADDED Requirements
### Requirement: Explicit configuration actions
The system SHALL separate viewing, editing, saving, testing and applying model configuration.
#### Scenario: Save under override
- **WHEN** CLI or process environment overrides a saved value
- **THEN** the effective source remains visible and saving does not pretend to change the active client
### Requirement: Credential and execution safety
The system SHALL hide credentials, gate file writes and probes, and refuse active-run client replacement.
#### Scenario: Probe
- **WHEN** the user explicitly confirms a connection probe
- **THEN** only a fixed minimal message is sent and the result does not expose credentials or provider error bodies
#### Scenario: Stale file
- **WHEN** the configuration file changed after editing
- **THEN** saving is refused and the draft and current client remain intact
### Requirement: Safe recovery
The system SHALL retain non-secret model configuration in run records and reject recovery with a conflicting saved snapshot.
#### Scenario: Resume after switching
- **WHEN** a recorded task is resumed under different model settings
- **THEN** it is stopped before a model request and explains how to restore the recorded configuration
### Requirement: Usable terminal editor
The system SHALL preserve conversation drafts, support keyboard interaction and remain usable at 32 by 12.
#### Scenario: Return
- **WHEN** the user leaves model settings
- **THEN** the original conversation draft remains intact and no task is automatically sent
