# 10 模式状态机：chat / act / plan / goal 与 background

> 状态：讨论稿（2026-11-02；2026-11-03 01–05 决策对齐见 §9，用户回答见 §10）；P0 chat/act 与 TaskRecord.mode 已实现，见 [modes spec](../../specs/modes.md)。
> 所属：进阶模式 / 第 1 块
> 前置/相关：[01 任务运行时](01-task-runtime.md)、[11 模式契约](11-mode-contracts.md)、[12 结构化 Plan 产物](12-plan-artifact.md)、[16 子 Agent 的有机结合](16-subagent-integration.md)；相关规格：[goal-mode](../../specs/goal-mode.md)、[multi-agent-computer-use](../../specs/multi-agent-computer-use.md)

## 1 问题与范围

用户要把 `/plan` 等进阶模式逐块落地。第一块不是「每个模式各自怎么实现」，而是：**harness 需不需要一个统一的「模式（mode）」概念，它归谁所有？** 2026-11-03 已确认（D1）：Python `TaskRuntime` 是编排权威，TUI 退化为客户端/视图；run/goal/plan/subagent 全部建模为 task，**mode 是 `TaskRecord` 的属性**，由 TaskRuntime 在创建任务时写入，不再是「TUI/CLI 的启动参数」。

本文范围：mode 的取值、挂载对象、合法转换、持久化与能力声明；澄清 mode、autonomy、background 三个常被混用的轴（**mode ≠ autonomy ≠ background**，其中 background 是任务运行时属性——是否由 worker 脱离 TUI 继续跑——不是模式）；给出 P0（mode 字段 + chat/act）与 P1（plan/goal）的切片边界。

不在本文范围：TaskRecord/TaskRuntime 的任务模型本体（[01](01-task-runtime.md)）、每个 mode 具体用哪些工具/审批/预算（[11](11-mode-contracts.md)）、Plan 的数据结构（[12](12-plan-artifact.md)）、计划审批门（13 块）、后台 worker/supervisor（[02](02-process-supervision.md)）。

| 概念 | 回答的问题 | 今天/确认后的事实源 | 与本文关系 |
|---|---|---|---|
| mode | 这个 task 是哪种工作契约 | `TaskRecord.mode`（TaskRuntime 写入，D1） | 本文定义取值与转换 |
| autonomy | 哪些动作可以不问用户就做 | `ApprovalGate.autonomy` + `TaskRecord.approval_policy`（01） | 正交，不合并 |
| background | 任务是否脱离 TUI 继续跑 | `TaskRecord` 的 worker/pid/lock/心跳（01/02） | 正交，不合并 |
| bench profile | 跑分时的无人值守提示词 | `app/eval/bench_profile.py` | 一种执行 profile，不是 mode |

## 2 现状与证据

| 维度 | 现状 | 证据 |
|---|---|---|
| 任务身份 | 不存在 TaskRecord/TaskRuntime；一次 run 就是全部上下文 | `app/stdio.py:598-604`、[01](01-task-runtime.md) §2 |
| 协议层 mode | 不存在。`run` 请求只接受 `text`，字段白名单固定 | `app/stdio.py:598-604` |
| chat | `--chat-only` 把 `tools_enabled=False`，MCP/技能/LLM 审计一并关闭，提示词换成 chat-only 版 | `app/__main__.py:83`、`app/__main__.py:261`、`app/__main__.py:268`、`app/__main__.py:296`、`app/runtime/agent.py:393`、`app/runtime/agent.py:490-492` |
| act | 默认路径，无开关；全部内置工具在同一 registry，`subset` 只给审计子 Agent 用 | `app/__main__.py:296`、`app/tools/registry.py:887-895`、`app/session/auditor.py:250` |
| plan | 不存在。最接近的是 `update_task(plan=[...])` 自由文本，不限制工具、不产生审批 | `app/tools/registry.py:385-400`、`core/task/state.py:200` |
| goal | TUI 客户端编排；每段是普通 run，注入 `[GOAL MODE]` 用户文本；harness 无 goal 分支（D1 决定 P1 迁段决策到 Python） | `docs/specs/goal-mode.md:9`、`tui/src/goal.ts:57`、`tui/src/app.tsx:234` |
| 任务性质 | `require_tool_use`（动作任务零工具调用时的纠偏）只在 oneshot/eval 使用，不是 mode | `app/runtime/agent.py:300`、`app/eval/oneshot.py:179` |
| background | 不存在。单连接、单 active run，忙时 `busy`；确认后为「每 task 一个 worker + per-workspace supervisor」 | `app/stdio.py:634`、[02](02-process-supervision.md) §9 |
| 能力声明 | `ready.capabilities` 是固定字符串列表，没有 mode 位 | `app/stdio.py:725-726` |

