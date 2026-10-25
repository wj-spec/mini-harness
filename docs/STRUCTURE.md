# 文件规划

> 目标：`core/` 是可复用的 Agent harness；`app/` 是第一个 consumer（coding agent 后端）；`tests/` 镜像模块；`docs/` 放协议和 spec；`archive/` 放历史草稿，不被 import。

## 目录

```
mini-harness/
  core/                 # 可复用 harness，不依赖界面
    __init__.py
    models.py           # Message / ToolCall / Response
    budget.py           # BudgetPolicy：S/M/T/H/R 分区
    context.py          # ContextBuilder：预算 / 分组 / offload / summary
    state.py            # TaskState：harness 侧任务进度
    summarize.py        # 确定性摘要，只依赖标准库

  app/                  # Python 后端：模型、工具、审批、会话、eval
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
    skills.py           # 项目/本机发现、常驻注册目录、临时正文与来源边界
    skill_history.py    # 旧技能正文的模型视图，保留原始历史
    summarize.py        # LLM 摘要 HTTP 适配器 + 确定性回退
    eval.py             # 10 任务 eval、check、报告、regression delta
    agent.py            # 唯一运行 loop：流式 + 工具执行 + context 接入
    stdio.py            # Ink JSONL：恢复、审批、MCP、命令、取消、会话记录

  tui/                  # Node.js + TypeScript + React/Ink 工作台（唯一界面入口）
    src/
      cli.tsx           # 参数、渲染、进程信号与清理
      backend.ts        # Python 子进程、帧校验与关闭
      backend-launch.ts # 交互后端与只读会话列表共用启动环境
      protocol.ts       # JSONL v1 类型与运行时校验
      app.tsx           # 阅读焦点、滚动、输入、审批与取消
      view-state.ts     # 实际活动、最终状态、完整消息与工具记录
      agent-status.tsx  # 活动、任务、工具与真实资源预算分区
      workbench.tsx     # 全屏阅读、消息卡片、滚动条与详情
      mouse.ts          # 有界鼠标滚轮协议解析与粘贴隔离
      use-transcript-scroll.ts # 阅读区命中、鼠标模式生命周期
      terminal-input.ts # 已识别鼠标包合并、原始输入与终端清理
      markdown.tsx      # 终端 Markdown、宽字符换行与安全显示
      theme.ts          # 浅深主题及真彩 / 256 / 16 色分层
      terminal-colors.ts # 与 Ink 共用颜色能力及禁色规则
      composer.tsx      # 多行草稿、输入历史、光标与粘贴
      editor.ts         # grapheme 编辑和终端单元格宽度
      sidebar/          # 独立 Git 变更侧栏（Ghostty quick terminal）
        cli.tsx         # 参数、终端标题、渲染与信号清理
        app.tsx         # 变更列表、diff、跟随与键盘交互
        git.ts          # 只读 git status / diff / numstat 与解析
        diff.ts         # unified diff 分类、宽字符换行
        state.ts        # 选中、跟随、焦点与滚动（纯函数）
        host.ts         # Ghostty 1.3+ AppleScript、quick terminal 位置预检与降级
        launch.ts       # 工作台到侧栏入口的路径、启动与 /sidebar setup
    tests/              # Node 通信与 Ink 渲染测试
    package-lock.json   # 固定 Node 依赖

  evals/                # tasks.json（10 个任务）
  examples/demo-repo/   # 3 分钟 demo 仓库
  examples/skills.json  # 可提交的静态技能配置示例
  scripts/demo.sh       # 复制 demo 并启动 Ink 工作台
  scripts/setup-ghostty-sidebar.sh # 写入右侧 quick terminal 配置（幂等）
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
    test_skills.py
    test_skill_discovery.py
    test_skill_lifecycle.py
    test_policy.py
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
  run_ink.sh            # 编译并启动 Ink（唯一交互入口）
  run_sidebar.sh        # 手动启动右侧 Git 变更侧栏
  run_vibe.sh           # Python 入口：--stdio 后端、--eval、会话列表
  run_tests.sh
  .gitignore
```

## 依赖方向

```
app  ──> core
tests ─> core + app
core ──> 只依赖 stdlib
```

**硬规则**：`core/` 只依赖标准库，不能 import `app/`、`httpx`、`rich`。core 一旦沾上界面，就不能复用到别的项目。

stdio 后端与 eval 都调用 `app.agent.Agent.run()`。Ink 通过本地 JSONL 与 stdio 后端交互，工具执行和审批决定仍由 Python 持有；Textual 界面已于 2026-10-23 退役。循环测试放在 `tests/test_agent.py` 等 Python 回归中，界面与传输测试另见 `tui/tests/`。
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
2. **core 不 import app**。如果 core 需要界面的东西，说明分层错了。
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
