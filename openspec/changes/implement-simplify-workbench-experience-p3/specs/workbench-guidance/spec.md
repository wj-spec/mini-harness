# Workbench guidance
## Purpose
帮助首次使用和日常使用的人理解对话、计划、检视、任务会话与配置帮助五个区域。根据当前运行状态提供可执行的下一步，并保持用户的草稿、审批决定、阅读位置及运行控制权，避免引导成为新的对话噪声。
## ADDED Requirements
### Requirement: Optional guidance
The workbench SHALL expose an explicitly opened five-area guide and allow its contextual tips to be disabled and re-enabled within the current workbench instance.
#### Scenario: Open and return
- **WHEN** the user opens guidance with a draft and then returns
- **THEN** draft text, cursor and transcript reading position remain intact and no backend action is sent
#### Scenario: Disable and reopen
- **WHEN** tips are disabled
- **THEN** contextual tips disappear while critical approval controls and an explicit guide/help entry remain available
### Requirement: Safe context and navigation
The guide SHALL show one relevant next step, advertise only available actions, and never execute tasks or approve tools merely through reading or navigating guidance.
#### Scenario: Example with existing draft
- **WHEN** a user selects an example while a draft exists or a task is running
- **THEN** the example does not replace or submit that draft and the reason is visible
#### Scenario: Approval arrives
- **WHEN** a new approval arrives while guidance is open
- **THEN** the guide yields to the approval interface without sending a decision or reopening automatically
#### Scenario: Missing capability
- **WHEN** a backend lacks a referenced feature
- **THEN** the guide explains the limitation and does not offer an executable navigation action for it
### Requirement: Keyboard and compact layouts
The guide SHALL support mouse buttons and keyboard selection, visible focus, scrollable content and a 32 by 12 terminal without overflow.
#### Scenario: Narrow terminal
- **WHEN** the user opens the guide at 32 by 12
- **THEN** the current topic, selectable controls and return action remain visible and content can be scrolled