结论：现状是「一组局部开关」，没有任务对象、也没有模式对象；模式差异只能通过启动参数和提示词表达，运行中不可切换、不可回放。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 无任务身份与模式标识 | run 只有 `text`；没有 TaskRecord 承载 mode/预算/审批归属 | mode 无处挂，D1 的任务模型是本块前置 |
| plan 无执行边界 | 计划只是状态里的自由文本，模型可在「计划」中直接写文件 | 「先看计划再执行」只能靠提示词自觉 |
| 模式与能力未绑定 | 工具/审批/预算在进程启动时定死，中途不能换 | 无法实现「进入 plan → 只读探索 → 批准 → act」 |
| 无转换守卫 | `/goal` 的「运行中拒绝启动」写在 TUI，后端无校验；TaskRuntime 尚无状态机 | 非法转换散落在 UI，可绕过、行为不一致 |
| 编排权在 TUI 且无持久化 | 目标/续段在 `tui/src/goal.ts` 与 `goal.json`；session `state.json` 不记录 task/mode | TUI 退出即中断，重启后模式与 task 脱节（D1 要迁 Python） |
| 概念重叠 | `--chat-only`、`--autonomy`、`require_tool_use`、`/goal` 四套开关并存 | 用户与模型各要记一套，文档易漂移 |

## 4 设计选项与推荐

### 4.1 三个候选

| 方案 | 模式事实源 | 优点 | 风险 | 结论 |
|---|---|---|---|---|
| A UI 专属 | TUI 自己维护模式，后端不变 | 零后端改动，最快 | 后端无法强制只读；CLI/eval 无法复用；不可测 | D1 已排除为终态，只作 `/goal` 过渡 |
| B task 级字段 | `TaskRecord.mode`，TaskRuntime 写入 | 执行层可强制；可回放可测；后台/多任务天然复用 | 依赖 01 TaskRuntime 落地 | **推荐** |
| C session 级状态机 | 后端持有会话模式状态与切换命令 | 为多任务控制面（07 块）铺路 | P0 过度设计；与「一 task 一 mode」冲突 | P2 再评估 |

### 4.2 推荐：mode 挂在 TaskRecord

- 取值 `mode ∈ {chat, act, plan, goal}`，字符串枚举；未知值 **fail closed**，拒绝创建 task。
- 权威：TaskRuntime 创建 task 时确定 mode 并写入 `TaskRecord.mode`；后续 run/段从 TaskRecord 读。交互入口（TUI/stdio `run` 帧/CLI flag）只提交「创建请求」，不直接改事实源。
- 入口默认：交互入口默认 `act`；`--chat-only` 映射 `chat`，`--bench` 映射 `act` + bench profile（bench 是执行环境，不是 mode）。session 是否记忆上次 mode 见 §8 Q1。
- `plan`/`goal` 是 `TaskRecord.kind`，复用同一个 Agent loop，只是 profile 不同（[11](11-mode-contracts.md)）；background 是否转后台由 TaskRuntime/worker 决定，与 mode 正交（D1 默认开、范围待 01 §9 确认）。
- P0 不把转换固化成独立状态对象：用 TaskRuntime 的「合法转换表 + 拒绝信息」保证可验证，等 [12](12-plan-artifact.md) 的 Plan 状态出现后再评估是否抽真状态机。

### 4.3 状态与转换

| 转换 | 触发（经 TaskRuntime） | 守卫 | 结果 |
|---|---|---|---|
| chat → act | 新建 task（mode=act）；或当前会话显式切换 | 无同 workspace 写冲突（04 单写者） | 新 `TaskRecord.mode=act` |
| act → plan | 用户 `/plan` → 创建 `kind=plan` task | 无同 workspace 写冲突 | 只读 task，产出 Plan 草案（[12](12-plan-artifact.md)） |
| plan → act | 用户批准计划（13 块） | Plan `status=approved`、schema 校验通过 | 创建 `kind=run` 执行 task，绑定 approved plan |
| act / plan → goal | 用户 `/goal` → 创建 `kind=goal` task | 无同 workspace 写冲突 | goal task；段内继承创建时 mode（默认 act） |
| goal → act | goal task 终态（完成/失败/取消） | — | 后续新建 run task |
| 任意 → chat | 新建 mode=chat task（或 `--chat-only` 入口提示） | — | 无工具 task |
| task 内改 mode | 未定（§8 Q1） | 未定 | 未定 |

