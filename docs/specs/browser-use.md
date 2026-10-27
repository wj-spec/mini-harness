# Browser Use：先做的 Computer Use 子集

日期：2026-10-27。状态：G0 MCP 工具级权限已实现；B1 只读观察已完成真实 Playwright MCP smoke；B2 本地受控动作已通过用户 CLI 真实模型与 Ink 两段功能验收，证据见 3.7；主观可读性与审批负担继续收集反馈。本文把 Computer Use 缩小到**浏览器**，不碰桌面 AX/视觉/系统级 GUI。

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
- B1 为 **只读观察 + 导航**；B2 独立配置开放本地 click/type/select，不开放提交；
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

- **方案 A（已实现）**：`McpServer` 支持 `allowed_tools`、`tool_risks`、`allowed_origins`，注册时过滤/覆盖 risk；这样只暴露 B1 白名单，后续 B2 再按工具审批；
- 方案 B：启动两个 Playwright MCP 实例，一个只读配置、一个动作配置；但同一 package 的工具集不能由 server 端裁剪到“只读”，仍需客户端过滤，收益小。

已实现的 G0（`app/mcp.py`）：

- `allowed_tools`：只注册白名单工具；缺失配置的白名单工具启动失败（fail closed）；
- `tool_risks`：工具级 risk 覆盖，`browser_navigate` 可标 `write`，其余只读；
- `allowed_origins`：对工具参数中的 `url` 做 scheme/origin 检查；非 http(s) 或不在 allowlist 的 URL 在调用 MCP server 前拒绝；
- 启动预览显示 allowed tools，审批仍显示完整命令。

**限制**：`allowed_origins` 只检查工具入参，不检查重定向后的最终 URL；B1 首版只用于本地 fixture/dev server，不用于不可信外部站点。重定向后检查留给 B1.1/B2。

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
- 导航时显示完整 URL；`allowed_origins` 先在客户端检查输入 URL；重定向后的最终 URL 还没有机器可验证的检查，因此 B1 只用于本地 dev server/fixture，不用于不可信外部站点；
- Playwright `--allowed-origins`/`--blocked-origins` 不是安全边界（README 明确说“does not serve as a security boundary and does not affect redirects”），只能在 **客户端/server 包装层**做 URL 策略；当前 G0 已做输入 URL 的 origin 检查，重定向限制仍存在；
- 首版不启用 `--extension`、`--cdp-endpoint`、`--allow-unrestricted-file-access`；
- 默认不启用 `--no-sandbox`。只有当前环境（例如本仓库的自动化沙箱）无法创建 Chrome sandbox 时，才在**本地 smoke**中显式开启并记录风险；日常运行保持 Chrome sandbox；
- 使用 `--headless` + 专用 `.vibe/browser/profile`（用完可删），不读取用户浏览器登录态；不传 `--storage-state`。Playwright MCP 的 `--isolated` 与显式 `--user-data-dir` 冲突，这里用专用 profile 代替；

### 2.3 启动配置

实际验证用 `examples/browser-mcp.json`（Playwright MCP **0.0.83**，Chromium 1247 安装在 `.vibe/ms-playwright`）：

```jsonc
{
  "mcpServers": {
    "browser": {
      "command": "npx",
      "args": [
        "--yes", "@playwright/mcp@0.0.83",
        "--headless", "--no-sandbox", "--browser", "chromium",
        "--user-data-dir", ".vibe/browser/profile",
        "--image-responses", "omit", "--snapshot-mode", "full",
        "--output-dir", ".vibe/browser/artifacts"
      ],
      "env": {
        "NPM_CONFIG_CACHE": ".vibe/npm-cache",
        "PLAYWRIGHT_BROWSERS_PATH": ".vibe/ms-playwright",
        "HOME": ".vibe/browser/home",
        "XDG_CACHE_HOME": ".vibe/browser/cache",
        "TMPDIR": ".vibe/browser/tmp",
        "PWTEST_SOCKETS_DIR": "/tmp/vibe-browser-sockets"
      },
      "cwd": ".",
      "timeout": 60,
      "risk": "read",
      "allowed_tools": [
        "browser_navigate", "browser_snapshot",
        "browser_console_messages", "browser_network_requests",
        "browser_close"
      ],
      "tool_risks": {"browser_navigate": "write"},
      "allowed_origins": ["http://localhost:3000", "http://127.0.0.1:3000"]
    }
  }
}
```

