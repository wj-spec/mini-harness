# Proposal
## Why
前台与后台 goal 预算来源不一致，手动续做缺少统一预算门。设置页需要可靠的新目标默认值。
## What Changes
- /settings 增加目标总段数、总时长、总 token、估算总费用，保存和应用分离。
- 后端在新目标创建时冻结预算；手动、自动、后台续段共同核对任务累计账本。
- 旧目标迁移匹配的历史预算，无法补证时固定启动默认并提示；损坏记录拒绝续做。
- 前台展示后端任务预算与累计状态，后台交接不重置预算。
## Capabilities
### New Capabilities
- goal-budget-settings: 目标预算设置与前后台一致恢复。
### Modified Capabilities
无。
## Impact
runtime/settings、TaskStore/TaskRuntime、worker、stdio、Ink goal 与设置、测试文档；不实现 MCP 热重连。