三个轴的组合示例：`mode=plan + approval_policy=queue + background` = 后台只读规划任务；`mode=act + background` 的写任务必须独立 worktree + 审批队列（03/04 已确认）。mode 只决定「做什么/用哪些能力」，不决定「能否不问就做」与「是否转后台」。

### 4.4 为什么不把 plan 塞进 autonomy 档位

autonomy 回答「能不能不问就做」，mode 回答「用哪些能力去做」。把 plan 做成 autonomy 的第五档会让同一档位同时管能力限制与授权范围；而且 autonomy 只在两次 run 之间切换（`/autonomy`，`app/stdio.py:344-356`），task 模型落地后写入 `TaskRecord.approval_policy`（01），无法表达「只读」这种能力限制。因此 mode 与 autonomy 保持正交：mode profile 只能给 autonomy 设**上限**（见 [11](11-mode-contracts.md)）。

### 4.5 TUI 命令与入口兼容

| 入口 | 今天 | 确认后方向 | 兼容策略 |
|---|---|---|---|
| `/goal` | 客户端目标状态 | TUI 请求创建 `kind=goal` task；P0 段决策仍在 TUI，P1 迁 Python | `VIBE_TASK_RUNTIME=0` 回退旧行为 |
| `--chat-only` | 无工具 | 入口提示 → 创建 mode=chat task | flag 保留，语义不变 |
| `/autonomy` | 授权档位 | 不变（正交），写入 TaskRecord.approval_policy | UI 同时显示 mode 与 autonomy |
| `/act`、`/chat` | 不存在 | TUI 客户端命令 → 新建/切换 task | P0 只新增 TUI 命令，走 task 创建请求 |
| `/plan` | 不存在 | P1：创建 `kind=plan` task | 需要 11/12 先落地 |
| TUI 关闭（有运行中 task） | 直接结束 | 提示 转入后台/等待完成/停止并退出/取消关闭（01/02） | 后台默认开启范围待 01 §9 确认 |
| `/help`、`COMMAND_COMPLETIONS` | 27 项 | 同步加 3 项 | `tui/src/protocol.ts:20-28` 单一来源 |

## 5 最小实现切片

**P0：TaskRecord.mode + chat/act**（前置：01 P0 的 `app/runtime/tasks.py` 最小 TaskStore）

```jsonc
// TaskRecord（01 schema 摘录，权威）
{"task_id": "t-...", "kind": "run|goal|plan", "mode": "chat|act", "status": "created"}
// 入口请求 → TaskRuntime：mode 只作为创建参数，不是事实源
{"v": 1, "type": "run", "id": "r1", "text": "...", "mode": "chat|act"}
```

- 新增 `app/runtime/modes.py`：`MODE_NAMES=("chat","act","plan","goal")`、`parse_mode(value) -> str`（未知值抛错）、`DEFAULT_MODE="act"`。
- `app/runtime/tasks.py`（01 P0，前置）：创建 task 时解析并写入 `TaskRecord.mode`；task 目录 meta 持久化，`list/get` 可读。
- 入口：`app/stdio.py` 的 `run` 请求增加可选 `mode`（校验后交给 TaskRuntime）；`ready.capabilities` 增加 `tasks`/`modes`（v2 协商见 07）；未协商的旧 TUI 不发送 mode，按 task 默认 act。runtime 开关关闭时按旧路径解释（兼容窗口）。
- `app/runtime/agent.py`：接受从 TaskRecord 解析出的 mode/profile；P0 chat 复用 `tools_enabled=False`（`app/runtime/agent.py:307`），不改成空 registry。
- `app/session/store.py`：round 记录 `task_id`/`mode`；TUI 状态栏显示 TaskRecord.mode 与后台任务列表。
- TUI：`protocol.ts` 类型与校验、`backend.ts` 的 `run(text, mode?)`、`app.tsx` 的 `/chat` `/act` 命令。

**P1：plan/goal**——`/plan` 创建 `kind=plan` task、read-only profile（[11](11-mode-contracts.md)）、Plan 产物（[12](12-plan-artifact.md)）、转换守卫、`/goal` 段决策迁 Python。

