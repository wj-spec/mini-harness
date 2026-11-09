# 右侧 Git 变更侧栏（Ghostty / macOS Terminal）

日期：2026-10-25。状态：工程实现、自动回归与 AppleScript 语法校验完成；真实 Ghostty 终端里的 quick terminal 打开、切换与视觉验收待用户确认。

## 2026-11-09 启动竞争与重复登记

侧栏新增启动与实例占用：同一工作台的多模式启动、不同会话的 quick 启动不会同时进入宿主操作；重复 CLI 拒绝覆盖登记，正常退出和强制结束后可重开。完整 Ink 643/643、Python 1915 passed / 4 skipped、八组并发压力与实际 PTY 复验通过；评审补齐路径别名竞争，Ghostty 原生 GUI 因工具安全限制未能核验，真人完整旅程与 Owner Review 仍待完成。见 [本批证据](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#侧栏启动竞争与重复登记2026-11-09)。

## 2026-11-09 内嵌专注检查

主区新增可点击的专注检查，与独立侧栏共用变更、准确计划和验证阅读；返回保留草稿/光标、页码与选区。无法自动分屏时，结果入口进入内嵌视图；新审批只提示返回处理，停止仍指向当前运行。全量 Ink 638/638、Python 1915 passed / 4 skipped 与实际终端复验通过；Ghostty 原生、完整真人旅程与 Owner Review 仍待完成，P6 保持实施中。在主区点击“专注检查”，窄屏为“检查”；Esc/q 或“返回草稿”返回，检查区内可再打开“独立侧栏”。详见 [证据](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#内嵌专注检查2026-11-09)。下方按增量保留当时状态。

## 2026-11-09 worktree 文件来源

检查记录已保存任务实际文件目录，attach 和历史选择据此读取 worktree 的当前 diff；项目与任务中的同名文件不会混用。目录失效或旧记录缺少来源时明确说明，附回选区同时复核来源与版本。实际后台写入、当前/重开侧栏和目录替换拒绝通过；内嵌专注检查、完整真人旅程和 Owner Review 仍待完成，P6 保持实施中。侧栏在读取前确认来源，标题区区分“任务目录 / 工作区”；文件路径详情与附回引用包含目录。切换来源时，同名文件的内容、滚动和已读状态重新归属。变更仍是所选来源当前目录与 HEAD 的比较，未保存历史 diff。见 [验证记录](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#worktree-文件来源与-diff-路由2026-11-09)；下方保留各批当时状态。

## 2026-11-09 attach 只读接入

独立 attach 已接通绑定任务的历史、准确计划与保存的验证事实；查询不进入执行队列，不广播给其他连接。worker 退出后保留离线只读入口与草稿，无 socket 时可读取已有记录。实际 worker/Ink/侧栏 PTY 通过，读取没有增加模型调用。worktree 文件来源/diff、内嵌专注检查和 Ghostty 真人/Owner Review 仍待完成，P6 保持实施中。“历史结果”按 attach 的任务筛选；选择准确计划后使用原有只读正文通道。离线不能执行或审批，未知结果不补猜完成。此项不证明 worktree 的文件/diff 已正确路由。完整证据见 [验证记录](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#attach-任务范围与离线检查2026-11-09)；下方保留各批当时状态，以本节为准。

## 2026-11-09 重开历史结果

主工作台点击“历史结果”或输入 `/inspections`，按原运行选择变更/计划/验证。侧栏沿用准确结果身份，重开后可读原命令退出码与修订 v2；未结束记录明确未知。历史查询不会启动模型或替换当前运行、审批、草稿和取消目标。分页失败保留原页，重试原游标；空记录和跳过损坏记录有提示。

完整 Python 1908 passed / 4 skipped、TUI 624/624，实际重开和侧栏读取证据见 [验证记录](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#重开历史结果入口2026-11-09)。独立 attach 与内嵌专注检查仍未接通；历史文件列表不等于历史 diff。以下保留分批实施记录，以本节为当前状态。

## 2026-11-09 持久检查记录基础

后端已随会话保存每次请求的文件路径、实际命令结果、任务/段与准确计划/修订身份，提供只读分页。最终回执缺失时保持未知；读取不执行任务，也不把新运行混入旧结果。保存层已通过实际正常结束、突然退出和重开核验，见 [验证记录](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#持久检查记录与只读分页2026-11-09)。

侧栏目前仍使用进程内结果；历史结果界面、独立 attach 的完整记录接入及内嵌专注检查未实现。持久文件列表不是历史 diff，当前 diff 仍取工作区。

## 2026-11-09 修订来源与产物

当前进程中发起修订时，检查区显示“修订来源”，绑定原计划的完整身份及修订任务/运行段。新版本发布后显示“修订产物”；正文、标题和哈希来自准确的新版本。正在阅读原版时只提示更新，点击“接入”才替换。修订结束结果下的“计划”也指向其产物，晚到回执只补原结果，另一运行不受影响。

修订产物表示计划已经发布，不代表计划已执行或验收通过；验收字段保持未知。错误任务、段、会话、来源哈希或冲突产物不得改写既有绑定，较旧回执不能撤回已确认产物。此关联暂不跨重启恢复，独立 attach 结果读取另行实施。

完整 TUI 618/618、Python 1891 passed / 4 skipped，真实后端取消后续做的第二段及原生终端固定阅读、结果入口、32×12 与中文光标通过，见 [本批证据](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#修订运行与准确产物关联2026-11-09)。

## 2026-11-09 计划跨页选区与版本已读

计划正文拖选后也可复制或附回。跨页时点击“续选”，翻到下一页后拖选终点，再点击“附回”；连续原文不会因分页额外插入换行。主区按准确计划身份、版本哈希和 Unicode 字符范围重读核对，出现新版则拒绝旧引用并保留选区。选区最多 32KB，侧栏保留最近三页；跨度过大或内容不在缓存时提示重选。

变更页提供“标已读 / 撤销”，标记只绑定当前文件所看 diff 版本。固定阅读期间发现新内容，显示“新版未读 · 旧版已读”；接入新内容后仍需重新确认。截断或读取失败内容没有完整已读按钮。标记只是阅读记录，不代表测试通过、审批或执行许可；进程内最多保留 200 个文件的最近标记，重开不恢复。

完整 TUI 613/613、Python 1891 passed / 4 skipped，原生跨页选择/附回、旧版拒绝、中文光标和 32×12 标记通过，见 [本批证据](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#计划跨页选区与版本已读2026-11-09)。历史/attach、修订完整关联、内嵌专注检查及 Ghostty 真人仍待完成；以下保留各历史增量。

## 2026-11-09 变更与验证选区附回

在变更或验证正文拖选后，可点击“复制 / 附回 / 清除”。附回保留已有输入和光标，仅追加带来源的引用；需收到成功回执才算附入，重复点击不重复追加。主区核对会话、原请求和内容版本；文件被外部改写或内容不符时拒绝，选区保留以便复制。32/20/12 列按钮按空间分行。

有选区的侧栏在主区退出后保留，只能阅读/复制；清除选区或 q 退出。无选区仍随主区关闭。完整 TUI 608/608、Python 1891 passed / 4 skipped，原生鼠标、版本拒绝与退出保留通过，见 [本批证据](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#选区附回草稿与退出保留2026-11-09)。未发送草稿/选区不跨进程恢复；计划跨页选区、已读与完整历史/专注检查仍待做，原生窗口与真人验收单列。

## 2026-11-09 主区结果入口

当前进程内结果下有可点击“变更 / 计划 / 验证”，打开绑定侧栏并选择该结果。侧栏显示“结果快照”，后续任务运行不替换其计划/验证/触及标记；主区“回到当前”接入当前运行。计划晚回执只补原结果；中文草稿、光标和当前停止目标保留。diff 仍是当前工作区相对 HEAD 的内容，不是历史版本。

完整 TUI 601/601、Python 1891 passed / 4 skipped，实际点击审批、跨轮固定/返回和离线重开通过，见 [本批证据](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#主区结果入口与同轮快照2026-11-09)。重开历史/独立 attach 结果、内嵌专注检查、已读、附回及真人仍待完成；以下保留历史增量。

## 2026-11-09 可点击检查页导航

顶部常驻变更/计划/验证，颜色和 `›` 标记当前页；点击切换后，各页保留原内容及阅读位置，计划正文不重复加载。新对象点击“接入”后替换，空页接收首份记录；当前 diff 有新版时“接入”，其他文件活动用“跟随”。隐藏页停用输入，验证支持滚轮；32 列保留完整入口，12 列缩为单字。

全量 TUI 595/595、Python 1891 passed / 4 skipped，原生终端完成审批/恢复及四页正文切页保位，见 [本批证据](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#可点击检查页与独立保位2026-11-09)。主区旧结果入口、已读和附回仍待完成；以下各节保留历史增量。

## 2026-11-09 完整计划正文更新

绑定主工作台且有准确计划身份时，`p` 读取完整正文；每页绑定原会话、计划版本和哈希，主区只转发已知对象的只读请求。滚轮 / ↑↓ 翻阅，点击上页/下页/重试/返回或用 ←→ 换页；失败保留当前页，重试原偏移。新对象仍需 `r` 显式接入，侧栏阅读不改变主区草稿和执行状态。窄栏保留页码，缩放按源行保位。

全量 TUI 591/591、Python 1891 passed / 4 skipped；真实后端与原生 PTY 读取四页中文/emoji 计划，零模型调用，详见 [本批证据](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#计划正文与只读请求通道2026-11-09)。历史结果入口、统一检查页导航、已读和附回仍待完成。以下各节保留分批实现记录。

## 2026-11-09 固定阅读与验证记录更新

`p` 打开计划摘要、`v` 打开当前请求的验证记录；两者固定内容与对象范围，后台更新只提示，`r` 接入、Esc 返回。验证页显示完成判定、命令、退出码及有界输出，支持中文换行和缩放保位；执行结果来自工具/harness 结构化事实，不解析自然语言。实际终端计划、审批、32×12 阅读和重开通过，详见 [本批证据](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#固定阅读与验证记录2026-11-09)。完整正文、历史结果入口、可点击导航、已读和附回仍待实现，以下保留分批记录。

## 2026-11-09 运行与计划范围更新

实例隔离后，侧栏进一步绑定当前请求与已知任务/运行段，并单列 Agent 追踪 ID。成功触及文件按请求筛选，新请求清空旧标记；完成后的费用查询保留该轮。计划摘要保留准确 ReviewRef，区分审阅、规划产物与执行，续做不继承旧验证；按 `p` 可核对版本和哈希摘要，未取得步骤结果时显示“进度未知”。重开历史不自动推断运行，普通输入没有持久任务时段号为空。

全量 TUI 579/579、Python 1888 passed / 4 skipped，实际后端允许/拒绝、主工作台与侧栏 PTY、会话重开零模型调用通过。修订/独立 attach 的完整结果关联、三类检查、固定计划阅读更新提示、已读、附回草稿和原生 Ghostty 仍待完成，见 [投影证据](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md#运行与计划投影2026-11-09)。以下保留各批实现记录。

## 2026-11-09 实例隔离更新

主工作台启动的侧栏绑定随机实例与父进程，信号和进程登记独立；退出侧栏只清理自己的登记，不删除主工作台信号。手动入口仅显示工作区，明确“未绑定主会话”。Ghostty 脚本按实例标题复用/校准，PID 与 TTY 定位来源；窗口关闭复核归属及用户是否新增 tab/pane。已有其他 quick 侧栏时提示使用 split/window。

完整 TUI 574/574、两个实际侧栏 PTY 的隔离通过，脚本编译通过；原生 GUI、并发 quick 启动仍待补验。task/run/segment、计划/验证检查与附回草稿仍在 P6 后续范围，见 [验证记录](../../openspec/changes/implement-improve-workbench-ux-p6/verification.md)。以下初版描述保留历史背景。

## 2026-11-08 稳定阅读更新

以下历史章节保留初版实现记录；当前阅读行为以 [使用指南](../USAGE.md#右侧-git-变更侧栏ghostty--macos-terminal) 和 [P2 实施约定](../../openspec/changes/implement-improve-workbench-ux-p2/design.md) 为准。

- 选择、滚动或查看路径进入固定阅读；`r` 接入新版，`f` 跟随最近有效成功写入；新版本、消失和查询错误均显式提示。
- 异步 Git 查询单次在途、重复触发合并；按完整 diff 内容识别版本，外部等行数更新也检测；最多保留两份受限文本。
- 40/44/60 列显示文件名、焦点、选择符与操作；路径可展开，缩放按原始 diff 行保留位置；开栏校准机制保持创建时执行。
- 工程与真人 Ghostty 验收分别记录，见 [P2 验证记录](../../openspec/changes/implement-improve-workbench-ux-p2/verification.md)。

## 背景与范围

用户希望在工作台之外、终端窗口右侧有一个跟随工作区的文件变更与 diff 面板，**不改变主 Ink 工作台的列宽与布局**。先支持 macOS + Ghostty；同日补齐 macOS Terminal 与独立窗口模式（`new window` / 手动摆位）。

本切片交付：

- 独立进程 `tui/src/sidebar/`：只读 Git 变更列表 + 选中文件 unified diff，1s 轮询跟随；
- Ghostty 三种形态：默认 `/sidebar`/`Ctrl+G` 窗口内右侧 split（跟随窗口尺寸）、`/sidebar quick` 屏幕右缘 quick terminal（不挤占 TUI）、`/sidebar window` 独立窗口（手动摆位）；
- macOS Terminal：没有 split/quick，`/sidebar` 自动使用独立窗口，按当前 TUI 窗口 bounds 放到右侧、同高；
- 主 TUI 的工作台入口、帮助与命令补全；
- 手动入口 `./run_sidebar.sh`，可用于任意终端或面板；
- 只读边界、错误降级与 AppleScript 语法校验。

不在本切片：从侧栏写回工作台（双向操作）、鼠标、side-by-side、语法高亮。

## 形态与依据

提供两种形态，命令可切换：**默认 `/sidebar`/`Ctrl+G` 用 Ghostty 窗口内 split**（跟随窗口尺寸），**`/sidebar quick` 用 quick terminal**（屏幕右缘浮层，不挤占 TUI）。

- quick terminal 是屏幕边缘的独立浮层窗口，不改变主终端窗口的行列数，Ink 的 120 列布局保持原样；
- `split` 会让主终端列数减少，Ink 在 `< 118` 列时隐藏内部状态侧栏，正是用户要避免的“挤占视觉效果”；
- quick terminal 由配置固定宽度（`quick-terminal-size`），可通过 `toggle_quick_terminal` 显隐，隐藏时进程仍保留。

**窗口尺寸限制（2026-10-25 补充）**：quick terminal 锚定在**屏幕边缘**，不是 Ghostty 窗口内部的 pane。右侧定位时它取屏幕可用区域、垂直居中（源码 `QuickTerminalPosition.finalOrigin`）；`quick-terminal-size` 只接受屏幕百分比/像素，默认把非主轴方向铺满屏幕，因此**不会随 Ghostty 窗口移动或缩放**。用户反馈“没有跟随 Ghostty 窗口尺寸”即由此而来，无法通过 AppleScript 或配置改变。

为了在需要“跟随窗口”时可用，新增 **split 模式，并设为默认**（2026-10-25 用户反馈 quick terminal 不跟随窗口尺寸后调整；`/sidebar quick` 保留原形态）：

- 命令：`/sidebar`、`/sidebar split`、`Ctrl+G`；quick 模式为 `/sidebar quick`；
- AppleScript：`new surface configuration` 设置 `initial working directory`，`split <terminal> direction right with configuration` 后向新终端 `input text` 启动命令、`send key "enter"`；
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

## macOS Terminal 与独立窗口

- **macOS Terminal**：`do script ":"` 创建新窗口并尝试用 `bounds` 放到右侧、同高，再 `do script "<启动命令>" in newWindow` 启动侧栏。实测自动摆位仍不准确（窗口出现但未靠右），已按用户决定降优先级；当前只保证创建与退出关闭，位置可手动拖。Terminal 不支持 `make new window`（AppleScript -10000）。
- **Ghostty 独立窗口**：`new window with configuration`，配置 `initial working directory` 与 `initial input`（启动命令 + return），返回 `started:<window id>`；Ghostty sdef 不暴露窗口 bounds，因此只能手动拖到右侧。
- 工作台把 `{host, windowId}` 记在内存中；退出 TUI 时先 `SIGTERM` 侧栏进程，再按 host 关闭窗口（Terminal `close window id` / Ghostty `close window`）。侧栏内 `q` 正常退出时，Terminal 通常随 shell 结束关闭，Ghostty 窗口随 surface 结束关闭；异常崩溃时窗口可能残留，需要手动关闭。
- 已有标题匹配的侧栏时返回 `focused` 并前置窗口，不重复创建。

## Agent 活动联动（2026-10-25）

主 TUI 在工具结果/回合状态变化时写一个轻量信号文件（`tmpdir()/mini-harness-sidebar/<sha1(workspace)>.signal.json`，与 lifecycle link 同目录）：

- 内容：`version`（条目数/最后条目/阶段/结果的稳定签名）、`files`（本轮成功的 `write_file` / `edit_file` / `apply_patch` 目标路径）、`active`、`updatedAt`；
- `apply_patch` 的路径从 diff 头（`diff --git` / `+++ b/`）提取；失败或不确定的调用不标记；
- 侧栏每 200ms 读取信号：版本变化时立即刷新 Git 状态，标记 `●`，跟随模式自动选中最新 Agent 改动的文件；1s 轮询保留为兜底；
- 侧栏退出时删除信号文件；TUI 继续运行会在下一次事件重新写入。无需 socket/改后端协议，Agent 与侧栏仍各自只读查询 Git。

回归：`tui/tests/sidebar-signal.test.*`、`sidebar-agent-activity.test.*`、`sidebar-app.test.*`（信号刷新/标记/跟随）、`app.test.*`（TUI 发布 payload）；TUI 全量 300 项、299 通过（唯一失败为沙箱无 PTY 的既有项）。

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
| macOS Terminal | 自动走独立窗口模式：按 TUI 窗口 bounds 放右侧、同高；退出 TUI 时按记录的 window id 关闭 |
| Terminal/未知终端 | 未知终端仍返回 `unsupported` 并提示手动命令 |
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
- `tui/tests/sidebar-host.test.ts`：终端识别、shell 转义、AppleScript 生成、quick/split/window 结果映射、Terminal window id 与关闭脚本、quick terminal 位置预检、`--parent-pid` 注入、权限/不支持降级，以及用 `osacompile` 校验 quick/split/window/close 脚本语法。
- `tui/tests/sidebar-args.test.ts`：`--workspace/--theme/--refresh-ms/--parent-pid` 解析与非法值。
- `tui/tests/sidebar-lifecycle.test.ts`：父进程存活判断（ESRCH/EPERM）、watcher 只触发一次/可停止、按 workspace 登记的 PID 只会终止同一父进程的侧栏，并用真实子进程验证 `SIGTERM` 生效；link 文件含侧栏列宽供比例校准。
- `tui/tests/sidebar-split-ratio.test.ts`：目标列数计算、70/30 校准收敛、已达标不 resize、拿不到列宽时放弃。
- `tui/tests/sidebar-setup.test.ts`：用临时 `HOME`/`XDG_CONFIG_HOME` 验证配置脚本的 `--dry-run`、幂等写入、拒绝覆盖非 right 配置；不触碰真实用户配置。
- `tui/tests/sidebar-app.test.tsx`：真实 Ink 组件渲染（分支/列表/diff/跟随/错误/键盘/宽度不溢出）。
- `tui/tests/app.test.tsx`：`/sidebar`（默认 split）、`/sidebar split`、`/sidebar quick`、`Ctrl+G`、`/sidebar setup` 调用注入的启动器/配置命令，未连接工作区与 `unsupported` 提示。

本轮工程验证记录：

- `npm --prefix tui test`：**292/293 通过**，唯一失败是沙箱无 PTY 的既有环境项（`interactive CLI enters and restores the alternate screen`）。
- AppleScript 已通过 `osacompile` 语法编译校验；未在本机执行 `osascript`，避免触发用户的终端自动化授权或切换其窗口。
- `./run_sidebar.sh --help` 正常；`readWorkspaceStatus` 在本仓库实际读取变更文件与 diff；`ghostty +show-config` 预检确认本机当前默认 `quick-terminal-position = top`，与用户反馈一致。
- 配置脚本已在本机临时目录验证写入与校验；本机真实 Ghostty 配置未被修改。

**仍待用户验收**：Ghostty 下 split 自动 70/30、跟随尺寸、退出联动；`/sidebar quick` 浮层；`/sidebar window` 手动摆位；macOS Terminal 下 `/sidebar` 自动右侧窗口、退出时关闭；以及主 TUI 的视觉与输入体验。

## 已知限制与后续

- quick terminal 锚定屏幕边缘，不跟随 Ghostty 窗口尺寸；宽度/高度由配置固定，窗口最大化时会覆盖右侧一窄条（覆盖而非挤压）。需要跟随窗口时用 `/sidebar split`，但它会减少 TUI 宽度。
- quick terminal 与 split 的关闭依赖侧栏进程退出；如果 Ghostty 保留了“进程已退出”的 surface，属于终端行为，需要单独确认。
- quick terminal 全局唯一、跨 tab 存在；本切片假设一个 Ghostty 进程只开一个工作台侧栏。
- 标题识别依赖侧栏进程设置的终端标题；若标题被覆盖，可能退化为 `opened` 提示手动运行。
- 尚未双向交互：侧栏不能把选中文件/diff 回传给主工作台（如“附到下一条 prompt”）。
- 后续：`new window` + 手动摆位；macOS 原生 Terminal（AppleScript `do script` / 窗口 bounds 需单独验证）；供 `Ctrl+G` 的侧栏宽度记忆与多显示器策略。
