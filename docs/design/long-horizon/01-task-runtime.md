# 01 任务运行时

> 状态：讨论稿（2026-11-02；2026-11-03 部分决策已确认，见 §9）；不是 spec，未承诺实现。
> 所属：长任务与后台任务 / 第 1 块（任务模型与编排权）
> 实现：P0 基础（TaskRecord/TaskStore）与 stdio/TUI 接入（`/goal`）已实现，见 [task-runtime spec](../../specs/task-runtime.md) 与 [integration spec](../../specs/task-runtime-integration.md)；worker/attach 见后续 spec。
> 前置/相关：[02 后台进程监督](02-process-supervision.md)、[05 跨段目标状态](05-goal-state.md)、[06 预算账本](06-budget-ledger.md)；相关规格：[task-runtime 实现 spec](../../specs/task-runtime.md)（P0 基础）；[goal-mode](../../specs/goal-mode.md)、[multi-agent-computer-use](../../specs/multi-agent-computer-use.md)

## 1 问题与范围

现状的“长任务”不是运行时能力，而是 TUI 里的一段纯函数循环：`/goal` 在每段 run 结束后调用 `goalDecision` 决定是否再发一次 `run` 帧（tui/src/app.tsx:250-259）。Python 后端只是每次 run 的执行器，不持有“任务”这个概念；TUI 一退出，编排与任务身份一起消失。

本文要回答的是：**任务由谁创建、谁保存、谁推进、谁可以被 attach**。范围：

- 任务（task）模型：goal 段、一次显式 run、未来的 plan/子 Agent 统一成什么数据结构；
- 编排权归属：继续放 TUI，还是抽到 Python 侧运行时；
- 任务 API：create / start / pause / resume / cancel / list / attach；
- 单工作区内的并发边界（串行还是允许两个后台任务）。

不在本文：进程怎么托管（02）、目标语义与验收标准（05）、钱和 token 怎么记（06）、审批归属（03）、协议帧（07）、worktree 隔离（04）。

## 2 现状与证据

**目标编排完全在 TUI（现状）**

- 目标状态与段决策是 TS 纯函数：`GoalMode` 字段、`goalDecision`、`resumeGoal`（tui/src/goal.ts:3-16,75-137）；目标持久化到会话目录 `goal.json`（tui/src/goal-store.ts:5-23）。
- 每段都是一次普通 `run` 帧；TUI 在 `done` 事件后同步判断并立即发下一段（tui/src/app.tsx:196-212,234-259）；给模型的续段提示词由 TS 拼接（tui/src/goal.ts:57-73）。
- 规格明确该切片“只做 Ink 工作台编排，不改变后端 Agent loop”（docs/specs/goal-mode.md:9）。

**Python 侧只有单次 run（现状）**

- `Agent.run(task)` 接收一段任务文本；首次 run 用文本初始化 `TaskState(goal=...)`，之后任务文本只是历史里的一条 user 消息（app/runtime/agent.py:568-593,617-619）。
- 后端进程一个连接、一个活跃 run：第二条 run/command 帧直接 `error busy`（app/stdio.py:633-642）；`serve()` 的定位就是“单连接 JSONL 适配器”（app/stdio.py:699）。
- 协议能力只有 run/cancel/steer/approval/command/session 记录（app/stdio.py:725-728），请求类型只有 run/cancel/steer/command/approval/shutdown（app/stdio.py:598-603）；没有 task、attach、list 之类原语。
- 会话身份由 stdio 在 `ready` 里带出（app/stdio.py:735-742），目录结构见 app/session/store.py:24-56；轮次记录按 run 追加（core/task/rounds.py:21-82；app/session/store.py:342-371），`/evidence` 只读最近一轮（app/stdio.py:342-343）。

**其它入口同样是一次性（现状）**

