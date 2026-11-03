# app/ 模块规划

> 状态：2026-11-03 起生效。`app/` 是第一个 consumer（coding agent 后端）：模型接入、Agent 运行、工具、审批、会话、评测。
> 模块落位规则与未来扩展位置以本文为准；移动/新增文件时同步 `docs/STRUCTURE.md`、`README.md`、`AGENTS.md` 与 `docs/` 里的 `app/...` 路径引用。

## 目录

```text
app/
  __init__.py            # 包说明（本文件同目录 README 才是规划）
  __main__.py            # CLI 入口：python -m app（--stdio / --eval / --task）
  config.py              # env / workspace / 预算等配置
  events.py              # Provider 事件契约（TextDelta / ToolCallsReady / ...）
  stdio.py               # Ink 使用的 JSONL v1 后端（会话、审批、命令）
  providers/             # 模型接入：base 协议 / fake / OpenAI-compatible SSE
  runtime/               # Agent 运行与编排
    __init__.py
    agent.py             # 唯一 Agent loop（原 app/agent.py）
    summarize.py         # LLM 摘要 HTTP 适配器（原 app/summarize.py）
    tasks.py             # TaskRecord/TaskStore：任务身份/状态机/原子写/恢复（P0 基础）
    task_runtime.py      # TaskRuntime：stdio run.task、goal 生命周期、task_state（接入层）
  tools/                 # 工具系统
    __init__.py
    registry.py          # ToolRegistry / ToolSpec 与内置工具（原 app/tools.py）
    patch.py             # unified diff 应用
    diff.py              # diff 预览
    web_search.py        # 免费 Web 搜索
    mcp.py               # stdio MCP 客户端
    skills.py            # 技能发现/激活
    skill_history.py     # 技能历史的模型视图
  safety/                # 审批与执行边界
    __init__.py
    policy.py            # ApprovalGate / 授权作用域 / 审计
    permissions.py       # T0–T4 层级矩阵、autonomy
    permission_policy.py # 用户/项目策略文件
    sandbox.py           # 命令黑名单、超时、Seatbelt/bwrap
    sandbox_proxy.py     # 沙箱网络白名单代理
  session/               # 会话、证据与审计
    __init__.py
    store.py             # JSONL 会话、state 投影、轮次账本（原 app/session.py）
    trace.py             # trace / usage / cost
    audit.py             # 只读环境审计
    auditor.py           # 可选 LLM 只读审计
    evidence.py          # /evidence 摘要
    context_display.py   # 上下文快照展示
  profiles/              # 项目背景与个人偏好
    __init__.py
    instructions.py      # AGENTS.md 注入
    project.py           # 项目背景
    user.py              # 用户偏好
  browser/               # 浏览器能力（受控动作走 MCP）
    __init__.py
    server.py            # 浏览器 MCP server
    policy.py            # 浏览器动作策略/快照
  eval/                  # 评测与跑分（非日常运行依赖）
    __init__.py
    internal.py          # 内部 10 任务 eval（原 app/eval.py）
    swebench.py          # SWE-bench Lite 入口（原 app/benchmark.py）
    container.py         # 评测容器
    data.py              # SWE-bench 数据
    budget_checks.py     # 离线预算机制检查
    bench_profile.py     # 跑分模式提示词
    oneshot.py           # 非交互 --task 入口
```

一次 `git mv` 迁移自原来的扁平布局（2026-11-03）；实现文件逐字搬移、行号不变，
因此 `docs/` 里按 `path:line` 的引用只需替换路径。

## 依赖规则

```text
entry 层：__main__.py / stdio.py
共享层：config / events / providers
领域层：
  runtime   -> tools, safety, session, profiles, providers, core
  tools     -> safety, providers?, core
  safety    -> core（+ 可选 providers 用于 LLM 判断）
  session   -> core
  profiles  -> core
  browser   -> tools?, safety?, providers（经 MCP/浏览器服务）
  eval      -> runtime, safety, providers, session, core
```

硬约束：

1. **app 可以依赖 core，反向禁止**：core 不能 import app。
2. **入口层不反向被领域依赖**：`app/__main__.py`、`app/stdio.py` 只做接线；领域模块不得 import 它们。
   `app/config.py` / `app/events.py` 是共享边界，可被任意领域依赖。
