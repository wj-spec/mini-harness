# 任务运行时接入（stdio / TUI `/goal`）

日期：2026-11-03。状态：P0 实现完成（Python stdio + TUI `/goal` + 测试）；worker/attach 在后续切片。依赖已完成的 [P0 基础](task-runtime.md)。
对应讨论稿：[01 任务运行时](../design/long-horizon/01-task-runtime.md)（§9 已确认）、[07 控制面与协议](../design/long-horizon/07-control-plane.md)（§9/§10 已确认）、[goal-mode](goal-mode.md)（现状）。
本文只做“**让既有 `/goal` 用上 TaskRecord**”的最小闭环；worker/attach、`/task` 列表、GoalRecord、预算执行、worktree 不在本切片。

## 0 第一性原理与不变量

1. **任务身份不能由界面持有**：TUI 的 `GoalMode` 只是视图状态；任务 id、状态、段账本必须由 Python `TaskStore` 写。TUI 重开/崩溃后，任务仍在。
2. **协议只增加必要字段**：现有 v1 是严格字段校验；本切片不能破坏普通 run。新增字段必须是**可选**的，旧行为在缺省路径下逐字节不变。
3. **服务端是权威**：任务由 Python 创建并返回 `task_id`；TUI 只保存并回传，不自行生成任务 id。
4. **单写者**：一次 run 对应一个 active segment；任务锁在 run 期间持有，段末释放；不并发写同一 task。
5. **失败不静默**：任务元数据写失败时给出结构化错误；崩溃留下 `running + stale lock`，下次启动标 `interrupted`，不自动重放。
6. **默认范围按已确认决策**：`/goal` 与显式长任务走任务运行时；普通 `--task`/eval/bench 一次性，不产生 task。

本切片不变量：

| # | 不变量 |
|---|---|
| J1 | 未传 `run.task` 的请求行为与 HEAD 完全一致，不创建 `tasks/` 目录 |
| J2 | 传了 `run.task` 且运行时开启时，服务端先落 TaskRecord 再开始 Agent run；失败则不启动 run |
| J3 | TUI 只在 `ready.capabilities` 含 `tasks` 时发送 task 元数据 |
| J4 | 同一 task 的两次 run 不能同时活跃（P0 由 stdio 单活跃 run + task lock 双保险） |
| J5 | `VIBE_TASK_RUNTIME=0` 时服务端不广告 `tasks`，收到 task 元数据明确拒绝 |
| J6 | 任务终态/暂停由 AgentResult 映射，不由模型文本决定 |
| J7 | 事件顺序：`task_state(created/running)` → 普通 run 事件 → `task_state(paused/completed)` → `done` |

## 1 范围与目标

**P0（本切片）**

- stdio `run` 帧新增可选 `task` 字段；
- 服务端 `TaskRuntime`：create/attach、acquire lock、start/end segment、状态映射、`recover_stale`；
- 新增 `task_state` 服务端事件，携带最小任务快照；
- `ready.capabilities` 增加 `tasks`（受 `VIBE_TASK_RUNTIME` 与 session purpose 控制）；
- TUI `/goal`：发送 `task` 元数据、保存 `taskId`、处理 `task_state`；
- 普通 run、`--task`、eval/bench 不产生 task。

**非目标**

- `/task list|get|cancel` 命令、TUI 后台任务列表（07/P1）；
- worker 进程、attach、detach、重启接管（02/07）；
- `--task-id` CLI 入口（P1；等 worker 切片一起做）；
- GoalRecord/`goal-record.json` 迁移（05）；
- 预算/审批执行点变化（06/03）；任务运行时只记录声明。

## 2 协议增量（v1 兼容）

### 2.1 `ready.capabilities`

运行时开启且 session purpose 允许时追加 `"tasks"`；否则不出现。TUI 以 capability 为唯一开关，不读后端 env。

### 2.2 `run` 请求（可选字段）

