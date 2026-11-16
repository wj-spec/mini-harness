# Startup configuration

## Purpose

让首次使用者仅提供模型连接所需信息即可启动工作台，并明确高级与开发配置的来源、默认行为和生效范围。减少无效配置与隐式覆盖带来的误解，同时保留安全保存、免 Key 演示和既有高级入口的兼容性。

## ADDED Requirements

### Requirement: Minimal first-run configuration
The launcher SHALL provide a default-service path requiring only an API Key after service selection, a custom-service path collecting endpoint and model, and a local Demo path requiring no Key.

#### Scenario: Default service
- **WHEN** the user selects the default service and supplies a Key
- **THEN** the launcher uses the built-in endpoint and model without an additional model-name prompt and presents the values before saving

#### Scenario: Custom service
- **WHEN** the user selects a custom service
- **THEN** the launcher collects a valid HTTP(S) endpoint, model and hidden Key before saving

#### Scenario: Local demo
- **WHEN** the user chooses Demo
- **THEN** the launcher neither requests a Key nor writes model configuration

### Requirement: Minimal template and accurate configuration guidance
The template SHALL contain only model connection settings and an optional proxy opt-in. Documentation MUST map every former template variable to its default, parser, consumer, scope, effective time, support status and validation evidence.

#### Scenario: Copy template
- **WHEN** a user copies the template and replaces only the Key placeholder
- **THEN** the default model configuration is valid and existing protection defaults remain enabled

#### Scenario: Advanced and unsupported variables
- **WHEN** a user needs a setting removed from the template
- **THEN** the documentation identifies its supported destination or explicitly states it is unsupported, without advertising a nonexistent in-app editor

### Requirement: Preserve configuration ownership and precedence
The launcher SHALL preserve CLI over shell over file precedence, parse the file as data, hide Keys and save atomically with owner-only permissions. Existing direct launch scripts SHALL continue to use process environment only.

#### Scenario: Cancel or failed save
- **WHEN** the user cancels configuration or atomic replacement fails
- **THEN** the old file remains unchanged and the launcher does not start using an unsaved configuration

#### Scenario: Shell aliases and command-line overrides
- **WHEN** shell Key or endpoint aliases and explicit model CLI arguments coexist with file values
- **THEN** shell aliases override their file group and explicit CLI arguments override corresponding environment values