- CLI 只有 `--stdio`/`--eval`/`--task`/`--continue`/`--session` 等一次性模式（app/__main__.py:69-95）；`--task` 走 oneshot，构造 Agent 后 `asyncio.run` 一次并退出（app/__main__.py:144-159；app/eval/oneshot.py:100-184,230-236）。
- TUI 退出会关闭后端（tui/src/cli.tsx:132-144；详见 02），因此任何“后台长任务”在现状下都不成立。

小结：**编排权在 TUI，权威执行/审批/证据在 Python，任务身份不存在**。长任务只在本进程 TUI 存活期间成立。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 无任务实体 | 只有 run/task 文本与 session 目录；goal 段只存在于 TUI 内存与 `goal.json` | TUI 重启后只能靠 `/goal resume` 人为续段；其他入口无法看到“有一个进行中的任务” |
| 编排权错位 | 段决策、续段提示词、目标上限判断都在 TS（tui/src/goal.ts:75-130） | 后台 worker、CLI、eval 无法复用同一套长任务语义；Python 无法校验“目标是否达成” |
| 无任务队列/调度 | `busy` 拒绝并发（app/stdio.py:633-635）；`/followup` 是 TUI 本地数组（tui/src/app.tsx:70,86-96,758-772） | 关闭 TUI 或崩溃即丢排队；无法表达“先做 A 再做 B” |
| 无 attach/list | 协议无 task 帧、无重连（app/stdio.py:598-603） | 无法从第二终端查看/接管正在跑的任务 |
| 归属与父级缺失 | run 只有 run_id，无 parent_id/goal_id（core/task/rounds.py:48-64） | 多段、未来子 Agent 的归属无法从数据回溯 |

## 4 设计选项与推荐

| 方案 | 编排权 | 后台/恢复 | 改动面 | 风险 |
|---|---|---|---|---|
| A 保留 TUI 编排 + 后台开关 | TUI（TS 仍是唯一状态机） | 由 TUI 进程托管一个 headless runner；TUI 崩溃仍失联 | 小：只加进程开关 | 编排逻辑出现 TS/Python 两份；非 TUI 入口仍不支持；与 05/06 的 Python 权威冲突 |
| **B 抽 Python TaskRuntime（推荐）** | Python 任务目录 + 生命周期状态机 | 任务状态在磁盘；worker 续跑；任何客户端可 attach | 中：新增 `app/runtime/tasks.py` 等；stdio 增加最小 task 能力 | 需要定义兼容窗口（P0 默认开关关闭，前台路径不变） |
| C 外部服务/daemon | 独立进程 + HTTP/IPC | 最完整 | 大：新部署面、鉴权、版本兼容 | 超出当前单用户本地工作台的需求 |

**推荐 B**，理由：

1. 权威事实已经在 Python：审批（ApprovalGate）、预算上限、完成守卫、trace/rounds 都由 Python 产生（app/runtime/agent.py:1536-1599；core/task/state.py:53-86）。编排如果继续留在 TS，就必须跨语言复制这些判定。
2. 02 的 worker 需要“任务目录 + 生命周期”，05 的 GoalRecord 需要 Python 可读，06 的账本需要按 task 聚合；三者都以 TaskRuntime 为宿主，避免各造一套。
3. 迁移可渐进：P0 默认关，TUI 旧 `/goal` 行为不变；打开开关后 TUI 退化为视图/控制器，仍通过既有 `run` 帧语义工作。

不推荐 A 作为终态（可作为 P0 过渡“双写”），不推荐 C（当前无多用户/远程需求）。

## 5 最小实现切片

### 任务 schema（草案）

