# 当前 TUI 调研：先让编码过程可读、可检查、可控制

日期：2026-10-20。状态：调研与路线更新；迁移第一切片已实现并通过自动验证，真实模型待验收。下列原始调研保留当时的 Textual 问题与选型依据，不代表所有问题都已修复。

## 选型更新：纳入长期全栈学习目标

用户进一步明确界面开发也是长期学习经验，希望朝全栈深入发展。基于这一目标，新 TUI 选择 **Node.js + TypeScript + React/Ink**，保留 Python Agent 后端。先用本地子进程和 JSON Lines 建立输入、流式事件、审批、取消与恢复的边界，再按真实编码任务分段验收；当前 Textual 入口继续使用到新入口验证完成。

首个实现已提供 `run_ink.sh`、Python stdio 后端与协议测试，支持新任务、流式文本、原生工具单次审批、取消和会话记录；恢复与 MCP 尚未接入。当前固定 Ink 8.0.0、React 19.3.1，依赖以 `tui/package-lock.json` 为准。范围与证据见 [迁移规格](../specs/ink-entry.md)。

迁移用于学习 TypeScript、React、状态管理和前后端通信。浏览器 DOM/CSS、HTTP API、鉴权与部署在后续浏览器入口中继续实践。该选择不包含跨框架性能优劣的实测结论；下列界面缺陷仍需逐项修复。方向已同步到 [建设方向](../roadmap.md) 与 [迁移切片](../MILESTONES.md#界面迁移与全栈学习切片)。

## 初始结论：以当前编码可靠性为优先

当前 TUI 的主要问题是执行结果的可信度、信息呈现和交互连续性，现有 Textual 8.2.8 已有足够的基础组件，不需要先更换框架。建议优先让失败命令显示真实状态，并修复长回复不可见、工具参数刷屏、结果无法展开；再完善多行输入、输入历史、运行状态和命令发现。运行中纠偏和可靠恢复应分别按 W2、W3 验收，不能仅通过更换输入组件宣布完成。

依据是当前 [建设方向](../roadmap.md)、[里程碑](../MILESTONES.md)、`app/tui.py`、`tests/test_tui.py`、实际环境 `.venv/lib/python3.12/site-packages/textual/` 的 8.2.8 源码，以及下列官方文档。结论区分源码观察、局部实测、框架能力与建议；未使用真实付费模型或外部 MCP 作为验收。

## 已有能力应保留

- 当前界面已有模型流式文本、审批预览与单次 / 精确规则 / 工作区授权、撤销权限、取消、退出、预算与上下文状态、会话恢复提示，以及 `/cost`、`/diff`、`/permissions`、`/resume`、`/help`。
- FastMCP 启动在 TUI Worker 中进行，服务启动与调用已有审批；审批等待期间取消、退出和恢复输入已有回归。完整工程的 339 项测试是此前基线，不能把这些基础重复列为未实现。
- `handle_turn` 和 `initialize_mcp` 已使用异步 Worker。需要处理的是仍然同步阻塞的局部路径，而不是再加一个异步框架。
- 当前交付主线仍是 W1：可信执行结果与真实任务闭环。TUI 改进要帮助核对实际操作、失败与验证证据；长期记忆和完整个人助手界面尚未交付。

## 本地发现与影响

| 发现 | 证据与影响 | 建议方向 |
|---|---|---|
| 命令失败仍显示绿色 `ok` | 经过 `ApprovalGate` 实际执行 `exit 7`，工具回调仍是 `ok=True`，Agent 结束状态为 `done`；任务记录的 `Tests run` 只记下执行过的命令，不提供通过证据。`app/tools.py:194–203` 返回含退出码的字符串，`app/tui.py:343–347` 根据外层布尔值染色 | 按已有“可信命令结果”切片提供退出码、超时、取消与未知结果等明确字段，loop、记录和界面使用同一事实。执行结束、检查通过、任务完成分别呈现 |
| 流式回复与历史被分成两个区域 | `#stream` 是 `max-height: 10` 的 `Static`；每个 delta 都把累计全文交给 `Static.update`。80×24 实测中日志区域从 16 行缩到 6 行；发送 50 行时只显示 `LINE_00`–`LINE_09`，后 40 行在完成前不可见 | 一条回复从生成到完成使用同一个可滚动消息组件；跟随最新内容，用户主动回看时停止自动跟随 |
| 调用工具前的说明从屏幕消失 | 同一回复先输出说明再调用工具时，Agent history 保留说明，`_on_tool` 却清空流式区，日志未收束这段说明。探针确认中间说明在历史中存在、界面中没有 | 在原消息位置保留说明和部分文本，再展示工具动作；显示记录独立于模型上下文的压缩窗口 |
| 工具参数没有摘要层 | `_on_tool` 直接输出 `str(call.args)`。约 20k 字符的 `write_file` 内容实测形成约 261 行日志 | 默认显示工具、目标文件 / 命令、状态；完整参数按需展开，写改内容放审批差异视图 |
| 工具结果被切断且没有入口 | `_on_result` 只显示 `output[:400]`，没有截断提示、展开操作或全文路径 | 默认摘要 + 明确截断提示 + 全文入口；错误与命令退出结果优先展示，完整证据保留在会话记录 |
| 内容没有按语义渲染 | 完成回复被 `escape` 后写入 `RichLog`；标题、代码块、列表和表格仍以原始 Markdown 文本呈现 | 回复采用 Markdown，工具输出保持纯文本 / 代码语义，避免对任意输出套用富文本标记 |
| 输入与任务执行互斥 | 使用单行 `Input`，运行时 `disabled=True`。Up 实测不能找回上一条用户输入 | 先支持多行编辑、草稿、输入历史；运行时保留草稿是界面改进，提交纠偏需要另定义 W2 的消费边界 |
| 状态数值多，当前动作不明确 | 常驻状态主要是 tokens、context、todo 计数、模型和路径；没有完整的等待模型、调用工具、等待审批、停止中、已停止等状态与耗时 | 先呈现可观测状态，再补所缺事件。现有 trace 不能直接提供所有工具耗时；模型维护的计划也不能作为验证通过证明 |
| `/diff` 可能阻塞所有界面响应 | `show_diff()` 在同步输入处理器中直接调用 `run_command(timeout=10)` | 命令走既有审批和审计边界，并放入可取消的异步执行路径；显示“读取中”与实际退出结果 |
| 变更与历史回顾不足 | `/diff` 仅展示工作区未暂存差异的末尾 4000 字符，不包含未跟踪文件和已暂存变化；恢复只回显末尾 6 条消息，每条再截到 160/300 字符 | 逐文件查看差异，明确统计范围；恢复页显示目标、已改文件、验证证据、未知结果和下一步，并提供历史检索。工作区全部改动不能直接归因于本次 Agent |
| MCP 状态只在启动日志里出现 | 当前可看到启动成功数量与失败消息，缺少服务状态和工具目录入口；暴露名称可能带防碰撞摘要 | 在核心阅读与输入改进之后，按需提供 `/mcp` 等详情入口，显示服务来源、连接状态、原始工具名和失败原因，重连仍执行审批 |
| 窄终端和长会话缺少专门策略 | `RichLog` 未覆盖默认 `min_width=78`、`auto_scroll=True`、`max_lines=None`，当前状态区固定最多 3 行 | 明确最小支持尺寸；窄屏压缩次要状态，回看时不抢滚动；显示层按需加载历史，不删除持久化记录 |
| 命令需要记忆，未知 `/命令` 会进入 Agent | 当前只有硬编码分支和 `/help` 文本，未注册产品命令 Provider | 用统一命令表驱动帮助、`/` 提示、快捷键与命令面板；未知命令先在本地提示 |

命令非零退出、尺寸 / 长文本、说明消失与输入历史来自本轮本地探针；这些是确定性界面复现，不代表真实模型任务已验收。回看后追加新日志也已复现自动跳到底部：80 列 `scroll_y` 从 0 跳到 250，120 列从 0 跳到 227。同步 `/diff` 的响应性结论来自调用链分析，尚未以慢命令计时场景确认。更窄终端、长会话内存和真实性能仍待测量，不把默认值直接当作所有终端上的已复现故障。

| 终端尺寸 | 空闲日志区（含边框） | 长流式回复时日志区 | 审批预览区 |
|---|---:|---:|---:|
| 80×24 | 16 行 | 6 行，正文仅 4 行 | 11 行 |
| 120×40 | 32 行 | 22 行 | 24 行 |

## Textual 8.2.8 能做到什么

### 流式 Markdown：已有组件，需按消息组织

官方 `Markdown` 支持标题、列表、代码块、表格；`Markdown.get_stream()` 会在输入快于渲染时合并多个片段。官方示例使用 Worker、`VerticalScroll.anchor()` 与 `finally: await stream.stop()`。本地 8.2.8 存在对应 `MarkdownStream.write/stop` 实现。[Markdown 官方文档](https://textual.textualize.io/widgets/markdown/#textual.widgets.Markdown.get_stream)、[8.2.8 源码](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/widgets/_markdown.py#L41)

工程判断：可以替换现有“累计全文 Static + 完成后重新写日志”的显示过程，减少每个 delta 的全量更新；是否更流畅仍需长回复压测。框架提供流合并，不会自动完成本项目的消息标识、取消后的部分文本保留、恢复记录或滚动意图管理。长会话创建大量 Markdown 子组件后的性能也需要测量。

### 多行输入：TextArea 可复用，快捷键需先定规则

`TextArea` 默认多行与软换行，提供选区、粘贴、撤销 / 重做、占位符和建议文本。代码高亮是可选依赖，普通多行提示词不必为了输入组件额外引入语法分析器。它的编辑历史是撤销栈，不是聊天输入历史。[TextArea 官方文档](https://textual.textualize.io/widgets/text_area/)、[8.2.8 源码](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/widgets/_text_area.py#L112)

本地绑定中 `Ctrl+C` 用于复制，会与当前取消任务语义冲突。替换输入框前应确定：选区复制、任务取消、清空草稿分别由谁处理；审批弹窗中的优先取消行为必须保留。发送与换行要有显示在界面上的规则，并保留 `Ctrl+J` 等可验证的替代方式。`Shift+Enter` 是否可用取决于终端和产品支持，不能把它当作唯一入口。

同类证据：Gemini CLI 官方将提交、换行、输入历史、外部编辑器和排队消息分别定义，并列出终端相关的按键限制。这支持“编辑”和“改变正在执行的任务”分开设计，不能照搬某套按键就认为所有终端都可用。[Gemini CLI 官方键盘参考](https://geminicli.com/docs/reference/keyboard-shortcuts/)

### 工具卡片：Collapsible 可以实现渐进展开

`Collapsible` 可以包含其他 Widget，支持初始折叠、动态标题、键盘 Enter 展开和展开 / 收起消息。适合默认显示摘要，按需打开参数、输出与错误。它本身不提供调用状态、耗时、退出码或会话检索；这些仍要从本项目事件和记录中组织。[Collapsible 官方文档](https://textual.textualize.io/widgets/collapsible/)、[8.2.8 源码](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/widgets/_collapsible.py)

建议以一次工具调用为单元绑定请求和结果，而不是继续向一个日志追加两段互不关联的字符串。失败结果默认展开；成功结果默认展示摘要。完整结果若已离线存放，应通过明确路径打开，避免只保留显示中的 400 个字符。

### 并发：Worker 已有，阻塞代码仍须移出事件循环

Textual 官方说明，消息处理器等待慢操作会延迟同组件消息处理；同步阻塞操作更会占住事件循环。异步 Worker 适合异步 I/O，`thread=True` 可承接同步 API；线程不能用普通取消强制中止，也不能直接安全地修改 UI。[Workers 官方文档](https://textual.textualize.io/guide/workers/)

建议只收敛现有同步热点：`/diff`、可能较大的会话读取 / 汇总。需要保证底层命令取消和进程清理，不能把“Worker 返回超时”展示成“命令已经终止”。这与 W2 已有验收要求一致。异步 `@work` 中直接执行同步慢函数，仍然会阻塞。

### 命令发现：已有 CommandPalette，不必自建搜索框

Textual 自带命令面板；默认入口是 `Ctrl+P`，可以通过 `Provider.search/discover` 添加产品命令、模糊搜索、说明与回调。当前 App 没有添加自身命令 Provider，所以不能把框架默认面板算作已经覆盖 `/resume`、`/permissions` 等产品操作。[Command Palette 官方文档](https://textual.textualize.io/guide/command_palette/)、[8.2.8 App 源码](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/app.py#L427)

`/` 就地补全与命令面板是两种入口：前者可用轻量建议列表，后者复用框架。两者应共用命令定义，按当前状态显示是否可用。`TextArea.suggestion` 只能补建议文本，不能自动承担多候选选择和命令参数校验。

### 响应式与回归：框架支持断点与尺寸测试

8.2.8 提供 `HORIZONTAL_BREAKPOINTS`、`VERTICAL_BREAKPOINTS`，会按终端尺寸为 Screen 设置 CSS 类。可以先保留纵向单列布局，在小屏隐藏次要说明、缩短状态；不必先增加固定侧栏。[8.2.8 App 断点源码](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/app.py#L488)

`run_test(size=(宽, 高))` 与 Pilot 可做无终端交互回归；官方 `pytest-textual-snapshot` 提供 SVG 截图比较。现有 `tests/test_tui.py` 已使用 Pilot 验证审批、取消、恢复与 MCP，但尚未形成多尺寸视觉基线。[Testing 官方文档](https://textual.textualize.io/guide/testing/)

建议覆盖 80×24、窄屏和低高度，并在发送、长流输出、审批、取消、展开工具结果、恢复旧会话后检查布局。截图用于发现裁切与覆盖，行为断言用于确认不会误发、多次执行或丢失文本。中文输入法、真实粘贴、终端按键编码、剪贴板和滚轮还需要在用户实际终端验证，headless 不能替代。

## 同类产品的可借鉴做法

Claude Code 官方把多行输入、历史搜索、简洁 / 详细记录切换、运行中消息排队和 diff 审阅定义为独立交互。Gemini CLI 官方提供紧凑工具结果、状态栏配置和快捷键帮助。这些资料说明可以逐步展开细节，让用户保持对过程的理解；它们不是本项目应完整复制的功能清单。[Claude Code 交互模式](https://code.claude.com/docs/en/interactive-mode)、[Gemini CLI 界面配置](https://geminicli.com/docs/reference/configuration/)

本项目适合先借鉴：工具默认摘要、失败详情可见、完整记录可回看、输入草稿与运行任务分开，以及把运行中输入标为“待处理 / 已采用”。是否允许立即纠偏要根据本项目的执行边界设计，不能照搬其他产品的发送语义或权限模式。

## Python / Textual 与 TypeScript 的取舍

Ink 官方提供 React 组件与 TypeScript 项目入口，应用运行在 Node.js 进程中。[Ink 官方仓库](https://github.com/vadimdemedes/ink)

工程判断：若保留当前 Python Agent、工具、审批与会话实现，再迁移到 Ink，需要增加 Python 与 Node 之间的事件和控制接口，覆盖流式消息、审批应答、取消、错误与恢复；这是本项目架构推断，不是 Ink 官方对本项目的结论。当前没有发现 Textual 无法支持首轮改进的组件瓶颈，因此建议继续 Textual。待实际需求证明有明确收益，再单独评估第二种界面。

## 建议的决策顺序

| 次序 | 候选切片 | 解决的问题 | 验收证据 |
|---|---|---|---|
| 1 | 结果可信、回复与工具显示可靠 | 失败显示成功，长输出不可见，参数刷屏，结果详情不可达 | `exit 7` 明确显示失败；50 行以上流文本能看到最新内容；回看不抢滚动；20k 参数默认摘要；400 字以上结果可明确展开 |
| 2 | 输入编辑与命令发现 | 单行输入、无历史、命令靠记忆 | 多行粘贴与发送不误触；历史恢复保留草稿；未知命令不调用模型；审批 / 取消回归通过 |
| 3 | 执行状态与证据 | 用户难分等待、执行、审批、停止 | 同一工具卡显示实际状态、耗时与结果；命令退出状态和结果未知明确区分；`/diff` 慢操作不冻结 UI |
| 4 | 运行中纠偏和续做视图 | 对任务失去控制、恢复时不清楚证据 | 按 W2 / W3 的真实场景验收；定义何时接收新约束，副作用不自动重放，恢复前核对文件状态 |

这是依据当前 W1–W3 的建议顺序，不是新增里程碑或实现承诺。布局配色可在首个切片中顺带统一；常驻目录树、复杂多面板、完整编辑器、多 Agent 面板和长期记忆管理不属于本次优先范围，应等待真实任务需要。

## 尚待决定 / 验证

- 用户主要终端、日常窗口尺寸和鼠标使用习惯；是否希望 Enter 发送还是默认换行。
- 工具全文是在卡片内展开，还是打开单独详情页；第一版只需选择一种清楚的方式。
- 运行期间先只保留草稿 / 排队，还是直接接收纠偏；后者需要 Agent loop 的明确协议。
- 大会话组件数量、滚动、恢复加载与 Markdown 性能基线；不能据组件存在直接宣称性能达标。
- 真实终端中的中文输入、多行粘贴、复制、快捷键、滚轮，以及已有停止和审批行为是否一致。

## 来源与证据边界

全部技术能力依据 Textual 官方文档与 `v8.2.8` 一手源码核对；同类交互依据 Claude Code、Gemini CLI 官方文档，技术取舍参考 Ink 官方仓库。网页会更新，因此本地锁定版本是实现可行性的基准。本文没有将同类产品的功能数量当作本项目的建设目标。

本地应用证据：`app/tui.py` 的 `compose`、`handle_turn`、`_on_text`、`_on_tool`、`_on_result`、`show_diff`、`_status_text`；测试证据：`tests/test_tui.py`；方向依据：`docs/roadmap.md` 与 `docs/MILESTONES.md`。新增调研产物未改变 `app/`、`core/`、测试或既有建设文档。

本地可重复探针与截图保存在忽略目录 `.vibe/tui-research/`：`probe_tui.py`、`probe-results.json`、80×24 / 120×40 的 SVG 截图。重跑命令：`PYTHONPATH=. .venv/bin/python3 .vibe/tui-research/probe_tui.py`。截图是构造场景，字体效果与真实终端可能不同，不能据此认定中文输入法或字体兼容性已验收。

## 补充：Textual、Node.js / Ink 与 OpenTUI（选型前记录）

2026-10-20 补充核对。建议当前一轮继续 Textual；若明确将 TypeScript / React 定为长期界面栈，再选择 Python 后端 + Ink。这个判断依据当前 W1–W3 的验收缺口，而非已有 Python 代码本身。消息丢失、错误显示成功、输入和停止语义仍需产品与运行层共同处理，迁移渲染框架不会自动解决。

| 方案 | 官方能力与限制 | 对本项目的判断 |
|---|---|---|
| Textual | 已有 Markdown 流、多行 TextArea、滚动容器、折叠组件和无终端测试；上文给出当前安装版本的一手依据 | 能承接本轮阅读、输入和工具详情改进，单进程接线便于核对审批、取消和恢复 |
| Node.js + Ink | React 终端渲染器，使用 Yoga 布局，提供组件和输入 / 焦点等 Hooks；核心不是浏览器 DOM，完整聊天编辑器、Markdown 与滚动体验仍需组件组合与应用策略 | 适合明确希望长期使用 React / TypeScript 的界面；不应把 React 熟悉度直接当成已经有可用编码工作台 |
| OpenTUI | Zig 原生渲染核心，提供 TypeScript、React、Solid 接口，以及 Textarea、ScrollBox、Markdown 等内置组件 | 对复杂全屏工作台值得单独做小样；需要同时验证原生依赖、目标终端与运行时，当前还没有本项目的性能对比证据 |

Ink 的能力依据[官方仓库](https://github.com/vadimdemedes/ink)；其主分支 README 明示包含尚未发布的能力，实现时应锁定稳定版本。OpenTUI 的组件依据 [Textarea](https://opentui.com/docs/components/textarea/)、[ScrollBox](https://opentui.com/docs/components/scrollbox/) 和 [Markdown](https://opentui.com/docs/components/markdown/) 官方文档。组件存在不代表输入历史、运行中纠偏、流式跟随和完整输出保留已经实现。

OpenTUI 0.6.6 官方支持 Bun ≥ 1.3.14，或 Node.js ≥ 26.4.0 + ESM + `--experimental-ffi`，因此不能再称为 Bun 专用。部分扩展仍需 Bun；官方也明确 React 适配器尚无专门的 Node.js CI 通道，不能把 Core 的运行验证直接推广到每个适配器。[运行时与平台支持](https://opentui.com/docs/getting-started/runtime-support/)

本地 `app/agent.py` 的 `Agent.run()` 已通过 `on_text`、`on_tool`、`on_result`、`on_context`、`on_state`、`on_message`、`on_history` 和 `trace` 回调输出信息，取消使用 `asyncio.Event`；这些是界面可替换的基础，但目前是进程内 Python 接口。若引入 Node.js 界面，建议保留 Python 的 Agent、FastMCP、ApprovalGate 与会话职责，再定义有请求标识的事件 / 控制协议：启动或恢复任务、流式内容、工具事实、审批请求与应答、取消和退出。子进程断连不能默认批准待处理操作，stdout 协议流也需与日志分离。这是对本项目的工程设计判断，尚未实现。

双进程协议增加接线与回归工作，也能为未来第二入口提供可复用边界；若多入口已经成为近期明确目标，这项成本具有建设价值。当前 roadmap 仍要求先验证单 Agent 编码闭环，因此建议先用 Textual 完成一个可靠的消息时间线和多行输入切片，再依据真实终端体验决定迁移。只有出现已测得的 Textual 瓶颈、明确的长期 TypeScript 投入，或近期多入口需求时，才改变这个选择。本补充没有安装 JavaScript 包、实现桥接或宣称跨框架性能优劣。
