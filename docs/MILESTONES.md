# 工作台建设里程碑

长期目标：越用越了解我的个人助手工作台，同时持续积累界面与全栈开发经验。当前主线：可靠、可理解的 vibe coding；界面迁移随真实任务分段交付。

新路线使用 **W0–W7** 编号；旧 M1–M8 的模块建设记录保存在 [历史计划](history/MILESTONES-M1-M8.md)，不与本轮验收混用。方向与取舍见 [roadmap](roadmap.md)。按验收顺序推进，暂不绑定日期。

## 总览与当前状态

| 阶段 | 里程碑 | 用户获得什么 | 当前状态 |
|---|---|---|---|
| 编码基础 | W0：建立可复现起点 | 能运行、能回退、能解释一次实际 loop | 已有运行与测试基线，回退和学习记录待补齐 |
| 编码基础 | W1：完成一个真实小改动 | 输入需求后得到代码变化和实际验证结果 | 真实验收完成（2026-10-23）：真实小任务、审批拒绝演示、失败修正与交付核对均通过 |
| 编码基础 | W2：掌控运行过程 | 能中途纠偏、拒绝动作、可靠停止 | 真实验收完成（2026-10-23）：纠偏、三种停止边界、超时进程回收、取消后交付均通过 |
| 编码基础 | W3：隔天继续同一个任务 | 中断后能核对状态并续做 | 真实验收完成（2026-10-23）：中断/恢复、外部改动、新旧任务、正常恢复、压缩后总结均通过 |
| 个人上下文 | W4：新任务复用项目背景 | 同一项目少重复交代背景 | 完成（2026-10-23）：profile 读取/注入/Ink 查看增删改 + 两个真实任务验收 |
| 个人上下文 | W5：关于你——个人偏好与画像 | 能查看和修正助手对自己的理解，决定何时使用 | 完成（2026-10-24）：用户级偏好 + `/about` 全链路，真实验收通过 |
| 个人上下文 | W6：验证个性化的实际收益 | 重复说明和反复纠正确实减少 | 已按协议完成对照，主假设未获支持（见 W6） |
| 工作台扩展 | W7：承接第二种常用工作流 | 编码之外的一项真实工作也能持续开展 | 条件性方向，出现明确使用需求后细化 |

**阶段状态（2026-10-26）**：W1–W5 已通过真实验收，W6 已完成对照（主假设未获支持）；当前重点是把 `/goal` 长任务、个性化精细化与评测扩题做成可验收闭环。W0 的回退与学习记录随使用补齐；W7 保持方向，不提前拆成平台功能清单。

## 界面迁移与全栈学习切片

2026-10-20 用户明确把界面开发纳入长期全栈学习。新 TUI 选用 Node.js + TypeScript + React/Ink，保留 Python Agent 后端；默认仍运行 Textual，`run_ink.sh` 为新入口。2026-10-21 用户明确要求：默认入口继续保留 Textual，直到 Ink 的开发与实际体验超越 Textual。功能补齐和测试通过不会自动触发切换。切片服务于 W1–W3 的完成条件，不提升产品里程碑的真实验收状态。

**Ink 复制能力（2026-10-23）**：用户反馈全屏界面里无法复制 Agent 回复。[复制规格](specs/ink-copy.md)：`Ctrl+Y` 或 `/copy`，工具详情打开时复制参数与完整输出、审批打开时复制预览、否则复制最近一条回复；优先本机剪贴板命令（`pbcopy` / `clip` / `wl-copy` / `xclip`），失败回退 OSC 52；单次上限 1 MiB。复制是本地只读操作，不经过审批。首版只有 `Ctrl+Y` / `/copy` 整段复制；用户反馈仍希望像终端一样拖拽选中，因此新增应用内拖选：鼠标模式改为 1002，按下/拖动/松开实时高亮选区，松开复制所选文本（按单元格切片，宽字符整字，可跨行）。自动回归覆盖鼠标解析、单元格选区、剪贴板回退与三种复制目标；真实终端里的拖选与复制效果待用户验收。

**默认入口切换与 Textual 退役（2026-10-23）**：用户明确 Textual 后续不再使用，Ink 成为唯一界面入口。已删除 `app/tui.py`、`app/context_view.py`、对应测试与 `textual` 依赖；`./run_vibe.sh` 保留为 Python 端 `--stdio` 后端、`--eval` 与会话列表入口，交互界面统一使用 `./run_ink.sh`（Node.js 22+）。这是入口决定，不改变 W1–W3 的其余真实验收条目。

| 切片 | 交付与验收 | 学习重点 | 状态 |
|---|---|---|---|
| 1：接通新入口 | Ink 启动 Python 子进程，以 JSON Lines 发送任务并接收流式文本、状态与错误；协议输出和诊断日志分开。先用确定性模型验证，再用真实模型完成一次读取文件后回答 | TypeScript 类型、React 组件、事件流与进程生命周期 | 工程接线已验证；首版界面据用户反馈改版，新版体验与真实模型待验收 |
| 2：完成真实编码任务 | 接通多行输入、连续消息与工具结果；按可信命令结果展示非零退出、超时、取消和未知结果。审批请求有明确标识，拒绝与取消能到达后端；完成 W1 的失败—修正—再验证闭环，并验证 W2 的停止边界 | 状态管理、交互反馈、前后端职责与集成测试 | 全屏、多行、详情、审批、用量与结构化执行结果已接通；确定性失败—修正—通过回归已有，真实模型任务待验收 |
| 3：恢复与功能补齐 | 接通同一任务恢复、权限撤销和已有 MCP 流程；断开界面时不批准待处理动作，不自动重放结果未知的写入或命令。在 80×24、120×40 和实际终端验证中文粘贴、长输出、审批与取消，按 W3 核对恢复。默认切换需 Ink 超越 Textual 后单独验收 | 持久化、故障恢复、终端兼容与迁移验收 | 恢复、显式续做、MCP、分级授权与管理命令已实现；全量与本地终端验收见功能补齐记录，真实体验待验收 |

每个切片都记录你能解释的核心机制与实际运行证据。运行中纠偏仍按 W2 单独验收。之后根据实际需求增加浏览器入口，练习 DOM/CSS、浏览器状态与 HTTP API，再逐步加入持久化、鉴权和部署；浏览器能力不计入 Ink 迁移的已完成范围。

2026-10-21 用户反馈首版界面过于简陋，本轮按 [Ink 工作台规格](specs/ink-workbench.md) 补布局与日常交互，并将实际画面检查纳入验收。代码测试、构造视觉场景和用户真实体验分别记录。

同日继续按 [Ink 功能补齐](specs/ink-parity.md) 推进恢复、MCP、授权与命令结果。对比基线包括：原有编码功能可用、失败结果可信、恢复无误执行、取消能回收、中文与长输出易读。还需真实任务与使用反馈证明改进，再讨论默认入口。

随后根据实际使用中“无法滚动、展示缺少重点”的反馈，补齐 [历史阅读与状态分区](specs/ink-reading-state.md)：滚轮 / 触控板、阅读焦点、稳定回看、任务与工具卡片、状态栏与真实预算。自动回归及 80×24 / 120×40 PTY 通过；用户终端体验继续验收，默认仍保留 Textual。

同日按恢复历史反馈，进一步分离完整原始记录与压缩模型上下文，并新增只读会话列表、按 ID 打开旧会话；已用 219 条旧记录副本验证完整回看，见上述规格的恢复章节。此项补齐 W3 的工程基础，真实任务续做仍待验收。

**右侧 Git 变更侧栏（2026-10-25）**：用户要求在不挤占主 Ink 工作台的前提下，于终端右侧额外显示跟随工作区的 Git 变更与 diff。新增独立侧栏进程与只读 Git 数据层，通过 Ghostty 1.3+ AppleScript 的 `toggle_quick_terminal` 在右侧 quick terminal 启动；工作台入口为 `/sidebar` 或 `Ctrl+G`，手动入口 `./run_sidebar.sh`。主 TUI 布局未改，侧栏只执行固定参数的 `git status` / `git diff`，不经过 shell、不写工作区、不联网。首次真实使用发现 quick terminal 默认停在屏幕顶部（Ghostty 默认 `quick-terminal-position = top`，本机此前无配置文件），已修正为打开前用 `ghostty +show-config` 预检位置：不是 `right` 时返回 `misconfigured` 并提示 `/sidebar setup`，不再在顶部打开侧栏；新增 `scripts/setup-ghostty-sidebar.sh` 幂等写入右侧配置（临时目录回归，不碰真实用户配置），改配置后必须完全重启 Ghostty 生效。随后按“跟随窗口尺寸”与“随工作台关闭”的反馈补充：quick terminal 是屏幕边缘锚定窗口、无法跟随 Ghostty 窗口，因此新增 `/sidebar split`（窗口内右侧分屏，跟随窗口尺寸，但减少 TUI 宽度）；启动命令注入 `--parent-pid`，侧栏登记 PID 并在工作台退出时先 `SIGTERM`，崩溃时由 1s 父进程看护兜底，避免留下孤儿面板。为回应“尺寸没变”的再次反馈，`/sidebar` 与 `Ctrl+G` 默认改为 split，`/sidebar quick` 保留原 quick terminal 形态；split 打开时先在 AppleScript 内按估算 resize 到接近 70/30，侧栏启动后再按回报列宽校准，并把焦点交回主 TUI。工程验证：Ink `npm --prefix tui test` **292/293 通过**（唯一失败为沙箱无 PTY 的既有环境项），Python `./run_tests.sh -q` 退出 0（收集 886 项，含 4 项跳过）；生成的 AppleScript 通过 `osacompile` 语法校验。另补齐 macOS 原生 Terminal（独立窗口自动靠右、同高，退出 TUI 关闭）与 Ghostty `/sidebar window` 手动摆位模式。真实 Ghostty/Terminal 终端里的打开、切换、首次授权与视觉体验待用户验收。规格见 [右侧 Git 变更侧栏](specs/sidebar-git-panel.md)。

