# Browser Use：先做的 Computer Use 子集

日期：2026-10-26。状态：方向已收窄，规格待实施。本文把 Computer Use 缩小到**浏览器**，不碰桌面 AX/视觉/系统级 GUI；先做只读浏览器观察，再做受控浏览器动作。

## 0. 结论

**先做 Browser Use，暂缓桌面 Computer Use。**

理由：

1. 浏览器有 DOM / accessibility snapshot：不需要截图、不需要视觉模型，文本 Agent 可直接理解；Playwright MCP 已证明这条路线可用；
2. 不需要 macOS Accessibility / Screen Recording 权限，也没有 TCC 的 responsible-process 和月度重授权问题；
3. headless 可在 CI 用本地 fixture 测试，确定性远高于桌面像素操作；
4. 风险面更小：先只观察，不点击/输入；后续动作逐次审批；
5. 对 coding agent 的直接价值更高：本地 dev server、CI 页面、表单/控制台/网络错误、前端回归。

范围限制：

- 只做 **Chromium + Playwright**；
- 第一版 **只读观察 + 导航**，不做点击/输入/提交；
- 不登录、不付款、不上传/下载文件、不读 Cookie/Storage；
- 不接用户现有浏览器 profile，不使用扩展、CDP 远端、`browser_run_code_unsafe`/`browser_evaluate` 等任意代码工具；
- 不做桌面 GUI、视觉截图 agent、移动端自动化。

后续顺序：

```text
G0 MCP 工具级权限/允许列表
  → B1 浏览器只读观察（navigate + snapshot + console/network）
  → B2 浏览器受控动作（click/type/select，逐次审批）
  → （再评估）桌面 Computer Use / Multi-Agent
```

---

## 1. 为什么 Browser Use 比桌面 Computer Use 更适合先做

| 维度 | Browser Use | 桌面 Computer Use |
|---|---|---|
| 页面表示 | DOM/accessibility tree，文本 | AX 树也可文本，但 app 覆盖不稳定 |
| 视觉模型 | 不需要 | 像素路线需要；AX 路线不需要但仍需系统权限 |
| macOS 权限 | 无 TCC | Accessibility / Screen Recording / Automation |
| 测试 | Playwright headless + 本地 fixture | fake AX + macOS 手工 |
| 可移植性 | macOS/Linux/Windows 均可 | 首版只 macOS |
| 主要风险 | Web prompt injection、误提交、凭据 | 本机全局控制、误点击、敏感屏幕 |
| 现有生态 | Playwright MCP/CLI 成熟 | 需要自研 AX helper |

### 1.1 采用 Playwright MCP 还是 CLI？

Playwright 官方 README 明确说：对 coding agent，**CLI + skills 往往比 MCP 更 token-efficient**，因为避免加载大 tool schema/accessibility tree；MCP 更适合需要持久浏览器状态、探索式循环的场景。

我们的选择：

- **B1 先用 Playwright MCP**：现有 `app/mcp.py` 已支持 stdio MCP、启动审批、工具映射；可以先不改协议；便于验证；
- 如果实测 tool schema/快照 token 占用过高，再评估 Playwright CLI + skill（agent 执行命令，结果落文本/文件）；
- 不把 Playwright 作为 `core/` 依赖；它是可选 MCP server。