```jsonc
{"v":1,"type":"run","id":"<uuid>","text":"…",
 "task": {                       // 可选；缺省 = 普通 run（J1）
   "kind": "goal",               // P0 只允许 goal
   "objective": "完成跨文件重构", // 首次创建必填；attach 时可省略
   "task_id": "t-20261103-…",     // 首次不传；后续段回传
   "goal_id": "g-20261103-…"      // 可选，GoalRecord 预留
 }}
```

校验：`task` 必须是对象；`kind` 只能是 `goal`（P0）；`objective` 非空 ≤4000；`task_id` 若出现必须匹配 TaskStore 格式；未知键拒绝（v1 严格风格）。`task` 出现而运行时关闭 → `error(code="task_runtime_disabled")`，不启动 run。

### 2.3 `task_state` 服务端事件（新增）

```jsonc
{"v":1,"type":"task_state","id":"<run request id>",
 "task":{"task_id":"t-…","kind":"goal","status":"running","segment":1,
         "objective":"…","goal_id":"…","updated_at":"…"}}
```

- `id` 绑定触发它的 run 请求（与普通 run 事件一致），便于 TUI 对账；
- 只含快照，不含工具输出/审批；
- 发送时点：创建并 start 段后、段结束映射后；未来 attach/list 可复用同一 payload；
- 旧客户端不认识该事件会按协议错误处理，因此只在 `tasks` capability 下发送。

## 3 服务端行为

### 3.1 TaskRuntime

新增 `app/runtime/task_runtime.py`（薄封装，复用 `TaskStore`）：

```python
class TaskRuntime:
    def __init__(self, store: TaskStore, *, enabled: bool, workspace: str): ...
    def prepare_run(self, request_id, meta: dict | None) -> RunTask | None
    def finish_run(self, run_task: RunTask | None, result: AgentResult) -> None
    def recover(self) -> list[TaskRecord]
    @staticmethod
    def payload(record: TaskRecord) -> dict   # task_state.task
```

`prepare_run`：

1. `meta is None` → 返回 None（J1）。
2. 未启用 → 抛 `TaskRuntimeDisabled`。
3. `task_id` 存在：`store.get`；校验 `kind=goal`、`workspace` 与当前 config 一致、`session_id` 一致；`acquire_lock`；`transition("running")`（created/paused/interrupted）或已是 running 则报 `TaskBusy`。
4. 无 `task_id`：`store.create(kind="goal", mode="goal", objective=…, workspace=…, goal_id=…)`，然后 `acquire_lock`、`transition("running")`。
5. `start_segment(run_id=request_id)`；返回 `RunTask(task_id, segment, objective)`。
6. 任一步失败：释放已拿的锁、若本次新建则把 task 标 `failed`（仅新建且未执行），抛结构化异常。

`finish_run` 映射：

| AgentResult | segment | task |
|---|---|---|
| `done` 且 `verified != False` | `done` | `completed`（终态） |
| `done` 且 `verified == False` | `done` | `paused(reason=unverified_completion)` |
| `stopped` | `stopped` + stop_reason | `paused(reason=stop_reason)` |
| `cancelled` | `cancelled` | `paused(reason=cancelled)` |
| `error` / `truncated` | `failed` | `paused(reason=error)` |
| 集成自身异常 | `failed` | `paused(reason=task_runtime_error)`，并发 `error` 事件 |

段结束后释放锁、追加 `task_state`。**`failed` 终态只留给“任务本身不可继续”**；普通暂停/错误保留 `paused` 以便 `/goal resume`。

### 3.2 stdio 接线

- `_Connection` 保存 `self.task_runtime` 与 `self.run_task`；`serve` 在创建 session 后构造 runtime，并调用 `recover()`（把 stale running → interrupted，发 `notice` 提示；不自动跑）。
- `accept` 的 `run` 分支：解析可选 `task`，先 `prepare_run`；成功后才 `send("task_state")` + `send("status running")` + create Agent run task；失败则 `error(...)`，`active_id` 不占用。
- `run` 的 `finally`：`finish_run` 必须执行；`task_state` 在 `done` 前发送（J7）。
- `command`/`cancel`/`steer` 语义不变；`cancel` 只取消 run，任务映射为 `paused`（`/goal stop` 语义），不做 task 终态自动取消。
- 运行时关闭时 `task_runtime=None`，`run.task` 报错；其它路径零变化。