## 如何认定完成

- “已有代码”“自动测试通过”“真实使用验收通过”分别记录；只有验收证据和用户实际体验都成立，才标记已验收。
- 每次实现只推进当前里程碑中的一个小切片，按 [协作协议](VIBE-PROTOCOL.md) 先定行为、补测试、再实现和复核。
- fake / mock 用于确定性接线和失败路径测试；真实模型验收用于验证模型决策与任务完成能力，两者分别记录。
- 每个里程碑留下一个成功场景、一个失败场景，以及你能解释的核心概念。学习笔记保留五行：问题、原来的判断、运行证据、现在的理解、仍不清楚的地方。
- 验收记录写清版本或快照、模型与配置、初始条件、实际输入、结果及未解决项。运行记录放 `.vibe/`，可复现用例放测试或 eval；未通过时保留失败结果。
- 编码正确性、审批边界和结果真实性是持续门槛；后续记忆或界面能力不能以它们退化为代价。

## W0：建立可复现起点

**用户场景**：打开这个目录，可以稳定启动，并能指出一个请求经过了哪些代码、如何回到改动前。

**已有证据**：2026-10-20 已验证 122 个单元测试通过、fake TUI 启动和退出、脚本模型读取文件后两轮完成；同步教学循环已移除，`core/` 已通过无应用依赖的独立进程测试。这些是工程基线，尚不证明真实编码任务可用。本次编写计划不重复运行这些检查。

**Git 基线（2026-10-22）**：此前多轮工作已提交为 `1cc933b`（评测、技能生命周期、reasoning 协议与 Ink 更新），loop 拆分重构已提交为 `b021c66`，并打标签 `baseline-20261022`。`.vibe/`、`.venv/`、`.pylibs/`、`node_modules/`、`tui/dist/` 由 `.gitignore` 排除，不进入提交；`.vibe/agent-loop-refactor/` 保留重构前后的文件与验证日志供对照。该标签只表示代码可回退，不改变其他验收项。

**可复现起点补记（2026-10-23）**：Textual 退役后按 Ink 后端复跑：`./run_eval.sh doctor` 输出保存在 `.vibe/w0/doctor.json`；fake stdio 后端起完整一轮（ready → 状态/文本 → done → closed，退出码 0）记录在 `.vibe/w0/backend-fake.log`；拒绝写入与无审批时 fail-closed 的两项回归通过，记录在 `.vibe/w0/denial-tests.log`。真实模型“读文件后回答”需要本机 API key，本次环境未执行；代码走查与拒绝副作用的现场演示仍由你确认。

**验收**：

- [x] 建立当前版本可恢复的 Git 基线或独立快照（标签 `baseline-20261022`），区分源代码与 `.vibe/` 等本地产物。
- [x] 按记录重现启动、测试和一次“读文件后回答”，把真实模型与 fake 的结果分开标注。fake 启动记录 `.vibe/w0/backend-fake.log`；真实模型读文件与解释见 W2/W3 会话（如 `w3-resume`）。
- [ ] 你能沿 `app/__main__.py → app/stdio.py → tui/src/cli.tsx → app/agent.py` 讲清入口、模型调用、工具结果回灌和结束条件。
- [x] 你能说明写文件、执行命令在哪里经过 `ApprovalGate`，并演示拒绝后没有该动作的副作用。拒绝演示见 W2 审批取消：审计 `edit_file deny`、调用写成 `not executed`、文件哈希不变；代码位置在 `app/agent.py` 的 `_request_approval` → `ApprovalGate`。

**学习重点**：模型提出动作，harness 控制执行；一次 step 与一次完整任务的区别。

**完成后**：选定 W1 的一个任务，写出改前的失败条件和改后的验收条件。

## W1：完成一个真实小改动

**用户场景**：“修复这个小 bug，不修改测试的预期行为。”助手读取代码、提出必要动作、完成修改和验证，最后交付可核对的结果。

**范围**：一个小 bug 或一个小功能；只补齐该任务暴露的缺口，复用已有工具和循环。

**使用反馈（2026-10-20）**：用户确认当前项目已能针对指定项目路径下的文件进行增删改查。本轮接受该反馈作为工具层使用证据；完整编码任务的验证结果和交付仍按以下条件验收。

**当前切片**：[实际使用可靠性](specs/practical-use-reliability.md)。在初版预算暂停与分层压缩基础上，根据用户实际使用记录改为自动压缩后继续：总预算默认 1M，压缩周期 200k、80% 触发，累计消耗不清零。同时补齐工作区授权与撤销、审批等待不计时、取消弹窗与恢复输入。自动测试与真实使用验收分别记录，后者仍待完成。

**后续使用反馈**：[工具进展判断与降级](specs/tool-progress.md)。一次仍有写改进展的运行被 50 次工具上限打断；移除工具次数及默认模型轮数上限，改用结果驱动的连续停滞检测，先纠偏再无工具收尾。任务总预算、时间与审批保持独立约束。

**MCP 接入反馈**：用户自行开发接入后，经审查迁移到 FastMCP Client。服务启动与工具调用独立审批，补齐异步超时和取消、进程组清理、工具名称防碰撞与取消后的恢复边界。34 项 MCP 回归及本地 FastMCP 服务集成通过；真实模型与外部 MCP 联合完成编码任务仍待验收，不据此提升 W1 的完成状态。

**Skill 接入反馈（2026-10-21）**：在用户新增的操作手册加载基础上，按用户明确的语义补齐 [技能生命周期](specs/skill-lifecycle.md)：名称、用途和路径常驻注册，项目与本机技能自动发现；正文按需临时激活，使用后主动卸载，本轮完成、暂停、取消或失败时统一清理。Textual / Ink 与 Agent 工具共用状态；正文不进入新工具历史，技能本身不授予写入或执行权限。确定性接线与真实模型使用效果分别验收。

**已实现的工程切片**：[可信命令结果](specs/command-results.md)。真实退出状态进入模型、任务记录和 Ink；已用确定性 provider 验证“失败测试—审批修复—重新验证”，真实模型闭环仍按以下条件验收。

**评测接入切片（2026-10-21）**：[编码 Agent 评测](specs/evaluation.md)。内部 eval 补齐实际预算、正常终态、测试文件保护及同配置比较；新增 `./run_eval.sh` 接入固定 SWE-bench Lite 开发清单、容器执行和官方独立评分，初版默认只选 3 题。已校验官方数据摘要，astroid 第一题完成空补丁 `0/1`、参考补丁 `1/1` 的评分器自检；pvlib 环境兼容失败保留证据。接入基线为 804 项单元测试及脚本模型接线通过。用户已用 `deepseek-flash` 运行首题，压缩后因缺少 `reasoning_content` 的 HTTP 400 中断，补丁为空、官方跳过测试；原报告保留于 `.vibe/benchmarks/swebench-lite-dev/model/20261021-164901-333616/`。协议字段已补齐本地回放与持久化回归；修复后真实任务与固定前三题整体环境仍待验收，W1 不标记已验收。

**第二轮真实评测反馈（2026-10-21）**：记录 `.vibe/benchmarks/swebench-lite-dev/model/20261021-170835-513176/`。`deepseek-flash` 的 11 次请求与两次预算压缩后的调用均成功，原 HTTP 400 未重现；模型重现原始异常后继续只读探索，累计 100711/100000 tokens 触发停止，未修改文件、未运行测试，官方因空补丁跳过。当时公开入口默认 50 步、100000 token、600 秒，并覆盖本机配置；本轮只证明该预算内未完成，原报告与诊断保留。

