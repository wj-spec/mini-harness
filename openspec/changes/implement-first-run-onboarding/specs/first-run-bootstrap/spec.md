## ADDED Requirements

### Requirement: Config-only startup state
When the interactive stdio backend starts without a usable model key, it SHALL enter a config-only state that exposes configuration overview, approval-gated save and explicit detection, and SHALL NOT create an Agent, task runtime or session.
#### Scenario: Missing or placeholder key
- **WHEN** `./start.sh` runs in a terminal and no key or only a placeholder key is configured
- **THEN** Ink opens the first-run page instead of a terminal questionnaire
- **WHEN** the same backend receives `run`, `resume`, `cancel` or `steer`
- **THEN** it returns a clear unavailable error without queueing work or writing a session

#### Scenario: Non-interactive launch
- **WHEN** stdin is not a terminal and configuration is missing
- **THEN** the launcher exits non-zero with executable commands for interactive launch, `--configure` and `--demo`

### Requirement: Approval-gated save and detection
Saving and testing model settings in the config-only state SHALL each go through ApprovalGate, and the API key SHALL never be returned to the frontend or written to previews, logs or process arguments.
#### Scenario: Save then detect
- **WHEN** the user chooses save-and-detect
- **THEN** a successful save is reported even if the later detection is denied or fails
- **WHEN** the user edits the key, address or model after a save
- **THEN** the previous detection result no longer counts as current

### Requirement: Capability switch on entering the project
The config-only state SHALL provide an explicit activation command that validates the saved local configuration and switches to the real runtime by re-executing the backend with the saved environment.
#### Scenario: Enter project
- **WHEN** saved configuration is locally valid and the user confirms entering the project
- **THEN** the backend re-executes itself and the new ready event provides the full runtime capabilities
- **WHEN** saved configuration cannot be parsed or lacks a usable key
- **THEN** activation is rejected, the page keeps the draft and no session is created

### Requirement: Configuration source honesty
Sources, saved state and effective state SHALL be reported separately; empty or placeholder environment keys SHALL count as unconfigured and SHALL NOT be presented as environment overrides of a newly saved file key.
#### Scenario: Placeholder shell key
- **WHEN** the shell exports an empty or placeholder key while a real key is saved in `.env`
- **THEN** the first-run page reports the file source and activation uses the saved key
