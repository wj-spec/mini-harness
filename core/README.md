# core/ 模块规划

> 状态：2026-11-03 起生效。`core/` 是可复用的 Agent harness 组件层：**纯数据与纯逻辑，只依赖标准库**。
> 模块落位规则、依赖方向与未来扩展位置以本文为准；新增/移动文件时同步 `docs/STRUCTURE.md` 与仓库 `README.md`。

## 目录

```text
core/
  __init__.py          # 窄公共 API：只 re-export 跨域常用类型（ContextBuilder/ContextResult/Message/Response/ToolCall）
  README.md            # 本文件
  contracts/           # 纯数据契约（叶子层，不依赖其它 core 子包）
    __init__.py
    messages.py        # Message / ToolCall / Response / ExecutionOutcome / Role / 解析与校验
  budget/              # 预算与额度
    __init__.py
    policy.py          # BudgetPolicy：S/M/T/H/R + scratch 分区、history 额度
  context/             # 上下文工程
    __init__.py
    builder.py         # ContextBuilder / ContextResult / token 估算 / stable_prefix_hash
    snapshot.py        # build_context_snapshot：请求前上下文快照
    summarize.py       # 确定性摘要（stdlib；LLM 摘要留在 app/）
  task/                # 任务与证据
    __init__.py
    state.py           # TaskState / CompletionDecision / completion_decision / delivery_receipt
    progress.py        # ProgressMonitor / ArtifactProgress（空转与进展检测）
    rounds.py          # RoundRecord schema 与 build_round_record
    outcome.py         # 增量结果事实、检查覆盖与同任务合并（不含 UI 文案）
```

一次 `git mv` 迁移自原来的扁平布局（`core/models.py`、`core/context.py` 等 8 个文件）；
迁移保持每个实现文件的行号不变，因此 `docs/` 中按 `path:line` 的引用只需替换路径。

## 依赖规则（硬约束）

```text
contracts  ←  budget  ←  context
contracts  ←  task
contracts  ←  core/__init__.py（只做 re-export）
```

1. **`core/` 只依赖标准库**：不能 import `app/`、`httpx`、`rich`、UI、MCP、网络、会话/文件 I/O。
   `tests/test_core_dependencies.py` 在 `-I -S` 子进程里验证这一点。
2. **`contracts/` 是叶子**：不 import 其它 core 子包，只放与行为无关的数据类型和纯解析/校验。
3. **依赖单向**：`budget` 可依赖 `contracts`（未来）；`context` 可依赖 `contracts`、`budget`；
   `task` 可依赖 `contracts`（未来可依赖 `budget`）；**禁止反向依赖**，也不允许 `context ↔ task` 互相依赖。
   跨域组合（如“任务状态 + 上下文”）放在 `app/` 的编排层，不塞回 core。
4. **core 不做 I/O 与运行时**：不做异步调度、会话持久化、审批、预算账本落盘、worker、评测、MCP；
   这些属于 `app/`。core 里也不写具体工具与提示词。
5. **根目录不再新增 `.py` 模块**（只允许 `__init__.py`）；新功能先归入现有域，确需新域时同步更新本文件、
   `docs/STRUCTURE.md` 与布局测试。

## 新增文件放哪

| 需求 | 落位 | 说明 |
|---|---|---|
| 新的消息/事件/通用数据结构 | `contracts/` | 例如未来的 subagent 结果契约、证据引用类型 |
| 预算/额度/账本的纯计算部分 | `budget/` | 持久化与查询留在 `app/`（见 `docs/design/long-horizon/06-budget-ledger.md`） |
| 上下文组装、压缩、快照、确定性摘要 | `context/` | LLM 摘要适配器仍留在 `app/` |
| 任务状态、完成判定、进展、轮次/证据记录 | `task/` | 未来：`task/plan.py`（Plan/PlanStep 校验与渲染）、`task/acceptance.py`（验收标准与覆盖检查）、`task/goal.py`（GoalRecord 纯 schema）、`task/step_ledger.py` |
| 跨域编排（任务运行时、审批、会话、worker、模式 profile、评测） | `app/` | 不能进 core |

> 未来模块的具体落位依据见 `docs/design/long-horizon/`：12 号文档的 Plan 产物、15 号文档的验收标准都设计为纯 stdlib 模块，放进 `core/task/`；06 号文档的 BudgetLedger 若需要跨消费方复用，纯合计逻辑可放 `core/budget/ledger.py`，落盘/查询留在 `app/`。

## 公共 API 与兼容

- 子包 `__init__.py` **只 re-export 稳定公共名**；内部实现文件可以调整，但子包导出名视为契约。
- `core/__init__.py` 保持很窄（当前只有 5 个名字）；不要因为“方便”把所有类都堆到顶层。
- 旧扁平路径（如 `core.context`）在迁移后仍可用，因为子包同名 re-export；但 `core.models`、
  `core.state`、`core.progress`、`core.rounds`、`core.context_snapshot`、`core.summarize` 已移除，
  代码与文档统一迁移到新路径。
- 新增/重命名公共名需要同时更新：子包 `__init__.py`、`core/__init__.py`（如需要）、`core/README.md`、
  `docs/STRUCTURE.md`、布局测试与全仓 import。

## 测试映射

迁移后测试文件名沿用原模块命名（不改名以避免无意义 churn），对应关系如下：

| core 模块 | 主要测试 |
|---|---|
| `contracts/messages.py` | `tests/test_agent.py`、`tests/test_state.py`、`tests/test_provider.py`（间接覆盖） |
| `budget/policy.py` | `tests/test_budget.py` |
| `context/builder.py` | `tests/test_context.py`、`tests/test_context_compression.py`、`tests/test_auto_compaction.py` |
| `context/snapshot.py` | `tests/test_context_snapshot.py`、`tests/test_context_observability.py` |
| `context/summarize.py` | `tests/test_summarize.py`、`tests/test_app_summarize.py` |
| `task/state.py` | `tests/test_state.py`、`tests/test_completion_guard.py`、`tests/test_delivery_receipt.py`、`tests/test_file_changes.py` |
| `task/progress.py` | `tests/test_progress.py`、`tests/test_tool_progress.py` |
| `task/rounds.py` | `tests/test_rounds.py` |
| 布局/依赖契约 | `tests/test_core_layout.py`、`tests/test_core_dependencies.py` |

## 测试

- `tests/test_core_dependencies.py`：core 在无第三方依赖下可导入，且不加载 `app`/`httpx`/`rich`。
- `tests/test_core_layout.py`：根目录只允许 `__init__.py` + 四个子包；子包依赖方向符合上面的单向规则；
  子包可以独立导入；公共 API 可导入。
- 迁移/新增模块后至少跑：`./run_tests.sh -q`，并更新 `docs/` 里的 `core/...` 路径引用。