**测试点**

- TaskRecord 往返：创建/重启后 `mode` 可读，未知 mode 拒绝创建；
- 缺省 mode 的请求行为与 HEAD `02a6b6c` 一致（runtime 关闭时回归不变）；
- `mode=chat` 的模型请求不含 tools 字段；模型文本工具调用仍走 chat-only error；
- `capabilities=modes` 协商：旧 TUI 不发送 mode；`--continue` 后 mode 从 TaskRecord 恢复；
- `/autonomy` 切换与 mode 互不覆盖（正交回归）。

**明确不做**：不做后端 session 状态机与多任务调度（01/07 块）；不改 autonomy/T0–T4；不做 worker/supervisor 本体（02 块）；不做 plan 审批门（13 块）；不新增第二套 Agent loop。

## 6 与其它块的关系

| 块 | 关系 | 方向 |
|---|---|---|
| [01 任务运行时](01-task-runtime.md) | mode 挂在 `TaskRecord.mode`；TaskRuntime 创建/推进 task | 本文依赖 01 |
| [11 模式契约](11-mode-contracts.md) | 10 定义 mode 身份，11 定义每个 mode 的 profile 与强制层 | 本文依赖 11 |
| [12 Plan 产物](12-plan-artifact.md) | `kind=plan` task 的输出物；plan→act 转换携带 approved plan | 相互依赖 |
| [13 计划审批门](13-plan-approval-gate.md) | `plan → act` 的守卫由 13 提供 | 本文依赖 13 |
| [16 子 Agent](16-subagent-integration.md) | 子 Agent 是 `kind=subagent` 任务节点，不是一种 mode | 相关 |
| [05 跨段目标状态](05-goal-state.md) | goal task 的状态载荷 GoalRecord（含 `plan_id`） | 相关 |
| [02 后台进程监督](02-process-supervision.md) | background 是 worker/supervisor 属性，与 mode 正交 | 正交/相关 |
| [07 控制面](07-control-plane.md) | task/mode 字段进 JSONL v2 与 `capabilities` | 本文依赖 07 的协议规范 |

## 7 与子 Agent 的关系

| 维度 | 本文结论 | 落地约束 |
|---|---|---|
| A 谁发起 | P0 只允许 harness 内部发起（复用 `app/session/auditor.py` 原型）；`plan` task 的只读探索 P1 开放用户显式触发；模型工具 `delegate` 必须等 mode profile 的委派策略就位 | 委派开关写在 [11](11-mode-contracts.md) profile，不写进 mode 名 |
| B 能力边界 | `plan`/`goal` 的 profile 规定子 Agent 只读；`act` 默认不挂 `delegate` 工具；共享工作区写首版不开放 | 子 Agent 用 `capability_profile=read_only`（见 [16](16-subagent-integration.md)） |
| C 结果可信度 | mode 不改变「子报告是 untrusted claim」；plan 里子 Agent 的探索结果只能写入 Plan 的 `source`/证据引用，不能标记步骤完成 | 父侧用证据/验证门复核（15 块） |

子 Agent 在本项目里应作为**任务节点（child task node）**：`TaskRecord.kind=subagent`，有 `parent_id`/`parent_run_id`/`delegation_id`、独立预算、独立工具子集、审批归属（`requester=child`）、trace/session 记录，而不是 Agent loop 的特殊分支。**不要为子 Agent 新增 mode 值**——否则模式矩阵按角色爆炸；用 `profile.delegation` 表达「这个 mode 允许委托什么」。

## 8 开放问题与验收标准

**待用户拍板**

1. mode 粒度：D1 已把它归到 `TaskRecord`；仍未决的是「同一 task 跨段是否允许改 mode」与「交互入口是否按 session 记忆上次 mode」，建议跨段不允许改、入口记忆待定。

回答：跨段不允许吧，然后入口记忆的话可以按照项目记录。上次在这个会话中的mode记忆。

2. `plan` task 写 `.vibe/` 计划产物的边界：D1 的 task 目录本身允许写产物（rounds/state 同类），仍未确认的是 `submit_plan` 是否要单独的 UI 确认/审计展示；工作区源码必须保持只读。

回答：需要。

3. `goal` 已是 `kind=goal` task（D1）；仍未决：goal 段内能否使用 plan/chat mode（当前建议默认 act），以及是否把 goal 作为正式 mode 标识暴露给用户。

回答：goal 段内能使用 plan。把 goal 作为正式 mode 标识暴露给用户