```jsonc
{
  "schema_version": 1,
  "task_id": "t-20261102-143000-abc123",
  "kind": "goal | plan | run | subagent",
  "parent_id": null,                 // 子任务指向父 task_id
  "goal_id": "g-20261102-143000",    // kind=goal 或 run 时可为空
  "objective": "完成跨文件重构并跑通全量测试",
  "mode": "goal | act | plan | chat",
  "status": "created | queued | running | paused | completed | failed | cancelled",
  "workspace": "/abs/path",
  "budget": {"max_run_tokens": 1000000, "max_goal_tokens": 0, "max_wall_seconds": 14400},
  "approval_policy": {"autonomy": "ask", "scope": "own"},
  "segments": [{"segment": 1, "run_id": "ab12cd34", "status": "stopped",
                 "stop_reason": "max_run_seconds", "round_ref": "rounds/round_003.json"}],
  "result_ref": "rounds/round_003.json",
  "created_at": "2026-11-02T14:30:00Z", "updated_at": "2026-11-02T15:06:00Z"
}
```

### 任务 API（草案，先 Python 内部模块，后接协议）

```text
tasks.create(kind, objective, *, workspace, budget, approval_policy, parent_id) -> TaskRecord
tasks.start(task_id) -> SegmentHandle          # 后台拉起 worker（02）；前台入口也复用同一目录
tasks.pause(task_id, reason) / tasks.resume(task_id) / tasks.cancel(task_id)
tasks.list(kind=None, status=None) -> [TaskRecord]     # 只读索引，来自 sessions/*/tasks/*/meta.json
tasks.get(task_id) -> TaskRecord
tasks.attach(task_id) -> event stream          # P0：本地 socket 透传既有 JSONL；帧设计见 07
```

### 切片

- **P0**：新增 `app/runtime/tasks.py`（TaskStore：目录、原子写 meta、append-only events、`list/get`）；`--task-id` 入口复用现有 stdio 循环；TUI 增加 `VIBE_TASK_RUNTIME` 开关：按 §9 决策默认覆盖 `/goal` 与长任务（`VIBE_TASK_RUNTIME=0` 可回退旧行为），打开时 `/goal` 每段改由 runtime 创建/推进。
- **P1**：`pause/resume/cancel/list/attach` 完整 API；任务索引用于 TUI 列表；段决策从 `goal.ts` 迁到 Python（与 05 一起做），TUI 只渲染 `TaskRecord`。
- **测试点**：TaskStore 原子写/损坏跳过；非法状态迁移被拒绝；同一 task 不能并发 start（锁/pid）；跨进程 list 可见；`/goal` 开关关闭时回归 tui/tests/goal.test.ts（docs/specs/goal-mode.md:46-48）；中断后 `tasks.get` 状态可解释。
- **明确不做**：同工作区并行写任务、跨机/远程控制、自动无限重试、协议 v2 全量设计（07）、审批策略变更（03）、worktree 隔离（04）。

## 6 与其它块的关系

- **02** 是 01 的执行载体：01 定义任务目录与生命周期，02 定义谁托管进程、如何 attach/恢复。
- **05** 是 `kind=goal` 任务的状态载荷：TaskRuntime 只负责“任务存在与推进”，目标语义/验收/段账本由 GoalRecord 提供。
- **06** 以 `task_id`/`goal_id`/`segment` 为维度记账；上限执行需要 01 提供“开始下一段前检查”。
- **03/04/07/08**（本目录其它块）：审批归属、隔离并发、协议与评测都消费 01 的任务身份；01 不替它们做决定。

## 7 与子 Agent 的关系

按统一三维：**A 谁发起**——P0 只允许用户显式（`/task`、`/goal`）与 harness 内部（段推进）；模型工具 `delegate` 属 M1 后续，默认关闭并受 01 任务开关约束。**B 能力边界**——第一版子 Agent 只读（现有原型是 app/session/auditor.py 的只读审计子 Agent，工具子集 + `approval=None`，app/session/auditor.py:250-272）；独立 worktree 写排后；共享工作区写不做。**C 结果可信度**——子 Agent 的文本是 untrusted claim，父侧必须用证据（文件读取、命令退出码、审计结论）复核后才写进 GoalRecord。

