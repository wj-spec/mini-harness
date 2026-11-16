# Proposal

## Why

首次启动在缺模型 Key 时停在终端问答，用户还没看到界面就被要求配置；验收环境启动即请求 MCP 启动审批；首屏同时铺开路径、模型、授权、上下文与统计。用户已批准[首次使用与真实任务入口方案](../../../docs/specs/first-run-onboarding-ui-ux.md)（2026-11-14），要求按 A–E 批次持续实施。

## What Changes

按方案 A–E 分批落地，每批先补测试，再改运行行为：

- A 启动状态与入口基础：缺配置也可进入 Ink；后端新增仅配置状态（不创建 Agent/会话、拒绝 run/resume）；配置服务与活跃 Agent 解耦；保存/检测/进入项目的能力契约。
- B 首次配置 UI：服务与 Key 表单、渐进展开、保存并检测、错误/取消、项目确认、初始化真实环境。
- C 首页与设置中心：配置摘要、真实任务示例、信息降噪、设置按目的分组、返回恢复。
- D MCP 主动连接：前台不自动连接，服务列表/详情、显式范围、连接与工具调用审批分离。
- E 完整旅程：全量回归、真实模型与终端、文档与真人体验交接清单。

## Capabilities

### New Capabilities

- first-run-bootstrap: 正常启动在缺必要配置时进入 Ink，并由仅配置后端提供状态、保存与检测；准备好后原子切换到真实运行环境。
- onboarding-home: 首次配置页、项目确认与已配置首页的信息层级。

### Modified Capabilities

- model-settings: 复用来源/校验/检测/保存，但不再要求已有运行 Agent。
- mcp-startup: 前台交互式启动默认不自动连接（D 批实施）。

## Impact

`scripts/start.py`、`app/__main__.py`、`app/runtime/bootstrap.py`、`app/runtime/model_settings.py`、`app/runtime/model_config.py`、`app/tools/mcp.py`、`tui/src/{protocol,backend,app,view-state,model-settings}.ts(x)`、新增首次配置页、测试与文档。沿用 Ink 唯一界面、ApprovalGate、配置优先级、任务快照、后台隔离与回执分层；不新增 Web/桌面界面、账号系统、自动安装 MCP 或权限引擎重设计。
