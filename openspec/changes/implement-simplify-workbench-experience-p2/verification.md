# 阶段 2：配置契约与最小模板验证

日期：2026-11-13。状态：工程实现与验证完成。范围：体验简化路线图阶段 2；后续功能指引和应用内设置尚未实现。

## 交付行为

- .env.example 从原来 47 个具名变量缩减为 1 个必填 Key，占位行外仅 3 个可选项：地址、模型和代理开关。
- 默认服务向导复用项目内置地址与模型，只需提供 Key；自定义服务保留完整输入，保存前显示服务与模型。
- [配置指南](../../../docs/CONFIGURATION.md)区分已有工作台内操作和需要重启的配置；[开发配置](../../../docs/CONFIGURATION-DEV.md)单列演示/调试/实验；[契约清单](../../../docs/CONFIGURATION-CONTRACT.md)覆盖原 47 项的默认、解析、消费、作用域、生效时点、状态与测试依据。
- 移除未接通的 VIBE_LLM_MAX_OUTPUT_TOKENS 示例并纠正 USAGE；修正 goal 段数/时长与验收总超时的零值说明。
- 用户已有 .env 未修改；配置解析、原子保存、权限、安全默认值、运行预算与高级入口行为保持兼容。

## 验证

| 检查 | 结果 | 本地证据 |
| --- | --- | --- |
| 行为修改前新增默认服务/取消测试 | 预期失败，确认原向导仍会额外询问模型 | .vibe/config-p2-red.log |
| 启动、配置、provider 专项 | 45 passed；包含默认/自定义服务真实 PTY 输入 | .vibe/config-p2-targeted.log |
| 模板 → Config → provider 请求 | 默认/自定义地址、model、Authorization 与代理开关断言通过；本地 MockTransport，不发外网请求 | tests/test_start.py |
| 旧入口环境语义 | 实际运行复制的 run_ink.sh / run_vibe.sh，子进程 stub 确认不会读取 .env，仍传递 shell 值 | tests/test_start.py |
| 新目录 start.sh --demo | 通过：无 Key、无 .env；两次审批、真实写文件、紧凑检查摘要、退出 0 | .vibe/config-p2-demo-result.log、config-p2-demo-terminal.log |
| 原模板去向/文档检查 | 47 项无遗漏，最小模板键集合及本地链接通过 | .vibe/config_p2_check.py |
| Python 全量 | 1993 passed / 4 skipped，退出 0；1 条既有 tarfile DeprecationWarning | .vibe/config-p2-python-final.log |

首次全量结果为 1992 passed / 4 skipped / 1 failed：新配置文档的 MCP 锚点大小写不匹配，已改为实际标题对应的小写 slug；原失败记录保留于 .vibe/config-p2-python.log。最终复验全部通过，不覆盖首次失败日志。OpenSpec strict、diff 空白检查和文档链接/锚点检查通过。

本次仅改变启动向导的默认服务输入与配置文档，未修改 Ink 源码；使用真实 Ink/Python Demo 做集成覆盖，未重复 Ink 全量。阶段 1 的 760/760 是此前证据，不宣称本轮重新执行。

## 限制与后续

- PTY 与模拟请求证明本地配置和启动路径可用，不证明服务商 Key、模型权限或远程接口可用。本轮没有真实付费请求。
- 本机 macOS 已验证；Linux 全新安装、无 uv 的全新安装本轮未再实测，pip 依赖分支沿用启动器单元回归。
- 新目录 Demo 复用当前已安装依赖，不将它当作冷安装成功。
- 文档列出相关测试与源码核对，未声称原 47 项都有独立端到端测试。后台进程不会因 .env 修改自动换配置。
- 真人连续使用体验仍待反馈；用户已明确授权进入阶段 2，阶段 1 的真人体验待确认项继续保留。
- 未 commit、push 或归档；后续从阶段 3“功能区与情境指引”细化。
