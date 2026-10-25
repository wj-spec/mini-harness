# 右侧 Git 变更侧栏（Ghostty quick terminal）

日期：2026-10-25。状态：工程实现、自动回归与 AppleScript 语法校验完成；真实 Ghostty 终端里的 quick terminal 打开、切换与视觉验收待用户确认。

## 背景与范围

用户希望在工作台之外、终端窗口右侧有一个跟随工作区的文件变更与 diff 面板，**不改变主 Ink 工作台的列宽与布局**。先支持 macOS + Ghostty；后续再扩展 `new window` 手动摆位与 macOS 原生 Terminal。

本切片交付：

- 独立进程 `tui/src/sidebar/`：只读 Git 变更列表 + 选中文件 unified diff，1s 轮询跟随；
- 两种形态：默认 `/sidebar`/`Ctrl+G` 在 Ghostty 窗口内右侧分屏（跟随窗口尺寸）；`/sidebar quick` 使用 Ghostty 1.3+ quick terminal（屏幕右缘浮层，不挤占 TUI）；
- 主 TUI 的工作台入口、帮助与命令补全；
- 手动入口 `./run_sidebar.sh`，可用于任意终端或面板；
- 只读边界、错误降级与 AppleScript 语法校验。

不在本切片：macOS Terminal 自动化、新窗口摆位、双向操作（从侧栏写回工作台）、鼠标、side-by-side、语法高亮。

## 形态与依据

提供两种形态，命令可切换：**默认 `/sidebar`/`Ctrl+G` 用 Ghostty 窗口内 split**（跟随窗口尺寸），**`/sidebar quick` 用 quick terminal**（屏幕右缘浮层，不挤占 TUI）。

- quick terminal 是屏幕边缘的独立浮层窗口，不改变主终端窗口的行列数，Ink 的 120 列布局保持原样；
- `split` 会让主终端列数减少，Ink 在 `< 118` 列时隐藏内部状态侧栏，正是用户要避免的“挤占视觉效果”；
- quick terminal 由配置固定宽度（`quick-terminal-size`），可通过 `toggle_quick_terminal` 显隐，隐藏时进程仍保留。

**窗口尺寸限制（2026-10-25 补充）**：quick terminal 锚定在**屏幕边缘**，不是 Ghostty 窗口内部的 pane。右侧定位时它取屏幕可用区域、垂直居中（源码 `QuickTerminalPosition.finalOrigin`）；`quick-terminal-size` 只接受屏幕百分比/像素，默认把非主轴方向铺满屏幕，因此**不会随 Ghostty 窗口移动或缩放**。用户反馈“没有跟随 Ghostty 窗口尺寸”即由此而来，无法通过 AppleScript 或配置改变。

为了在需要“跟随窗口”时可用，新增 **split 模式，并设为默认**（2026-10-25 用户反馈 quick terminal 不跟随窗口尺寸后调整；`/sidebar quick` 保留原形态）：

- 命令：`/sidebar`、`/sidebar split`、`Ctrl+G`；quick 模式为 `/sidebar quick`；
- AppleScript：`new surface configuration` 设置 `command` 与 `initial working directory`，再 `split <terminal> direction right with configuration`；
- 分屏属于窗口布局，**跟随窗口尺寸重排**，高度与窗口一致；代价是主 TUI 宽度减少（窗口不足 118 列时 Ink 切紧凑布局）；
- 已有标题匹配的侧栏终端时只 `focus`，不重复分屏。

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
主 Ink 工作台
  /sidebar、Ctrl+G（split）或 /sidebar quick
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
- **生命周期联动**：启动命令带 `--parent-pid <主 TUI pid>`。侧栏启动时把 `{pid, parentPid}` 写入系统临时目录（按 workspace hash 命名，不写工作区），正常退出时删除；工作台正常退出会先按登记文件发送 `SIGTERM`（仅 `parentPid` 匹配时），崩溃/被杀时由侧栏每 1s 检查父进程兜底（`ESRCH` 视为退出，`EPERM` 视为存活）。split 的 `command` 进程退出后分屏关闭；quick terminal 的 `exec` 进程退出后 surface 关闭。手动 `run_sidebar.sh` 不传该参数，保持独立运行。

### split 模式的 AppleScript