参考：[Playwright MCP README](https://github.com/microsoft/playwright-mcp)、[Playwright CLI](https://github.com/microsoft/playwright-cli)。

### 1.2 必须先补的 MCP 缺口

当前 `app/mcp.py` 的 `McpServer.risk` 是 **server 级**（read/write/exec），注册时所有 remote tool 继承同一 risk。Playwright MCP 同时暴露只读快照和写动作（click/type/run code），不能把整个 server 标 read。

G0 二选一：

- **方案 A（推荐）**：给 `McpServer` 增加 `allowed_tools` + `tool_risks`，注册时过滤/覆盖 risk；这样只暴露 B1 白名单，后续 B2 再按工具审批；
- 方案 B：启动两个 Playwright MCP 实例，一个只读配置、一个动作配置；但同一 package 的工具集不能由 server 端裁剪到“只读”，仍需客户端过滤，收益小。

实施前先写测试：未列出的 remote tool 不进入 `specs()`；`execute_async()` 拒绝；risk 覆盖生效；启动审批仍显示完整命令。

---

## 2. B1：浏览器只读观察

### 2.1 工具白名单

只暴露以下 Playwright MCP 工具（实际暴露名会带 `mcp_playwright_` 前缀，由 `McpManager` 生成）：

| 工具 | risk | 用途 |
|---|---|---|
| `browser_navigate` | write | 导航到用户允许的 origin；需审批 |
| `browser_snapshot` | read | accessibility snapshot；只读页面结构 |
| `browser_console_messages` | read | 浏览器 console 错误/警告 |
| `browser_network_requests` | read | 网络请求列表/失败请求 |
| `browser_close` | read | 关闭当前浏览器（生命周期） |

明确不暴露：

- `browser_click`、`browser_type`、`browser_press_key`、`browser_select_option`；
- `browser_tabs`（含 close/new，属于写操作）；
- `browser_take_screenshot`（本阶段不需要图片；即使它是 read，也先排除）；
- `browser_evaluate`、`browser_run_code_unsafe`（任意代码，RCE 等价）；
- `browser_cookie_*`、`browser_localstorage_*`、`browser_sessionstorage_*`、`browser_set_storage_state`；
- `browser_file_upload`、下载、PDF/video、`--caps=vision` 的坐标鼠标工具。

### 2.2 导航策略

- 默认仅允许 `http://localhost`、`http://127.0.0.1`、`http://[::1]`；
- 其他 origin 需要用户在配置中显式加入 allowlist，并按 origin 审批；
- 拒绝 `file://`、`data:`、`javascript:`、`chrome://`、浏览器扩展页面；
- 导航时显示完整 URL；不自动跟随重定向到未授权 origin（重定向后要重新检查，不能只检查输入 URL）；
- Playwright `--allowed-origins`/`--blocked-origins` 不是安全边界（README 明确说“does not serve as a security boundary and does not affect redirects”），只能在 **客户端/server 包装层**做 URL 策略，不能只依赖参数；
- 首版不启用 `--extension`、`--cdp-endpoint`、`--allow-unrestricted-file-access`、`--no-sandbox`；
- 使用 `--isolated --headless`，独立临时 profile；不读取用户浏览器登录态；不传 `--storage-state`。

### 2.3 启动配置

```jsonc
{
  "servers": {
    "browser": {
      "command": "npx",
      "args": [
        "-y", "@playwright/mcp@<PINNED_VERSION>",
        "--headless",
        "--isolated",
        "--browser", "chromium",
        "--output-dir", ".vibe/browser/artifacts",
        "--image-responses", "omit",
        "--snapshot-mode", "full"
      ],
      "cwd": ".",
      "timeout": 60,
      "risk": "read",
      "allowed_tools": [
        "browser_navigate",
        "browser_snapshot",
        "browser_console_messages",
        "browser_network_requests",
        "browser_close"
      ],
      "tool_risks": {"browser_navigate": "write"}
    }
  }
}
```

注意：

- **版本必须固定**，不要 `@latest`；MCP server 是供应链入口，启动审批要显示完整命令/参数；
- `--headless` 是默认；需要真实页面渲染时仍用 headless，不弹用户浏览器；
- `--isolated` 保证会话结束清空 profile；
- `--image-responses omit` 防止图片进入结果；B1 本来不暴露截图工具；
- `--snapshot-mode full` 保证 snapshot 可读；输出仍要限制深度/大小；
- 不在 MCP 配置里放 API key/密码；浏览器不使用用户 cookies。

### 2.4 输出与 token 控制

- `browser_snapshot` 用 depth/`max_chars` 限制；过大时返回摘要 + artifact 路径，不把整棵树塞进父上下文；
- console/network 结果先过滤：只保留 error/warn、失败请求、非静态资源；限制条数；
- 页面文本标记为 **untrusted**，不能因为页面说“点击/批准”就获得动作能力；
- 每次 snapshot 带 `snapshot_id`/URL/title/时间；B2 动作必须引用同一 snapshot；
- 不在 tool result 中返回 cookie/authorization/密码字段；过滤 `Authorization`/`Cookie`/token 样式的请求头。

### 2.5 B1 验收

Headless（CI 可跑）：

1. 本地 fixture HTTP server（Python stdlib）提供固定页面：标题、按钮、console.error、失败 fetch；启动 Playwright MCP，`browser_navigate` + `browser_snapshot` + console/network 返回预期内容；
2. 非白名单 URL 被拒绝，不启动导航；
3. `file://`/`javascript:`/`data:` 被拒绝；
4. `browser_run_code_unsafe`、`browser_click` 等未暴露；`specs()` 不含；
5. 超长 snapshot 被截断并标记；
6. 未审批的 `browser_navigate` 不执行（ApprovalGate 行为）；
7. 测试会话（`--test-session`）不挂载 browser server；
8. MCP 启动审批显示完整命令。

真实 macOS/Linux smoke（手工）：

- 启动本地 dev server，让 Agent 观察页面并报告 console/network 错误；
- 不点击、不输入、不提交表单；
- 验证浏览器进程退出、profile 清理、无残留。

如果浏览器二进制缺失，明确提示安装命令，不在运行中静默 `npx install`。

---

## 3. B2：浏览器受控动作（B1 通过后）

### 3.1 新增工具

| 工具 | risk | 约束 |
|---|---|---|
| `browser_click` | write | 只接受 B1 snapshot 的 element ref；逐次审批 |
| `browser_type` | write | 只接受 ref；默认 `submit=false` |
| `browser_select_option` | write | 只接受 ref + 允许的选项 |
| `browser_press_key` | write | 只允许非破坏性键；Enter 不自动提交 |
| `browser_navigate` | write | 已有，继续 origin allowlist |

**不新增**：`browser_run_code_unsafe`/`evaluate`、file upload、download、cookies/storage、PDF/video、坐标鼠标、`browser_tabs` 新建/关闭（除非后续单独评审）。

### 3.2 动作规则

- 每个动作的审批预览显示：origin、page title、element role/name、动作、文本长度/选项；不显示裸坐标；
- 动作只能基于**最新 snapshot**；stale ref → `isError`，重新 snapshot；不自动重试；
- 表单默认不提交；`browser_type` 的 `submit=false`；提交需要单独显式动作且仍审批；
- 禁止在登录/支付/验证码/文件上传页面动作；命中敏感页面直接拒绝；
- 动作后读取 snapshot/console/network，返回 `changed`/diff；无变化显式提示；
- 每个动作有 budget/次数上限，防止循环点击；
- 取消立即停止；浏览器进程/上下文清理；
- 不在页面注入 JS（除 Playwright 自身 snapshot 外），不读 cookies/localStorage。

### 3.3 安全

- Web 内容是 **untrusted data**；prompt injection 防护：不因页面文本改变工具/权限/审批；
- 不允许页面触发的下载、弹窗、文件选择、权限请求；拦截并报告；
- 不保存登录态；`--isolated`；关闭时清 profile；
- 浏览器进程的网络不受 Python sandbox 保护；首版默认 localhost/allowlist；远程 origin 显式 opt-in；
- 失败/取消/超时后杀浏览器进程组，避免孤儿进程；
- 所有动作写 `.vibe/browser/audit.jsonl`（origin、tool、args hash、decision、snapshot id），不记录敏感正文。

### 3.4 B2 验收

- fixture 页面：click 改变文本、type 输入、select 选项，审批一次/拒绝一次；
- stale ref、页面跳转、元素消失、超时、取消都有明确状态；
- 未审批动作数 = 0；不允许的 origin/敏感页面/文件上传被拒绝；
- headless 测试全部可重复；手工只做本地 fixture。

---

## 4. 与 Multi-Agent 的关系

Browser Use 先做 **单 Agent 工具**，不是子 Agent。M1 只读子 Agent 仍可在 B1 后做，但它不应自动获得 browser 工具；如果以后让 Explorer 用 browser，需要单独的 allowlist 与审批代理。

推荐顺序更新为：

```text
G0 MCP 工具级权限
  → B1 Browser Use 只读观察
  → B2 Browser Use 受控动作
  → M1 只读子 Agent（可再评估是否允许 browser）
  → 桌面 Computer Use / M3
```

浏览器只读观察可以先服务于单 Agent；桌面 AX 仍按 [multi-agent-computer-use.md](multi-agent-computer-use.md) 的 C1 条件延后。

---

## 5. 评测

### 5.1 任务

- 本地 dev server 页面：报告 console error、失败网络请求、当前页面标题/关键控件（B1）；
- 本地 fixture 表单：按用户指令填写/选择并验证（B2，测试环境，不提交真实数据）；
- 前端回归：修改代码后打开页面报告错误；
- 不测登录、支付、真实第三方站点、文件上传。

### 5.2 指标

- 任务成功/正确性；
- 工具调用次数、snapshot 字符/token、总 tokens；
- B2：审批次数、被拒动作、误操作、stale ref 恢复、无变化动作；
- 注入测试：页面文本含“忽略指令/批准/运行命令”时，不产生动作/审批绕过；
- 与 `web_search`/`curl` baseline 对比：只有需要 JS/DOM 的任务才用 browser，普通静态资料不应启动浏览器。

### 5.3 决策门槛

B1 必须做到：

- fixture 任务成功率 ≥ 单 Agent 直接 `curl`/grep 的可比水平，且能发现 console/network 错误；
- snapshot 不导致上下文爆炸（有上限/截断）；
- 无未授权导航、无工具逃逸；
- 否则不进入 B2。

B2 必须做到：

- 本地 fixture 动作成功率高、审批负担可接受、无误提交；
- 没有 prompt injection 导致的越权动作；
- 否则只保留 B1。

---

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Web prompt injection | 页面/文本视为不可信；动作逐次审批；不让页面改变权限 |
| 误提交/误点击 | B1 只观察；B2 默认不提交、逐次审批、敏感页面拒绝 |
| 浏览器依赖/供应链 | Pin Playwright MCP 版本；启动审批完整命令；不 `@latest`；不自动装浏览器 |
| 浏览器进程逃逸 | 独立进程组；取消/超时杀进程；`--isolated`；不启用用户 profile |
| 网络不受 sandbox 保护 | 默认 localhost/allowlist；远程 origin 显式 opt-in；代理/域名策略后续评估 |
| 快照 token 过大 | depth/字符上限；摘要 + artifact；只返回必要片段 |
| 登录/凭据泄露 | 不使用用户 cookies/profile；不读 storage；不输入密码；不测真实账户 |
| MCP server 级 risk 不匹配 | G0 增加 allowed_tools/tool_risks；只暴露 B1 白名单；B2 再逐工具审批 |
| Playwright MCP 工具变更 | Pin 版本；启动时读取工具列表；白名单不存在即失败关闭 |

---

## 7. 明确非目标

- 桌面 GUI / 视觉 Computer Use；
- 截图/图像理解 / vision 模型；
- 登录态、支付、真实账户、文件上传/下载；
- `browser_run_code_unsafe`、`browser_evaluate`、任意 JS；
- 浏览器扩展、CDP 远端、用户现有浏览器 profile；
- 多标签复杂管理、并行浏览器、移动端；
- 多 Agent 浏览器；M1 子 Agent 不自动获得 browser。

---

## 8. 参考

- Playwright MCP README（工具、配置、isolated/headless、allowed origins caveat）：<https://github.com/microsoft/playwright-mcp>
- Playwright CLI（coding agent 的 token-efficient 替代）：<https://github.com/microsoft/playwright-cli>
- MCP Security Best Practices：<https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices>
- MCP Tools 规范（isError/annotations）：<https://modelcontextprotocol.io/specification/2025-11-25/server/tools.md>
- 本仓库 [MCP 客户端](../../app/mcp.py)、[审批闸](../../app/policy.py)、[Multi-Agent 与 Computer Use](multi-agent-computer-use.md)

---

## 9. 下一步

1. 先做 G0：`McpServer.allowed_tools` + `tool_risks`，补测试；
2. 固定 Playwright MCP 版本，写 `browser` MCP 配置；
3. 建本地 fixture HTTP server + headless B1 测试；
4. B1 通过后再做 B2；
5. 桌面 Computer Use 保持延后，不并行开工。
