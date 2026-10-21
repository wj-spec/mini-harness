# mini-harness

一个以终端为入口的本地 Coding Agent。当前采用单进程、单 Agent loop：模型提出下一步，Python harness 负责组装上下文、审批动作、执行工具并记录结果。

随着 Agent 能执行的步骤增多，最终回答不足以说明它依据了什么、实际做了什么，以及验证是否真的通过。这个项目尝试让这些过程有据可查，并以此为基础，逐步发展为更了解用户的个人助手工作台。

## 当前能力

支持读取、搜索和修改工作区文件，运行命令、联网检索，并通过 stdio MCP 接入外部工具。模型侧支持 OpenAI-compatible 流式接口；本地 fake 模式可用于不联网地检查交互与协议。默认入口是 Textual，可选的 React/Ink 工作台通过本地 JSON Lines 连接同一个 Python Agent。

## 架构

```mermaid
flowchart LR
    user["用户"] --> textual["Textual TUI（默认）"]
    user --> ink["React / Ink TUI（可选）"]
    ink -- "JSON Lines" --> stdio["Python stdio 适配"]
    textual --> agent["Agent.run() · 单 Agent loop"]
    stdio --> agent
    agent <--> provider["Provider · OpenAI-compatible / fake"]
    agent --> core["core/ · 消息、上下文、预算、任务状态"]
    agent -- "只读工具" --> tools["ToolRegistry"]
    agent -- "需授权工具" --> gate["ApprovalGate · 预览、授权、审计"]
    gate --> tools
    tools --> world["工作区 / 命令 / Web / stdio MCP"]
    agent -- "消息、状态、Trace" --> session["SessionStore"]
    session --> local[".vibe/ 会话与运行记录"]
```

[`core/`](core/) 只依赖 Python 标准库，负责可复用的消息、预算、上下文与任务状态；模型接入、工具和执行流程在 [`app/`](app/)。Textual 与 Ink 共用 [`Agent.run()`](app/agent.py)，界面更换不会产生第二套执行逻辑。

## 核心设计

1. **模型提议动作，harness 决定执行。** Agent 对非只读工具先生成预览，再经过 [`ApprovalGate`](app/policy.py)；没有审批处理器时默认拒绝。授权可限定为单次或精确规则，内置写入与命令还可按工作区授权，支持撤销和审计；MCP 服务启动另行审批。命令仍以当前用户权限运行，审批和超时不构成操作系统级沙箱。
2. **从实际请求观察上下文。** [`ContextBuilder`](core/context.py) 控制预算、压缩旧历史，并用引用保留可回取的原文。模型请求前的[上下文快照](core/context_snapshot.py)按最终组装的消息和工具定义统计来源、分区及估算用量；`/context` 可以查看变化。这里展示的是可核对的输入构成，token 分区数字是估算值，不是模型生成的行为解释。
3. **把执行结果与恢复状态当作证据。** 命令结果保留退出码，以及成功、失败、超时、取消等状态；工具结果回灌模型并写入任务记录。[`SessionStore`](app/session.py)保存原始消息、状态和运行轨迹；打开旧会话不会自动重放可能已生效的动作，显式输入 `/resume` 后才继续。结果未知时，先核对实际文件和命令结果。

## 快速体验

需要 Python 3.11+ 和 `uv`。以下命令启动无需模型 API 的界面演示：

```bash
uv sync --extra test
./run_vibe.sh --fake
```

配置 `VIBE_API_KEY`、`VIBE_BASE_URL`、`VIBE_MODEL` 后，可以用 `./scripts/demo.sh` 在临时示例仓库里体验真实编码任务。Ink 入口需要 Node.js 22+：

```bash
npm --prefix tui ci
./run_ink.sh --fake
```

安装、模型配置与操作方式见[使用指南](docs/USAGE.md)；演示步骤见[三分钟 Demo](docs/DEMO.md)。fake 模式用于确定性接线检查，不代表真实模型的任务完成效果。

## 验证

Python 回归使用 `./run_tests.sh -q`，Ink 构建与测试使用 `npm --prefix tui test`。内部 eval 与具体里程碑的验收依据见[工作台里程碑](docs/MILESTONES.md)。

## 后续方向

当前先把单 Agent 编码任务做得可靠、可理解。后续探索由主 Agent 编排、子 Agent 执行的协作方式；“关于你”则计划展示 Agent 从日常协作中保留的用户偏好及其来源，让用户能够查看和修正。这些属于[开发规划](docs/roadmap.md)，尚未作为现有能力交付。

采用 [MIT](LICENSE) 许可证。
