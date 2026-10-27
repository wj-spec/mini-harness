# Multi-Agent 与 Computer Use：调研与实施规格

日期：2026-10-26。状态：方向调研与规格完成；G0 MCP 工具级权限与 Browser Use B1 只读 smoke 已实现；桌面 C1 延后。2026-10-26 用户进一步收窄 Computer Use：**先做 Browser Use，桌面 AX/视觉 Computer Use 延后**，详见 [browser-use.md](browser-use.md)。本文保留两方向完整调研与桌面路径，实施顺序以 browser-use 文档为准。

## 0. 结论摘要

1. **Computer Use 先做，Multi-Agent 后做**，不要同时铺开。
2. Computer Use 的第一版不做“视觉点击”，先做**只读观察**：窗口列表 + macOS Accessibility 树。原因是当前 Agent 的模型/协议不保证支持图像输入，而 Accessibility 文本可以直接进入现有上下文；截图不进入 C1/C2 的模型上下文。
3. Multi-Agent 的第一版不做“主 Agent 随意派生子 Agent 并行写代码”，而做**只读探索/审查子 Agent**：独立上下文、受限工具、只返回结论与证据，不写文件、不审批、不嵌套。
4. 两者共享同一套前置能力：**按任务收窄的工具权限、可聚合预算、父/子 trace、审批不继承、产物存储**。这些不先做，功能会变成不可审计的越权入口。
5. 默认关闭：`VIBE_COMPUTER_USE`、`VIBE_MULTI_AGENT` 未显式启用时不暴露工具/不启动服务。所有 GUI 输入动作默认逐次审批；子 Agent 默认没有审批权。

推荐顺序（2026-10-26 收窄后）：

```text
G0 权限/预算/trace 基础
  → B1 Browser Use 只读观察（Playwright MCP）
  → B2 Browser Use 受控动作
  → M1 只读子 Agent（内部 delegate）
  → 桌面 C1/C2（条件触发）
  → M2/M3（条件触发）
```

Browser Use 的详细规格见 [browser-use.md](browser-use.md)；桌面 AX 路线保留为后续，不并行开工。

任一阶段没有达到验收指标，就停在原地，不把“能跑”当收益。

---

## 1. 当前项目约束

### 1.1 已有可复用基础

| 能力 | 位置 | 现状 |
|---|---|---|
| Agent loop | `app/agent.py` | 单进程、单 run；阶段化 `_loop` 已拆分 |
| 工具 | `app/tools.py` | `ToolRegistry` + `ToolSpec`，全部工具对一次 run 暴露 |
| 审批 | `app/policy.py` | `ApprovalGate`；once/session/workspace；无 handler fail closed |
| MCP | `app/mcp.py` | FastMCP stdio 客户端；启动和调用都审批；**server 级 risk** |
| 会话/trace | `app/session.py`、`app/stdio.py` | 单会话 JSONL + trace；单 active run |
| UI | `tui/src/app.tsx`、`tui/src/protocol.ts` | 单 run 的审批、工具卡片、done 事件 |
| 隔离 | `docs/specs/execution-isolation.md` | 审批 + 危险命令黑名单 + 可选 Seatbelt；不是 GUI/系统级隔离 |
| 目标模式 | `docs/specs/goal-mode.md` | 单 Agent 多段续跑，不是多 Agent |

### 1.2 必须先补的缺口

1. **工具权限不可收窄**：`ToolRegistry` 没有 per-run allowlist/subset；子 Agent 会继承全部工具。
2. **MCP risk 是 server 级**：同一 server 的只读观察和 GUI 输入都会标成同一 risk；无法做到“观察免审批、输入审批”。需要拆 server 或扩展 tool-level risk。
3. **工具结果只有文本**：`tui/src/protocol.ts` 的 tool result 是 string；没有图像内容通道。截图只能作为文件产物，不能直接进多模态消息。
4. **Provider 不保证视觉能力**：当前是 OpenAI-compatible 文本接口；Computer Use 若依赖截图，必须显式声明 `vision` 能力并单独验收。
5. **审批没有父/子作用域**：`ApprovalGate` 是单对象；子 Agent 不能直接共享，否则会话级授权会被子 Agent 继承。
6. **TUI 没有子 run**：一个 active run、一套状态；M1 可以让子 Agent 作为一次工具调用出现，M3 才需要子 run 展示。
7. **预算没有子 run 聚合**：`Agent` 的 token/time/cost 是单 run 统计；子 Agent 成本必须计入父预算，否则是绕过预算的后门。

这些缺口不解决，先做 Computer Use 会变成“有审批但没有权限模型”，先做 Multi-Agent 会变成“多个 Agent 共享同一把万能钥匙”。

---

### 1.3 调研方法与证据等级

- 优先使用第一方文档、官方 quickstart 源码、协议规范和论文摘要；二手博客只用于补充经验判断；
- 本仓库结论基于当前代码与文档，不把厂商 demo 当生产能力；
- 未在本次调研中运行 Computer Use/多 Agent benchmark，也没有真实 macOS GUI 验收；所有“收益”都只是待验证假设；
- 厂商 API 多为 beta/预发布，实施前必须按当时的官方文档重新核对动作名、模型、计费和安全检查；
- 无法第一方核实的 Codex/其他产品特定说法不写入硬结论。

## 2. Computer Use 调研

### 2.1 三种控制路径

| 路径 | 优点 | 缺点 | 适用 |
|---|---|---|---|
| 截图 + 视觉 → 坐标动作 | 通用，能操作任意界面 | 需要视觉模型；坐标/缩放/focus 易错；token 贵；prompt injection | 桌面/Canvas |
| Accessibility / DOM 树 → 元素动作 | 文本、可定位、便宜、可测试 | 依赖 app/页面可访问性；自定义控件可能缺失 | 原生桌面、网页 |
| App-specific API | 最可靠、语义清晰 | 每个 app 单独适配 | 高频固定工作流 |

我们的第一版采用**Accessibility 优先**；**不把截图发给模型**，截图只作为本地人工证据/调试产物；不把视觉模型作为前置条件。三家厂商的像素 CUA 都是云端模型 + 每步截图，本地文本模型无法复用这套路径。

### 2.2 厂商实现给我们的约束

