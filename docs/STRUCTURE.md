# 文件规划

> 目标：`core/` 是可复用的 Agent harness；`app/` 是第一个 consumer（coding agent 后端）；`tests/` 镜像模块；`docs/` 放协议和 spec；`archive/` 放历史草稿，不被 import。

## 目录

```
mini-harness/
  core/                 # 可复用 harness，不依赖界面；模块落位见 core/README.md
    __init__.py         # 窄公共 API：ContextBuilder / ContextResult / Message / Response / ToolCall
    README.md           # core 模块规划：分域、依赖方向、新增文件规则
    contracts/          # 纯数据契约（叶子层）
      __init__.py
      messages.py       # Message / ToolCall / Response / ExecutionOutcome
    budget/             # 预算与额度
      __init__.py
      policy.py         # BudgetPolicy：S/M/T/H/R + scratch 分区
    context/            # 上下文工程
      __init__.py
      builder.py        # ContextBuilder：预算 / 分组 / offload / summary
      snapshot.py       # 请求前上下文快照
      summarize.py      # 确定性摘要（LLM 摘要适配器留在 app/）
    task/               # 任务与证据
      __init__.py
      state.py          # TaskState / 完成守卫 / 文件变动与验证证据
      progress.py       # 进展与空转检测
      rounds.py         # 一轮 run 的紧凑记录（不存正文）

  app/                  # Python 后端：模型接入、Agent 运行、工具、审批、会话、评测
    __init__.py         # 包说明（分域索引）
    __main__.py         # vibe 入口（--stdio / --eval / --task）
    config.py           # env / workspace / 预算
    events.py           # TextDelta / ToolCallsReady / Error
    stdio.py            # Ink JSONL：恢复、审批、MCP、命令、取消、会话记录
    README.md           # app 模块规划：分域、依赖方向、未来模块落位
    providers/          # 模型接入
      base.py           # Provider 协议
      fake.py           # 脚本 provider
      openai_compat.py  # SSE /chat/completions
    runtime/            # Agent 运行与编排
      agent.py          # 唯一运行 loop：流式 + 工具执行 + context 接入
      summarize.py      # LLM 摘要 HTTP 适配器 + 确定性回退
      tasks.py          # TaskRecord/TaskStore：任务身份、状态机、原子写、恢复（P0 基础）
      task_runtime.py   # TaskRuntime：stdio run.task、goal 任务生命周期、task_state payload
      supervise.py      # WorkerSupervisor：worker.json/心跳/判死/--tasks（P0a）
      worker.py         # 后台 worker：独立 stdio 子进程 + ctrl.sock + attach + worker.log（P0b）
      goals.py          # GoalRecord v2：目标状态/迁移/段账本同步（P0）
      budget.py         # BudgetLedger：budget.jsonl 追加、totals、段/任务结算（P0）
      approvals.py      # ApprovalInbox：approvals.jsonl、一次性审批/过期/脱敏（P0）
      workspace.py      # WorkspaceLock / WorktreeManager：单写者锁与 git worktree 隔离（P0）
      merge.py          # MergeGate：worktree 合并审批、漂移/脏工作区/冲突拦截（P1）
      killswitch.py     # workspace kill switch：stop 信号/SIGTERM→SIGKILL/worker 状态（P1）
    tools/              # 工具系统
      registry.py       # 工具注册表与内置工具（read/write/bash/...）
      patch.py          # unified diff 解析、校验与 hunk 应用
      diff.py           # unified diff 预览
      web_search.py     # 免费 Web 搜索
      mcp.py            # FastMCP Client：stdio 接入、启动审批、工具映射与清理
      skills.py         # 项目/本机发现、常驻注册目录、临时正文与来源边界
      skill_history.py  # 旧技能正文的模型视图，保留原始历史
    safety/             # 审批与执行边界
      policy.py         # 审批闸、session allowlist、审计
      permissions.py    # T0–T4 层级矩阵、autonomy
      permission_policy.py # 用户/项目策略覆盖
      sandbox.py        # 命令黑名单、超时、进程组 kill、输出截断
      sandbox_proxy.py  # 沙箱网络白名单代理
    session/            # 会话、证据与审计
      store.py          # JSONL 事件、state 投影、轮次账本、--continue、崩溃对账
      trace.py          # TraceRecorder / Pricing
      audit.py          # 只读环境审计：git 基线、文件 hash、完整性结论
      auditor.py        # 可选 LLM 只读审计：全新上下文 + 只读工具子集
      evidence.py       # 最近一轮 RoundRecord 的中文证据摘要（/evidence）
      context_display.py # 上下文快照展示
    profiles/           # 项目背景与个人偏好
      instructions.py   # AGENTS.md 加载与注入
      project.py        # 项目背景
      user.py           # 用户偏好
    browser/            # 浏览器能力（受控动作走 MCP）
      server.py         # 浏览器 MCP server
      policy.py         # 浏览器动作策略/快照
    eval/               # 评测与跑分（非日常运行依赖）
      internal.py       # 10 任务 eval、check、报告、regression delta
      swebench.py       # SWE-bench Lite 入口
      container.py      # 评测容器
      data.py           # SWE-bench 数据
      budget_checks.py  # 离线预算机制检查
      bench_profile.py  # 跑分模式提示词（无人值守、容器内验证）
      oneshot.py        # 非交互跑分入口：--task、自动审批、telemetry

  bench/                # Harbor 跑分适配层（不作为 app 依赖）
    adapter.py          # BaseInstalledAgent：容器内安装并执行 --task
    support.py          # 模型/环境/telemetry 纯逻辑，可离线测试
    requirements-bench.txt # 容器内运行依赖（与 pyproject 对齐）
    README.md           # 单题 smoke、覆盖项与限制

  tui/                  # Node.js + TypeScript + React/Ink 工作台（唯一界面入口）
    src/
      cli.tsx           # 参数、渲染、进程信号与清理
      backend.ts        # Python 子进程、帧校验与关闭
      backend-launch.ts # 交互后端与只读会话列表共用启动环境
      protocol.ts       # JSONL v1 类型与运行时校验
      app.tsx           # 阅读焦点、滚动、输入、审批与取消
      approval-view.ts  # 审批预览的层级解析与标题（T0–T4 前置显示）
      view-state.ts     # 实际活动、最终状态、完整消息与工具记录
      mascot.tsx       # 彩色像素猫 Mini 的 sprite 与半格渲染
      goal.ts          # /goal 目标状态、段决策、提示与上限
      goal-store.ts    # 目标状态写入/恢复 session 的 goal.json
      preference-observer.ts # 从 session 用户消息提出 proposed 偏好
      skills-menu.ts    # /skills 的动态补全菜单（↑↓ 滚动、Tab 补全）
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
      sidebar/          # 独立 Git 变更侧栏（Ghostty split/quick、Terminal 独立窗口）
        cli.tsx         # 参数、终端标题、渲染与信号清理
        app.tsx         # 变更列表、diff、跟随与键盘交互
        agent-activity.ts # 从工具记录提取 Agent 成功改过的文件（含 apply_patch diff 头）
        signal.ts       # TUI → 侧栏的 workspace 信号文件（版本/文件/active）
        git.ts          # 只读 git status / diff / numstat 与解析
        diff.ts         # unified diff 分类、宽字符换行
        state.ts        # 选中、跟随、焦点与滚动（纯函数）
        host.ts         # Ghostty 1.3+ AppleScript、quick/split、quick terminal 位置预检与降级
        launch.ts       # 工作台到侧栏入口的路径、启动、/sidebar setup 与退出联动
        args.ts         # 侧栏 CLI 参数（workspace/theme/refresh/parent-pid）
        lifecycle.ts    # 父进程看护、侧栏 PID/终端尺寸登记与终止
        split-ratio.ts  # 读取侧栏列宽并把 Ghostty split 校准到 70/30

    tests/              # Node 通信与 Ink 渲染测试（含 sidebar signal/agent-activity）
    package-lock.json   # 固定 Node 依赖

  evals/                # tasks.json（10 个任务）
  examples/demo-repo/   # 3 分钟 demo 仓库
  examples/skills.json  # 可提交的静态技能配置示例
  scripts/demo.sh       # 复制 demo 并启动 Ink 工作台
  scripts/doctor.py     # 首用环境自检（--doctor；仅标准库，缺项给命令）
  .env.example          # 环境变量模板（不含真实 key，.env 不提交）
  scripts/setup-ghostty-sidebar.sh # 写入右侧 quick terminal 配置（幂等）
  scripts/acceptance_seed_task.py  # 07 attach 人工验收：创建隔离 test session/task（可带 worktree）
  scripts/acceptance_attach_probe.py # 07 attach 人工验收：controller/observer/v2 帧低层探针
  AGENTS.md             # 项目规则（自举）

  tests/                # 镜像模块
    test_budget.py
    test_context.py
    test_core_dependencies.py
    test_core_layout.py
    test_app_layout.py
    test_task_store.py
    test_task_runtime.py
    test_stdio_tasks.py
    test_supervise.py
    test_cli_tasks.py
    test_worker.py
    test_worker_attach_control.py
    test_goals.py
    test_budget_ledger.py
    test_approvals.py
    test_workspace.py
    test_merge.py
    test_killswitch.py
    test_cli_merge_stop.py
    test_app_summarize.py
    test_state.py
    test_summarize.py
    test_tools.py
    test_agent.py
    test_agent_budget.py
    test_completion_guard.py
    test_evidence.py
    test_file_changes.py
    test_rounds.py
    test_audit.py
    test_auditor.py
    test_session.py
    test_trace.py
    test_eval.py
    test_bench_mode.py
    test_bench_adapter.py
    test_instructions.py
    test_skills.py
    test_skill_discovery.py
    test_skill_lifecycle.py
    test_policy.py
    test_provider.py
    test_git_tools.py
    test_patch_tool.py

  docs/
    USAGE.md           # 安装、配置与运行参考
    HARNESS.md         # harness 技术方案：分层、主循环、上下文、审批边界、状态与恢复、评测与取舍
    VIBE-PROTOCOL.md    # 协作规则
    MILESTONES.md       # 工作台里程碑与验收状态
    roadmap.md          # 产品方向与阶段取舍
    STRUCTURE.md        # 本文件
    assets/             # README 与规格文档使用的图片
    specs/              # 每个切片的 spec
      task-runtime.md   # 任务运行时基础：TaskRecord/TaskStore 数据模型、状态机与恢复
      task-runtime-integration.md  # 任务运行时接入：stdio run.task、task_state、TUI /goal
      process-supervision.md       # 后台进程监督：worker 状态/心跳/--tasks（P0a）
      goal-record.md              # GoalRecord：目标 schema v2、迁移与段同步（P0）
      budget-ledger.md            # 预算账本：budget.jsonl、trace 接线与终态结算（P0）
      approval-inbox.md           # 审批收件箱：approvals.jsonl、一次性审批/过期/脱敏（P0）
      workspace-isolation.md      # 工作区隔离：单写者锁、git worktree、diff（P0）
      control-plane-attach.md     # 控制面 attach：TUI 连接运行中 worker（P0）
      acceptance-report.md        # 全链路验收收束：验证结果、限制与复现命令
      manual-acceptance-07-attach.md  # 07 v2/TUI attach 真实终端人工验收步骤与用例
      manual-acceptance-07-results.md # 07 attach 人工验收记录（进行中）
    design/             # 未来方向的分块讨论（长任务/后台/进阶模式/子 Agent），拍板后收敛为 spec
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
  run_vibe.sh           # Python 入口：--stdio 后端、--eval、--task 跑分、会话列表
  run_tests.sh
  .gitignore
```

