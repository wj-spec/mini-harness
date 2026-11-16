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
      outcome.py        # 结构化结果事实与同任务聚合

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
      task_navigation.py # 当前会话任务只读目录、分页、控制可用性与 worker/审批事实（P8）
      plan_revision.py  # PlanRevision：持久反馈修订、准确任务绑定、只读查询与显式续做（P4）
      inspection.py     # 检查记录：请求/任务/计划绑定、命令事实、原子保存与只读分页（P6）
      attach_inspection.py # attach 任务范围只读查询与离线身份（不持有执行器）
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
      writer.py         # stdio 运行/启动恢复共用进程锁，拒绝同会话并行执行
      trace.py          # TraceRecorder / Pricing
      audit.py          # 只读环境审计：git 基线、文件 hash、完整性结论
      auditor.py        # 可选 LLM 只读审计：全新上下文 + 只读工具子集
      evidence.py       # 最近一轮 RoundRecord 的中文证据摘要（/evidence）
      observability.py  # trace.jsonl 只读聚合（--trace-summary/--trace-all-sessions/TUI /trace）
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
      history.py        # 长任务 benchmark 历史（--bench-history/--bench-history-mode）

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
      use-plan-review.ts # 准确计划卡、请求归属、显式决策、分页与未知结果恢复
      use-task-navigation.ts # 任务目录请求、作用域和迟到回执管理
      use-task-connection.ts # 接入/取消生命周期与按任务隔离的窗口内草稿
      task-navigation-panel.tsx # 按任务身份选择、完整目标/状态与历史入口
      use-followups.ts # 后续待办、恢复暂停与已发请求追踪
      followup-store.ts # 会话队列原子保存、范围校验与单写者占用
      followup-panel.tsx # 后续正文阅读、独立编辑与可点击动作
      use-inspection-history.ts # 只读历史结果分页、身份校验、失败保页与请求生命周期
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
      sidebar/          # 共用检查阅读器与独立 Git 侧栏（Ghostty split/quick、Terminal 独立窗口）
        cli.tsx         # 参数、终端标题、渲染与信号清理
        app.tsx         # 变更列表、diff、跟随与键盘交互
        agent-activity.ts # 从工具记录提取 Agent 成功改过的文件（含 apply_patch diff 头）
        signal.ts       # TUI → 侧栏的 workspace 信号文件（版本/文件/active）
        git.ts          # 只读 git status / diff / numstat 与解析
        file-source.ts  # 读取前复核来源目录身份与 Git 根，拒绝回退主项目
        diff.ts         # unified diff 分类、宽字符换行
        state.ts        # 选中、跟随、焦点与滚动（纯函数）
        host.ts         # Ghostty 1.3+ AppleScript、quick/split、quick terminal 位置预检与降级
        launch.ts       # 工作台到侧栏入口的路径、启动、/sidebar setup 与退出联动
        args.ts         # 侧栏 CLI 参数（workspace/theme/refresh/parent-pid）
        lease.ts        # 启动/实例占用、进程存活判断与按代次释放
        lifecycle.ts    # 父进程看护、侧栏 PID/终端尺寸登记与终止
        split-ratio.ts  # 读取侧栏列宽并把 Ghostty split 校准到 70/30

    tests/              # Node 通信与 Ink 渲染测试（含 sidebar signal/agent-activity）
    package-lock.json   # 固定 Node 依赖

  evals/                # tasks.json（10 个任务）
  examples/demo-repo/   # 3 分钟 demo 仓库
  examples/skills.json  # 可提交的静态技能配置示例
  scripts/demo.sh       # 复制 demo 并启动 Ink 工作台
  scripts/start.py      # 首次启动编排、依赖安装与启动分流（模型配置在 Ink 首次配置页完成）
  scripts/launcher_config.py # .env 数据解析与原子保存
  scripts/doctor.py     # 首用环境自检（--doctor；仅标准库，缺项给命令）
  .env.example          # 环境变量模板（不含真实 key，.env 不提交）
  scripts/setup-ghostty-sidebar.sh # 写入右侧 quick terminal 配置（幂等）
  scripts/acceptance_seed_task.py  # 07 attach 人工验收：创建隔离 test session/task（可带 worktree）
  scripts/acceptance_attach_probe.py # 07 attach 人工验收：controller/observer/v2 帧低层探针
  scripts/bench_long.py    # 长任务评测：fake 15 场景 + real-only 3 + 双模式 1
  scripts/real_p1_acceptance.py # 真实模型验收：foreground/background/queue/goal/repair/merge
  scripts/delegate_ab.py   # 只读委派 A/B（survey/digest，真实模型，JSON/Markdown 报告）
  scripts/verify_all.sh    # 一键验收（Python/TUI/bench/doctor/redteam/真实 suite/real-bench）
  benchmarks/long/         # 长任务场景配置（modes: fake/real）
  AGENTS.md             # 项目规则（自举）

  tests/                # 镜像模块
    test_budget.py
    test_context.py
    test_core_dependencies.py
    test_core_layout.py
    test_app_layout.py
    test_task_store.py
    test_task_runtime.py
    test_plan_revision.py # 修订持久化、冲突、崩溃与显式恢复的公共接口回归
    test_inspection_records.py # 检查记录归属、未知结果、分页、损坏与保存恢复
    test_inspection_file_source.py # 实际工具目录、旧记录来源未知与 attach worktree 身份
    test_stdio_tasks.py
    test_stdio_task_navigation.py # 审批中查询、范围隔离、坏记录和零写入
    test_cli_session_recovery.py # worker/前台重开保护、双进程互斥、正常结束与强退恢复
    test_stdio_session_sync.py # 下一模型请求接入后台结果/快照/状态，同会话并行运行拒绝
    test_stdio_inspections.py # 历史查询不占执行、帧上限、错误隔离与参数边界
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
      long-horizon-eval.md        # 长任务评测 P0：bench_long fake worker 场景
      unattended-profile.md      # 无人值守 P0：保守 profile 与 worker 能力拒绝
      modes.md                   # 模式 P0：chat/act TaskRecord.mode 与 TUI 命令
      plan-artifact.md           # 结构化 Plan P0：schema/存储/渲染
      plan-approval.md           # 计划审批 P0：propose/approve/reject、hash/审计
      plan-execution.md          # 计划执行 P0：step ledger/证据推进
      plan-acceptance.md         # 计划验收 P0：criteria→verification 三态
      subagent-runner.md         # 只读子 Agent P0：runner/结构化结果
      p0-closure-report.md       # 长任务/后台/进阶模式 P0 收束报告
      manual-acceptance-16-plan-subagent.md  # Plan/SubAgent 人工验收 TC-16-01..08
      real-p1-plan-smoke-2026-11-07.md      # 真实模型 Plan P1 冒烟记录
      manual-acceptance-16-results.md       # Plan/SubAgent 验收结果（自动化部分）
      manual-acceptance-tui-quick.md        # 真实终端 TUI 快速人工验收清单（10 分钟）
      open-decisions.md                     # 待用户拍板事项（委派/并行写/人工验收/集中观测）
    design/             # 未来方向的分块讨论（长任务/后台/进阶模式/子 Agent），拍板后收敛为 spec
    history/            # 旧 M1–M8 模块建设计划

  openspec/
    roadmaps/           # 跨多个变更的设计、阶段计划与执行记录；规划不代表已实施
      build-agent-lab/   # Agent 实验台：design.md / roadmap.md / task-log.md

  archive/              # 历史草稿，不 import
    loop_v1.py
    loop_react_draft.py
    tools_v0.py
    memory_v0.py
    test_loop_v1.py

  README.md
  pyproject.toml
  run_vibe.sh
  start.sh              # 推荐启动入口：检查依赖、加载 .env、配置模型、启动 Ink
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