3. **领域之间避免环**：当前允许的依赖方向是 `runtime → tools → safety`、`runtime → session`、`runtime → profiles`、
   `eval → runtime/safety/session`；不要新增 `safety → runtime`、`session → runtime` 这类反向依赖，需要跨域组合时在 `runtime`/入口层完成。
4. **`eval/` 不是运行依赖**：日常运行路径不能 import `app.eval.*`；`bench/` 适配层可以。
   既有例外：`session/auditor.py` 在函数内延迟导入 `runtime.agent.Agent`（避免循环），后续可改为由 runtime 注入工厂。
5. **子包 `__init__.py` 保持轻**：只放 docstring；不 re-export 重模块，避免 import 环与隐式副作用。
   需要公共 API 时通过显式子模块导入（`from app.safety.policy import ApprovalGate`）。
6. **根目录只允许边界/入口文件**：`__init__.py`、`__main__.py`、`config.py`、`events.py`、`stdio.py`；
   其它新模块先进领域目录，确需新域时更新本文、`docs/STRUCTURE.md`、`AGENTS.md` 与布局测试。

## 新增文件放哪

| 需求 | 落位 | 说明 |
|---|---|---|
| Agent loop、任务运行时、worker、goal/plan 编排、mode profile、子 Agent 调度 | `runtime/` | 对应 `docs/design/long-horizon/` 的 01/02/05/06/10/11/16 号设计；建议模块名 `tasks.py`、`worker.py`、`goals.py`、`budget.py`、`modes.py`、`subagents.py` |
| 工具/补丁/MCP/技能/Web 搜索 | `tools/` | 新工具优先加在 `registry.py` 的 `ToolSpec` 表 |
| 审批、层级、策略、沙箱、审批收件箱 | `safety/` | 03/04/09 号设计：`approvals.py`（收件箱）、`unattended.py`（无人值守 profile）落这里 |
| 会话、trace、审计、证据、上下文展示 | `session/` | 05/07 号设计的任务/段账本若只是读写可放这里；纯 schema 放 `core/task/` |
| AGENTS.md、项目背景、个人偏好 | `profiles/` | 只做注入与来源管理，不做策略 |
| 浏览器服务与动作策略 | `browser/` | 受控动作仍走 MCP + `ApprovalGate` |
| 评测任务、跑分入口、数据集、预算自检、长任务评测 | `eval/` | 08 号设计的长任务场景脚本放 `scripts/`，报告逻辑可进 `eval/`；日常运行不依赖 |
| 跨域接线（新 CLI flag、stdio 帧） | `__main__.py` / `stdio.py` | 不在领域模块里直接改协议 |

> 注意：`runtime/` 是未来增长最快的域。任务运行时、后台 worker、模式/计划、子 Agent 都先在这里落位，
> 等其中某个域出现稳定边界与第二个消费者时，再单独提为子包（例如 `runtime/subagents/`）。

## 公共 API 与兼容

- 当前没有稳定外部 API；仓库内一律使用显式子模块导入。
- 旧扁平路径（`app.agent`、`app.tools`、`app.policy`、`app.session` 等）已迁移；全仓 import 与文档路径已更新。
- 新增/重命名模块时同步更新：`docs/STRUCTURE.md`、`README.md`、`AGENTS.md`、`docs/` 路径引用、
  `tests/` 的 import 与 monkeypatch 字符串（monkeypatch 用 `"package.module.attr"` 形式时要跟着改）。
- 测试文件名沿用旧命名（`tests/test_<old>.py`），对应关系见 `docs/STRUCTURE.md`；避免无意义改名。

## 测试

- 全量：`./run_tests.sh -q`（改行为先加/改测试）。
- 布局：`tests/test_app_layout.py` 约束 app 根目录只保留边界/入口文件、旧扁平模块不再出现、领域模块不 import 入口层。
- 依赖方向（本文的 runtime/tools/safety/session/profiles/eval 顺序）仍靠 review；等任务运行时落地后，
  再评估是否把完整方向也做成静态检查。
