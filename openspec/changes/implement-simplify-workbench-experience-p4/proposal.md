# Proposal
## Why
首次配置已简化，但运行后仍需退出编辑模型。阶段 4 提供应用内配置，并明确保存、检测和应用的区别。
## What Changes
- /model 提供来源、地址、模型、隐藏 Key 与代理编辑；保存沿用安装目录 .env。
- 显式连接检测只发最小固定请求，提示可能计费；不发送工作区或会话内容。
- 保存与当前应用分开；空闲应用，运行中只编辑。CLI/进程环境覆盖继续优先。
- 续做核对非敏感配置快照；变更时拒绝静默换模型，提示恢复配置。
## Capabilities
### New Capabilities
- model-settings: 工作台内模型配置。
### Modified Capabilities
无。
## Impact
app/runtime、启动配置来源、stdio、Ink 面板与文档；不实现阶段 5。