`env` 是为了把 npm cache、Playwright 浏览器、Chrome profile 都收在 workspace 内，避免写入默认 `~/Library/Caches`；相对路径按 MCP server 的 `cwd` 解析。`--no-sandbox` 仅为本机自动化环境的实测需要，正常桌面环境应去掉并让 Chrome sandbox 生效。

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
8. `--test-session` 隔离会话范围与个人偏好；只有显式配置 browser MCP 才挂载，启动和动作仍分别审批；
9. MCP 启动审批显示完整命令和 allowed tools。

真实 macOS/Linux smoke（手工）：

- 启动本地 dev server，让 Agent 观察页面并报告 console/network 错误；
- 不点击、不输入、不提交表单；
- 验证浏览器进程退出、profile 清理、无残留。

如果浏览器二进制缺失，明确提示安装命令，不在运行中静默 `npx install`。

---

### 2.5.1 已完成的真实 smoke（2026-10-26）

`scripts/browser_smoke.py` 用本地 fixture HTTP server + 真实 Playwright MCP 0.0.83/Chromium 1247 验证：

- navigate 到 `http://127.0.0.1:<port>/` 成功；
- `browser_snapshot` 返回 title、heading、button ref；
- `browser_console_messages` 捕获页面 `console.error`；
- `browser_network_requests` 返回请求列表；
- 全程无 click/type/表单提交；server 通过 `allowed_tools` 只暴露 B1 工具；
- 结果：**B1 smoke PASS**。

测试入口：`RUN_BROWSER_SMOKE=1 PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_browser_smoke.py -q`（默认跳过，避免每次下载/启动浏览器）。

## 2.6 B1 实施前置

- 固定 Playwright MCP 版本（本机实测 `@playwright/mcp@0.0.83`）；不要使用 `@latest`；
- 用 **MCP 自带安装器**安装匹配的 Chromium（0.0.83 期望 Chromium 1247），不要只装通用 `playwright` 的最新浏览器：

```bash
mkdir -p .vibe/ms-playwright
PLAYWRIGHT_BROWSERS_PATH="$PWD/.vibe/ms-playwright" \
  NPM_CONFIG_CACHE="$PWD/.vibe/npm-cache" \
  npx --yes @playwright/mcp@0.0.83 install-browser chrome-for-testing
```

这是可选运行时依赖，不进入 `core/`；
- 用 `VIBE_MCP_CONFIG`/`VIBE_MCP_SERVERS` 加载 browser 配置；
- 启动时审批完整 `npx` 命令和 allowed tools；
- 如果浏览器二进制缺失，明确提示安装，不在后台静默安装。

## 3. B2：浏览器受控动作（B1 通过后）

### 3.0 本轮实施切片（2026-10-27）

- B1 配置保持只读；新增独立受控 MCP 适配器与 `examples/browser-actions-mcp.json`，只用于显式允许的本地 loopback origin。复用固定版本 Playwright 运行时，不开放上游任意代码工具。
- 本轮开放 navigate/snapshot/console/network/close 与 click/type/select；暂不开放 press_key 和提交工具。动作要求最新 `snapshot_id` + `ref`；输入仅 fill，`submit` 只能为 false，select 只能选择快照中存在且可用的 value。
- 通过现有 ToolRegistry / Agent / ApprovalGate 验证：导航和动作固定 write、只允许 once/deny，不复用 session/workspace 授权；预览显示缓存的 URL/title/role/name、输入长度或选项，不输出输入正文。
- worker 在执行前再次核对当前 URL、DOM 快照和目标；审批期间变化、失败、超时、取消均使旧快照失效，无自动重试。动作后返回新快照、changed、console/network；每个浏览器会话最多 20 次动作。
- 独立无持久化 browser context；固定的可信 DOM 检查用于读取控件类型/选项和敏感页面判断，不向模型开放 JS。拒绝登录/支付/验证码/文件控件；拦截提交事件、动作触发的文档导航、非 GET/HEAD 请求、所有重定向、越域请求、下载、弹窗和权限请求。导航窗口之外的新网络请求也拒绝，覆盖 GET fetch 等发送路径；这是离线 UI 草稿模式，会影响后台轮询、懒加载和联网交互。重定向保守拒绝，不能仅依赖最终 URL 的事后检查。
- 页面文本仍是不可信数据；上述边界服务于本地开发页面，不能把 DOM 启发式识别当成对恶意网站的隔离保证。真实账户、外部站点和显式提交留待后续单独切片。
- 所有动作审批另外写 `.vibe/browser/audit.jsonl`：origin、tool、args hash、decision、snapshot_id，不保存输入正文；运行时及验证日志统一留在 `.vibe/`。
- 超时/取消后 worker 不重启；POSIX 在关闭前扫描并回收 Node/Chromium 后代，进程扫描不可用时仍终止 Node 并明确报告清理受限。非 POSIX 的强杀后代回收未提供同等保证，当前真实验收范围为 macOS 本地 fixture。