## 依赖方向

```
app  ──> core
tests ─> core + app
core ──> 只依赖 stdlib
```

core 内部按域单向依赖，禁止环形与跨域反向引用（细则见 [core/README.md](../core/README.md)）：

```
contracts  ←  budget  ←  context
contracts  ←  task
```

**硬规则**：`core/` 只依赖标准库，不能 import `app/`、`httpx`、`rich`。core 一旦沾上界面，就不能复用到别的项目。
**布局约束**：`core/` 根目录只允许 `__init__.py` 与四个域目录；新功能先按 `core/README.md` 的“新增文件放哪”归域，
需要新域时同时更新 `core/README.md`、本文件与 `tests/test_core_layout.py`。

`app/` 内部按域组织（细则见 [app/README.md](../app/README.md)）：

```
runtime  -> tools -> safety
runtime  -> session / profiles
eval     -> runtime / safety / session
入口层：__main__.py / stdio.py 只做接线；config.py / events.py 为共享边界
```

- 领域模块不得 import 入口层；`eval/` 不是日常运行依赖（`bench/` 适配层除外）。
- 根目录只允许 `__init__.py`、`__main__.py`、`config.py`、`events.py`、`stdio.py`；其余新模块先进领域目录。

stdio 后端与 eval 都调用 `app.runtime.agent.Agent.run()`。Ink 通过本地 JSONL 与 stdio 后端交互，工具执行和审批决定仍由 Python 持有；Textual 界面已于 2026-10-23 退役。循环测试放在 `tests/test_agent.py` 等 Python 回归中，界面与传输测试另见 `tui/tests/`。
`app.runtime.summarize.LLMSummarizer` 通过现有 `summarizer` 参数注入 `ContextBuilder`，`core/` 不负责网络请求。