4. 新旧协议协商：未知字段应拒绝还是忽略？建议 `capabilities` 协商 + fail closed，避免「静默忽略 mode」。

回答：按照你的建议，后续我们要保留一种协议。

5. `plan`/`goal` task 的完成判定何时从「done+verified」切到「用户确认验收标准 + 验证门」？D5 已定标准来源，切换时机建议随 15 块的前置切片。

回答：这部分我需要你专业的建议供我判断。

**可验证验收条件（P0）**

- `mode=chat` 的 task：provider 收不到 tools 字段，模型请求工具直接 error（`tests/test_agent.py:147` 同类断言）；
- 缺省 mode 的请求行为与 `02a6b6c` 一致；全量 Python 回归退出 0；
- 未知 mode 被拒绝且不创建 task；`TaskRecord.mode` 跨重启可读；
- `ready.capabilities` 含 `modes`，未协商的 TUI 不发送 mode；mode 与 autonomy 同时变更互不覆盖；
- `VIBE_TASK_RUNTIME=0` 时旧路径行为与现状一致。

## 9 决策对齐（2026-11-03）

本节记录 01–05 已确认决策对本块的影响；§8 的未决问题保持不变。

1. **任务运行时权威（D1）**：mode 改述为 `TaskRecord.mode`，由 Python `TaskRuntime` 写入；TUI/CLI/stdio `run` 帧只是创建任务的入口，不再持有模式事实源（§1/§2/§4.1/§4.2/§5）；`plan`/`goal` 改为 `kind=plan|goal` 的 task，转换经 TaskRuntime（§4.3）。
2. **background 归属（D1/D2）**：保留 mode ≠ autonomy ≠ background 论述，并补上归属——background 是 TaskRecord 的 worker/pid/lock/心跳与 per-workspace supervisor 属性，不是 mode；后台默认开启、范围仍待 01 §9 确认（§1/§4.3/§4.5）。
3. **TUI 客户端化**：`/goal` 等命令改为 TUI → TaskRuntime 的创建/控制请求；TUI 关闭提示「转入后台/等待/停止/取消」（§4.5）。
4. **路径**：`app/agent.py`→`app/runtime/agent.py`、`app/tools.py`→`app/tools/registry.py`、`core/state.py`→`core/task/state.py`、`app/oneshot.py`→`app/eval/oneshot.py`，行号按当前工作树重核；新模块落位 `app/runtime/modes.py`（§5）。
5. **仍未决（保留）**：mode 粒度与跨段切换（§8 Q1）、`plan` 写 `.vibe/` 产物例外（§8 Q2）、goal 段内 mode 与正式标识（§8 Q3）、v2 协议协商（§8 Q4）、完成语义切换时机（§8 Q5）；后台默认开启范围以 [01](01-task-runtime.md) §9 为准。

## 10 用户回答处理（2026-11-03）

用户回答原文见 §8。处理结果：

**已确认**
- Q1：同一 task 跨段**不允许改 mode**；入口记忆上次 mode（建议实现为“项目级最近 mode + 会话覆盖”，请确认这个口径）。
- Q2：`submit_plan` 需要独立的 UI 确认与审计展示；工作区源码保持只读。
- Q3：goal 段内可以使用 plan；`goal` 作为正式 mode 标识暴露给用户。
- Q4：采用 capability 协商 + fail closed；后续只保留一种协议。

**Q1 vs Q3 已确认（2026-11-03）：采用方案 A**
- `TaskRecord.mode=goal` 在 goal task 生命周期内保持不变；
- goal 段内可以创建/批准 `kind=plan` 的**子任务或 plan artifact**（走 12/13），而不是切换 `task.mode`；
- 不采用方案 B（段边界切换 mode）。

**Q5 已确认（2026-11-03，采用 Lead 建议）**
- 完成判定切换时机：等 15 的验证门 + 用户确认的 acceptance criteria 就绪后再全面切换；P0 保持 `done+verified`。
- 过渡期：有 `confirmed=true` 的 criteria 时用严格判定；没有时走旧语义并标 warning；全面切换用 feature flag（如 `VIBE_COMPLETION_CRITERIA=1`），在 15 前置切片验收后默认开。
- 入口 mode 记忆采用“项目级最近 mode + 会话覆盖”。

**实现备注**：本块无剩余待拍板项（mode 粒度、入口记忆、goal+plan 口径、协议协商、完成切换均已确认）。