`tui/tests/focused-inspection.test.tsx` 覆盖内嵌检查的准确分页、草稿/光标、旧结果停止目标、审批提示与选区附回。

`tui/tests/sidebar-launch-concurrency.test.ts` 通过实际 Node 进程验证启动竞争、独立 split 与强制结束后的并发恢复；宿主动作使用假 runner。

`tui/tests/followup-app.test.tsx` 从公开 App 覆盖后续待办的失败暂停、编辑/撤回/继续、断线、退出、审批与中文草稿。`followup-persistence.test.tsx` 覆盖会话重开、单写者接入、发送未知、坏文件/写失败、范围隔离与目录替换；BackendClient 测试核对预分配请求 ID 的实际传输。

结果展示：tui/src/outcome.ts 统一正文、状态和检视的摘要语义；core/task/outcome.py 提供纯事实，app/stdio.py 完成计划验收和任务聚合，原始 receipt 保留在轮次及检视详情。

配置文档：`docs/CONFIGURATION.md` 面向用户的配置入口与生效范围；`docs/CONFIGURATION-DEV.md` 保存开发/诊断参数；`docs/CONFIGURATION-CONTRACT.md` 追踪首次模板移出项的解析和消费证据。

功能指引：`tui/src/guidance.ts` 定义五区说明与情境文案；`guidance-panel.tsx` 处理键盘/鼠标阅读，App 复用现有导航，指引不直接调用运行或审批接口。


模型设置：app/runtime/user_config.py 管理用户 TOML、项目覆盖和独立凭据；app/runtime/model_config.py 共享配置读写并兼容 .env，scripts/launcher_config.py 仅兼容导出；app/runtime/model_settings.py 负责来源、脱敏、审批保存、显式检测及快照核对。tui/src/model-settings.ts 定义协议校验，use-model-settings.ts 管理请求身份，model-panel.tsx 管理独立编辑草稿。后端仍持有凭据与执行权限。

首次启动：缺模型配置时 `python -m app --stdio` 进入 app/runtime/bootstrap.py 的仅配置状态，不创建 Agent、任务或会话；只提供 model list/save/test 与进入项目，run/resume 明确拒绝。保存与检测仍走 ApprovalGate；进入项目时用保存后的环境重新 exec 自身，新进程按既有启动路径发送带 `run` 能力的 ready。TUI 以 `config-bootstrap` 能力渲染首次配置页（bootstrap-panel.tsx + use-bootstrap-settings.ts），保存并检测、跳过检测确认与进入项目都在同一页。

设置中心：app/runtime/limit_settings.py 负责单段限制与运行作用域，stdio 记录和恢复 run_limits；tui/src/settings.ts、use-settings.ts、settings-panel.tsx 分别负责契约、请求与数字编辑。复用模型与权限入口，MCP 可进入独立配置面板，沙箱与通知继续展示状态。

目标预算：goal_limits.py / goal_settings.py 负责契约和默认设置；TaskStore 固定 goal_limits，TaskRuntime 在创建新段前核对账本，worker 续段复用任务快照。tui/src/goal-budget.ts 定义投影，goal-store.ts 恢复匹配任务的预算与累计消耗，设置页按 g 切换分组。

MCP 配置：app/runtime/mcp_settings.py 处理来源、脱敏视图、审批与原子保存；stdio 复用连接管理边界。tui/src/mcp-settings.ts、use-mcp-settings.ts、mcp-panel.tsx 分别负责协议、请求身份与编辑草稿。

体验验收：scripts/workbench_acceptance.py 创建隔离工作区、虚构配置；examples/mcp-demo/server.py 提供本地回显服务；start.sh --acceptance 沿用依赖准备和 ApprovalGate，产物写 .vibe/workbench-acceptance。
