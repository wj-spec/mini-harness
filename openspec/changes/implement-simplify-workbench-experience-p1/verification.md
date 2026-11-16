# 阶段 1：结果回执分层验证

日期：2026-11-13。状态：代码与工程验证完成；真人体验待确认。范围仅为回执分层，配置契约、功能区指引与应用内设置仍属于后续阶段。

## 用户可见行为

- 普通问答、成功只读任务不追加独立回执卡；回答与证据入口保留。
- 有文件修改时显示紧凑摘要，例如“修改 1 个文件 · 检查通过”；未验证、失败、过期检查明确提示。
- 无文件 diff 的普通命令仍显示操作影响待核对；拒绝、工具异常、环境审计异常不被静默。
- 暂停、取消、错误保留原因及已知影响。自动续段收起中间终态摘要，最终摘要保留同任务前段影响。
- 正文、状态区、验证详情共享展示规则；历史数据缺少或损坏结构化事实时显示结果待核对。
- 原始回执保存在验证详情和轮次记录中，主区不再展示原始技术字段。详情截断时显式说明。

“检查通过”只表示记录的检查通过，不宣称需求已满足；审批、完成守卫与预算控制保持既有语义。

## 验证证据

| 验证 | 结果 | 本地日志 |
| --- | --- | --- |
| Python 全量：`./run_tests.sh` | 1987 passed / 4 skipped，退出 0；既有 tarfile DeprecationWarning 1 条 | `.vibe/receipt-python-verified.log` |
| 最后补充的损坏字段校验与 core 边界回归 | 10 passed，退出 0 | `.vibe/receipt-outcome-final.log` |
| Ink 全量：build 后 `NO_COLOR=1 node --test --test-concurrency=2 dist/tests/*.test.js` | 760/760，退出 0 | `.vibe/receipt-ink-bounded.log` |
| 实际 Ink/Python PTY Demo | 通过：两次单键审批、实际写文件、紧凑修改/验证摘要、退出 0 | `.vibe/receipt-smoke-result.log`、`.vibe/receipt-smoke-terminal.log` |
| OpenSpec strict 校验、diff 空白检查、文档链接 | strict 校验通过；diff 空白与 Markdown 本地链接检查通过 | 本 change 与 roadmap 包 |

Python 全量最后一次运行期间补充了字段类型校验，因此另跑整个 outcome 测试文件与 core 依赖边界测试。未发送真实付费模型请求。

此前默认并发 Ink 全量出现过固定等待时间相关失败；最近一次为 759/760，任务切换时尚未渲染 ready。该测试文件独立复跑 12/12 通过，未放宽断言或修改等待时间；最终以较低并发执行完整测试集，原失败日志保留在 `.vibe/receipt-ink-verified.log`。更早的沙箱 socket/PTY 权限失败与旧文案断言调整也保留日志，不当作最终通过记录。

## 场景与实现边界

| 场景 | 核验位置 |
| --- | --- |
| 同文件再次修改、验证过期、失败不能被无关成功覆盖、同检查重跑 | `tests/test_outcome.py` |
| 普通命令没有 diff、审批拒绝 | 实际 Agent/ToolRegistry 测试 |
| 同 task 跨两段保留修改，新任务清空，持久快照与最终结果一致 | 实际 stdio Harness 测试 |
| 最终计划检查覆盖 Agent 提前结论 | `tests/test_outcome.py` 的计划结果测试 |
| 静默问答、修改/异常摘要、旧结果保守回退、重复 done、续段 | `tui/tests/outcome.test.ts`、App/状态/检视相关测试 |
| worker 确定续段后在实时/回放帧标记继续，最终段正常收尾 | `tests/test_worker.py` 实际 worker 目标循环 |
| 两次审批后的真实终端展示 | fake PTY smoke |

聚合按 task、上一 segment、工作区文件来源精确读取已有 inspection；前段证据缺失或来源不一致时标记部分证据待核对。文件/检查条数有上限，截断后保守显示，不推断完整性。

## 待人工体验

在常用终端连续执行问答 → 只读检查 → 修改并验证 → 检查失败/取消 → 恢复或继续，确认摘要密度、窄屏可读性与证据入口符合预期。实际 PTY 自动化不等同真人 Ghostty 体验；阶段 1 不标整体验收，阶段 2–5 尚未实施。

本轮未 commit、push 或归档。既有 Logo、README、启动器改动保留，未一并申报为本切片的新功能。
