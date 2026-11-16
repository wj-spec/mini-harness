# 阶段 3：功能区与情境指引验证

日期：2026-11-13。状态：工程实现与验证完成；真人常用终端体验待确认。

## 交付行为

- Ctrl+T、/guide 与宽屏底部“指引”主动打开五区说明；无自动弹出的教程。
- 主区复用一行给出情境下一步；/guide off 和 /guide on 关闭/恢复当前工作台实例的提示。
- 示例只填入空闲状态的空草稿，不发送；中文草稿、光标和原阅读位置保留。
- 审批到来自动收起指引，不自动批准或再次打开；Ctrl+C/Ctrl+Q 保留原有控制语义。
- 导航调用已提供的计划、工作区检视、任务与权限入口；能力缺失时隐藏动作。模型配置仍使用 ./start.sh --configure。
- [使用指南](../../../docs/USAGE.md#功能区与情境指引)说明全部操作和作用域。

## 验证

| 检查 | 结果 | 本地证据 |
| --- | --- | --- |
| 行为修改前的面板与 App 测试 | 预期失败：模块不存在、App 尚无指引 | .vibe/guidance-p3-red.log、guidance-p3-app-red.log |
| 初轮指引专项 | 10/10；后续增加阅读位置回归，共新增 11 项纳入最终全量 | .vibe/guidance-p3-targeted.log；tui/tests/guidance*.test.tsx |
| 32×12、80×24、120×40 | 精确行数、字符宽度、键盘导航、关闭通过 | tui/tests/guidance.test.tsx |
| App 交互 | 中文光标、拒绝覆盖、提示开关、鼠标入口、缺少能力、审批抢占、运行控制及阅读位置通过 | tui/tests/guidance-app.test.tsx |
| Python 全量 ./run_tests.sh | 1993 passed / 4 skipped；1 条既有 tarfile DeprecationWarning | .vibe/guidance-p3-python.log |
| Ink build + 完整低并发回归 | 771/771，0 failed、0 skipped，退出 0 | .vibe/guidance-p3-ink-final.log |
| 实际 start.sh --demo PTY | 80×24；查看指引→拒绝覆盖 Demo 草稿→关闭提示→重开→返回→两次单键审批→写文件→紧凑结果→退出 0 | .vibe/guidance-p3-demo-result.log、guidance-p3-demo-terminal.log |
| 文档与规格 | 文档链接/锚点、OpenSpec strict、git diff --check | 收尾校验记录 .vibe/guidance-p3-checks.log |

首次 Ink 全量为 769/771，两项回归：新命令插入改变原有 /help、/context 的菜单顺序；情境提示覆盖计划验收失败的操作提示。已将指引命令移至既有前两个入口后，并保留计划验收/未完成提示优先级；原断言未放宽。首次日志 .vibe/guidance-p3-ink.log 保留，最终全量通过。

## 边界与后续

- 提示开关仅保存在当前 App 实例；重启或切换实例后恢复默认开启，未新增配置持久化。
- 应用内模型编辑/连接检测属于阶段 4，本阶段只指向现有向导。
- 真实 PTY 使用 fake 模型和已有依赖；不产生付费模型请求、不修改用户 .env，不等于冷安装、真实 API 或真人 Ghostty 体验。
- 真人需确认：入口是否容易发现、五区是否好理解、窄屏阅读与返回是否顺畅、连续任务中提示是否仍有干扰。现有阶段 1、2 验收缺口继续保留。
- 未提交、推送或归档；运行产物只写 .vibe/。