**预算分层验收切片（2026-10-21）**：按用户反馈先验证已有预算机制，再逐步验收编码任务。新增 `./run_eval.sh check-budget`，固定使用 fake，无需 API Key 或 Docker，可按 `context`、`compaction`、`total` 层级检查；前层未通过则跳过后层，预期的预算暂停表示机制符合断言，不计为编码完成。公开入口当前默认 1 题，显式 `--limit 3` 再扩展；预算默认继承 `Config` 与环境变量，仅显式参数覆盖。未配置时累计预算 1M、压缩周期 200k、上下文容量 1M、`max_steps=0`，可用 `doctor` 核对。CLI 分开汇总正常未解决、预算暂停与真实接口/环境/评分错误，独立评分依据保持不变。上一轮协议修复的完整回归基线为 845 项通过；本切片的全量回归另行记录，真实模型尚未因这些工程检查而通过 W1。

本切片完整 Python 回归 **875 项通过**，无失败或跳过，记录 `.vibe/budget-layers-tests.xml` 与 `.vibe/budget-layers-tests.log`。这些是确定性机制与回归证据，下一阶段仍需真实模型完成内部小任务与公开首题。

预算 CLI 已实际完成 **3/3 层检查**，报告为 `.vibe/benchmarks/budget-checks/20261021-133905-502317/report.json`。上下文层固定目标超窗时未请求模型；压缩层在同一 run 中两次周期压缩，实际读取、修改并运行 `calc` 原测试成功；总预算层同时拦截写文件与命令，保留累计用量。这些是 fake 驱动的工程证据。

**内部真实首题反馈（2026-10-21）**：`.vibe/evals/model/20261021-135000-933796/` 中，`deepseek-flash` 第 1 步仅回复 `I'll create the file.` 后正常停止，消耗 1111 tokens、工具请求为 0，未创建 `hello.txt`。Agent 的 `done` 终态没有被评分器误计为成功，文件检查正确失败；此次没有触及预算，也没有 HTTP 错误，原记录保留。

**操作任务结束约束切片**：内部与公开评测显式启用 `require_tool_use=True`，普通问答默认关闭。初始指令明确要求实际操作并遵守审批；本次 run 尚未请求工具而给出非空回答时，在原预算内仅追加一次通用执行提醒，仍不请求工具则正常停止为 `no_action`。取消、步数、时间、token、费用和上下文门槛继续生效，审批拒绝后不会因此被强制重试。至少一次工具请求只表示进入过执行路径，独立检查与官方评分继续决定是否完成，评分断言和期望内容不会用于纠偏。

内部协议更新为 `internal-v3`，记录 `metadata.agent.require_tool_use=true`；公开协议更新为 `protocol_version=2`，记录 `metadata.require_tool_use=true`。旧协议结果保留，不与新协议混算同配置变化。内部 CLI 现在给出具体原因，全部通过退出 `0`，正常未完成或检查失败退出 `1`，真实运行错误退出 `2`。本地 fake 与 HTTP 回放已覆盖“意向回复后执行”、持续无工具停止、评分不回灌；这些回放与下述真实模型结果分开记录，W1 的真实代码修复与验证仍未验收。

该修复后的完整 Python 回归 **889 项通过**，无失败或跳过，记录 `.vibe/action-completion-tests.xml` 与 `.vibe/action-completion-tests.log`；预算分层入口再次 3/3 通过。

**内部真实首题通过（2026-10-21）**：用户重跑 `.vibe/evals/model/20261021-142446-747494/`，`deepseek-flash` 在 `internal-v3` 下 3 步、4341 tokens、约 3.48 秒完成 `create_file`。实际创建 11 字节的 `hello world`，随后执行内容核对命令并退出 0；写入与命令均有审批审计，独立 `file_equals` 通过，未触发纠偏。首个文件操作任务已通过，下一步按既定顺序运行内部 3 题；真实代码修复及公开仓库任务仍待验收，W1 不因此标记完成。

**内部真实前三题通过（2026-10-21）**：`.vibe/evals/model/20261021-143720-292084/` 中，`deepseek-flash` 完成创建、编辑、读取复制，独立评分 **3/3**。核对真实文件为 `hello world`、`hi\n`，复制前后均为 `42\n`；审批链完整，执行的验证命令退出 0。合计 20673 tokens、14 步、约 17.25 秒，无纠偏、预算暂停或运行错误。基础文件操作阶段通过，按既定顺序进入 SWE 首题的真实代码修复与独立评分；W1 继续保持未验收。

**交付可信规则（2026-10-23）**：真实任务暴露 Agent 会凭历史记忆声称工作区状态（W1 修复的最终回复凭空列出未提交的 skill 改动）。基础系统提示补充“结论必须来自工具输出”：涉及未提交改动、文件存在性、测试结果时先运行命令并引用实际输出，未运行或未知要明说；回归测试断言提示词包含该规则。真实效果由本地基准与后续任务验证，不据此宣称已解决。首轮基准后又补充根因与安全边界规则（“通过现有测试不等于修复根因；路径/权限/不可信输入要检查符号链接、相对路径与畸形值”），作为第一个用基准 A/B 验证的改动。

**本地编码基准（2026-10-23）**：[规格](specs/local-coding-benchmark.md)。新增 3 个真实仓库副本任务（recall 越界、Ink 页脚重复、stdio 取消计数），隐藏测试评分、初始 RED、恢复原始测试防篡改；`--prepare` 三题全部 RED，未注入 bug 时三题 check 全部通过，`--fake` 链路正确记 FAIL。首轮真实基线（`20261023-113622`）：**2/3 通过**，59 步、约 1.04M tokens、73 次工具调用；`recall-guard` 失败——可见测试通过但未处理符号链接绕过，失败分类 `incomplete_fix`，两份通过的最终报告与证据一致。据此补充“根因与安全边界”系统提示规则并进入第二轮 A/B。第二轮（`20261023-114623`）报告 3/3，但审计发现工作区仍含基准夹具，Agent 直接读取并运行了隐藏测试，该轮不计入 A/B；runner 已隔离基准文件并记录 `workspace_clean` / `hidden_leak_attempts`，第三轮（`20261023-115821`）在隔离后 **3/3 通过**（88 步、约 1.62M tokens），无隐藏泄漏、无测试改动，`recall-guard` 正确加了 `resolve` 路径校验；但审计发现工作区仍嵌在外层仓库内，`tool-count` 的验证命令跑到了外层仓库，且剔除脚本导致副本里 `tests/test_bench_local.py` 变红。该轮只作能力信号（2/3 → 3/3）；runner 已改为仓库外独立临时目录 + 独立 git 仓库并补剔除测试，第四轮（`20261023-125926`）把工作区移到仓库外并初始化独立 git：`recall-guard` 与 `tool-count` 均在副本内通过（39 步/756k tokens、13 步/199k tokens），无越界命令；`footer-outcome` 的 Agent 仍 cd 到外层真实仓库验证，判为 `wrong_directory`（seed 已在副本生效）。任务提示已补“唯一工作区绝对路径 + 不要访问其它仓库”和越界检测后，footer 单独重跑通过（10 步、187k tokens、无越界命令）。合并干净结果：**能力 2/3 → 3/3**；成本 59→62 步（+5%）、1.04M→1.14M tokens（+10%）、73→77 次工具调用（+5%），耗时 205s→294s（+44%，主要来自 recall 安全任务的额外验证）。能力提升成立，但 3 题均为“恢复已知修复”且提示规则针对这批任务调过，存在过拟合风险；下一步加未见过的任务族或转入 W2/W3 真实验收。

**首个本地真实小任务（2026-10-23）**：任务为修复 Ink 页脚重复结果标签（`本轮结束 · 本轮结束` / `已取消 · 已取消`）。先由验收测试固定失败（`a564ec6`，`footer shows one outcome label for finished, cancelled and stopped runs` 红），再在 Ink 中交给 `deepseek-flash`：session `20261020-224244-563557`、run `ad8e12b2`，28 步、609,166 tokens、35 次工具调用、约 119 秒，正常 `done`。审批记录：`edit_file` 3 次、`bash` 多次，全部 `workspace_allow`；它实际运行了 `npm --prefix tui test` 与 `./run_tests.sh -q`，退出码均 0；最终 diff 为 2 文件 +7/−2（`tui/src/app.tsx`、`tui/src/view-state.ts`），验收测试未被修改。独立复核：Python 820 通过；Ink 197 项 196 通过，唯一失败为无 PTY 的环境项（用户本机 197/197）。验收项 1/3/5 勾选；2（拒绝演示）与 4（交付准确性）未勾选，原因见验收项备注。

