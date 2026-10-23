# AGENTS.md

## 项目

长期目标是越用越了解用户的个人助手工作台，当前重点是 vibe coding 能力与 agent 原理学习。
Ink 是唯一界面入口（Textual 已于 2026-10-23 退役）；`core/` 是可复用 harness，`app/` 负责模型接入和后端。

## 开发取舍

- 优先把单 Agent 的编码任务做可靠、可理解，再根据实际使用反馈扩展。
- 讨论新功能、长期记忆或界面方向时，先读 `docs/roadmap.md`；后续方向与已实现能力分开描述。
- 安排开发顺序或更新进度时，以 `docs/MILESTONES.md` 的当前里程碑和验收证据为准。

## 规则

- `core/` 只依赖标准库，不能 import `app/`、`httpx`、`rich`。
- 改行为先加/改测试；提交前跑 `./run_tests.sh`。
- 运行时产物写 `.vibe/`，不要提交。
- 写文件、执行命令必须经过 `ApprovalGate`；不要绕过审批。
- 回答用中文；代码注释尽量短。

## 验证

- 单元测试：`./run_tests.sh -q`
- smoke：`./run_ink.sh --fake`
- eval：`./run_vibe.sh --eval --eval-limit 3`