```applescript
set sidebarHits to (every terminal whose name contains "mini-harness sidebar")
if (count of sidebarHits) > 0 then
  focus (item 1 of sidebarHits)
  return "focused"
end if
set cfg to new surface configuration
set initial working directory of cfg to "<workspace>"
set target to focused terminal of selected tab of front window
set newTerminal to split target direction right with configuration cfg
delay 0.6
input text "<sidebar 启动命令>" to newTerminal
send key "enter" to newTerminal
return "started"
```

split 不需要 quick terminal 配置，也不经过 `quick-terminal-position` 预检；失败路径与 quick 模式共用 TCC/权限错误映射。

## 只读边界与安全

`tui/src/sidebar/git.ts` 的所有 Git 调用都满足：

- 固定参数数组，`spawnSync('git', args)`，**不经过 shell**，不接受用户拼接的命令字符串；
- 只允许 `status --porcelain=v2`、`diff`、`--numstat` 等只读子命令；`GIT_OPTIONAL_LOCKS=0`、`--no-optional-locks`，避免与前台 git 抢锁；
- `cwd` 限当前工作区；2 秒超时；输出 4 MiB 上限；diff 2000 行 / 40 万字符截断；
- `-c core.quotepath=false` 与 `-z` 保证中文路径不被八进制转义；
- 不写文件、不联网、不展开工作区外路径。

这是界面侧只读查询，不是 Agent 工具：不扩大模型的写入/命令权限，`/diff` 与写工具继续走 `ApprovalGate`。若未来把侧栏数据改由后端提供，应保持同样的只读分类与固定参数约束。

## 配置预检（2026-10-25 修正）

用户实际使用发现：首次按 `/sidebar` 后，quick terminal 出现在**屏幕顶部**而不是右侧。原因是 Ghostty 默认 `quick-terminal-position = top`，而本机此前没有 Ghostty 配置文件；工作台当时假设用户已手工配置，直接打开了顶部 quick terminal。

现在 `openSidebar` 在打开前会：

1. 找到 Ghostty 命令行二进制（`GHOSTTY_BIN_DIR`、`GHOSTTY_RESOURCES_DIR`、`/Applications/Ghostty.app/...`）；
2. 只读执行 `ghostty +show-config`，读取实际生效的 `quick-terminal-position`（未配置按默认 `top` 处理）；
3. 位置不是 `right` 时返回 `misconfigured`，**不打开** quick terminal，并提示 `输入 /sidebar setup 写入右侧配置，然后完全退出并重启 Ghostty`；
4. 找不到二进制或 `+show-config` 失败时跳过预检，保持旧行为（不误拦截）。

配套 `scripts/setup-ghostty-sidebar.sh` 与工作台命令 `/sidebar setup`：

- 写到 `${XDG_CONFIG_HOME:-~/.config}/ghostty/config`，只追加带注释的配置块；
- 已有 `quick-terminal-position` 时不覆盖：`right` 直接通过，其他值提示手动修改；
- 写入后用 `ghostty +validate-config --config-file=...` 校验（失败只警告，不自动回滚）；
- `--dry-run` 支持预览；脚本有独立回归测试，不会碰真实用户配置。

`quick-terminal-position` 只在 Ghostty 启动时读取，因此**必须完全退出并重启 Ghostty**；只改配置不重启仍会打开到旧位置。

## 错误与降级

| 情况 | 行为 |
|---|---|
| 非 Git 工作区 | 侧栏显示“当前工作区不是 Git 仓库”，继续轮询 |
| 未安装 git | 显示“未找到 git 命令” |
| 读取 diff 失败 | 选中区显示错误，列表与轮询继续 |
| Ghostty < 1.3 / 非 Ghostty | 自动打开返回 `unsupported`，工作台提示手动命令；`./run_sidebar.sh` 仍可用 |
| macOS Terminal | 明确提示“自动化尚未实现”，避免误导 |
| quick 模式且位置不是 right | 返回 `misconfigured`，不打开；提示 `/sidebar setup` 并重启 Ghostty（split 模式不受影响） |
| 自动化权限被拒 | 提示到系统设置授予后重试 |
| quick terminal 已打开但标题不匹配 | 返回 `opened`，提示在 quick terminal 内手动运行启动命令 |
| 侧栏进程退出 | quick terminal/分屏关闭；再次 `/sidebar` 重新打开并注入启动命令 |
| 主 TUI 退出（正常） | 按侧栏登记 PID 发送 `SIGTERM`，侧栏立即退出、面板关闭 |
| 主 TUI 崩溃/被杀 | 侧栏 1s 父进程看护发现后退出，面板关闭 |
| split 模式 | 不需要 quick terminal 配置；已有侧栏时 `focus`，否则在当前窗口右侧分屏 |

