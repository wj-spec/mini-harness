# Ink 迁移第一切片：接通新入口

状态：工程实现与自动验证完成；用户复核与真实模型任务待验收。对应 W1–W3 的界面迁移切片 1。

本文记录第一切片当时的范围与协议基线；随后已补 [全屏工作台](ink-workbench.md) 和 [恢复、MCP、权限与可信结果](ink-parity.md)。下文“尚不支持”仅描述第一切片，当前运行方式以 [使用指南](../USAGE.md) 为准。默认入口继续保留 Textual，直到 Ink 的开发与实际体验超越 Textual。

## 行为

新增 `./run_ink.sh` 启动 Node.js + TypeScript + React/Ink 界面，由 Node 启动 Python `./run_vibe.sh --stdio` 子进程。任务仍由同一个 `app.agent.Agent.run()` 执行，复用模型配置、上下文、预算、原生工具、审批和会话记录。`./run_vibe.sh` 默认仍启动 Textual。

本切片支持输入任务、连续流式回复、状态、工具调用与结果、单次批准 / 拒绝、取消和退出。用户明确批准前，写入与命令不能执行；界面断开不等于批准。取消后的副作用仍可能需要核对，不能把取消解释成回滚。命令执行的取消需要终止并回收进程组。

本切片保留新会话记录，恢复、运行中纠偏、MCP、精确规则 / 工作区授权、多行编辑、完整 Markdown 与历史浏览继续按后续切片推进。新入口对尚不支持的恢复、eval 和 MCP 配置明确报错，不静默忽略。工具返回值当前仍是原有布尔值与文本；命令退出码可信度按既有“可信命令结果”切片补齐，界面不能直接显示“测试通过”。

## 本地协议 v1

两端使用 UTF-8 JSON Lines，一行一个对象。请求与事件都有 `v: 1`、`type`、`id`；请求 ID 是非空、最长 128 字符的唯一字符串，启动事件的 ID 是 `null`。Python stdout 只输出协议，诊断写 stderr。单帧上限为 1 MiB，不含换行。

| 请求 | 必填字段 | 行为 |
|---|---|---|
| `run` | `id`, `text` | 开始一轮任务；同一进程只允许一个活跃运行 |
| `cancel` | `id`, `target` | 只取消匹配 `target` 的活跃运行 |
| `approval` | `id`, `approval_id`, `decision` | 只接受当前审批的 `once` 或 `deny` |
| `shutdown` | `id` | 拒绝待审批动作，取消活跃任务，清理后退出 |

| 事件 | 主要字段 | 含义 |
|---|---|---|
| `ready` | `workspace`, `model`, `session_id`, `capabilities` | 后端可接收任务 |
| `status` | `phase` | `idle` / `running` / `waiting_approval` / `stopping` |
| `text_delta` | `delta` | 当前运行的增量文本 |
| `tool_call` / `tool_result` | `call`；结果另带 `ok`, `output` | 调用与结果按工具调用 ID 配对 |
| `approval_request` | `approval_id`, `call`, `preview` | 后端审批闸等待用户决定 |
| `approval_resolved` | `approval_id`, `decision` | 审批结束，旧审批不可再次使用 |
| `done` | `result` | 每个被接受的 `run` 恰好一个终结事件，结果包含实际 Agent 状态 |
| `ack` | `action` | 已接收取消或审批回复，不代表副作用已撤销 |
| `error` | `code`, `message` | 非法请求、并发冲突或执行错误 |
| `closed` | 无 | 关闭完成；正常主动关闭关联 shutdown 请求 ID |

运行相关事件使用原 `run` 的 ID；`ack` / 请求错误使用对应请求 ID。`call` 包含 `id`、`name`、`args`。`done.result` 使用 `AgentResult` 的字段，状态包含 `done`、`stopped`、`cancelled`、`error`、`truncated`。

非法 JSON、未知版本、缺少字段、过大输入、重复 ID、运行中重复提交、错误目标取消、迟到或失配的审批均不得产生新动作。读取请求与 Agent 执行并发，等待模型或审批时仍能接收取消。输入 EOF、进程信号与退出请求均触发清理；Node 对异常子进程退出显示错误，清理等待有上限。

## 验证与学习

- 先用失败测试验证协议、流式文本、工具回灌、拒绝 / 单次允许、取消、EOF 和错误处理，再实现。
- Node 测试验证 UTF-8 分片、帧校验、进程失败与退出；Ink 测试验证连续回复、结果不重复、审批与状态。
- 使用真实 Python 子进程验证 fake 流式请求、会话记录与正常退出；在终端检查输入、输出和快捷键。
- 原工程测试继续通过；真实模型读取文件后回答独立记录结果，未运行时保持待验收。

阅读路径：`run_ink.sh → tui/src/cli.tsx → backend.ts → app/stdio.py → Agent.run()`。能解释请求 ID、流式事件、审批权威与进程生命周期后，再进入下一切片。

## 本切片验证记录（2026-10-20）

- Python 完整回归：`./run_tests.sh -o addopts='' -q`，**377 passed**，包含原有 Textual、MCP、预算与恢复回归。
- Node 编译与回归：`npm --prefix tui test`，**35 passed**，包含真实 Python fake 子进程、UTF-8 分片、帧上限、退出回收、审批与界面事件。
- PTY：80×24 下中文输入与两轮 fake 回复、Ctrl+Q 退出通过；120×40 下中文输入、fake 回复与空闲 Ctrl+C 退出通过。原 Textual 入口 fake 启动、回复、退出通过。
- 修复了集成测试发现的上下文字段接线错误、fake provider 第二轮耗尽异常，以及后端父进程退出但后代持有 stderr 时连接挂住的问题。原生命令取消 / 超时回收进程组已有失败回归。
- 真实模型读取文件后回答尚未执行：当前环境未配置 API key。中文输入法组合、不同外部终端和真实编码任务仍需实际使用验收；PTY 注入中文不等于输入法兼容性验证。

本轮没有运行真实模型 eval，也未把确定性脚本通过记作 W1–W3 完成。运行记录保存在忽略目录 `.vibe/`。