**公开仓库首题通过（2026-10-21 22:45）**：`.vibe/benchmarks/swebench-lite-dev/model/20261021-224536-308814/` 中，`deepseek-flash` 在公开协议 v2 下完成 `pylint-dev__astroid-1196`，官方 `swebench==4.1.0` 评分 **resolved 1/1**（无空补丁、无错误）。24 步、323,577 tokens（输入 312,967、输出 10,610）、约 149 秒，正常结束；trace 保留 28 条命令执行记录，补丁与官方评分目录一并保留。2026-10-23 `./run_eval.sh doctor`（[记录](specs/evaluation.md)）确认继承预算为 1M tokens / 0 步数上限 / 600 秒，旧入口写死的 10 万 token 限制已被突破；本次核对环境没有 API key 与 Docker，未重跑。该结果是公开评测闭环证据，仍不替代 W1 要求的本地真实任务交付与用户验收，W1 不标记完成。

后续按“离线预算自检 → 内部真实 eval 1/3 题 → SWE 首题 → 固定 3 题”推进；收费阶段仅在用户显式运行时开始，不因未解决自动增额或重跑。首题已通过并核对预算；固定前三题仍需先逐题完成参考补丁自检，再用 `--limit 3` 扩大评测。各阶段都保留暂停与失败证据。详细命令与退出码见 [编码 Agent 评测](specs/evaluation.md)。

**验收**：

- [x] 在真实仓库或保留真实上下文的副本中，使用真实模型完成一次小任务；初始失败可复现，完成条件在动手前确定（见上方 2026-10-23 记录）。
- [x] 写文件和执行命令经过审批；拒绝后文件不被该写动作改变，错误如实回灌。W2 审批取消补齐拒绝现场演示：`edit_file deny`、`not executed`、`calc.py` 不变；W3 `/resume` 把被取消测试当作未知结果，先核对再重跑。
- [x] 至少演示一次验证失败后的修正，并再次实际运行验证（红测试 → 修复 → `npm test` / `./run_tests.sh -q` 重跑）。
- [x] 交付列出实际文件变化、运行过的验证命令及退出结果、仍未完成的事项；可与 diff 和执行记录核对。原始 W1 报告曾有未核实声明；已补“结论必须来自工具输出”规则，W2/W3 真实报告均与 diff/命令核对一致（如 `w3-normal-resume`）。
- [x] 非零退出、超时和未运行验证时，不被汇总成“测试通过”或“任务完成”（本次如实说明了退出码与 `-qq` 抑制汇总行）。

**结果核对**：新命令同时保存结构化 `execution` 与实际输出，`TaskState.command_results` 记录执行证据；`tests_run` 兼容旧数据及显式标记的验证命令。旧记录没有证据时保持未知，命令零退出也不能替代需求验收。

**学习重点**：需求如何变成工具动作；工具执行成功、测试通过、任务完成是三个不同判断。

## W2：掌控运行过程

**取消测试加固（2026-10-23）**：`test_command_results.py` 的取消用例固定 2 秒等待在高负载下偶发超时；已放宽到 15 秒（启动等待也加长），连续 20 次通过。生产取消路径未发现确定性缺陷，W2 仍需真实任务里的停止边界验收。

**用户场景**：任务执行到一半时，你改变范围或要求停止，后续行为采用你的最新要求，并清楚展示已经发生的变化。

**W2 真实验收完成（2026-10-23）**：W2-A 纠偏、W2-B 三种停止边界、超时进程回收、取消后已写修改保留均通过；界面阶段可见性修复后，用户在真实会话确认“停止请求已发送 → 后端已确认停止请求 → 已取消：已发生的修改仍需核对”三阶段记录可见。W2 五项验收全部满足，进入 W3。

**W2-A 通过（2026-10-23）**：session `20261023-171648-357460` / run `afe6a47b`。Agent 运行 `slow_test.py` 期间用户提交纠偏“只改 calc.py 的内部实现；修完用中文说明根因；不要修改任何测试文件”。trace 记录 `steering_applied` 在 step 4、43 字符；纠偏作为真实 user 消息持久化；随后模型继续，8 步、81,933 tokens、11 次工具调用、约 60 秒后 `done`。最终 diff 仅 `calc.py`（`a - b` → `a + b`），`test_calc.py`/`slow_test.py`/`slow_command.py` 哈希未变；审批 5 条均为 workspace_allow；无残留进程；最终回复按纠偏要求只改内部实现并说明根因。证据 `.vibe/w2w3/evidence/w2-steer-verified-20261023-172000.json`。W2 其余停止边界与交付核对待执行。

**W2-B 三种停止边界（2026-10-23）**：
- 模型等待：session `20261023-174312-142378` / run `9eb63883`，流式输出中途取消：`llm_end.finish_reason=null`，assistant 正文被截断，`run_end=cancelled`，1 步、0 工具调用、无审批/命令、工作区无改动；
- 审批等待：session `20261023-174443-226686` / run `29a8b19c`，编辑审批面板出现时 Ctrl+C：审计 `edit_file → deny`，待执行调用写成 `ERROR cancelled: not executed`，`run_end=cancelled`，`calc.py` 哈希不变；
- 命令运行：session `20261023-174518-680007` / run `9d767e8c`，`python3 slow_command.py` 已启动后取消：`command_result status=cancelled, exit_code=-9`，任务记录注明 `ERROR cancelled: process group reaped`，无残留进程、工作区无改动。

证据：`.vibe/w2w3/evidence/w2-stop-model-20261023-174414.json`、`w2-stop-approval-20261023-174458.json`、`w2-stop-command-20261023-174610.json`。超时进程回收与取消后已写修改保留已另行验证。用户实测取消时只看到“已取消”，状态栏的“正在停止”被快速覆盖；已改为在 transcript 中显式记录三阶段：Ctrl+C 立即写“停止请求已发送，等待后端确认…”→ 后端 ack 写“后端已确认停止请求，等待收尾…”→ done 写“已取消：已发生的修改仍需核对”。Ink 回归 214 项通过（唯一失败为沙箱 PTY 环境项），待用户在真实会话确认三阶段可见后勾选 W2 第 2 条。

**W2 超时进程回收（2026-10-23）**：session `20261023-181022-745418` / run `3c3815e2`，按默认 30 秒超时运行 `python3 slow_command.py`：`command_result status=timed_out, exit_code=-9`，任务记录注明 `ERROR timeout after 30.0s ... process group reaped`；无残留进程，工作区无改动。未把外层等待结束当成命令正常结束。证据 `.vibe/w2w3/evidence/w2-stop-timeout-20261023-181452.json`。

**W2 取消后已写修改（2026-10-23）**：session `20261023-181558-527201`。run `a128b127`：编辑 `calc.py` 已被允许并实际写入（`edited calc.py (1 replacement)`），随后在 `python3 test_calc.py` 的 bash 审批处 Ctrl+C，取消结果写成 `ERROR cancelled: not executed`，`run_end=cancelled`；接着 run `3a6dea4e` 接受“检查当前工作区，报告已发生的改动；不要撤销、重写或提交”，如实报告仅 `calc.py` 未提交修改、未撤销。最终 git status `M calc.py`、文件哈希为修复后版本。证据 `.vibe/w2w3/evidence/w2-cancel-after-write-20261023-181924.json`。取消没有被描述为撤销。

**真实验收发现 1（2026-10-23）**：W2-A 纠偏提交后，stdio 把“已应用纠偏”的 `notice` 带上了 run id，而 Ink 协议要求 `notice.id === null`，触发 `protocol_error: 后端消息字段无效：notice`，Ink 断开连接、run 被取消（trace：`steering_applied` 后 `run_end status=cancelled`）。已改为 `send("notice", None, ...)` 并在 parity 测试断言 notice id 为 `None`；证据 `.vibe/w2w3/evidence/w2-bug-notice-20261023-171449.json`。W2-A 需在修复后重跑，恰好证明真实验收能抓到自动测试没覆盖的跨端协议不一致。

**真实验收（2026-10-23）**：环境、三种停止边界与纠偏步骤见 [W2/W3 真实验收](specs/w2-w3-acceptance.md)；用 `.venv/bin/python3 scripts/w2w3_setup.py` 准备副本，`scripts/w2w3_evidence.py` 采集会话、审批、命令与残留进程证据。执行后按证据勾选。

**验收**：

- [x] 运行中可以提出一条纠偏，例如“保留公开接口，只改内部实现”；后续模型输入和工具动作采用新约束。证据见下方 W2-A 记录。
- [x] 发出停止后，在可观测的停止边界不再发起新的模型请求或工具操作；界面区分“正在停止”和“已停止”。证据：三阶段 notice（停止请求已发送 → 后端已确认 → 已取消仍需核对）在真实会话可见，截图 `.vibe/w2w3/evidence/w2-approval-notices.png`。
- [x] 在模型等待、审批等待、命令运行三种状态下验证停止；已开始的操作能说明完成、终止或结果未知。证据见下方 W2-B 记录。
- [x] 超时后核对进程是否仍在运行及实际文件变化，不能把外层等待结束当作底层命令已经停止。证据见下方 W2 超时记录。
- [x] 停止后的交付能核对已发生的修改；取消不被描述为撤销，已写入的内容仍如实显示。证据见下方 W2 取消后写入记录。

