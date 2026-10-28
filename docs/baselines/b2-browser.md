# B2 浏览器基线（可恢复）

登记日期：2026-10-28。基线代码：`ac438c2`（本文档随后的提交只加文档/自检脚本）。适用范围：**本地 Chromium + Playwright MCP 的 B1 只读观察与 B2 受控动作**；不含远程站点、登录、提交、下载/上传、Cookie/Storage 读取与桌面 Computer Use。

## 固定组件

| 组件 | 固定值 | 位置 / 校验 |
|---|---|---|
| Playwright MCP | `@playwright/mcp@0.0.83` | `examples/browser-mcp.json`、`examples/browser-actions-mcp.json`；不得用 `@latest` |
| Chromium | `chromium-1247`（MCP 0.0.83 匹配版本） | 仓库内 `.vibe/ms-playwright/chromium-1247` |
| Node.js | ≥ 22（登记环境 v24.16.0） | 自检脚本读取 `node --version` |
| fastmcp | `fastmcp==4.1.1` | `pyproject.toml`（`app/browser_server.py` 适配器依赖） |
| Python | 3.12（`.venv`） | `uv.lock` |

自检（离线、不联网、不调用模型）：

```bash
.venv/bin/python3 scripts/browser_baseline_check.py          # PASS/FAIL + 细节
.venv/bin/python3 scripts/browser_baseline_check.py --json   # 机器可读
.venv/bin/python3 scripts/browser_baseline_check.py --allow-missing-chromium  # 仅查版本固定与入口
```

检查项：两个配置固定版本且无 `@latest`；node 版本；`.vibe/ms-playwright/chromium-1247` 存在；`fastmcp==4.1.1` 固定；B1/B2 验收与 fixture 入口文件齐全。

## 复现验收

1. **B1 只读 smoke**（可选、真实浏览器）：`RUN_BROWSER_SMOKE=1 .venv/bin/python3 -m pytest tests/test_browser_smoke.py -q`
   直跑等价命令：`.venv/bin/python3 scripts/browser_smoke.py`。期望：navigate + snapshot + console/network 正常，无 click/type。
2. **B2 脚本化 smoke**：`.venv/bin/python3 scripts/browser_actions_smoke.py`。期望：真实 Chromium 下 click/type/select 经审批链路通过、提交数为 0。
3. **B2 真实模型 + Ink 验收**：在已配置 `VIBE_API_KEY` 的终端执行 `.venv/bin/python3 -m scripts.browser_acceptance`；或 `--serve-only` 只起本地 fixture，再在另一个终端用 `VIBE_MCP_CONFIG=examples/browser-actions-mcp.json ./run_ink.sh` 按提示词分两段操作。
   期望证据：`run_dir/report.json` 的 17 项自动检查全部通过、提交数 0、逐次 once 审批；第二段有一次拒绝且无重试、新快照确认状态未变。`report.json` 与 `review.json` 保存在 `.vibe/browser/acceptance/<stamp>/`。

**注意**：真实模型验收需要模型凭据，只能由用户在本机终端执行；自检脚本与自动化回归不需要凭据与网络。

## 当前基线状态（登记时）

- Python 全量：`./run_tests.sh -q` 退出 0（收集 **1072** 项，含浏览器 opt-in 跳过项）。
- Ink 全量：**337 项、336 通过**；唯一失败为既有“沙箱无 PTY 的交互 CLI”环境项，不是浏览器功能回归。
- 历史验收：B2 真实模型 + Ink 两段验收（2026-10-27，会话 `20261027-142127-403528`）17/17 检查通过、0 提交；证据见 [Browser Use 规格](../specs/browser-use.md#37-b2-真实模型与-ink-验收) 与 `.vibe/browser/acceptance/`。

## 已知边界（基线语义）

- 仅本地 loopback origin；远程站点、重定向、非 GET/HEAD 请求、下载与弹窗全部拒绝。
- 动作只有 click/type/select，**没有** press_key、显式提交、上传/下载、登录/支付。
- 每次动作单独 once 审批，不继承 session/workspace 授权；动作绑定最新快照，页面变化需重新观察；单会话最多 20 次动作。
- 临时无持久化 context；不读 Cookie/Storage；敏感页面（登录/支付/验证码/文件控件）拒绝动作。
- 导航之外的网络请求被阻断（含 GET fetch），会影响后台轮询与懒加载。
- 浏览器 DOM 观察不构成对恶意站点的隔离保证；仅用于本地开发页面。

## 环境漂移/升级时如何重建

1. 改 `examples/*.json` 与 `scripts/browser_baseline_check.py` 的固定版本（同时更新本文档），禁止 `@latest`；
2. 用 Playwright MCP 自带安装器装匹配 Chromium 到 `.vibe/ms-playwright`（当前 0.0.83 → 1247）；
3. 跑自检 → B1 smoke → B2 脚本化 smoke → 真实模型 + Ink 验收；
4. 记录新证据路径与测试结果，更新本文件日期与基线 commit。

升级或扩展范围（如 press_key/显式提交/远程站点）必须同时更新 [Browser Use 规格](../specs/browser-use.md) 与本文档，并重新走真实验收；未通过前不把新范围写进基线。
