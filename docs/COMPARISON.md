# 和 opencode / Codex CLI 的对照

> 只比较**公开行为**和本项目自己的实现；不猜测闭源内部。具体能力以对应版本为准。

| 维度 | vibe（本项目） | opencode | Codex CLI |
|---|---|---|---|
| 定位 | 学习用最小 TUI harness | 开源终端 coding agent | OpenAI 官方终端 agent |
| 入口 | Textual TUI | TUI / CLI | CLI / TUI |
| provider | 任意 OpenAI-compatible | 多 provider | OpenAI / 兼容端点 |
| 上下文 | 1M 窗口，S/M/T/H/R 分区 + recall + LLM 摘要 | 会话与自动压缩（公开能力） | 自动压缩 / 长任务 |
| 工具 | read / grep / list / write / edit / bash / recall | 文件、搜索、shell、LSP、MCP 等 | shell、apply_patch、搜索、MCP 等 |
| 写/执行审批 | `risk` + diff + `y/a/s/n/d` | 权限提示 | approval modes + sandbox |
| 会话恢复 | JSONL + `--continue` + 崩溃对账 | session / resume | resume / 会话 |
| 可观测 | trace + `/cost` + 10 任务 eval | 日志 / 分享 | 日志 / 用量 |
| 扩展 | 代码级 `ToolRegistry` | 插件 / MCP / LSP | MCP / 配置 |
| 隔离 | 审批 + 命令黑名单 + timeout，**不是沙箱** | 权限模型 | OS sandbox（Seatbelt/Landlock） |
| 体量 | 几千行，能读完 | 产品级 | 产品级 |

## 取舍

- 本项目把 **context / memory / state / approval / recovery / eval** 拆开讲清楚，代价是工具生态和隔离能力弱。
- opencode 的优势是开箱即用的产品体验和扩展；Codex CLI 的优势是强 sandbox 和官方模型集成。
- 三者共同的骨架其实一样：**loop + 工具 + 上下文 + 审批 + 会话 + 可观测**。差别在工程厚度。

## 学完应该能回答

1. 为什么 1M 上下文仍要分区，而不是把窗口填满？
2. 为什么 offload 之后必须有 `recall`，摘要不能替代所有历史？
3. 为什么写/执行要审批，且默认 fail closed？
4. 为什么 session 要 append-only，崩溃后为什么不能假装工具成功？
5. 为什么 eval 要记录 trace/cost，而不是只看“感觉能跑”？

## 8 个维度

| 维度 | vibe | opencode | Codex CLI |
|---|---|---|---|
| 控制流 | 显式 `Agent.run` loop + max steps/tool calls/time | 产品级 agent loop | 产品级 agent loop |
| 状态 | `TaskState` 投影，harness 侧 | 会话状态 | 会话状态 |
| 持久化 | `events.jsonl` + `state.json` | session 存储 | 会话/rollout |
| HITL | `ApprovalGate` + diff + `y/a/s/n/d` | 权限提示 | approval + sandbox |
| 多 Agent | **不做** | 公开能力因版本而异 | 公开能力因版本而异 |
| 工具 | `ToolRegistry`，代码级扩展 | 插件/MCP/LSP | MCP/内置工具 |
| 可观测 | trace + `/cost` + eval | 日志/分享 | 日志/用量 |
| 边界 | 无容器沙箱；审批+黑名单+timeout | 权限模型 | OS sandbox |