### 3.3 会话与目录

- task 目录跟随 session：`.vibe/sessions/<id>/tasks/<task_id>/`；与 P0 基础 spec 一致。
- TUI 退出/后端关闭：若 run 被取消，映射为 paused；若进程被 kill，下次启动 `recover` → interrupted；都不自动重放。
- 普通 run / `--task` / eval/bench 不创建 tasks 目录；`purpose="test"` 默认不广告 `tasks`（避免评测污染），显式 env 才开启。

## 4 TUI `/goal` 接入

- `GoalMode` 增加可选 `taskId?: string` 并写入 `goal.json`；旧 goal.json 无该字段时按 undefined 处理（兼容）。
- `launchGoalSegment`：
  - 若 `ready.capabilities` 含 `tasks`：`client.run(goalPrompt, {kind:"goal", objective, ...(goal.taskId ? {task_id} : {})})`；
  - 否则保持旧 `client.run(prompt)`。
- 处理 `task_state`：
  - 若当前 goal 活跃且 `task.task_id` 不存在：写入 `goal.taskId` 并持久化；显示一次 notice（含 task id 短码）。
  - 若状态为 `interrupted`/`paused`/`completed`：更新底部 hint/目标状态展示；不自行恢复。
  - 事件找不到对应 goal 时只记 diagnostic，不影响对话。
- `/goal stop`：保持现有行为（取消当前 run）；任务由服务端映射为 paused，不删 task；下一次 `/goal resume` 带 `taskId` attach。
- `/goal resume`：恢复时继续用原 `taskId`；服务端 attach 失败（任务不存在/损坏）→ TUI 暂停目标并提示改用新 `/goal`。
- 普通输入 run（非 goal）不发 `task`，行为与 HEAD 一致。

## 5 开关与兼容

- `VIBE_TASK_RUNTIME`：
  - `0`：关闭；不广告 `tasks`；`run.task` → `task_runtime_disabled`；
  - 未设置或 `1`：开启；仅 `/goal`/显式长任务路径实际使用；
  - `VIBE_TASK_RUNTIME` 非法值按 `1` 处理并写 diagnostic（不静默 disable）。
- `session.purpose == "test"`：默认关闭 task capability；`VIBE_TASK_RUNTIME=1` 显式开启（用于集成测试）。
- eval/bench/oneshot 启动后端时注入 `VIBE_TASK_RUNTIME=0`；普通 TUI/CLI 不注入。
- 协议仍是 v1；`task_state` 是新增事件类型，旧 TUI 与新后端不同步时会被协议校验拒绝——因此 capability 协商要求同版本发布；07 的 v2 迁移在后续处理。

## 6 失败与恢复

| 场景 | 行为 |
|---|---|
| store 写失败（磁盘/权限） | `prepare_run` 抛 `task_runtime_error`，不启动 run；`finish_run` 失败发 error，但不改写 Agent 结果 |
| 同一 task 第二次 run 并发 | 锁冲突 → `task_busy` error；stdio 本身也只允许一个 active run |
| 后端被 kill -9 | 下次启动 `recover()`：running + stale lock → interrupted；发 notice，不自动跑 |
| TUI 在 goal 段中退出 | 后端随 TUI 退出，run cancelled → task paused；目标状态从 goal.json 恢复，`/goal resume` 带 taskId attach |
| task_id 不属于当前 session/workspace | 拒绝 `task_mismatch`，不启动 run |
| task 目录损坏 | `get` 抛 `TaskCorrupt` → error；不覆盖、不删除 |
| TUI 收到 task_state 但 goal 已换 | 只记 diagnostic，不写入新 goal |

## 7 安全与边界