对 01 的直接影响：子 Agent 未来是 `kind=subagent` 的任务节点（`parent_id` 指向父 task），不是主循环的特殊分支；它有自己的预算条目（06）、工具子集（02 的独立 worker 或同进程子运行）、审批归属（03）与 trace（07）。规格见 docs/specs/multi-agent-computer-use.md:284-340,402-427。

## 8 开放问题与验收标准

待用户拍板：

1. **编排权**：接受 Python TaskRuntime 为权威、TUI 退化为客户端吗？还是坚持“界面即编排者”？

回答：按照你的建议，接受 Python TaskRuntime 为权威

2. **默认形态**：后台任务默认开启还是显式 `--background`/`/goal --bg` 才启用？

回答：后台任务默认开启，tui要现实执行的后台任务，另外后台任务的回收退出要设计好

3. **任务粒度**：只给 `/goal` 建 task，还是 run/plan/子 Agent 全部建 task（推荐全部，但 P0 只做 goal/run）？

回答：按照你的建议全部建设，可以按照优先级逐步推进

4. **并发**：同一 workspace 是否允许两个后台任务（非写冲突场景，如一个只读调查）？默认串行是否符合预期？

回答： 可以一开始先允许两个后台任务，先默认串行，后续可以又主Agent根据任务情况指定并行

5. **生命周期**：TUI 关闭后 worker 继续，还是要求显式 detach？自动重启策略是否需要？

验收条件（建议）：

- 后台跑一个 `/goal`，关闭 TUI 后 worker 继续推进段；重新打开可 `list` 并 `attach` 看到完整事件回放。
- 任务记录在磁盘可解释：任一时刻 `tasks.get` 给出 status、当前段、stop_reason 与结果引用。
- 旧路径零回归：默认关闭时 `/goal`、`/followup`、`/resume` 行为与现行规格一致（docs/specs/goal-mode.md）。
- 同一 task 不会双开；异常退出留下可判定的 stale 状态而不是静默孤儿。

## 9 决策记录（2026-11-03）

用户已确认（对应 §8）：

1. **编排权**：接受 Python TaskRuntime 为权威，TUI 退化为客户端/视图。`/goal` 的段决策与续段提示词从 `tui/src/goal.ts` 迁到 Python（P1）；P0 先实现任务身份与最小 task API。
2. **任务粒度**：run / goal / plan / subagent 全部建模为 task，按优先级逐步推进；P0 只做 goal/run，schema 的 `kind` 保持可扩展。
3. **后台默认**：后台任务默认开启，TUI 展示正在执行的后台任务；范围采用建议（P0 覆盖 `/goal` 与显式长任务，普通 `--task`/eval 保持一次性，`VIBE_TASK_RUNTIME=0` 回退），见下方已确认。
4. **并发**：起步允许同时存在两个后台任务；写任务默认串行（见 04 单写者），只读任务可并发；后续由主 Agent 按任务声明决定并行。
5. **TUI 关闭与回收（Q5，已确认）**：
   - TUI 关闭且存在运行中任务时提示：`转入后台 / 等待完成 / 停止任务并退出 / 取消关闭`。确认后 worker 继续，TUI 断开不等于取消。
   - worker 正常终态写完结果后退出；TUI 重启/启动时检测到运行中任务，提示 `attach / resume / cancel`，不自动重跑。
   - 不自动重启已产生副作用的段；只有“启动阶段、尚未执行任何动作”的 worker 可由 supervisor 标记 `interrupted`，等待用户确认。
   - 后台任务必须有 pid/lock/心跳/日志；孤儿进程由 supervisor 按心跳回收并记录原因。

**已确认（2026-11-03）**：Q2 后台默认开启范围采用建议——P0 覆盖 `/goal` 与显式长任务（`VIBE_TASK_RUNTIME=0` 可回退旧行为），普通 `--task`/eval 仍默认一次性，避免评测口径漂移。