### 3.1 新增工具

| 工具 | risk | 约束 |
|---|---|---|
| `browser_click` | write | 最新 snapshot_id + element ref；逐次审批 |
| `browser_type` | write | snapshot_id + ref；仅 `submit=false` |
| `browser_select_option` | write | snapshot_id + ref + 已观察到的可用选项 |
| `browser_press_key` | write | 后续方向，本轮不暴露 |
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
- 模型不能提供 JS；适配器只执行固定的 DOM 检查/提交拦截与 Playwright snapshot，不读 cookies/localStorage。

### 3.3 安全

- Web 内容是 **untrusted data**；prompt injection 防护：不因页面文本改变工具/权限/审批；
- 不允许页面触发的下载、弹窗、文件选择、权限请求；拦截并报告；
- 不保存登录态；B2 使用临时 browser context，关闭时释放；
- 浏览器进程的网络不受 Python sandbox 保护；首版默认 localhost/allowlist；远程 origin 显式 opt-in；
- 失败/取消/超时后杀浏览器进程组，避免孤儿进程；
- 所有动作写 `.vibe/browser/audit.jsonl`（origin、tool、args hash、decision、snapshot id），不记录敏感正文。

### 3.4 B2 验收

- fixture 页面：click 改变文本、type 输入、select 选项，审批一次/拒绝一次；
- stale ref、页面跳转、元素消失、超时、取消都有明确状态；
- 未审批动作数 = 0；不允许的 origin/敏感页面/文件上传被拒绝；
- headless 测试全部可重复；手工只做本地 fixture。

### 3.5 本轮工程验证（2026-10-27）

- `scripts/browser_actions_smoke.py` 使用脚本模型，经过真实 Agent → ApprovalGate → FastMCP → Playwright/Chromium：click/type/select 成功，17 次审批，fixture 提交数 0；拒绝、旧快照、submit=true、GET/POST 表单、越域、重定向、敏感页面及审批期间 DOM 改变均明确失败。日志 `.vibe/browser/b2-smoke.log`。
- `tests/browser_worker.test.cjs` 的 opt-in 真实浏览器回归补充同 URL reload、同 markup 替换、GET fetch 零发送、敏感值脱敏；单元回归覆盖固定 write、once-only、审批取消、失败失效、stdio 菜单及 worker 中断清理。
- 当前环境的 Chromium smoke 需要显式关闭 Chrome sandbox；日常 B2 配置默认启用。发现相对 HOME/TMPDIR 会导致点击等待稳定超时，worker 启动前会将运行时路径转换为绝对路径。
- 最终 Python 全量 980 项：976 通过、3 跳过、1 项既有 Seatbelt 代理连接失败；干净 HEAD 副本同样失败，证据 `.vibe/browser/b2-baseline-seatbelt.log`。完整报告 `.vibe/browser/b2-python-tests.xml`；Ink 337/337 通过。
- 工程验证不等同于真实模型任务完成或用户 Ink 体验验收；press_key、显式提交、远程站点和真实账户仍不开放。

### 3.6 真实模型与 Ink 验收入口（已交付，本地功能验收通过）

