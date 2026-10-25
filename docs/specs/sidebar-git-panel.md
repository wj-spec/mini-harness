# 右侧 Git 变更侧栏（Ghostty quick terminal）

日期：2026-10-25。状态：工程实现、自动回归与 AppleScript 语法校验完成；真实 Ghostty 终端里的 quick terminal 打开、切换与视觉验收待用户确认。

## 背景与范围

用户希望在工作台之外、终端窗口右侧有一个跟随工作区的文件变更与 diff 面板，**不改变主 Ink 工作台的列宽与布局**。先支持 macOS + Ghostty；后续再扩展 `new window` 手动摆位与 macOS 原生 Terminal。

本切片交付：

- 独立进程 `tui/src/sidebar/`：只读 Git 变更列表 + 选中文件 unified diff，1s 轮询跟随；
- Ghostty 1.3+ 的 quick terminal 右侧浮层：`/sidebar` 或 `Ctrl+G` 打开/切换；
- 主 TUI 的工作台入口、帮助与命令补全；
- 手动入口 `./run_sidebar.sh`，可用于任意终端或面板；
- 只读边界、错误降级与 AppleScript 语法校验。

不在本切片：macOS Terminal 自动化、新窗口摆位、双向操作（从侧栏写回工作台）、鼠标、side-by-side、语法高亮。

## 形态与依据

采用 Ghostty **quick terminal** 停靠右侧，而不是 `split`：

- quick terminal 是屏幕边缘的独立浮层窗口，不改变主终端窗口的行列数，Ink 的 120 列布局保持原样；
- `split` 会让主终端列数减少，Ink 在 `< 118` 列时隐藏内部状态侧栏，正是用户要避免的“挤占视觉效果”；
- quick terminal 由配置固定宽度（`quick-terminal-size`），可通过 `toggle_quick_terminal` 显隐，隐藏时进程仍保留。

配置（`~/.config/ghostty/config`，或 `ghostty +edit-config`）：

```ini
quick-terminal-position = right
quick-terminal-size = 420px
quick-terminal-autohide = false
quick-terminal-animation-duration = 0
keybind = global:cmd+shift+g=toggle_quick_terminal
```

修改 `quick-terminal-position` 后需完全重启 Ghostty。第一次由工作台调用 `osascript` 时，macOS 会弹出“自动化”授权；拒绝后需要到 系统设置 → 隐私与安全性 → 自动化 重新允许。若设置了 `macos-applescript = false`，自动打开会失败。

## 架构

```
主 Ink 工作台（列宽不变）
  /sidebar 或 Ctrl+G
        │  spawnSync osascript
        ▼
Ghostty 1.3 AppleScript（macos/Ghostty.sdef）
  - 已有标题为 mini-harness sidebar 的终端 → toggle_quick_terminal
  - 否则打开 quick terminal；等新终端出现后 input text 启动命令
        ▼
tui/src/sidebar/cli.tsx（独立 TTY / quick terminal 内进程）
  - 设置终端标题 mini-harness sidebar，供下一次切换识别
  - 1s 轮询只读 git status，选中文件按需 git diff
  - 退出时清理标题；quick terminal 关闭或保留最后画面
```

- **数据源**：整个工作区 Git 状态，不是只统计 Agent 本轮改动。
- **跟随**：默认开启；轮询发现文件签名（状态与增删行数）变化时自动选中变化文件；手动 `↑↓/jk` 后转为手动模式，`f` 重新开启。
- **刷新**：固定 1s 轮询 `git status`（本仓库约 10ms）；`.vibe/` 被 gitignore 忽略，不会因会话日志产生变更列表抖动。没有使用 fs.watch，避免忽略规则与写入风暴。
- **协议**：侧栏不经过 Python 后端、不新增 JSON Lines 事件；只读数据由独立进程直接获取，主 TUI 只负责打开/切换。

## 只读边界与安全

`tui/src/sidebar/git.ts` 的所有 Git 调用都满足：