## 测试与证据

- `tui/tests/sidebar-git.test.ts`：porcelain v2（分支/ahead/behind、M/A/D/R/?/冲突、中文路径关闭 quotepath）、numstat、无 Git/非 Git、diff 截断、固定参数。
- `tui/tests/sidebar-diff.test.ts`：unified diff 分类、宽字符换行、空行保留。
- `tui/tests/sidebar-state.test.ts`：首次选择、跟随变化文件、手动选择保持、文件消失回退、滚动 clamp。
- `tui/tests/sidebar-host.test.ts`：终端识别、shell 转义、AppleScript 生成、quick/split 结果映射、quick terminal 位置预检（top 拒绝 / right 通过 / 读取失败放行）、split 不预检、`--parent-pid` 注入、权限/不支持降级，以及用 `osacompile` 分别校验 quick 与 split 脚本语法。
- `tui/tests/sidebar-args.test.ts`：`--workspace/--theme/--refresh-ms/--parent-pid` 解析与非法值。
- `tui/tests/sidebar-lifecycle.test.ts`：父进程存活判断（ESRCH/EPERM）、watcher 只触发一次/可停止、按 workspace 登记的 PID 只会终止同一父进程的侧栏，并用真实子进程验证 `SIGTERM` 生效；link 文件含侧栏列宽供比例校准。
- `tui/tests/sidebar-split-ratio.test.ts`：目标列数计算、70/30 校准收敛、已达标不 resize、拿不到列宽时放弃。
- `tui/tests/sidebar-setup.test.ts`：用临时 `HOME`/`XDG_CONFIG_HOME` 验证配置脚本的 `--dry-run`、幂等写入、拒绝覆盖非 right 配置；不触碰真实用户配置。
- `tui/tests/sidebar-app.test.tsx`：真实 Ink 组件渲染（分支/列表/diff/跟随/错误/键盘/宽度不溢出）。
- `tui/tests/app.test.tsx`：`/sidebar`（默认 split）、`/sidebar split`、`/sidebar quick`、`Ctrl+G`、`/sidebar setup` 调用注入的启动器/配置命令，未连接工作区与 `unsupported` 提示。

本轮工程验证记录：

- `npm --prefix tui test`：**286/287 通过**，唯一失败是沙箱无 PTY 的既有环境项（`interactive CLI enters and restores the alternate screen`）。
- AppleScript 已通过 `osacompile` 语法编译校验；未在本机执行 `osascript`，避免触发用户的终端自动化授权或切换其窗口。
- `./run_sidebar.sh --help` 正常；`readWorkspaceStatus` 在本仓库实际读取变更文件与 diff；`ghostty +show-config` 预检确认本机当前默认 `quick-terminal-position = top`，与用户反馈一致。
- 配置脚本已在本机临时目录验证写入与校验；本机真实 Ghostty 配置未被修改。

**仍待用户验收**：重启工作台后，`/sidebar` 或 `Ctrl+G` 确认 split 打开并自动调到约 70/30、跟随窗口尺寸、退出工作台关闭侧栏；`/sidebar quick` 确认原来的浮层模式；以及两种模式下主 TUI 的视觉与输入体验。

## 已知限制与后续

- quick terminal 锚定屏幕边缘，不跟随 Ghostty 窗口尺寸；宽度/高度由配置固定，窗口最大化时会覆盖右侧一窄条（覆盖而非挤压）。需要跟随窗口时用 `/sidebar split`，但它会减少 TUI 宽度。
- quick terminal 与 split 的关闭依赖侧栏进程退出；如果 Ghostty 保留了“进程已退出”的 surface，属于终端行为，需要单独确认。
- quick terminal 全局唯一、跨 tab 存在；本切片假设一个 Ghostty 进程只开一个工作台侧栏。
- 标题识别依赖侧栏进程设置的终端标题；若标题被覆盖，可能退化为 `opened` 提示手动运行。
- 尚未双向交互：侧栏不能把选中文件/diff 回传给主工作台（如“附到下一条 prompt”）。
- 后续：`new window` + 手动摆位；macOS 原生 Terminal（AppleScript `do script` / 窗口 bounds 需单独验证）；供 `Ctrl+G` 的侧栏宽度记忆与多显示器策略。
