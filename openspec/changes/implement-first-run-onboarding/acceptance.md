# 验收矩阵与证据（截至 2026-11-14）

本文件把方案 §12 的条目映射到可复现证据。**只有自动化证据通过的条目才勾选**；真人/真实模型/真实 PTY 条目保持未勾选，不把 Demo 或测试替身当作真人验收。

## §12.1 自动与实际终端（自动化）

- [x] 无 .env、缺 Key、占位 Key、已有有效配置、环境覆盖的分流：`tests/test_start.py`、`tests/test_bootstrap.py`、`tests/test_first_run_journey.py`、`tests/test_model_settings.py`。
- [x] 配置文件语法损坏进入 Ink 修复页，显示位置与修法、不覆盖原文件、不回显内容：`tests/test_start.py`、`tests/test_bootstrap.py`、`tui/tests/config-problem.test.tsx`；非交互环境仍 exit 2 并给出修复命令。
- [x] 普通启动不自动变 Demo、不自动发模型检测：首次配置只在显式操作时保存/检测；普通启动 ready 后 `status idle`，无网络请求（`tests/test_bootstrap.py`、真实 FastMCP 集成用例）。
- [x] 配置准备状态拒绝任务运行、续做和后台执行：`tests/test_bootstrap.py`；无不可见排队。
- [x] 保存/网络检测均经过审批；拒绝、冲突、取消与迟到回执：`tests/test_bootstrap.py`、`tests/test_model_settings.py`（网络失败/401 分支、版本冲突、取消、重复 id）。
- [x] Key 不出现在概览、界面、审批预览、日志或进程参数：Python 事件断言 + TUI 面板测试（`sk-` 字符串不出现）；Key 仅经审批写入 `.env`。
- [x] 地址/模型/Key/代理变化后旧检测结果失效：`tui/tests/view-state.test.ts`（modelCheck 流转）、后端 revision 校验。
- [x] CLI/环境覆盖、旧任务模型快照、后台作用域和不能热切换：既有回归（`tests/test_model_settings.py`、`tests/test_worker.py` 等）。
- [x] 32×12、80×24、120×40 下主要动作、焦点、错误与返回：`tui/tests/bootstrap-panel.test.tsx`、`settings.test.tsx`、`workbench.test.ts`。
- [x] 中文输入、粘贴、鼠标、键盘导航、返回、调整窗口保留草稿：`tui/tests/home-app.test.tsx`（中文草稿、光标、阅读位置、尺寸变化）、`bootstrap-app.test.tsx`；首页示例、项目/模型摘要按钮（查看项目展开/收起完整路径、更改打开模型设置）、首次配置页服务字段、设置分类行均可点击（`home-app`、`bootstrap-panel`、`settings` 测试）。
- [x] 显式 `--acceptance` 持续标注“验收环境 · 隔离配置”并说明隔离范围，不与 Demo/真实入口混淆：`tests/test_workbench_acceptance.py`、`tests/test_stdio_parity.py`、`tui/tests/agent-status.test.tsx`。
- [x] 前台配置了 MCP 仍无开场启动审批；主动连接才审批：`tests/test_mcp_foreground.py`、真实 FastMCP 集成用例（`tui/tests/backend.test.ts`）；旧 Ink 浏览器 smoke 已改为显式 `/mcp` 连接（需 PTY/浏览器，本环境未执行）。
- [x] MCP 单服务、失败回退、授权清理、取消重试与后台原有行为：`tests/test_mcp.py`、`tests/test_mcp_lifecycle.py`、`tests/test_mcp_settings.py`、`tests/test_mcp_foreground.py`。
- [~] `./run_tests.sh`、Ink 构建与全量测试通过；真实 PTY 旅程未运行：本环境无 `pty`（`openpty: out of pty devices`），仅有真实进程 stdio 旅程 `tests/test_first_run_journey.py`。

最近一次全量：

- Python：`./run_tests.sh -q` → **2103 collected / 2091 passed / 12 skipped**，exit 0。
- Ink：`npm --prefix tui test`（串行全量）→ **850 tests / 848 pass / 2 fail**，两项失败均为环境无 PTY 的既有用例。

## §12.2 真实场景（需要用户参与）

以下全部未勾选，按 `handoff.md` 中的步骤在用户终端、用户选择的真实模型上执行：

- [ ] 新用户普通启动，在 UI 填写自己的模型信息，主动检测后进入自己的项目。
- [ ] 已配置用户普通启动，直接发送真实任务；没有强制重复检测或向导。
- [ ] 首个真实任务为只读理解项目，确认确实收到真实模型回复。
- [ ] 第二个任务为用户认可的小改动，经过文件/命令审批，检查实际变更和验证结果。
- [ ] 真实鉴权或网络失败可恢复，且无额外任务自动发送。
- [ ] 有 MCP 配置的真实项目能够先正常进入，再按需连接；服务名称和风险可理解。
- [ ] 用户在常用终端确认：无需解释即可开始，状态不误导，审批时机自然，返回不丢内容。

## 环境限制

- 本沙箱没有 `/dev/pty`：Ink 的真实 PTY 用例与交互旅程无法执行；不是产品失败。
- 本会话不读取、不使用用户已保存的 API Key；真实模型调用只在用户明确操作用户终端时发生。
- Windows/Linux 冷安装、真实远程 MCP 未验证。
