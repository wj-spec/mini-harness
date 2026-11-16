# Proposal
## Why
阶段 5 需要把分散的高级入口按场景组织。先交付设置中心和可靠的单段限制，避免编辑环境变量时不清楚生效范围。
## What Changes
- /settings 集中任务限制、模型、权限与高级状态。
- 四项单段默认限制可编辑、审批保存与空闲应用，来源可见。
- 开始执行时保存限制快照，旧任务/续段恢复原限制，新任务使用新默认。
- MCP、沙箱、通知只展示已核实状态和现有配置路径；goal 总预算、MCP 热重连为阶段 5 后续切片。
## Capabilities
### New Capabilities
- workbench-settings: 设置中心和单段限制。
### Modified Capabilities
无。
## Impact
app/runtime、stdio、启动来源元数据、Ink 与文档；不重做权限或改变安全默认值。
