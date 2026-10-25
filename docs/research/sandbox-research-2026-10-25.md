# Coding Agent 执行隔离调研（2026-10-25）

目标：为本项目（Python harness + Ink 前端，macOS 开发机，无容器依赖）选择可落地的 OS 级执行隔离方案。本文调研主流 coding agent 的做法，并给出推荐与验收口径。

结论先行：**不引入容器作为默认方案，先用 macOS Seatbelt（`sandbox-exec`）做可选的原生沙箱开关**；Linux 后续用 bubblewrap 对齐。这与 OpenAI Codex 和 Anthropic Sandbox Runtime 的方向一致（两者都不用容器做默认隔离）。

## 候选项目

### 1. OpenAI Codex CLI（`openai/codex`）

- 默认所有命令在沙箱内执行；三种模式：`read-only`（可读全盘、不可写）、`workspace-write`（默认，可写 cwd 与 `--add-dir`，`.git/` 保持只读）、`danger-full-access`。
- 平台实现：macOS 用 **Seatbelt**（动态生成 `sandbox-exec` 策略）；Linux 用 **Landlock + seccomp**（默认阻断出网）；Windows 用受限令牌 + job object。
- 网络可配置；沙箱内需要更大权限时走**升级审批**，与审批流程集成。
- 仓库对应 `codex-rs/sandboxing`、`codex-rs/bwrap`、`codex-rs/windows-sandbox-rs` 等独立 crate。
- 资料：<https://mintlify.wiki/openai/codex/concepts/sandboxing>、<https://github.com/openai/codex>、[Simon Willison 的实现分析](https://simonwillison.net/2025/Nov/9/codex-sandbox-investigation/)。

### 2. Anthropic Sandbox Runtime（`anthropics/sandbox-runtime`，`srt`）

- 不用容器的轻量 OS 级沙箱：macOS `sandbox-exec`（动态 Seatbelt 策略）、Linux `bubblewrap`、Windows 专用沙箱账户 + 过滤平台。
- 文件系统模型：**读** 默认放开、`denyRead` 可排除敏感路径（deny-then-allow）；**写** 默认全禁，`allowWrite` 白名单（`.`、`/tmp` 等），`denyWrite` 覆盖。
- 网络：**默认全禁**，通过本地 HTTP + SOCKS5 代理做域名白名单；macOS 用 localhost 端口，Linux 用绑定挂载的 Unix socket。
- 违规可观测：macOS 沙箱违规日志、代理拒绝记录，且能把违规归因到具体命令。
- 定位：Claude Code 的沙箱研究预览，也可用于包装 MCP server 和任意进程；API 仍可能变化。
- 资料：<https://github.com/anthropics/sandbox-runtime>、npm `@anthropic-ai/sandbox-runtime`。

### 3. Gemini CLI（`google-gemini/gemini-cli`）

- `--sandbox` / `GEMINI_SANDBOX` 支持 Docker、Podman 与 macOS Seatbelt 三种后端；示例中偏好容器，Seatbelt 作为宿主内选项。
- 资料：<https://github.com/google-gemini/gemini-cli>、[PR #3537](https://github.com/google-gemini/gemini-cli/pull/3537/files)。

### 4. OpenHands

- 运行时即 Docker 沙箱（agent-server docker sandbox），企业版支持沙箱内嵌 Docker。
- 参考资料：<https://docs.openhands.dev/sdk/guides/agent-server/docker-sandbox>、<https://docs.openhands.dev/enterprise/docker-in-sandbox>。

### 5. Python 生态可参考实现