**已有基础与缺口**：已有审批、取消事件和预算；TUI 当前运行时禁用输入，尚不能运行中纠偏。停止后的收尾也需实际核验。

**运行中纠偏与后续消息工程切片（2026-10-22）**：[运行中纠偏与后续消息](specs/run-time-correction.md)。`Agent.steer()` 把运行中的用户纠偏排队，在下一步组装请求前写成真实 user 消息并持久化；模型即将收尾时到达的纠偏会再走一步，不静默丢弃。stdio 新增 `steer` 帧、`ack action=steer` 与 `steer` capability；Ink 运行中保持输入可用，Enter 提交纠偏，`/followup` 排到本轮结束后自动继续。2026-10-23 修复 Ink 控制流：命令执行中不再误发纠偏、成功提示以后端 ack 为准、失败与晚到错误不再污染整轮状态、取消时清空排队并提示、自动续跑不清空草稿；同时把 `recall` 的文件引用校验集中到 `ContextBuilder`，阻止读取工作区外 JSON；同日清理取消/停止机器串进入 transcript、命令帮助与补全清单漂移、取消 run 的工具计数、重复消息反序列化与 `state.json` 非原子写。顺带把请求前 `max_run_seconds` 停止的 `steps` 改为已完成步数。确定性回归 Python **901 passed**、Ink **193/194**（唯一失败为沙箱无 PTY 的环境项）与 fake 预算检查；真实模型与真实终端使用验收未做，W2 第一条不勾选。

**学习重点**：取消请求、执行停止、结果确认分别发生在什么位置。

## W3：隔天继续同一个任务

**用户场景**：退出或意外中断后重新打开，知道上次做到哪里、哪些结果可靠，并能继续同一任务。

**真实验收（2026-10-23）**：中断/恢复/外部改动/新旧任务区分步骤见 [W2/W3 真实验收](specs/w2-w3-acceptance.md)；验收点在读、写、命令附近分别中断后用 `--continue` 恢复，并用证据脚本核对未知结果与文件状态。

**验收**：

- [x] 正常退出后恢复，能看到原目标、关键约束、实际改动、验证结果和下一步。证据见下方 W3 记录（`w3-normal-resume`）。
- [x] 在读工具、写工具、命令执行附近分别构造中断；未知结果先核对，再决定是否续做，不自动重放可能已生效的写入或命令。证据：读中断 `w3-interrupt-read` + `/resume` 的未知结果核对；写中断复用 W2 `w2-cancel-after-write`；命令中断复用 W2 `w2-stop-command`。
- [x] 恢复前文件被外部改变时，重新读取或确认差异，不继续采用过期内容写入。证据：`w3-external` / `w3-external-resume`。
- [x] 明确区分“继续旧任务”和“开启新任务”；新任务不沿用旧目标和旧完成标记。证据：`w3-newtask` 新会话只处理 README 问题。
- [x] 长任务触发上下文压缩后，仍能说明原目标、关键约束和未完成项，必要时通过 recall 取回原文再行动。证据见下方 W3 压缩记录。
- [x] 至少完成三次真实小任务的使用记录，其中包含一次失败修正、一次中断续做，并把发现的问题转成回归用例。记录见下方 W3 段落；本轮把 notice 协议、取消阶段可见性等问题转成了回归测试。

**阶段出口（2026-10-23）**：W1、W2、W3 均完成真实验收，W0 只剩“你能讲清入口/模型/工具回灌/结束条件”的代码走查与学习记录待你确认（其余 W0 证据已补）。当前产品达到“你能使用并掌控的单 Agent 编程助手”的能力阶段。下一步按真实使用反馈进入 W4（项目背景复用），或先补一组未参与调优的开放式基准任务验证泛化，再决定投入方向。

**W3 真实验收记录（2026-10-23）**：
- **中断与恢复**：session `20261023-200430-465694`，run `7653dfff` 在读工具后取消（2 步、18,649 tokens、无写入）；`/resume` 新 run `93f2d208`（3 步、30,906 tokens）先说明“被取消的测试命令未执行、结果未知”，核对 `git status` 与文件后才重新执行测试（`test_exit=1`），没有直接重放。
- **外部改动**：session `20261023-200836-979126`，读中断后脚本给 `calc.py` 追加 `# external change`；恢复 run `de6d37bf` 用 `git status`/`git diff` 识别出第 3 行外部注释，保留注释后修复 bug，`python3 test_calc.py` 退出 0，没有用过期内容覆盖。
- **新旧任务**：session `20261023-200653-464455`，新会话 run `b3fa3b6c` 的目标为“解释 README”，未沿用旧目标或完成标记。
- **正常完成与恢复**：session `20261023-201043-045361`，run `0237724f` 修复 + 测试退出 0（5 步、47,075 tokens）；恢复 run `d35ca9b4` 能总结目标、改动、验证结果与未提交项（2 步、20,180 tokens）。
- 证据：`.vibe/w2w3/evidence/w3-interrupt-read-20261023-200446.json`、`w3-resume-20261023-200623.json`、`w3-newtask-20261023-200751.json`、`w3-external-20261023-200844.json`、`w3-external-resume-20261023-201015.json`、`w3-normal-20261023-201057.json`、`w3-normal-resume-20261023-201210.json`。
- **上下文压缩**：session `20261023-204121-209116`，在 24k 上下文窗口 / 6k 压缩周期下读取 1200 段 `big_notes.md`：trace 共 11 次 `history_compacted`，多次 `before_tokens > after_tokens`、`summary_layers=2`、offload 非空；压缩后的追问仍准确说明原目标、约束、已完成/未完成项，并如实指出 `big_notes.md` 未跟踪、未改代码。证据 `.vibe/w2w3/evidence/w3-compaction-20261023-204314.json`。
- W3 六项验收全部通过。

**已有基础与缺口**：已有 JSONL 会话、TaskState、`--continue` 和缺失工具结果标记；这些尚不等于完整的副作用核对流程。

**学习重点**：聊天历史、任务状态和实际文件状态为何需要对账。

**阶段出口**：W0–W3 验收后，当前产品达到“你能使用并掌控的单 Agent 编程助手”；再根据反复遇到的上下文问题进入 W4。

## W4：新任务复用项目背景

**项目背景 MVP（2026-10-23）**：[项目背景规格](specs/project-profile.md)。实现 `<workspace>/.vibe/project-profile.json` 的读取/校验/渲染；stdio 在每次 run 前重新加载并作为独立 `[PROJECT BACKGROUND]` system 段注入（逐条带来源、4000 字符上限、换工作区不串用、损坏/缺失按空背景处理）；Ink 新增 `/project`、`/project add <文本>`、`/project remove <序号>`，直接编辑文件同样生效。Python 与 Ink 回归覆盖加载、注入、重载、增删与损坏兼容；真实任务价值验收（2026-10-23）通过：用户在同一项目跑两个不同新任务（Ink 增加 `/project source` 命令并补测试；Python 增加 source 空白归一化回归测试），均无需重复交代“Ink 是唯一界面、测试怎么跑、core 边界、审批规则”；`/project` 能看到 6 条带来源背景，换到 demo 工作区为空、没有串用；新增/删除条目在下一次任务生效。两个任务的改动已复核并通过回归（`/project source` 支持带空格来源与非法序号提示；Python 空白 source 归一化为 user）。会话：`20261023-205920-617110`、`20261023-210023-792084`。


**用户场景**：在同一项目开启一个全新任务，不再从头说明项目用途、约定和已经确认的重要决定。

**验收方向**：在新会话中复用一份可查看、可修改、带来源的项目背景；换项目时不串用；背景更新后不继续注入失效内容。至少用两个不同任务演示背景复用，并记录少重复了哪些说明。

这一阶段复用项目事实；W3 负责续做同一任务，二者分开验收。先用少量显式内容验证价值，再决定存储和检索方式。

**验收**：

- [x] 新会话复用一份可查看、可修改、带来源的项目背景；两个不同任务均不需要重复交代背景。
- [x] 换项目不串用（demo 工作区 `/project` 为空）。
- [x] 背景更新/删除后，新任务使用新内容，旧内容不再注入。
- [x] 区分项目背景与个人偏好（W4 只存项目事实；个人偏好在 W5）。

**学习重点**：当前对话、项目背景、任务进度的作用范围。

## W5：关于你——个人偏好与画像

**用户场景**：在日常任务中说过“以后先说明方案再修改”，打开“关于你”可以看到助手逐步整理的偏好、目标与工作方式，了解其来源，并确认、修正或删除相关理解。

**验收方向**：“关于你”先展示少量来自日常任务协作的偏好与画像条目，保留来源和适用范围，并区分待确认的观察与用户已确认的偏好；能查看、确认、修改、停用、删除；只在相关任务使用。当前明确指令优先于历史偏好；删除或停用后，在新会话中不再注入和使用该条偏好。项目规则与个人偏好能区分，一次临时要求不会自动变成长期偏好。