- 新增单一手工验收入口：自动启动本地 fixture、生成仅允许该端口的 MCP 配置，再启动现有 Ink；沿用用户 CLI 环境中的模型/key，不读写或保存凭据。
- 任务一要求点击 Increment、Note 填入固定草稿、Shade 选 Green，观察最终快照；随后在同一 Ink/MCP 生命周期中单独发送任务二，更改 Note 时由用户拒绝审批，再观察原草稿仍在。两段完成后才退出；新 MCP 的临时 context 从空白页开始，不继承旧页面或表单值，也不通过恢复聊天历史恢复页面。所有审批仍由 Ink 用户逐次决定。
- 退出后基于本次新建会话的 events/trace、快照和 fixture 提交计数生成报告；通过必须同时满足动作成功、最终值正确、拒绝后没有对应执行、提交数为 0。模型口头声称完成不计证据。
- 不因多个旧会话或多个新会话猜测当前会话；多候选时报告 ambiguous，缺证据报告 incomplete。报告区分工程检查与真实模型/用户体验；人工可读性和审批负担由用户反馈，不能自动标记通过。
- 无 key 时不启动付费调用，说明需要在用户已配置 key 的终端启动；可选择仅运行 fixture 并输出现有 Ink 命令。运行产物放 `.vibe/browser/acceptance/`，无需新前端入口或协议。
- 入口：在仓库根目录执行 `.venv/bin/python3 -m scripts.browser_acceptance`；`--serve-only` 可供另一个已配置 key 的终端启动 Ink。缺 key 默认退出 2；缺证据或失败退出 1。报告 `real_model_verified=false` 与 `user_experience=pending` 需人工核对，自动检查不代替真实体验。
- 本切片完整 Python 1006 项：1001 通过、4 跳过、1 项既有 Seatbelt 失败（`.vibe/browser/b2-acceptance-tests.xml`）；opt-in Ink PTY 冒烟另行使用真实 Ink/MCP/Chromium 与本地模型 stub 验证，不调用外部模型。
- 两轮 PTY 场景与手工入口对齐，实际会话日志由同一 `collect_report` 核对，17 项自动检查全部通过、提交数 0；`s/p` 不放行、拒绝后草稿保留、终端恢复也通过。证据 `.vibe/browser/ink-smoke/309dc558d44d/report.json`，模型明确标记 `b2-ink-scripted`，`real_model_verified=false`。
- 真实使用修正：原 30k 任务预算导致每次约 10k–14.5k tokens 的模型请求只能执行少量动作就暂停；验收入口提高为有界 100k（12 步、180 秒保持）。用实际请求用量回放先复现 `token_budget`，修正后同一动作链正常完成。第一段的动作/值检查独立于第二段拒绝是否出现；缺第二段仍为 incomplete。历史原报告保留，重算与人工分段核对放各自 `review.json`，不跨场次拼接证据。
- 会话连续性修正：用户任务一在 `055548` 的 63847 端口完成，而任务二分别新开 `055740` / `055904`，端口为 64124 / 64269，均未先导航。新 context 的 `about:blank` 原来被笼统报告为 origin 错误；现专门返回 `browser_not_navigated`，说明旧页面状态不恢复、须经审批导航。真实 Chromium 首次 snapshot/console 回归先红后绿，未扩大 allowlist、未让读取自动导航。手工入口与提示词明确同一窗口分两次发送，任务二完成后才退出。
- 本轮完整回归两次各 1008 项：1002 通过、4 跳过、2 失败（Seatbelt 代理及各一次命令取消超时）；命令结果测试文件单独复跑通过，完整结果保留 `.vibe/browser/b2-session-continuity-{tests,recheck}.xml`，不以单项通过覆盖全量失败记录。

### 3.7 用户真实模型与 Ink 功能验收通过（2026-10-27）

场次 `.vibe/browser/acceptance/20261027-062108-b00a93af/`，会话 `20261027-142127-403528`，本地 origin `http://127.0.0.1:51901`，用户 CLI 配置 `openai` 兼容 provider / `deepseek-chat`。原报告 17 项检查全部通过，原始 events/trace 哈希匹配，独立重新计算结论一致；两轮均 done，分别 80676 / 53034 tokens，未触发预算停止。

- navigate/click/type/select 全部逐次 once 审批；最终 count=1、Note=`B2 验收草稿`、Shade=`green`。
- 第二轮仅一次 `拒绝写入` 输入尝试，审批 deny、工具终态 denied，无重试；后续新快照 `cb282d1d-636c-4bf7-90ee-ec5710dd5bc4` 确认上述值保持。
- fixture 提交计数 0，Ink 正常退出码 0；没有将页面注入文本变成越权操作。
- 原 `report.json` 保留自动占位字段，人工复核写入同目录 `review.json`，认定本地 B2 功能场景通过。主观阅读体验与审批负担仍待明确反馈；现有 Seatbelt/命令取消回归失败另行保留，不据此宣称全项目测试通过。

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

1. G0 已完成：`McpServer.allowed_tools` + `tool_risks` + `allowed_origins`，并有 MCP 回归；
2. 固定 Playwright MCP 版本，写 `browser` MCP 配置；
3. 建本地 fixture HTTP server + headless B1 测试；
4. B2 本地受控动作已实现；下一步在 Ink 中做真实模型与用户体验验收，再决定是否扩范围；
5. 桌面 Computer Use 保持延后，不并行开工。