- TaskRuntime 不执行工具、不审批、不读工作区内容；`workspace` 只做一致性校验。
- `task_state` 不包含命令输出、文件正文、审批预览或密钥；只含 id/状态/段号/objective。
- 任务元数据写失败不绕过 ApprovalGate：run 直接不启动，而不是“先跑后补记录”。
- 不新增网络面；stdio 仍是本地单连接。

## 8 测试计划

**Python（测试先行）**

- `tests/test_task_runtime.py`：prepare/finish 映射表逐行；task_id attach；workspace/session 不匹配；锁冲突；store 失败；recover。
- `tests/test_stdio_tasks.py`（socket/pipe 或 fake agent harness）：
  - 普通 `run` 不产生 `.vibe/sessions/*/tasks/**`（J1）；
  - `run.task` 创建 task 并先发 `task_state`，done 后 task `completed`；
  - 第二段带 task_id attach，段号递增；
  - stopped/cancelled 映射 paused；
  - `VIBE_TASK_RUNTIME=0`：ready 无 `tasks`，带 task 的 run 返回 `task_runtime_disabled`；
  - test purpose 默认关闭；显式开启可创建；
  - kill -9 模拟 stale lock → 新连接启动 notice + interrupted。
- 全量回归 `./run_tests.sh`。

**TUI（vitest）**

- `goal.test.ts`：GoalMode 新增 taskId 往返、旧 goal 兼容；
- `app.test.tsx`：`/goal` 在 capability `tasks` 下发出的 run 包含 task 元数据；无 capability 时不发；收到 task_state 写 taskId 并持久化；task_state interrupted 显示正确；
- 普通输入 run 不带 task 字段；
- `npm test`/现有 TUI 测试稳定。

## 9 验收标准

- 真实 TUI + fake provider 下，`/goal` 三段任务：每段 `.vibe/sessions/<id>/tasks/<task_id>/meta.json` 的 segments 依次递增，终态正确；TUI 重启后 goal.json 恢复 taskId；
- 普通 `--task`/eval/bench 回归：不产生 task 目录，ready 无 `tasks`；
- `VIBE_TASK_RUNTIME=0` 与 HEAD 行为一致（现测试全绿）；
- kill -9 后重启检测到 interrupted 并提示，不自动重放；
- 全量 Python 回归 + TUI 测试通过；现有 `docs/specs/goal-mode.md` 行为在运行时关闭时不变。

## 10 实现顺序与后续

1. Python：`TaskRuntime` + 单元测试（红→绿）；
2. stdio：`run.task` 解析/接线 + `task_state` + 启动 recover + 集成测试；
3. protocol.ts：`run.task`、`task_state` 类型与解析；后端 capability 门控；
4. TUI：GoalMode.taskId、launchGoalSegment、task_state 处理、goal-store 持久化 + vitest；
5. eval/bench 注入 `VIBE_TASK_RUNTIME=0`，全量回归；
6. 下一 spec：02 worker/supervisor（task 生命周期跨进程）；之后 05 GoalRecord、03 approval inbox、04 worktree。

## 11 风险与未决

- **协议 v1 加新事件**：旧客户端会拒绝未知事件；必须同版本发布，且只有 `tasks` capability 下发送。若需要更平滑，等 07 的 v2 双栈；本切片接受同版本。
- **task_state 与 done 的顺序**：约定 task_state 在 done 前；TUI 的 goal 完成逻辑在 done 触发，task_state 只更新 id/状态，不参与 goalDecision。
- **`/goal stop` 的任务语义**：本切片映射 paused（可 resume），不是 cancelled 终态；如果用户希望 stop=cancel，需要后续 `/task cancel`（07）。
- **test purpose 默认关闭**：与 eval 隔离一致；集成测试显式开。
- **taskId 持久化在 goal.json**：旧 goal 文件没有 taskId，首次运行新建任务；如果用户用旧 goal 续段，会得到新 task 而非旧 task，这是兼容代价。
- **`--task-id` 未做**：显式长任务入口留到 worker 切片；本切片只覆盖 `/goal`。