- 固定参数数组，`spawnSync('git', args)`，**不经过 shell**，不接受用户拼接的命令字符串；
- 只允许 `status --porcelain=v2`、`diff`、`--numstat` 等只读子命令；`GIT_OPTIONAL_LOCKS=0`、`--no-optional-locks`，避免与前台 git 抢锁；
- `cwd` 限当前工作区；2 秒超时；输出 4 MiB 上限；diff 2000 行 / 40 万字符截断；
- `-c core.quotepath=false` 与 `-z` 保证中文路径不被八进制转义；
- 不写文件、不联网、不展开工作区外路径。

这是界面侧只读查询，不是 Agent 工具：不扩大模型的写入/命令权限，`/diff` 与写工具继续走 `ApprovalGate`。若未来把侧栏数据改由后端提供，应保持同样的只读分类与固定参数约束。

## 错误与降级

| 情况 | 行为 |
|---|---|
| 非 Git 工作区 | 侧栏显示“当前工作区不是 Git 仓库”，继续轮询 |
| 未安装 git | 显示“未找到 git 命令” |
| 读取 diff 失败 | 选中区显示错误，列表与轮询继续 |
| Ghostty < 1.3 / 非 Ghostty | 自动打开返回 `unsupported`，工作台提示手动命令；`./run_sidebar.sh` 仍可用 |
| macOS Terminal | 明确提示“自动化尚未实现”，避免误导 |
| 自动化权限被拒 | 提示到系统设置授予后重试 |
| quick terminal 已打开但标题不匹配 | 返回 `opened`，提示在 quick terminal 内手动运行启动命令 |
| 侧栏进程退出 | quick terminal 关闭；再次 `/sidebar` 重新打开并注入启动命令 |

## 测试与证据

- `tui/tests/sidebar-git.test.ts`：porcelain v2（分支/ahead/behind、M/A/D/R/?/冲突、中文路径关闭 quotepath）、numstat、无 Git/非 Git、diff 截断、固定参数。
- `tui/tests/sidebar-diff.test.ts`：unified diff 分类、宽字符换行、空行保留。
- `tui/tests/sidebar-state.test.ts`：首次选择、跟随变化文件、手动选择保持、文件消失回退、滚动 clamp。
- `tui/tests/sidebar-host.test.ts`：终端识别、shell 转义、AppleScript 生成、结果标记映射、权限/不支持降级，以及用 `osacompile` 对生成的 AppleScript 做**语法校验**（不执行、不控制 Ghostty）。
- `tui/tests/sidebar-app.test.tsx`：真实 Ink 组件渲染（分支/列表/diff/跟随/错误/键盘/宽度不溢出）。
- `tui/tests/app.test.tsx`：`/sidebar` 与 `Ctrl+G` 调用注入的启动器、未连接工作区提示、`unsupported` 提示。

本轮工程验证记录：

- `npm --prefix tui test`：**263/264 通过**，唯一失败是沙箱无 PTY 的既有环境项（`interactive CLI enters and restores the alternate screen`）。
- AppleScript 已通过 `osacompile` 语法编译校验；未在本机执行 `osascript`，避免触发用户的终端自动化授权或切换其窗口。
- `./run_sidebar.sh --help` 正常；`readWorkspaceStatus` 在本仓库实际返回 11 个变更文件并成功读取首个 diff。

**仍待用户验收**：真实 Ghostty 1.3.1 下 `/sidebar` 打开右侧 quick terminal、首次授权、切换显隐、关闭后重开，以及 120 列主工作台布局是否完全不受影响。

## 已知限制与后续

- quick terminal 宽度由 Ghostty 配置固定，需按屏幕空位选择 `300px`–`500px`；窗口最大化时会覆盖右侧一窄条（覆盖而非挤压）。
- quick terminal 全局唯一、跨 tab 存在；本切片假设一个 Ghostty 进程只开一个工作台侧栏。
- 标题识别依赖侧栏进程设置的终端标题；若标题被覆盖，可能退化为 `opened` 提示手动运行。
- 尚未双向交互：侧栏不能把选中文件/diff 回传给主工作台（如“附到下一条 prompt”）。
- 后续：`new window` + 手动摆位；macOS 原生 Terminal（AppleScript `do script` / 窗口 bounds 需单独验证）；供 `Ctrl+G` 的侧栏宽度记忆与多显示器策略。