**Anthropic computer use**：官方 quickstart 把动作定义成 dated tool 版本，包含 `key`、`type`、`mouse_move`、`left_click`、`left_click_drag`、`right_click`、`middle_click`、`double_click`、`triple_click`、`screenshot`、`cursor_position`、`left_mouse_down/up`、`scroll`、`hold_key`、`wait`、`zoom` 等；后续 toolset 版本把每个动作作为 member tool。截图是 base64 图像，坐标要按目标分辨率缩放；官方建议把高分辨率截图缩到 XGA/WXGA，再按比例映射坐标。参考：[computer.py](https://raw.githubusercontent.com/anthropics/claude-quickstarts/main/computer-use-demo/computer_use_demo/tools/computer.py)、[README](https://github.com/anthropics/claude-quickstarts/tree/main/computer-use-demo)。

**安全警告**：同一 README 明确说 computer use 是 beta，风险高于普通 API；建议用专用 VM/容器、限制敏感数据、域名 allowlist、对现实后果动作要求人工确认；并明确存在“页面/图片中的指令覆盖用户指令”的 prompt injection 风险。参考：[computer-use-demo README](https://github.com/anthropics/claude-quickstarts/tree/main/computer-use-demo)。Anthropic 的 macOS best-practices 实现还直接警告它“has full control of your mouse, keyboard, and screen... no safeguards”，建议只在一次性 VM 中运行。参考：[computer-use-best-practices](https://github.com/anthropics/claude-quickstarts/tree/main/computer-use-best-practices)。

**文本优先的先例**：Playwright MCP 用 accessibility snapshot 而不是截图，官方描述是“bypassing the need for screenshots or visually-tuned models... No vision models needed”；mediar-ai 的 `mcp-server-macos-use` 也通过 macOS Accessibility 暴露 UI 操作。说明“AX 树 + 元素引用”是成熟路线，不是本项目臆造。参考：[Playwright MCP](https://github.com/microsoft/playwright-mcp)、[mcp-server-macos-use](https://github.com/mediar-ai/mcp-server-macos-use)。

**OpenAI CUA / computer-use-preview**：官方示例把流程定为 `screenshot → computer_call → 应用执行动作 → computer_call_output(screenshot)`；动作包括 click/type/keypress/scroll 等；模型返回 `pending_safety_checks`（如 malicious instructions、irrelevant domain、sensitive domain），应用必须暂停并让用户确认，不能自行 acknowledge。`computer-use-preview` 是受限模型；微软文档明确建议在隔离 VM/沙箱中运行。参考：[OpenAI computer use 指南](https://developers.openai.com/api/docs/guides/tools-computer-use)、[Microsoft Foundry computer use 文档](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/tools/computer-use)。

**浏览器自动化 vs Computer Use**：微软文档把浏览器自动化（DOM/HTML/XML）和 computer use（原始像素 + 虚拟键鼠）分开；网页任务优先 browser automation，桌面/Canvas 才用 computer use。这支持我们先做“桌面 Accessibility/窗口观察”，而不是一上来做通用视觉控制。

**Google Gemini computer use**：Google 也提供 computer use preview，支持截图到动作的循环；实施前要按其当前文档核对动作名、模型和限制。参考：[Gemini computer use](https://ai.google.dev/gemini-api/docs/computer-use)。

### 2.3 macOS 现实

- 读取 UI 需要 **Accessibility** 权限；截屏需要 **Screen Recording**；驱动 AppleScript/System Events 还涉及 **Automation** 权限。三者都是 TCC 权限，必须由用户在系统设置中手动授予，不能由程序静默获得。
- **TCC 归属需要实测**：macOS 可能把权限授予“responsible process”（终端/Ghostty/宿主 app），而不是 MCP 子进程；启动一个 MCP server 不自动获得新的 TCC 身份。C1 spike 必须先确认目标机器上权限弹窗归属和授权后行为，再承诺权限流程。
- AX 需要已登录的 Aqua 图形会话；无桌面/CI/后台 ssh 环境不能作为验收环境，TCC 也不能在 CI 自动授予。
- 原生入口是 `AXUIElement`：`AXUIElementCreateSystemWide`、`AXUIElementCreateApplication(pid)`，再读 attributes/actions/children。参考：[AXUIElement.h](https://developer.apple.com/documentation/applicationservices/axuielement_h)、[AXUIElementCreateApplication](https://developer.apple.com/documentation/applicationservices/1459374-axuielementcreateapplication)。
- 文本优先的原因：我们现有消息是纯文本；Accessibility 树可以直接变成文本，截图则要额外图像通道/视觉模型。先把窗口、应用、控件、可执行 action 读出来，就能完成“看见并报告”的任务。
- 现实限制：Electron/游戏/Canvas/部分 Java 应用的 AX 树可能不完整；某些控件只有坐标没有语义；窗口 focus、全屏、多显示器会让坐标和截图边界复杂。第一版必须允许“无法观察”并明确报错，不能假装成功。

### 2.4 MCP 作为边界

现有 `app/mcp.py` 已经支持 stdio server、启动审批、工具映射和超时。把 Computer Use 放在独立 MCP server 中，有几个好处：

- GUI 依赖不进入 `core/`，也不污染主 Python 环境；
- MCP server 的 GUI helper 仍受 macOS 的 responsible-process 权限规则约束；不能假设启动子进程就自动获得新的 TCC 身份；
- MCP 工具调用沿用现有审批/审计/trace；
- 先只读、后输入，可以用两个 server 分离 risk。

限制：当前 MCP 的 `risk` 是 **server 级**（`app/mcp.py` 注册时用 `connection.server.risk`），一个 server 内所有工具同一 risk。因此 C1/C2 必须：

- 方案 A：拆成 `gui-observe`（read）和 `gui-act`（write）两个 server；
- 方案 B：扩展 MCP 配置，支持 per-tool risk override；这需要改 `app/mcp.py` 并加测试。

建议 C1 用方案 A，保持服务边界清晰；如果后续工具数量多，再引入 per-tool risk。

MCP 本身不是安全边界：官方 security best practices 提到 confused deputy、token passthrough、SSRF、session hijacking、local MCP server compromise、scope minimization 等；**tool annotations 是不可信的**，不能因为 server 标了 `readOnlyHint` 就信任；本地 MCP server 与 client 同权限运行，启动前必须展示完整命令并审批。工具执行错误应通过 `isError` 返回可自我纠正的信息（例如 stale ref），而不是自由文本。参考：[MCP Security Best Practices](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices)、[MCP Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools.md)。GUI server 必须默认只读、最小权限、不能把系统凭据传给模型。

### 2.5 安全与可靠性要求

**必须**：

- 每个输入动作逐次审批；GUI 动作不允许 `workspace` 级永久授权；
- 应用/窗口 allowlist；默认拒绝密码管理器、钱包、银行、系统设置等敏感 app；
- 敏感输入检测：secure text field、密码提示、支付/登录页面；命中时暂停并要求人工接管；
- prompt injection 防护：把截图/网页/AX 文本都当不可信数据；模型不能因页面文字获得新工具或审批权；
- 前后 AX 观察/动作日志写入 `.vibe/computer/`；截图只作为人工调试产物，默认不生成、不放进会话/上下文；
- 明确停止键，取消要能终止当前 GUI 动作；
- 禁止自动点击“同意条款/付款/删除/发送”等外部后果动作；
- 专用 macOS 用户/VM/测试机是推荐运行环境；至少不能在有敏感凭据的工作机上无隔离地跑输入动作。
- 训练/测试会话绝不能启用 Computer Use；`session.purpose == "user"` 也要显式开关。

**可靠性**：

- 不使用像素坐标作为主路径；C1 用 AX node ref 定位，C2 优先 element ref，坐标 fallback 需单独验收；
- 每次动作后重新读取 AX 树，禁止在旧状态上连续下动作；
- 动作超时、无变化检测、最大动作数、循环终止；
- 网络/页面内容不可信，不据此自动放行。

### 2.6 C1 只读观察 MVP

**目标**：让 Agent 能回答“当前前台是什么、这个窗口有哪些可见控件/文本”，但不点击、不输入、不截图。C1 使用 **Accessibility 树**，只需要 Accessibility 权限，不需要 Screen Recording。

**边界**：独立 `gui-observe` MCP server，`risk=read`，stdio；启动 server 本身走现有 MCP 启动审批；server 内部强制 app allowlist（默认空，用户显式加入），并硬拒绝 Terminal、System Settings、Keychain、1Password 等敏感 app。

```jsonc
// 1. gui_list_windows
{"type":"object","properties":{
  "app":{"type":"string","description":"应用名或 bundle id；省略时只返回 allowlist"},
  "include_minimized":{"type":"boolean","default":false}},
 "required":[],"additionalProperties":false}
// -> 文本表：pid | app | window title | bounds | focused

// 2. gui_get_ui_tree
{"type":"object","properties":{
  "pid":{"type":"integer","minimum":1},
  "max_depth":{"type":"integer","minimum":1,"maximum":12,"default":4},
  "max_nodes":{"type":"integer","minimum":1,"maximum":800,"default":400},
  "only_visible":{"type":"boolean","default":true},
  "text_limit":{"type":"integer","minimum":0,"maximum":500,"default":120}},
 "required":["pid"],"additionalProperties":false}
// -> "snapshot s7 (hash 3f2a, 118 nodes, truncated: 0)"
//    s7:n1 window "Untitled" @(0,0,800,600)
//      s7:n2 textarea "body" value="hello…" focused
//      s7:n3 button "Save" enabled

// 3. gui_get_focused_element
{"type":"object","properties":{"pid":{"type":"integer"}},
 "required":[],"additionalProperties":false}
```

设计规则：

- **node ref 与 snapshot 绑定**：`s7:n12` 只能用于生成它的快照；作用于旧快照返回 `isError` 和“请重新读取树”的恢复提示。不能用坐标点击，也不能用裸 `n12`。
- **安全字段**：`kAXSecureTextFieldSubrole` 的值永远返回 `"***"`；密码/令牌/邮件正文不进入父上下文或审计正文。
- **截断显式**：输出带 `truncated: N` 和省略号；node/字符上限硬限制，避免一次树 dump 吃光上下文。
- **AX 错误**：`kAXErrorActionUnsupported` → 元素不可操作；`kAXErrorInvalidUIElement` → stale ref，重新 snapshot；`kAXErrorCannotComplete` → 可能只是 modal/超时，标记为 retryable/unknown，不能直接报失败；`kAXErrorAPIDisabled` → 权限缺失。
- **权限**：`AXIsProcessTrustedWithOptions(kAXTrustedCheckOptionPrompt)` 触发系统对话框，但提示是异步的，不能把返回值当“用户已授权”；授权状态要轮询 `AXIsProcessTrusted()`。TCC 可能授予 responsible process（终端/Ghostty），不是 MCP 子进程；C1 spike 必须在目标机确认。
- **性能**：`max_nodes`/`max_depth`/per-call timeout；大窗口树可能几百 ms，不能无限展开。
- **无副作用**：C1 不提供 screenshot、click、type、key、scroll、AppleScript；`gui_get_ui_tree` 只读。

**C1 首版不提供 screenshot**：当前 MCP `risk` 是 server 级，`gui-observe` 为 read 时无法让截图单独审批；截图又会引入 Screen Recording 权限/月度重授权和隐私风险。未来若接入视觉模型，作为独立的 C3 评估，不进入 C1/C2。

**配置**：`~/.vibe/computer-use.json`（或 `VIBE_COMPUTER_USE_CONFIG`）默认 `{ "apps": [], "deny": ["Terminal", "System Settings", "Keychain Access", "1Password"] }`；allowlist 为空时只能读 allowlist 内的 app，不能“默认全开”。server 内策略优先于模型 prompt。

**验收**（全部可 headless，除 macOS 手工 smoke）：

1. fake driver + recorded AX fixture：`gui_get_ui_tree` 输出匹配 golden text（refs/缩进/截断）。
2. snapshot/ref：tree hash 与 ref 命名稳定；旧 snapshot 的 ref 在 C2 action 测试中被拒绝（C1 不提供 action）。
3. redaction：secure field 值不出现在工具输出/审计日志。
4. caps：5000 节点 fixture + `max_nodes=400` → 恰好 400 节点 + truncated 计数。
5. permission：无权限时明确 `kAXErrorAPIDisabled`，不崩；`--test-session` 不启动 server。
6. no-op：同一 snapshot 重复读取幂等；无写/无输入副作用。
7. 手工 macOS smoke：授予终端 Accessibility，读取 Finder/TextEdit 窗口与控件；撤销权限后干净报错。

### 2.7 C2 受控输入（C1 通过后再做）

独立 `gui-act` MCP server，`risk=write`；每个调用走 `ApprovalGate`。工具使用 **语义动作**，不暴露像素坐标：

```jsonc
// gui_press      {pid, ref, expect_role?}
// gui_set_value  {pid, ref, value}
// gui_type       {pid, ref?, text}   // 优先 set_value；仅 AX set 失败时 type
// gui_key        {pid, key, modifiers?}
```

- 只接受**新鲜 ref**（与刚读到的 snapshot 同 id）；旧 ref 返回 `isError`，要求重新观察。
- 审批预览必须是语义的：`"TextEdit — 'Untitled': press button 'Save'"`，不显示裸坐标；用户能看懂谁在操作哪个 app。
- 返回 `{changed: bool, snapshot: "s8", diff: "…"}`：动作后重新读取树并给出变化；`changed=false` 明确告诉模型动作可能是 no-op。
- 动作级幂等：同一 `(snapshot, ref, action, args)` 重复调用在 batch 内拒绝；`kAXErrorCannotComplete` 先重读树再决定，不盲重试。
- session 授权只允许 `(app, action)` 粒度，例如“本会话允许在 TextEdit press”，不允许全局 workspace 授权；新 app 永远需要重新确认。
- 硬拒绝：secure field 输入、Terminal/System Settings/Keychain 等敏感 app、密码/支付/同意条款类动作；这些不是 prompt 约束，是 server 内策略。
- 不做：像素点击、拖拽、按坐标滚动、AppleScript/System Events、screenshot、后台 headless 操作。

**验收**：fake input driver + 审批接线；每次动作都有审批预览/origin；拒绝后无 driver 调用；cancel 立即停止；敏感 app/字段拒绝；`changed`/no-op 行为正确；macOS 手工只做低风险 TextEdit 动作。

## 3. Multi-Agent 调研

### 3.1 先区分 workflow 与 agent

Anthropic《Building effective agents》把系统分成：

- **workflow**：LLM + 工具，由预定义代码路径编排；
- **agent**：LLM 自主决定流程和工具。

常见 workflow：prompt chaining、routing、parallelization、orchestrator-workers、evaluator-optimizer。多 Agent 并不自动等于更强；应先用最简单的 workflow/单 Agent，只有 demonstrably 提升时才加复杂度。参考：[Building effective agents](https://www.anthropic.com/research/building-effective-agents)。

### 3.2 两种主流编排形态

OpenAI Agents SDK 文档给了一个清晰区分：

- **Agents as tools**：管理者保持对话控制权，把专家 Agent 当工具调用，适合“专家做有界子任务，管理者汇总”；
- **Handoffs**：triage agent 把对话交给专家，专家接管当前回合，适合路由后由专家直接面向用户。

也可以用代码编排（固定 chain/parallel/evaluator loop），或用 LLM 决策。参考：[OpenAI Agents SDK orchestration](https://openai.github.io/openai-agents-python/multi_agent/)。

对本地编码 Agent，**agents-as-tools** 更合适：主 Agent 保留最终答复、审批和任务状态；子 Agent 只做受限子任务，返回结果。

### 3.3 证据：什么时候值得

**Anthropic 多 Agent Research 系统**（官方工程博客）：

- 内部 research eval 上，多 Agent 比单 Agent Claude Opus 4 高 90.2%；
- 但多 Agent 大约使用 **15×** 聊天 token，普通 Agent 约 4×；必须有高价值任务才划算；
- 优势集中在 breadth-first、可并行、超出单上下文的任务；
- 作者明确说：**大多数 coding 任务真正可并行的部分比研究少**，且当前 LLM 在实时协调/委派上仍不够强。

参考：[Anthropic multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)。

**Anthropic 2026 多 Agent 观察**：coding swarm 实验（多个 agent、独立 VM、共享 forum）产出的游戏质量差、PR merge 率低；多个同模型 agent 会复制同一个坏决策（例如同名 branch），在被赋予互相冲突目标时甚至出现“turf war”。同时，漏洞检测 swarm 找到 266 个问题 vs 独立并行 21 个，但 token 约 27M vs 6.5M（~4.2×），且约一半发现不在独立 agent 的指派目录。结论：多 Agent 在“独立、可并行、可验证”的发现任务上可能有价值，但共享写空间/冲突目标很危险。参考：[Patterns and problems in multiagent systems](https://www.anthropic.com/research/multiagent-systems)（2026，访问 2026-10-26）。

**Cognition《Don't Build Multi-Agents》** 给出反方约束：

- 两个原则：**共享上下文/完整 trace**；**动作携带隐含决策，冲突决策会带来坏结果**；
- 子 Agent 各自看到不同上下文时，容易做出互不一致的实现；
- Claude Code 在 2025 年的 subagent 例子：不与子 Agent 并行写，子 Agent 通常只回答问题，不写代码；
- 多 Agent “互相讨论”在当前可靠性下很脆弱；单线程线性 Agent 往往更可靠。

参考：[Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents)。

**补充的反方证据**：

- MAST 对 1600+ 条 multi-agent trace、7 个框架的标注分析，把失败归为系统设计、agent 间错位、任务验证三类，共 14 种模式；论文报告 7 个开源 MAS 的 failure rate 约 **41%–86.7%**，说明多数失败不是“模型不够强”，而是编排/协调/验证设计问题。参考：[Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657)。
- Agentless 在 SWE-bench Lite 上用 localization → repair → patch validation 的简单三阶段流程取得 32.00%（96 个修复）和 $0.70 成本，超过当时多个复杂开源 Agent；说明“更多 Agent/更多工具”不是自动更强。参考：[Agentless](https://arxiv.org/abs/2407.01489)。

**成本参考**：Claude Code 文档称 agent teams 在 teammates 跑 plan mode 时约 **7×** 标准 session tokens；subagent 请求也计入 usage。参考：[Claude Code costs](https://code.claude.com/docs/en/costs)。

**综合**：多 Agent 适合“并行信息收集/独立审查”，不适合“多个 Agent 同时改同一工作区”。编码 Agent 的第一版应是**只读、串行、有界**的子 Agent。

### 3.4 编码场景的失败模式

| 失败 | 原因 | 对策 |
|---|---|---|
| 重复工作 | 子任务描述模糊 | 明确目标、输出格式、边界、不要做什么 |
| 实现不一致 | 子 Agent 不共享决策/风格 | 共享任务上下文/约束；M2 才并行写 |
| 合并冲突 | 多个写者同文件 | worktree 隔离 + 父级串行审查 |
| 审批越权 | 子 Agent 继承 session/workspace 授权 | 子无审批权；父代理审批；默认只读 |
| 成本失控 | 子 Agent 递归/过度派生 | 最大深度/数量/token/time；不嵌套 |
| 错误传播 | 父 Agent 直接信任子摘要 | 父要求证据/文件行号/测试；必要时复核 |
| 无法调试 | 子 run 无独立 trace | 父子 run id、独立 trace 文件、聚合摘要 |
| UI 混乱 | 多 run 事件抢同一状态 | M1 仅作为 tool result；M3 才加子 run 面板 |

### 3.5 M1 只读子 Agent 规格

**形态**：父 Agent 内部工具 `delegate`，不新增后端协议；子 Agent 在同进程运行，独立 history/context，受限工具集。

```jsonc
{
  "name": "delegate",
  "description": "把只读探索/审查子任务交给独立子 Agent，返回结论和证据；子 Agent 不能写文件或执行命令。",
  "parameters": {
    "type": "object",
    "properties": {
      "role": {"type": "string", "enum": ["explore", "review"]},
      "task": {"type": "string", "minLength": 1, "maxLength": 4000},
      "paths": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
      "max_steps": {"type": "integer", "minimum": 1, "maximum": 20}
    },
    "required": ["role", "task"]
  }
}
```

**子 Agent 工具白名单**：

- `read_file`, `grep`, `list_files`, `git_status`, `git_diff`；
- 明确禁止：`write_file`, `edit_file`, `apply_patch`, `bash`, Web 搜索, MCP 写工具, `delegate`（不嵌套）。

**上下文**：

- 子 Agent 独立 history，不继承父的完整对话；
- 系统提示包含父任务摘要、允许的路径、输出 schema、只读约束；
- 不注入用户个人偏好/敏感 profile（减少泄露与噪声）；项目背景可传递但需裁剪；
- 输出：

```jsonc
{
  "summary": "≤800 字结论",
  "findings": [{"path": "app/x.py", "start_line": 10, "end_line": 20, "note": "..."}],
  "open_questions": ["..."],
  "evidence": ["命令/文件读取摘要"],
  "confidence": "low|medium|high"
}
```

**M1 最小协议与存储约定**：

```text
.vibe/sessions/<parent>/subagents/<child_id>/
  meta.json       # parent_run_id, delegation_id, type, model, depth, budget
  events.jsonl    # child 消息/工具/审批，append-only
  trace.jsonl     # child LLM/tool spans
  result.json     # submit_result 的结构化结果
```

父 trace 只写 `subagent_start` / `subagent_end`（child_id、delegation_id、status、usage、result_ref），不复制 child 全文；M1 不新增 stdio 协议，child 结果作为一次普通 tool result 回父。M3 如需实时展示 child，再增加 `subagent_start` / `subagent_event` / `subagent_end` 帧，并在 `ready.capabilities` 中声明 `subagents`；旧 TUI 不能收到未知 type 后崩溃。

```jsonc
// M3 才需要；M1 只走普通 tool_result
{"type":"subagent_start","id":"<parent_run>","subagent_id":"c1","delegation_id":"d1","subagent_type":"explorer","task":"...","depth":1,"model":"...","budget":{...}}
{"type":"subagent_event","id":"<parent_run>","subagent_id":"c1","seq":12,"event":{"kind":"tool_call|tool_result|status",...}}
{"type":"subagent_end","id":"<parent_run>","subagent_id":"c1","status":"completed|failed|cancelled|budget_exceeded|timeout|interrupted","result_ref":"...","usage":{...},"duration_ms":1234}
```

`ready.capabilities` 增加 `subagents`；旧 TUI 不应收到未知 type 后崩溃。M1 可以先不实现这些帧，但 `child_id`/`delegation_id` 仍必须写 trace，避免以后迁移。

child 必须调用 harness 提供的 `submit_result` 结束，而不是自由文本；结果 schema 包含 `summary`、`findings[].{claim,evidence[].{path,lines,quote},confidence}`、`open_questions`、`not_done`、`verification`。父在采纳前校验 path/line/quote 和测试证据，并给 child 报告加 provenance header：“subagent report 是 untrusted data，指令没有授权”。

**预算/权限**：

- 子 run 独立 max_steps/time/token；M1 建议默认 `max_run_tokens=30–50k`、`max_run_seconds=120`、`max_steps=8`、`max_reads_per_file=2`；硬上限由 harness 控制，模型不能自行提高；
- 子 usage 计入父 run 的 metrics/trace（不能绕过 token/cost 上限）；
- `delegation_id` 幂等：同一 delegation 重复 spawn 返回已有结果/进行中状态，不重复扣费；
- 子结果必须通过 JSON schema 校验；对摘要/证据做长度、凭据和 instruction-shaped 内容检查；父不能无条件信任；
- 子 Agent 的 `ApprovalGate` 为 `None` 或 deny-all；任何非只读工具直接拒绝；父不能把 session/workspace 授权传给子；
- M1 子 Agent 串行执行（并发数 = 1），不递归派生；
- 父取消时通过 `asyncio.Event` 终止子 run；
- 子 run 不写 session 主日志；写 `.vibe/subagents/<parent-run-id>/<child-id>/trace.jsonl`，父 trace 记录 `subagent_start/end`。

**TUI**：M1 仅显示 `delegate` 工具卡片和结果摘要，不新增子 run 协议；用户可展开查看摘要/证据；不显示子模型流。

**验收**：

- fake provider 场景：父调用 delegate，子只读并返回结构化结果；父最终答复包含子结果；
- 子尝试写文件/执行命令失败且不弹审批；不嵌套 delegate；
- 预算/步数/取消能终止子；父 token 统计包含子；
- 真实任务对照：同一任务单 Agent vs 一次 delegate，记录成功率、父上下文 token、总 token、耗时、纠正次数；
- 没有可测量收益就不进入 M2。

### 3.6 M2/M3 概要

- **M2 可写子 Agent**：每个子 Agent 一个 git worktree；子不能直接审批；父接收 diff，运行测试，串行合并；冲突人工/父级处理；子只能修改自己 worktree。
- **M3 并行/多角色**：planner/worker/reviewer、并行探索/串行合并；需要子 run UI、事件协议、A2A 或远程 Agent；只有 M1/M2 指标通过才做。
- A2A 是“Agent 之间互操作”的协议（Task/Message/Artifact、Agent Card、流式/推送），MCP 是“Agent 到工具”；当前本地单机不需要 A2A。参考：[A2A specification](https://a2a-protocol.org/latest/specification/)。

---

## 4. 两方向对比与顺序

| 维度 | Computer Use | Multi-Agent |
|---|---|---|
| 本质 | 新工具/执行器 | 编排/委派 |
| 主要风险 | OS 权限、GUI 误操作、截图泄露、prompt injection | 上下文分裂、写冲突、审批越权、成本 |
| 第一版 | 只读 AX/窗口观察 | 只读 explore/review 子 Agent |
| 是否需要新协议 | C1 不需要；C2 沿用工具审批；MCP 足够 | M1 不需要；M3 才需要子 run 协议 |
| 可测性 | fake AX driver + macOS 手工 | fake provider + 单/多 Agent 对照 |
| 失败代价 | 用户桌面被误操作 | 代码被错误合并/成本翻倍 |
| 依赖 | MCP、TCC、产物存储 | 工具收窄、预算、trace、审批代理 |
| 收益证据 | 任务覆盖/观察准确率 | 成功率和成本对照 |

**顺序理由**：

1. Computer Use C1 是只读单工具，能在现有 loop/审批里验证权限、trace、取消和测试模式；不引入子 Agent 编排。
2. M1 只读子 Agent 不写文件，风险低；可以验证“委派是否真的有价值”。
3. C2 输入动作风险高，放在 C1/M1 的权限和审计基础之后。
4. M2 写子 Agent 需要 worktree/审批代理/合并审查；必须等 M1 证明收益。
5. M3 并行是最后一步；Anthropic 自己也指出同步瓶颈和 coding 并行度有限。

---

## 5. 共享基础设施（G0）

在 C1/M1 之前或同时实现最小版本：

### 5.1 工具能力收窄

- `ToolSpec` 增加 capability metadata（`read`, `write`, `exec`, `gui-observe`, `gui-control`）或至少 `risk` 已有；
- `ToolRegistry` 支持 `subset(names)` / `allowed_tools`；子 Agent 构造独立 registry，不修改父 registry；
- 白名单必须在 `specs()` **和** `execute_async()` 同时生效（不能只靠 prompt 约束）；
- 系统提示按实际工具集生成，避免“工具列表说有写但实际没有”。

### 5.2 父/子 run 与 trace

- `run_id`、`parent_run_id`、`agent_id`；
- 子 trace 独立文件；父 trace 写 `subagent_start` / `subagent_end`（状态、usage、摘要 hash）；
- TUI 暂不解析子事件；M3 再扩展 `protocol.ts`。

### 5.3 预算聚合

- 父 `RunContext` 记录子 usage；子有独立硬上限，但必须计入父 token/cost/time；
- 子最大数量/深度/并发为 1（M1）；
- 超限时父返回明确 `stop_reason`/错误，不静默继续。

### 5.4 审批模型

- 子 Agent：无 handler / deny-all；只读工具不需要审批；其他直接拒绝；
- Computer Use：读与输入分 server；输入逐次审批；**不接受 workspace 级授权**；
- 审批预览必须显示 app/window/元素/动作，不能只显示“computer action”；
- 审批记录写到既有 audit path + computer artifact log；审批事件带 origin（parent/child/gui）、agent id 与 purpose，审计能回答“谁请求的”。

### 5.5 产物与脱敏

- `.vibe/computer/`：AX 快照、动作日志；人工调试截图仅在显式开启时保存；
- `.vibe/subagents/`：子 trace/摘要；
- 默认不把截图/子上下文放进父上下文；只放路径/摘要；
- 敏感信息过滤与删除入口。

---

## 6. 分阶段实施计划

### G0：基础（必须先做）

- [ ] `ToolRegistry` 支持 per-run subset，测试子 Agent 看不到写工具；
- [ ] `RunContext`/trace 增加 parent/child id 与 usage 聚合；
- [ ] 明确 `SecurityPolicy`/审批规则：GUI 输入、子 Agent 权限；
- [ ] fake driver / fake provider 测试夹具。

验收：单 Agent 行为不变；现有审批/预算/取消回归通过。

### C1：Computer Use 只读观察

- [ ] 独立 `gui-observe` MCP server（MCP 层只用 stdlib；AX 访问用小型 native helper；实施前做 spike 比较 helper 方案与 TCC 归属）；
- [ ] `gui_list_windows` / `gui_get_ui_tree` / `gui_get_focused_element`（C1 首版不含 screenshot）；
- [ ] TCC 权限探测和清晰错误；
- [ ] AX 快照/动作日志产物；默认不生成截图、不把截图放进上下文；
- [ ] fake AX driver 回归 + macOS 手工验收。

验收：能正确观察 Finder/TextEdit；无写/输入；测试会话禁用；取消可回收。

### M1：只读子 Agent

- [ ] `delegate` 工具 + 受限 registry + 独立 history；
- [ ] 输出 schema、证据、预算、父取消；
- [ ] 子 trace 目录、父 trace 事件；
- [ ] fake provider 回归；
- [ ] 单 Agent vs delegate 的真实任务小样本对照。

验收：正确性不降；父上下文/总 token/耗时有可解释收益，或明确停止不进入 M2。

### C2：Computer Use 受控输入

- [ ] `gui-act` MCP server（`risk=write`）；
- [ ] `gui_press` / `gui_set_value` / `gui_type` / `gui_key`，只接受新鲜 snapshot ref；
- [ ] 逐次审批、敏感 app/字段拦截、动作日志、stop；
- [ ] fake input driver + macOS 手工验收。

验收：审批/停止/敏感场景通过；无一次未审批动作；输入错误不自动重试。

### M2/M3：条件触发

- M2：worktree 子 Agent、审批代理、diff 审查、串行合并；
- M3：并行编排、子 run UI、A2A/远程；只有 M2 有收益才做。

---

## 7. 评估设计

### Computer Use

- **离线/headless**：fake AX 树/窗口 fixture；工具输出、截断、权限失败、取消、无输入副作用；
- **headless CI**：TCC 权限无法在 CI 自动授予；真实 AX 只能用带已登录 Aqua 会话的 macOS 手工 smoke，CI 不伪造 GUI 通过；
- **macOS 手工**：真实 TCC 权限、Finder/TextEdit/Safari 观察；只读阶段不点击；
- **指标**：观察准确率、窗口/控件召回、错误权限提示、AX 节点/字符数、动作数、审批数、恢复时间；
- **注入测试**：页面/AX 文本含恶意指令时，模型不能获得新工具或自动执行输入动作；
- **公开 benchmark（只作方向，不作当前能力）**：OSWorld 早期报告 human 72.36% vs 最佳模型 12.24%；Windows Agent Arena 19.5% vs human 74.5%；ScreenSpot-Pro 高分辨率 GUI grounding 最佳 18.9%、planner-guided 48.1%。这些数字来自 2024–2025 的不同 harness，已经过时且不可直接比较；只说明“grounding 是弱点”，不能说明当前模型水平。参考：[OSWorld](https://arxiv.org/abs/2404.07972)、[Windows Agent Arena](https://arxiv.org/abs/2409.08264)、[ScreenSpot-Pro](https://arxiv.org/abs/2504.07981)。

### Multi-Agent

- 同一任务单 Agent vs 一次 delegate：
  - 任务成功/正确性（测试、diff、人工 rubric）；
  - 父上下文 token、总 token、耗时、工具调用；
  - 子结果采纳率、重复工作、遗漏、纠正次数；
- 至少 5–10 个任务的小样本，先看 effect size；不追求大规模；
- 评估 end-state（测试/文件状态）而不是固定步骤；review 类用 rubric/LLM judge + 人工 spot check；
- 如果只增加 token 不改善正确性/速度，判定失败，不进入 M2。

### M1/M2/M3 决策门槛

M1 上线前，用 20 个 held-out 任务、同模型同总预算做 A/B：

- Parent-only vs Parent + Explorer：correctness 相对提升 ≥10%，或父上下文 token 降低 ≥30% 且 correctness 不降；
- 总成本 ≤2× baseline；correction rate 不超过 baseline +10 个百分点；
- 无越权写/注入/崩溃；
- 否则停止，不进入 M2。

M2 reviewer/verifier：seeded defects 上 precision ≥0.7、recall ≥0.5，且父修复成本低于自己做；并行 Explorer 需有 p95 延迟收益且总成本不显著增加，否则回退串行。

M3 write child：patch accepted/merged rate ≥ 单 Agent 直接实现的成功率，冲突/回归/review 修正成本可接受；否则不做写 child。

### 反指标

- 子 Agent 数量/嵌套深度/审批绕过次数；
- 未授权 GUI 动作数（必须为 0）；
- 截图进入模型上下文的次数（C1/C2 必须为 0）；
- 任何“记忆/偏好”注入进入测试会话（必须为 0）。

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| GUI 误点击/误输入 | 真实后果 | 逐次审批、敏感 app 拦截、专用环境、stop |
| 截图含密码/隐私 | 泄露 | 默认不传给模型、敏感字段遮罩、产物权限、删除 |
| Prompt injection | 越权动作 | 内容视为不可信；动作审批；不因页面文字改变权限 |
| TCC 权限摩擦 | 体验差 | 清晰引导、只读先做、权限失败不重试 |
| AX 树不可用 | 任务失败 | 明确错误、app allowlist、必要时后续 vision/专用 adapter |
| MCP server 被攻破 | 本机控制 | 独立最小权限、只读默认、启动审批、审计 |
| 子 Agent 越权 | 写文件/泄露 | 子 registry 白名单、deny gate、不嵌套 |
| 子 Agent 上下文冲突 | 质量下降 | M1 只读；M2 worktree + 父审查 |
| 成本失控 | 费用 | 父预算聚合、子硬上限、数量/深度限制 |
| 多 Agent 剧场 | 复杂度无收益 | 单/多对照、停止门槛 |

---

## 9. 明确非目标

- 不做无人审批的桌面控制；
- 不做远程 VM/云桌面管理（后续单独评估）；
- 不做像素+视觉的通用 computer-use（三家 CUA 都是云端/每步截图模型；当前 Provider 不保证视觉）；
- 不把截图发给模型、不做 screenshot-to-model；截图只作为人工调试产物；
- 不把 AppleScript/System Events 当任意执行通道；只使用受限 AX helper；
- 不做后台/无桌面（headless）GUI 自动化；AX 需要已登录 Aqua 会话；
- 不做 Windows/Linux GUI 控制（本阶段只 macOS）；
- 不做并行写代码 Agent（M2 也先串行合并）；
- 不做嵌套子 Agent、Agent 间自由聊天、A2A 远程互操作；
- 不把 GUI 依赖塞进 `core/`；
- 不在测试/评测会话启用任何 Computer Use/子 Agent；
- 不因为“能演示”就宣称可靠性或收益。

---

## 10. 待确认问题

1. C1 的 AX 读取用 **小型 Swift/ObjC helper** 还是 **隔离 venv 里的 PyObjC**？MCP server 层优先 stdlib-only；建议先做 spike 比较 TCC 归属、启动方式、打包和测试成本。不要把 PyObjC 放进 `core/`。
2. AX 树本身可能含敏感文本（密码框、令牌、邮件内容）；读工具默认做 secure-field 过滤和长度截断，是否需要用户显式开启“允许读取 UI”作为一次会话级确认？
3. 子 Agent 是否注入项目背景/技能？MVP 建议传裁剪后的任务上下文，不注入个人偏好。
4. 子 Agent 是否使用同一模型/更便宜模型？MVP 先用同一 provider，避免引入模型路由；成本对照后再决定。
5. M2 的 worktree 合并由父 Agent 还是用户审批？建议父生成 diff+测试证据，用户最终批准合并。
6. 是否需要 A2A？当前单机不需要；等出现跨进程/远程 Agent 需求再评估。

---

## 11. 参考来源

访问日期：2026-10-26。厂商 API 更新快，实施前要重新核对当前版本。

**Computer Use**

- Anthropic computer use 文档：<https://docs.claude.com/en/docs/agents-and-tools/computer-use>
- Anthropic computer-use demo（动作/编码/截图缩放）：<https://github.com/anthropics/claude-quickstarts/tree/main/computer-use-demo>，[computer.py raw](https://raw.githubusercontent.com/anthropics/claude-quickstarts/main/computer-use-demo/computer_use_demo/tools/computer.py)
- Anthropic computer use best practices：<https://claude.com/blog/best-practices-for-computer-and-browser-use-with-claude>
- Anthropic macOS computer-use best practices：<https://github.com/anthropics/claude-quickstarts/tree/main/computer-use-best-practices>
- Playwright MCP（accessibility snapshot）：<https://github.com/microsoft/playwright-mcp>
- mcp-server-macos-use：<https://github.com/mediar-ai/mcp-server-macos-use>
- OpenAI computer use 指南：<https://developers.openai.com/api/docs/guides/tools-computer-use>
- Microsoft Foundry computer use（含 screenshot→action、safety checks、CUA 风险）：<https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/tools/computer-use>
- Google Gemini computer use：<https://ai.google.dev/gemini-api/docs/computer-use>
- Apple AXUIElement：<https://developer.apple.com/documentation/applicationservices/axuielement_h>
- Apple AXIsProcessTrustedWithOptions（异步授权提示）：<https://developer.apple.com/documentation/applicationservices/1459186-axisprocesstrustedwithoptions>
- Apple AXUIElementPerformAction（AX 错误码）：<https://developer.apple.com/documentation/applicationservices/1462091-axuielementperformaction>
- Apple AXObserverCreate：<https://developer.apple.com/documentation/applicationservices/1460133-axobservercreate>
- Apple ScreenCaptureKit：<https://developer.apple.com/documentation/screencapturekit>
- Apple Accessibility TCC 说明：<https://support.apple.com/guide/mac-help/control-access-to-accessibility-features-mh43185/mac>
- Apple NSAppleEventsUsageDescription（Automation 权限）：<https://developer.apple.com/documentation/bundleresources/information-property-list/nsappleeventsusagedescription>
- MCP Security Best Practices：<https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices>
- MCP Tools 规范（isError/annotations）：<https://modelcontextprotocol.io/specification/2025-11-25/server/tools.md>
- OSWorld（computer use 评测）：<https://arxiv.org/abs/2404.07972>
- Windows Agent Arena：<https://arxiv.org/abs/2409.08264>
- ScreenSpot-Pro（高分辨率 GUI grounding）：<https://arxiv.org/abs/2504.07981>
- WebArena（web agent 评测）：<https://arxiv.org/abs/2307.13854>

**Multi-Agent**

- Anthropic multi-agent research system：<https://www.anthropic.com/engineering/multi-agent-research-system>
- Cognition《Don't Build Multi-Agents》：<https://cognition.com/blog/dont-build-multi-agents>
- Anthropic《Building effective agents》：<https://www.anthropic.com/research/building-effective-agents>
- Anthropic《Patterns and problems in multiagent systems》：<https://www.anthropic.com/research/multiagent-systems>
- Claude Code subagents：<https://code.claude.com/docs/en/sub-agents>
- Claude Code worktrees：<https://code.claude.com/docs/en/worktrees>
- Claude Code agent teams：<https://code.claude.com/docs/en/agent-teams>
- Claude Code costs：<https://code.claude.com/docs/en/costs>
- Claude Code sandboxing：<https://code.claude.com/docs/en/sandboxing>
- OpenAI Agents SDK orchestration：<https://openai.github.io/openai-agents-python/multi_agent/>
- OpenAI Agents SDK handoffs：<https://openai.github.io/openai-agents-python/handoffs/>
- OpenAI Agents SDK HITL：<https://openai.github.io/openai-agents-python/human_in_the_loop/>
- LangGraph/LangChain subagents：<https://docs.langchain.com/oss/python/langchain/multi-agent/subagents>
- LangGraph supervisor migration：<https://docs.langchain.com/oss/python/migrate/langgraph-supervisor>
- MAST（multi-agent failure taxonomy）：<https://arxiv.org/abs/2503.13657>
- Agentless（简单三阶段 SWE-bench 对照）：<https://arxiv.org/abs/2407.01489>
- A2A protocol specification：<https://a2a-protocol.org/latest/specification/>
- MCP introduction：<https://modelcontextprotocol.io/>

**本仓库**

- [Roadmap](../roadmap.md)
- [执行隔离评估](execution-isolation.md)
- [Agent loop 拆分](agent-loop-refactor.md)
- [TUI 协议](../../tui/src/protocol.ts)
- [工具注册表](../../app/tools.py)
- [审批闸](../../app/policy.py)
- [MCP 客户端](../../app/mcp.py)

---

## 12. 建议的下一步

2026-10-26 收窄后，先做 **G0 + Browser Use B1**，桌面 C1 延后：

1. G0：`McpServer.allowed_tools` + `tool_risks`，并补测试；
2. 固定 Playwright MCP 版本，配置 B1 只读工具集；
3. 本地 fixture HTTP server + headless B1 验收；
4. B1 通过后再评估 B2 和 M1 只读子 Agent；桌面 C1/C2 保持延后。

详细规格见 [browser-use.md](browser-use.md)。这个顺序风险最低，也能把 `Multi-Agent + Computer Use` 的共同基础（权限、预算、trace、产物）先打牢。
