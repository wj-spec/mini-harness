# Run outcome presentation

## Purpose

让用户在连续对话中只看到需要关注的运行结果，同时能追溯完整工具、变更与验证证据；明确区分运行结束、检查通过与需求满足，并保持历史和接入视图一致。

## ADDED Requirements

### Requirement: Quiet successful conversations
系统 SHALL 对成功问答和无异常的只读任务保留回答及证据入口，不追加独立终态回执卡。
#### Scenario: Read-only answer
- **WHEN** 任务成功完成且无变更、操作影响或异常
- **THEN** 不追加独立回执卡，用户仍能查看证据

### Requirement: Evidence-based compact results
系统 SHALL 基于实际证据展示修改、检查、操作影响和异常，不通过自然语言或任务措辞推断成功。
#### Scenario: Repeated edit
- **WHEN** 同一文件在本轮再次修改
- **THEN** 摘要包含该文件，验证未覆盖最新修改时提示需重新验证
#### Scenario: No diff operation or denial
- **WHEN** 操作无文件变化、影响无法确认、工具失败或拒绝
- **THEN** 保留相应结果或提醒，不按只读成功隐藏
#### Scenario: Stopped run
- **WHEN** 任务错误、取消或因预算停止
- **THEN** 显示原因、已保留影响和证据入口，不声称需求已完成

### Requirement: Consistent and compatible result facts
系统 SHALL 在实时、状态、检视、历史及接入中使用同一结果事实；旧记录只提供保守的原始证据。
#### Scenario: Plan acceptance overrides early result
- **WHEN** 计划验收发生在 Agent 回复之后
- **THEN** 所有结果区域反映最终计划验收，不沿用提前成功
#### Scenario: Legacy or malformed result
- **WHEN** 记录无已知结构字段或格式不合法
- **THEN** 不从文字推断通过，提供原始记录或证据不足提示

### Requirement: Task-level continuation
系统 SHALL 对自动续段显示进度而非重复终态卡，并在最终结果中保留同一任务的前段影响。
#### Scenario: Continue and reconnect
- **WHEN** 调度方确认自动续段，随后发生接入或回放
- **THEN** 不重复追加该段终态摘要，完整分段证据可查看
#### Scenario: Final segment has no changes
- **WHEN** 前段有修改但最终段只回答
- **THEN** 最终结果仍包含前段影响，未覆盖的失败/验证不被新回答消除
