# 沙箱内网络白名单：最小设计对比

当前状态：`--sandbox-network` 是**全开/全关**。真实任务里常见需求是"只允许 npm/uv 的仓库域名"，而不是全开。本文对比三种做法并记录选择。

## 方案对比

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A. IP 白名单 | 构建策略前解析域名，允许这些 IP | 无代理，实现小 | CDN/轮换 IP 失效；DNS 重绑定可绕过；域名语义丢失 |
| B. 本地代理 + 域名白名单 | 沙箱内只允许连本地代理端口；代理按域名放行/拒绝；子命令通过 `HTTP_PROXY/HTTPS_PROXY` 走代理 | 按域名判定、与 srt 一致、可审计 | 依赖工具尊重代理环境变量；非 HTTP(S) 协议需 SOCKS5；实现约 200–300 行 |
| C. 维持全开/全关 | 现状 | 无成本 | 无法表达"只允许仓库域名" |

选择 **B（仅 macOS Seatbelt）**：Seatbelt 策略里 `(deny network*)` 后只允许 `(remote ip "localhost:PORT")`，代理进程在 harness 内监听 127.0.0.1，按 hosts 白名单转发 HTTP 与 CONNECT。Linux bwrap 暂不做（`--unshare-net` 下访问宿主代理需要 Unix socket 转发，复杂度高，等 Linux 实机需求再评估）。

## 实现要点

- 配置：`--sandbox-network-allow github.com,registry.npmjs.org`（或 `VIBE_SANDBOX_NETWORK_ALLOW`），逗号分隔；支持 `*.example.com`。
- 子命令环境：注入 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`http_proxy`、`https_proxy`；`NO_PROXY` 不含外网域名。
- 代理行为：HTTP 绝对 URI 与 CONNECT 都按域名白名单判定；不在名单返回 403 并记录；无白名单条目则不启动代理（等价全关）。
- 与 `--sandbox-network` 的关系：全开优先（不启动代理）；两者同时给出时报错或全开优先，文档写明。
- 审计：被拒请求写入 stderr/工具输出前缀，可归因。

## 验收

- 白名单内域名（如 `registry.npmjs.org`、`pypi.org`）在沙箱内可访问；
- 未列出的域名被 403 拒绝，工具输出可见；
- 直连 IP、非代理流量被 Seatbelt 拒绝；
- 不传白名单时行为与现在一致（全关）；
- 单元测试覆盖：代理放行/拒绝、CONNECT 隧道、profile 只放行本地代理端口；集成测试在可应用沙箱的环境执行，嵌套沙箱自动跳过。

## 风险与限制

- 若工具不读代理环境变量（少部分二进制），白名单模式下会直接失败；文档要求这类工具走全开或沙箱外执行。
- HTTPS 的 CONNECT 只校验域名，不检查路径；这是白名单的语义边界。
- DNS 解析在代理侧完成，避免沙箱内 DNS 绕过；代理到上游仍可能受 DNS 污染影响（与普通代理相同）。