**W5 真实使用验收（2026-10-24）**：用户确认手动验收通过——`/about` 展示条目与来源、`add/propose/confirm/remove/on/off` 全链路可用；已确认偏好在新会话生效，待确认观察不注入；`off` 后不再使用，项目规则与审批约束不被偏好覆盖。W5 关闭，进入 W6 收益对照。

**个人偏好 MVP（2026-10-23）**：[个人偏好规格](specs/user-profile.md)。用户级 `~/.vibe/user-profile.json`（`VIBE_USER_PROFILE` 可覆盖）支持 `confirmed` / `proposed` 两种状态；stdio 每次 run 前重读，只对用户会话、启用状态注入已确认条目（带来源、3000 字符上限）；测试/基准会话不注入。Ink 提供 `/about`、`/about add/propose/confirm/remove/on/off`。Python/Ink 回归覆盖加载、状态过滤、用户会话注入、停用与增删。真实使用验收与 W6 收益对照待做。

**验收**：

- [x] 展示少量来自日常任务协作的偏好条目，保留来源与适用范围，区分待确认观察与已确认偏好。
- [x] 能查看、确认、修改、停用、删除。
- [x] 当前明确指令优先于历史偏好；项目规则与个人偏好能区分，一次临时要求不自动变成长期偏好。
- [x] 删除或停用后，在新会话中不再注入和使用该条偏好。

**学习重点**：从任务互动形成个人上下文，以及持久记忆的确认、优先级、范围和失效。

## W6：验证个性化的实际收益

**用户场景**：助手在相似任务里更贴合你的工作方式，你能指出减少了哪些重复说明或纠正。

**验收方向**：选择至少三组有代表性的真实任务，在初始条件可比的新会话中，对比启用和停用相关背景、偏好后的表现；记录必要背景补充次数、纠正次数、任务正确性和你的实际评价。只有重复交代或纠正减少，且正确性和审批边界没有回退，才认定有收益。展示实际使用了哪条背景或偏好；发现错误记忆后能纠正，并在后续任务中生效。

小样本对照用于检验当前使用价值，不据此宣称普遍效果；“能存能读记忆”只说明接线成功。

**W6 对照实验工具（2026-10-24）**：[W6 规格](specs/w6-benefit.md)。`scripts/w6_run.py` 对三组真实任务（解释 /about 实现、增加 /about edit 命令、诊断 load_user_profile）各跑启用/停用两遍，隔离工作区、同一提示词；启用组注入项目背景 + 3 条已确认偏好，停用组无背景、偏好为空。`scripts/w6_evidence.py` 自动采集 run 指标、测试命令、用户消息数、git 状态与实际注入内容；`scripts/w6_report.py` 汇总对照表。三组 ×2 会话已于 2026-10-24 跑完（证据：`.vibe/w6/evidence/`）。

**W6 对照结果（2026-10-25）**：

| 任务 | 用户消息（补充/纠正） | 步数 | tokens | 工具调用 | 测试通过数 | 改动文件 |
|---|---|---|---|---|---|---|
| task-a（解释 /about 实现） | 1 / 1 | 15 / 12 | 301,234 / 213,539 | 26 / 19 | 4 / 2 | 无 / 无 |
| task-b（增加 /about edit） | 1 / 1 | 10 / 15 | 153,680 / 266,031 | 12 / 20 | 3 / 6 | 2 / 5 |
| task-c（诊断 load_user_profile） | 1 / 1 | 27 / 26 | 439,527 / 428,320 | 29 / 27 | 10 / 7 | 4 / 2 |

（每格为「启用 / 停用个性化」；三组 run 状态均为 done）

按本里程碑的判定口径（**只有重复交代或纠正减少，且正确性和审批边界没有回退，才认定有收益**）：

- **重复说明/纠正未减少**：三组用户消息均为 1 条，主假设未获支持；
- **正确性与审批边界无回退**：三组均正常结束，测试通过数启用组均不低于停用组；
- **可观察的正面信号（单次、不可外推）**：task-b 启用组的改动收敛到 2 个文件、tokens 低 42%（停用组改了 5 个文件，含 USAGE、specs、cli.tsx 的范围扩张）；
- **成本风险**：task-a 启用组 tokens 高 41%，说明注入内容并非总是净收益；
- 用户主观评价：整体体验不错（口头反馈，未写入证据文件，不作结论依据）。

**W6 结论**：按协议完成对照，主假设（减少重复说明）**未获支持**；不宣称个性化收益成立。下一步优化方向：偏好条目更具体/更少、按任务相关性过滤，并在日常真实任务中继续观察；不据此扩展自动观察或画像功能。

**对单次成本差异的修正（2026-10-25）**：随后 `execution-chain` 同一题同配置三次真实运行的 tokens 为 19.5 万 / 44.3 万 / 72.5 万（3.7 倍）、步数 11/24/36，说明单次 token 差异主要由运行波动主导。因此 W6 中 task-b 的 -42% 与 task-a 的 +41% 不能当作个性化效应证据，只能作为待验证线索；后续对照需多次运行并看分布（`--runs` + `per_task` 统计）。

**学习重点**：把“更了解我”转成能观察、能纠正的行为变化。

## W7：承接第二种常用工作流

**启动条件**：你在编码以外，提出一个反复需要助手参与的具体场景，例如项目推进或学习复盘。

**验收方向**：选择其中一种场景，在同一工作台中支持“编码任务 + 这一种辅助工作流”；能接续各自的目标与进度，并复用适用的个人上下文。两类任务的上下文和权限不混淆，实际使用中能减少来回整理背景的成本。

届时再细化验收任务和界面；TUI、其他界面、MCP、多 Agent 都是可评估的手段，不作为完成标准。

## README 完善项验收（2026-10-25）

README「当前完善重点」六项已修复并附针对性回归，真实任务核对已用保留题完成。

**保留题**：新增 `benchmarks/local/stream-truncation`、`restore-guard`，`meta.json` 标注 `held_out: true`（未参与调优），分别覆盖流式截断完整性与重开会话的重新读取守卫。

**判定口径**：`artifact_correct`（隐藏检查通过）、`run_complete`（正常结束）、`comparable`（协议有效且改前失败基线成立）分开记录；`verified_fix` 要求改前失败、改后通过。取消、越界、改动测试、隐藏泄漏等情况保留证据并判为无效。

**真实运行证据**：`.vibe/bench-local/20261025-141504/summary.json`（2 道保留题 × 3 次 = 6 次真实运行，版本 `7c74168`，预算 1,000,000；`VIBE_MODEL` 未设置，实际使用默认 `deepseek-chat`，该次报告 `config.model` 记为 `unset`，记录逻辑已在本次修复）：

- 6/6 次 `artifact_correct`、`run_complete`、`comparable`、`valid`、`verified_fix` 全部为真，`config_frozen: true`；
- 每次 `red_rc=1` → `check_rc=0`；`test_edits`、`hidden_leak_attempts`、`outside_workspace_commands` 均为空；
- 单次 10–22 步、改动 1 个文件；同一题三次运行结果一致，具备可比较性。

**夹具缺陷**：`footer-outcome` 隐藏测试缺少 `afterEach(cleanup)`，`node --test` 进程不退出导致 `check.sh` 挂起；已修复（11.2s 通过）。5/5 题 `--prepare` 初始 red、恢复修复文件后 `check.sh` 全部通过。

**跨层压测题真实运行（2026-10-25）**：新增 `execution-chain`（执行证据链在 agent/core/stdio/tui 五处断裂，隐藏检查同时覆盖 Python 与 Ink）。真实模型 3 次运行证据 `.vibe/bench-local/20261025-143841/summary.json`：

- 3/3 次 `artifact_correct`、`run_complete`、`comparable`、`valid`、`verified_fix` 全部为真；无改测试、无隐藏泄漏、无越界命令；
- 每次均改动全部 5 个文件，未出现"只修一端"的跨层遗漏；
- 但成本波动显著：步数 11 / 24 / 36，tokens 194,808 / 443,496 / 725,198（3.7 倍），单次运行不足以比较成本；汇总已加入 `per_task` 的 min/median/max 分布。

**暂停与续做真实运行（2026-10-25）**：`.vibe/bench-local/20261025-144802/summary.json`，`--pause-tokens 60000` 跑 `execution-chain`：

- 首轮在 60,173 tokens / 5 步处按预算暂停（`stopped`）；
- 新进程 `--continue` 重开会话并 `/resume` 续做，最终 `done`，五处文件全部修复，`check_rc=0`；
- 无改测试、无隐藏泄漏、无越界；`valid`/`comparable`/`verified_fix` 均为真；
- 报告成本口径已修正为 `phase_totals`：**12 步 / 167,389 tokens**（此前只记续做阶段 7 步 / 107,216，低估 36%）。

