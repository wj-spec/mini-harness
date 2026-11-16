# Proposal

## Why

首次使用的环境模板混合模型连接、权限、预算和测试开关，增加理解成本，且存在未接入的输出上限参数。落实体验简化路线图阶段 2，让最小配置可用，所有移出的项有准确去向。

## What Changes

- 默认模板只保留 Key、可选地址/模型和代理开关；Demo 不需要 Key。
- 默认服务向导直接采用项目内置地址与模型，只询问 Key；自定义服务仍填写地址和模型。
- 配置说明分为用户高级设置、开发测试参数与原模板配置契约，修正未支持的输出上限说明。
- 同步 README、使用指南和启动帮助，验证覆盖、保存、取消、失败与免 Key 路径。
- 保留现有配置解析、运行保护默认值及高级入口的环境加载语义；暂不实现应用内设置。

## Capabilities

### New Capabilities
- `startup-configuration`: 首次模型配置、最小模板与配置来源/生效范围的可解释契约。

### Modified Capabilities

无现有主规格变更。

## Impact

.env.example、scripts/start.py、tests/test_start.py、配置文档与导航。无需新依赖，不改变协议或模型请求；不修改用户现有 .env。