- `sandtrap`、`sandlock`（PyPI）：Python 风格的沙箱封装/SDK，可作为 Python 调用 `sandbox-exec`/bwrap 的写法参考。
- `hazmat` 的 [macOS sandboxing internals](https://github.com/dredozubov/hazmat/blob/master/docs/research/macos-sandboxing-internals.md)：Seatbelt 策略与 `sandbox-exec` 用法细节。
- 注意：`sandbox-exec` 在 macOS 上已标记 deprecated 但仍在系统内可用，Codex、srt、Gemini CLI 均在用；需在验收里标注该风险并保留降级路径。

## 对比

| 方案 | 平台 | 网络控制 | 依赖 | 成本 | 适合本项目 |
|---|---|---|---|---|---|
| 原生 Seatbelt 包装（Codex/srt 思路） | macOS | 默认拒网（可放开） | 系统自带 | 中（一次实现 ~100–200 行 + 策略模板） | **首选** |
| 以 CLI 调用 `srt` | macOS/Linux/Windows | 域名白名单代理（最完整） | Node/npm，beta | 低（外部依赖） | 备选/快速验证 |
| bubblewrap | Linux | namespace 隔离，可断网 | 需 bwrap | 中 | Linux 对齐时再做 |
| Docker/Podman | 全平台 | 容器网络 | 容器运行时，重 | 高 | 评测容器可复用，不作日常默认 |
| 静态黑名单/路径护栏（现状） | 全平台 | 无 | 无 | 低 | 已完成第一小步，继续保留 |

## 推荐方案（分阶段）

1. **P1（现在）**：`app/sandbox.py` 增加可选 Seatbelt 后端：`--sandbox` / `VIBE_SANDBOX=1` 时用 `sandbox-exec -f <profile>` 包裹 `/bin/bash -lc`。
   - 策略：允许读取系统路径与工作区（含 `/usr`、`/bin`、`/opt`、`/Library`、工具链缓存），**写仅工作区 + `/tmp`**，**默认拒绝网络**（`(deny network*)`，可用 `--sandbox-network` 放开）；`.git/` 保持可写（需要提交）但可后续收紧。
   - 沙箱拒绝要作为结构化工具错误返回（`ERROR sandbox_denied: <path/operation>`），模型能据此调整；审计、审批、Trace 不变。
   - 非 macOS 平台保持现状并提示"沙箱不可用"，不静默降级。
2. **P1（Linux 对齐）**：同接口换 bubblewrap 后端（`--unshare-net`、`--ro-bind` 系统目录、`--bind` 工作区）。未使用 Linux 前不实现。
3. **P2（网络白名单）**：若确实需要在沙箱内联网（npm/uv），再评估 srt 式代理白名单，或维持"默认断网、按需 `--sandbox-network` 全开"的简单策略。
4. **不做**：把 Docker 作为日常默认（与个人工作台体验冲突），继续只用于评测容器。

## 验收口径

- 开启沙箱后：`echo x > ~/file`、`rm ~/file` 等写外部失败并返回 `sandbox_denied`；`cat ~/.ssh/id_rsa` 读取被拒（按策略）；`curl https://example.com` 默认被拒。
- 正常工作流：`npm --prefix tui test`、`uv run python -m pytest`、`git status/diff/commit` 在沙箱内可通过；`proxy`/本地端口如需要则写进白名单。
- 关闭沙箱：行为与当前一致，全部既有回归通过。
- 关闭/不可用场景：无可用后端时明确提示；`sandbox-exec` 缺失/嵌套沙箱时给出可操作错误而不是崩溃。
- 已实现补充（2026-10-25）：`.git/hooks` 与 `.git/config*` 写入被拒；Linux bubblewrap 后端已接入（argv 单测通过，实机待验）。
- 证据：新增 `tests/test_sandbox_seatbelt.py`（darwin 专用 skipif），记录被拒命令与放行命令；回填 [执行隔离评估](../specs/execution-isolation.md)。

## 开放问题

- Seatbelt 弃用风险：上游若移除 `sandbox-exec`，是否切换到 srt（外部 CLI）或自建 XPC？短期用能力探测 + 明确错误兜底。
- 是否允许沙箱内网络：默认断网会破坏 `npm install`、`uv sync` 等；建议首次安装类操作走"沙箱外、显式审批"，或提供 `--sandbox-network` 开关。
- `.git/` 写权限：提交需要写 `.git`，但恶意命令也可改 hooks；可考虑提交操作走独立路径（如 harness 代为 commit）。
- 与审批的关系：沙箱是兜底，不替代审批；两者同时开启时的错误文案要区分"审批拒绝"与"沙箱拒绝"。

## 知识依据

本地知识库强调执行隔离只是纵深防御的一层，不能声称不可绕过；沙箱范围与任意桌面权限不同，需与应用校验、审批、审计配合（`agentic-guide-yeasy` ch04 p.167–171、p.196–198，ch11 p.585–588）。