**仍待做**：前三题参考补丁自检与 `--limit 3` 扩题；`/goal` 真实长任务验收。

**执行隔离评估（2026-10-25）**：新增 [执行隔离评估](specs/execution-isolation.md)，梳理当前边界（审批 + 12 条正则黑名单 + 超时 + 进程组回收）与六类可绕过点（工作区外读写、黑名单绕过、网络外发、workspace 授权窗口、子进程逃逸），列出 A–D 四项方案与建议顺序（先护栏与授权语义，再环境收敛实测，最后可选 OS 沙箱）。随后实施了选项 B 的第一小步：`bash` 显式写/删工作区外路径（绝对、家目录、`..` 逃逸与重定向）被 `PermissionError` 拦截，`/tmp` 与相对路径放行；回归见 `tests/test_sandbox.py`，限制（`cd` 后相对写入、嵌套 shell、`.venv` 符号链接等仍可绕过）已写入评估文档。用户决定保留 workspace 级永久授权（选项 A 否决）。沙箱方向完成 GitHub 调研（[沙箱调研](research/sandbox-research-2026-10-25.md)）：Codex 与 Anthropic sandbox-runtime 均不用容器做默认隔离。随后实现 macOS Seatbelt 可选沙箱：`--sandbox` / `VIBE_SANDBOX=1` 写入限工作区与临时目录、默认断网、敏感读取拒绝、拒绝标注 `sandbox_denied`、启动探针失败直接拒绝；回归 `tests/test_sandbox_seatbelt.py`（集成部分在嵌套沙箱环境跳过，用户实机 8 通过/1 跳过）。Ink 入口已支持 `--sandbox`/`--sandbox-network` 转发。补充：`.git/hooks` 与 `.git/config*` 写入拒绝；Linux bubblewrap 后端已接入（argv 单测，实机待验）；macOS 网络白名单已实现（本地代理 + 域名放行，HTTP/CONNECT 按域名判定，未列出返回 403 并标注 `sandbox_denied`），实机集成用例待用户环境确认。

**侧栏 ↔ Agent 联动（A，2026-10-25）**：主 TUI 在工具结果/阶段变化时写 workspace 信号文件（版本签名 + 本轮 `write_file`/`edit_file`/`apply_patch` 目标路径），侧栏每 200ms 读取，版本变化立即刷新并在文件前标记 `●`，跟随模式自动选中最新改动；1s 轮询保留兜底，侧栏退出删除信号。无需 socket/改后端协议，Agent 与侧栏仍各自只读查询 Git。回归：`sidebar-signal` / `sidebar-agent-activity` / `sidebar-app` 信号刷新、标记与跟随，TUI `app.test` 发布 payload；TUI 全量 **300 项、299 通过**（唯一失败为沙箱无 PTY 的既有项）。

**apply_patch（2026-10-25）**：新增 `risk=write` 的 `apply_patch`，一次应用 unified diff（多文件、多 hunk、新建、删除），审批预览列出文件与增删行数；解析阶段拒绝重命名/二进制/mode-only/绝对路径/`..`/`.git`/symlink，hunk 上下文严格校验并容忍 ±200 行偏移，写前检查读取哈希，支持首次读取守卫；跨文件写盘非事务，失败按 `.vibe/backups` 核对。`core/state` 跟踪 patch 文件进入交付回执；TUI 侧栏信号从 patch 头提取路径。回归 `tests/test_patch_tool.py` 9 项 + state/agent prompt 断言；全量 Python `./run_tests.sh -q` 退出 0（收集 913 项）。规格见 [apply_patch](specs/apply-patch.md)。

**只读 Git 工具（2026-10-25）**：新增 `git_status` 与 `git_diff` 两个 `risk=read` 工具（免审批），让 Agent 在报告工作区状态前直接核对分支、未提交文件与 unified diff，减少过去必须走 `bash` 审批的自检摩擦；写文件、`bash`、web、MCP 的审批边界不变。实现固定参数数组、不经过 shell，清理 `GIT_*` 环境并禁用外部 diff / textconv / fsmonitor，路径限制在工作区内，diff 截断到 1200 行 / 40k 字符。系统提示加入对应使用约束（引用真实输出、非 Git 仓库要说明）。回归 `tests/test_git_tools.py` **7 项**；全量 `./run_tests.sh -q` 退出 0（收集 902 项）。规格见 [只读 Git 工具](specs/git-readonly-tools.md)；真实编码任务中的收益待验收。

**技能选择菜单（2026-10-25）**：`/skills` 不再只是把长列表塞进对话；后端在 `ready` 与 `command_result(skills)` 中附带结构化 `{name, summary, active, source}` 目录，TUI 在输入 `/skills`、`/skills activate `、`/skills unload ` 时弹出动态菜单：`↑↓` 滚动、`Tab` 补全完整命令、`Enter` 执行；已激活技能显示卸载入口。菜单复用现有 CommandMenu 的滚动与宽度处理，未新增 IPC。回归：`tui/tests/skills-menu.test.ts`、`protocol.test.ts`、`app.test.tsx` 菜单/补全/执行，Python `test_skills.py` 与 `test_stdio.py` 的结构化目录事件；TUI 全量 **305 项、304 通过**（唯一失败为沙箱无 PTY 的既有项），Python 全量收集 **915 项**退出 0。

**预算语义与压缩解耦（2026-10-26）**：按用户确认的 1+2 方案调整：交互默认 `max_run_tokens=0`（不限累计任务 token，评测/基准仍显式保留 1M）；`_automatic_history_target` 不再用剩余累计预算反推 history，目标只由单请求上下文容量决定；剩余累计预算装不下“固定前缀+工具定义+最小 history”时，不再把上下文压到 1k 级别，而是进入收尾/暂停判断；TUI 状态面板把“任务 Tokens”改为“任务累计 Tokens”、“上下文估算”改为“当前上下文”，避免把单请求容量与累计消耗混读。回归：`tests/test_agent_budget.py` 新增 history target 不受余额影响，`tests/test_config.py`/`test_benchmark_budget_cli.py` 覆盖交互不限与评测 1M，TUI 标签测试更新；TUI 全量 **305 项、304 通过**（唯一失败为沙箱无 PTY 的既有项），Python 全量 `./run_tests.sh -q` 退出 0（收集 917 项）。第 3 项（自动分段续做）留作下一片。

**Harness 空转护栏（2026-10-26）**：针对“反复读文件/重复调用/交替空转/错误后原样重试”补充 ProgressMonitor 与纠偏：同一 `read_file` 路径在两次成功写操作之间读取超过 5 次触发 `repeated_read`；`工具名 + canonical JSON 参数` 哈希重复达到 6 次触发 `repeated_call`（即使输出变化）；累计 4 次停滞警告后直接停止（即使中途有新结果），避免“新文件 → 旧循环”交替；`apply_patch` 计入成功修改并重置读取/调用计数；同一批内完全相同的调用只执行一次，其余返回 `duplicate_call` 并跳过。`_tool_guidance()` 为首次工具错误、超时、重复调用、重复读取、审批拒绝、仅更新计划等状态注入具体纠偏指令（读错误后改参数、拆分/加大 timeout、换方法、不要原样重试）。阈值通过 `max_reads_per_file` / `max_repeated_calls` / `max_progress_warnings` 暴露到 Config/env（`VIBE_MAX_READS_PER_FILE` / `VIBE_MAX_REPEATED_CALLS` / `VIBE_MAX_PROGRESS_WARNINGS`），Ink、`run` 与 benchmark 共用。新增 **ArtifactProgress**：动作任务连续 `max_calls_without_progress`（默认 20）次调用没有文件内容变化或验证结果变化时先提示落到修改/验证，再一个窗口仍无进展则 `no_progress` 停止；文件指纹取自成功修改后的内容 hash，验证结果取自 `verification=true` 的 bash 状态/exit/output；QA/审查任务不启用。新增 `tests/test_progress.py` / `tests/test_agent.py` / `tests/test_config.py` / `tests/test_tools.py` 回归；全量 Python `./run_tests.sh -q` 退出 0（收集 931 项）；TUI **306 项、305 通过**（唯一失败为沙箱无 PTY 的既有项）。现有 context_window、错误、审批拒绝、时间与取消停止边界不变，预算默认值不因本片改变。

**首屏与单轮时限（2026-10-26）**：交互默认 `max_run_seconds` 从 600 秒提高到 **1800 秒**（30 分钟，审批等待不计入），评测/基准仍在未配置时保留 600 秒，保证分数可比；`VIBE_MAX_RUN_SECONDS` / `--max-seconds` 可覆盖。TUI 首屏改为“工作台首页”：顶部 header 彩色像素猫 Mini（12×10 宽屏猫头、8×6 窄屏脸部、无彩色保留剪影、过窄隐藏）与三条 starter prompt、当前工作区/模型/会话、常用入口与输入提示；≤72 列用纯文本分区，宽屏用现有卡片表面，首屏标题与底部 hint 同步。新增 `tui/tests/workbench.test.ts` 首屏内容/宽度回归；TUI 全量 **309 项、308 通过**（唯一失败为沙箱无 PTY 的既有项），Python 全量 `./run_tests.sh -q` 退出 0（收集 931 项）。设计见 [TUI 首屏](specs/home-screen.md)。

