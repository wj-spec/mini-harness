# 阶段 5.A：设置中心与单段限制验证

日期：2026-11-13。状态：5.A 工程实现与自动验证完成；阶段 5 的 goal 总预算、MCP 生命周期编辑尚未实施。

## 行为与边界

/settings 和指引中的配置入口提供四项单段默认限制：步数、时长、token、估算费用。保存与应用独立，CLI/进程环境优先；保存经过 ApprovalGate、文件版本复核、原子替换与 0600 权限，只替换这四项。已有模型、Key、代理与其他配置保持原样。

每个任务首次执行在 trace 记录 run_limits。普通 /resume、指定 task 和 goal 续段保持原限制；新任务使用新默认。缺少快照时提示使用本次启动限制；损坏快照阻止续做。Agent 字段在运行结束或异常后恢复，下次执行再选择对应默认/快照。计划策略仍可进一步收紧。

模型和权限复用原面板，返回保留设置草稿。MCP 连接数、沙箱、通知仅显示状态与重启要求，不回传命令参数、环境或 webhook。goal 总预算、独立探索、已运行 worker 不由该页更新。

## 验证证据

| 检查 | 结果 | 本地证据 |
| --- | --- | --- |
| Python 限制专项 | 14 项；数值边界、审批拒绝/文件漂移、敏感项保留、0600、覆盖来源、异常恢复、快照归属、新默认与恢复隔离、运行互斥、损坏快照恢复、跨任务 goal 续段 | tests/test_limit_settings.py；.vibe/settings-p5-goal.log |
| 关联回归 | 限制、模型、stdio、启动器 106 项通过（追加 goal 专项前） | .vibe/settings-p5-python-targeted.log |
| Ink 设置专项 | 7/7；三尺寸、数字校验、提交与导航、中文草稿/光标、嵌套面板返回、拒绝后重试 | .vibe/settings-p5-ui.log |
| Python 全量 | 2021 passed / 4 skipped，退出 0；1 条既有 tarfile DeprecationWarning | .vibe/settings-p5-python-full.log |
| Ink build + 全量 | 785/785，0 failed / 0 skipped，退出 0 | .vibe/settings-p5-build.log、settings-p5-ink-full.log |
| 真实 PTY | 80×24：指引→设置→步数改为 12→审批保存→应用→返回 Demo 原草稿→两次工具审批→写入和检查通过→退出 0；trace 确认新任务步数为 12 | .vibe/settings-p5-demo-verified.log、settings-p5-demo-terminal.log |
| 文档和规格 | OpenSpec strict、文档链接/锚点与 diff 空白检查 | .vibe/settings-p5-checks.log |

测试先于对应实现：首次专项因限制模块缺失收集失败；损坏续做快照回归出现 error 而非受控 stopped，补充校验和收尾后通过。保留红灯日志 settings-p5-red.log、settings-p5-corrupt-red.log，不将其记为成功。

真实 PTY 使用 .vibe 下隔离安装目录、workspace 和虚构 Key；断言保存仍保留原 Key 且终端无凭据回显。未修改用户现有 .env。

## 未覆盖与后续

- 没有真实 Key、付费模型请求或真实 MCP 重连测试；本批不提供 MCP 重连操作。
- 费用是配置单价的估算限制；未配置单价时不能提供有效费用保护，界面有提示。
- 旧任务没有历史快照时无法恢复未知旧值；明确使用启动限制，新默认不会静默覆盖。
- 数字编辑采用末尾追加/删除与 Ctrl+U 清空；未保存草稿只在面板存活期间保留。
- 5.B 先统一前后台 goal 总预算及恢复契约，5.C 再实施 MCP 生命周期与高级编辑；不将本次完成记为整个阶段 5 完成。
- macOS 自动 PTY 不代替真人常用终端和跨平台确认。既有阶段的真人体验缺口保留。
- 未提交、推送或归档。
