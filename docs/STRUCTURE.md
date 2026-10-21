# 文件规划

> 目标：`core/` 是可复用的 Agent harness；`app/` 是第一个 consumer（TUI coding agent）；`tests/` 镜像模块；`docs/` 放协议和 spec；`archive/` 放历史草稿，不被 import。

## 目录

```
mini-harness/
  core/                 # 可复用 harness，不依赖 TUI
    __init__.py
    models.py           # Message / ToolCall / Response
    budget.py           # BudgetPolicy：S/M/T/H/R 分区
    context.py          # ContextBuilder：预算 / 分组 / offload / summary
    state.py            # TaskState：harness 侧任务进度
    summarize.py        # 确定性摘要，只依赖标准库

  app/                  # TUI product
    __main__.py         # vibe 入口
    config.py           # env / workspace / 预算
    events.py           # TextDelta / ToolCallsReady / Error
    providers/
      base.py           # Provider 协议
      fake.py           # 脚本 provider
      openai_compat.py  # SSE /chat/completions
    tools.py            # read/list/grep/recall/update_task/write_file/edit_file/bash
    mcp.py              # FastMCP Client：stdio 接入、启动审批、工具映射与清理
    policy.py           # 审批闸、session allowlist、审计
    diff.py             # unified diff 预览
    sandbox.py          # 命令黑名单、超时、进程组 kill、输出截断
    session.py          # JSONL 事件、state 投影、--continue、崩溃对账
    trace.py            # TraceRecorder / Pricing
    instructions.py     # AGENTS.md 加载与注入
    summarize.py        # LLM 摘要 HTTP 适配器 + 确定性回退
    eval.py             # 10 任务 eval、check、报告、regression delta
    agent.py            # 唯一运行 loop：流式 + 工具执行 + context 接入
    tui.py              # Textual 界面
    stdio.py            # Ink JSONL：恢复、审批、MCP、命令、取消、会话记录

  tui/                  # Node.js + TypeScript + React/Ink 新入口
    src/
      cli.tsx           # 参数、渲染、进程信号与清理
      backend.ts        # Python 子进程、帧校验与关闭
      protocol.ts       # JSONL v1 类型与运行时校验
      app.tsx           # 任务输入、流式文本、单次审批与取消
      view-state.ts     # 界面事件状态、完整消息与工具记录
      workbench.tsx     # 全屏阅读区、工具卡片与完整详情
      markdown.tsx      # 终端 Markdown、宽字符换行与安全显示
      theme.ts          # system / light / dark 语义配色
      composer.tsx      # 多行草稿、输入历史、光标与粘贴
      editor.ts         # grapheme 编辑和终端单元格宽度
    tests/              # Node 通信与 Ink 渲染测试
    package-lock.json   # 固定 Node 依赖

  evals/                # tasks.json（10 个任务）
  examples/demo-repo/   # 3 分钟 demo 仓库
  scripts/demo.sh       # 复制 demo 并启动 TUI
  AGENTS.md             # 项目规则（自举）

  tests/                # 镜像模块
    test_budget.py
    test_context.py
    test_core_dependencies.py
    test_app_summarize.py
    test_state.py
    test_summarize.py
    test_tools.py
    test_agent.py
    test_agent_budget.py
    test_session.py
    test_trace.py
    test_eval.py
    test_instructions.py
    test_policy.py
    test_tui.py
    test_provider.py

  docs/
    USAGE.md           # 安装、配置与运行参考
    VIBE-PROTOCOL.md    # 协作规则
    MILESTONES.md       # 工作台里程碑与验收状态
    roadmap.md          # 产品方向与阶段取舍
    STRUCTURE.md        # 本文件
    specs/              # 每个切片的 spec
    history/            # 旧 M1–M8 模块建设计划

  archive/              # 历史草稿，不 import
    loop_v1.py
    loop_react_draft.py
    tools_v0.py
    memory_v0.py
    test_loop_v1.py

  README.md
  pyproject.toml
  run_vibe.sh
  run_ink.sh            # 编译并启动 Ink，默认入口仍是 run_vibe.sh
  run_tests.sh
  .gitignore
```

## 依赖方向

```
app  ──> core
tests ─> core + app
core ──> 只依赖 stdlib
```

**硬规则**：`core/` 不能 import `app/`，也不能 import `textual` / `httpx`。core 一旦沾上 TUI，就不能复用到别的项目。

Textual、stdio 后端与 eval 都调用 `app.agent.Agent.run()`。Ink 通过本地 JSONL 与 stdio 后端交互，工具执行和审批决定仍由 Python 持有。循环测试放在 `tests/test_agent.py` 等 Python 回归中，界面与传输测试另见 `tui/tests/`。
`app.summarize.LLMSummarizer` 通过现有 `summarizer` 参数注入 `ContextBuilder`，`core/` 不负责网络请求。

## 新文件放哪

| 切片 | 新文件 | 职责 |
|---|---|---|
| D3 | `app/policy.py` | 风险分级、审批决定、session allowlist |
| D3 | `app/diff.py` | unified diff 预览 |
| D3 | `app/tools.py` 增 `write_file` / `edit_file` | 写工具 |
| D3 | `app/session.py` | JSONL 事件、审批记录（D5 扩展 resume） |
| D4 | `app/tools.py` 增 `bash`；`app/sandbox.py` | 命令白名单、超时、输出截断 |
| D5 | `app/session.py` 扩展 | checkpoint、`--continue` |
| D6 | `app/trace.py`；`eval/tasks/`、`eval/runner.py` | span、成本、10 个任务 |
| 每切片 | `tests/test_<module>.py` | 至少覆盖成功 + 一个失败路径 |

## 规则

1. **一个文件一个职责**。不要建空占位文件；要用时再建。
2. **core 不 import app**。如果 core 需要 TUI 的东西，说明分层错了。
3. **每个新模块配测试**：成功路径 + 一个失败路径。
4. **移动/新增文件后**，更新 `README.md` 和本文件。
5. **运行时产物**放 `.vibe/`（offload / backups / sessions），已 gitignore。
6. **archive 只读**：不 import、不修、不删；它是你的学习历史。
7. **spec 先于实现**：D3 起，每个切片在 `docs/specs/` 放一页 spec，再写代码。

## 命名

- 模块：`snake_case.py`
- 类：`PascalCase`
- 工具：动词开头（`read_file` / `write_file` / `edit_file` / `grep` / `bash`）
- 事件：`XxxEvent` 或 `Xxx`（现有 `TextDelta` / `ToolCallsReady` / `StreamError`）