**/goal 目标模式（2026-10-26）**：TUI 编排长任务目标：`/goal <目标>` 第 1 段开始；当段因 `max_run_seconds` / token / cost / steps 资源停止时，自动核对并开启第 N 段（默认最多 8 段、总 4 小时，可调），`done` 完成；`no_progress`、`context_window`、`error`、`cancelled` 暂停而不续段。`/goal` 查看、`/goal stop` 停止、`/goal resume` 恢复。每段仍走普通 run 的审批/工具/会话边界；目标状态写入 `<session>/goal.json`，重启/`--continue` 恢复为 paused，显式 `/goal resume` 续段。回归：`tui/tests/goal.test.ts` 纯函数与 `app.test.tsx` 自动续段/停止；TUI 全量 **319 项、318 通过**（唯一失败为沙箱无 PTY 的既有项）。真实长任务成本与跨段效果待验收。

**P1 Profile v2 + 相关性注入（2026-10-26）**：按 [用户偏好、画像与记忆路线图](specs/memory-personalization-roadmap.md) 实施：用户 profile 升级 v2，新增 `id/kind/scope/triggers/evidence/status/confidence/时间戳/expires_at`，v1 自动兼容；`select_user_preferences` 按当前任务与 triggers 匹配，最多 3 条 / 800 字符，无 triggers 的旧条目保持兼容注入；`/about` 新增 edit/triggers/disable/enable，显示来源与触发词。后端只在用户会话注入，测试/评测仍隔离。回归：`tests/test_user_profile.py` 覆盖 v2 迁移、选择器、预算/过期与只注入相关条目；`tui/tests/user-profile.test.ts`、`tui/tests/app.test.tsx` 覆盖 v2 落盘与编辑/触发词/停用。TUI 全量 **321 项、320 通过**（唯一失败为沙箱无 PTY 的既有项），Python 全量收集 **935 项**退出 0。P0 W6 v2 夹具已切换为带触发词的 v2 偏好，新增 `scripts/w6_report_v2.py` 按 task/mode 聚合多次运行分布（min/median/max），`trace.jsonl` 记录 `user_profile_selection`；真实模型运行尚未执行，建议每个 task-on/off ≥3 次。

**P2 手动观察 → proposed（2026-10-26）**：在 P1 之后实现证据驱动的第一步：`/about observe` 只读本会话 `events.jsonl` 的用户消息，按显式长期偏好线索（以后/记住/始终/from now on 等）生成最多 2 条 `proposed`，带 `source=observation`、`evidence=session:<id>`、建议 triggers 和置信度 0.6；与已有条目规范化文本去重，proposed 永不注入，必须 `/about confirm`。新增 `VIBE_AUTO_OBSERVE=1` opt-in 自动观察（done/stopped 后，cancelled/error 不触发）与 `mergeProposals` 四态雏形：相同文本 NOOP、相似 proposed 更新、confirmed/stale 不静默改写；proposal 默认 30 天过期。confirmed 的 UPDATE/CONFLICT 升级与 A-MAC 准入仍在后续；P3 第一版已补读取路径清理过期 proposed、confirmed 过期转 stale、50 条容量淘汰 proposed、`/about expire` 与 `clear proposed|stale`，confirmed 相似冲突只计数不改写。回归：`tui/tests/preference-observer.test.ts`、`app.test.tsx`；TUI 全量 **330 项、329 通过**（唯一失败为沙箱无 PTY 的既有项）。P0 W6 v2 task-a 已完成 3+3 次但无收益信号；task-b/task-c 仍待执行。夹具已抽到 `scripts/w6_preferences.py`，新增 `tests/test_w6_preferences.py` 离线验证三组任务只选中对应偏好。

**P3 遗忘、容量与冲突第一版（2026-10-26）**：读取路径维护 profile：过期 proposed 清理，过期 confirmed 标记 stale 而不静默删除；超过 50 条时优先淘汰低置信/旧的 proposed；`/about expire <序号> <天数|ISO|->` 设置过期，`/about clear proposed|stale` 清理对应状态；相似 confirmed 冲突只计数并跳过，不自动改写。后台自动清理、confirmed 交互式替换、导出与 A-MAC 准入待后续。回归：`tui/tests/user-profile.test.ts` 覆盖清理/容量/confirmed 保留，`preference-observer.test.ts` 覆盖冲突计数，`app.test.tsx` 覆盖 expire/clear；TUI 全量 **330 项、329 通过**（唯一失败为沙箱无 PTY 的既有项）。

**P4 安全与审计第一版（2026-10-26）**：偏好写入前拒绝明显凭据（`sk-*`、GitHub/AWS token、私钥、`api_key/password/token=` 等），`/about observe` 自动跳过，Python 加载器再次过滤，防止手工 profile 中的凭据进入上下文；profile 以 `0600` 落盘；`/about export` 导出脱敏 JSON；`[USER PROFILE]` 明确声明偏好不能授予权限或改变审批边界；`user_profile_selection` trace 保留注入审计。回归：Python 凭据过滤/注入边界，TUI `looksSensitive`、`/about add` 拒绝、导出过滤；TUI 全量 **337 项、336 通过**（唯一失败为沙箱无 PTY 既有项），Python 全量收集 **936 项**退出 0。补 confirmed 冲突 proposal 保留展示、`/about resolve` 一键替换、`/about reset` 二次确认完整擦除与 `user-profile-audit.jsonl`/`/about audit`（只记动作/数量/id，不写正文）；后台审计、A-MAC 准入待后续。

**P0 W6 v2 离线校验（2026-10-26）**：W6 v2 偏好夹具与任务抽到 `scripts/w6_preferences.py`，`scripts/w6_run.py` 复用；新增 `tests/test_w6_preferences.py` 验证三组任务的选择结果——task-a 只选“解释”，task-b 选“修改 + 测试”，task-c 选“测试”，并确认停用组为空。Python 全量收集 **938 项**退出 0。task-a v2 已完成 3+3 次：用户消息 1/1/1 对 1/1/1，主假设未获支持；启用组中位 tokens/步数更高；task-b/task-c v2 仍待执行。报告已修复为只统计 v2 证据，早期 v1 混入已被排除。

**W6 v2 对照结论（2026-10-26）**：三组任务均未观察到重复说明/纠正下降，主假设未获支持。过滤 v1 与失败运行后：task-a 3/3 有效，用户消息 1/1/1 两侧相同，tokens 中位 260,704 vs 173,872；task-b 3/2 有效（各 1 失败），中位 tokens 245,304 vs 211,745；task-c 2/2 有效，中位 tokens 783,473 vs 527,780。相关性选择正常（task-a 解释、task-b 修改+测试、task-c 测试），停用组从未选中。结论：不进入 P5，不扩自动画像；用户决定停止 P0 扩张，不再补跑剩余样本，保留显式偏好管理。报告已修复为排除旧 v1 与失败运行。

**多 Agent 与 Computer Use 方向调研（2026-10-26）**：按用户要求完成两方向调研与实施规格 [multi-agent-computer-use](specs/multi-agent-computer-use.md)：比较 Anthropic/OpenAI/Google computer use、macOS Accessibility/TCC、MCP 安全、Claude Code/Cognition/OpenAI Agents SDK 多 Agent 经验；结论是先做只读 GUI 观察（C1）与只读子 Agent（M1），再考虑受控输入/可写子 Agent/并行。当前未实现，所有开关默认关闭。

## 当前下一步

W1–W5 已完成真实验收；W6 v2 三组对照完成，主假设未获支持。2026-10-26 用户决定停止 P0 扩张、不进入 P5，保留显式偏好管理能力；后续只有新证据/新评估方法才重新评估个性化。接下来：

1. **评测扩题**：固定前三题参考补丁自检与 `--limit 3` 扩题，并用 `--runs` 多次运行建立基线；
3. **W7 启动条件**：出现第二个高频非编码工作流后再细化，不提前拆平台功能。

评测侧：README 六项完善已通过保留题真实核对（2026-10-25）；`validate` 已支持 `--limit`，可用 `./run_eval.sh validate --limit 3` 做前三题参考补丁自检（真实 Docker 自检待跑）；新增 `execution-chain` 跨层压测题（5 文件注入 + 双端隐藏检查，真实模型 3/3 通过）；`bench_local.py` 新增 `--pause-tokens` 暂停/续做压测（首轮低预算暂停 → `--continue` 重开续做，fake 冒烟与真实运行均已通过）。W0 剩入口/循环代码走查与学习记录。