## 新文件放哪

| 切片 | 新文件 | 职责 |
|---|---|---|
| D3 | `app/safety/policy.py` | 风险分级、审批决定、session allowlist |
| D3 | `app/tools/diff.py` | unified diff 预览 |
| D3 | `app/tools/registry.py` 增 `write_file` / `edit_file` | 写工具 |
| D3 | `app/session/store.py` | JSONL 事件、审批记录（D5 扩展 resume） |
| D4 | `app/tools/registry.py` 增 `bash`；`app/safety/sandbox.py` | 命令白名单、超时、输出截断 |
| D5 | `app/session/store.py` 扩展 | checkpoint、`--continue` |
| D6 | `app/session/trace.py`；`eval/tasks/`、`eval/runner.py` | span、成本、10 个任务 |
| 每切片 | `tests/test_<module>.py` | 至少覆盖成功 + 一个失败路径 |

> `core/` 内部新增文件不在此表：按 [core/README.md](../core/README.md) 的域划分落位
> （契约 → `contracts/`、预算 → `budget/`、上下文 → `context/`、任务与证据 → `task/`）。
> `app/` 内部新增文件同理按 [app/README.md](../app/README.md) 的“新增文件放哪”落位。

## 规则

1. **一个文件一个职责**。不要建空占位文件；要用时再建。
2. **core 不 import app**。core 内部按 `contracts ← budget ← context`、`contracts ← task` 单向依赖；
   分域与落位规则见 [core/README.md](../core/README.md)。如果 core 需要界面的东西，说明分层错了。
3. **每个新模块配测试**：成功路径 + 一个失败路径。
4. **移动/新增文件后**，更新 `README.md` 和本文件。
5. **运行时产物**放 `.vibe/`（offload / backups / sessions），已 gitignore。
6. **archive 只读**：不 import、不修、不删；它是你的学习历史。
7. **spec 先于实现**：D3 起，每个切片在 `docs/specs/` 放一页 spec，再写代码。
8. **core 根目录只允许** `__init__.py` 与四个域目录；新增域或移动模块时同步更新 `core/README.md` 与
   `tests/test_core_layout.py`，不要恢复扁平模块。

## 命名

- 模块：`snake_case.py`
- 类：`PascalCase`
- 工具：动词开头（`read_file` / `write_file` / `edit_file` / `grep` / `bash`）
- 事件：`XxxEvent` 或 `Xxx`（现有 `TextDelta` / `ToolCallsReady` / `StreamError`）
