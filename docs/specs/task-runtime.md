# 任务运行时基础（TaskRecord / TaskStore）

日期：2026-11-03。状态：spec，待实现（P0 基础切片）；对应讨论稿 [01 任务运行时](../design/long-horizon/01-task-runtime.md) §9 已确认决策 D1。
本文只覆盖**持久化任务身份与生命周期**；stdio/TUI 接入、worker/attach、GoalRecord、预算账本、worktree 分别见后续 spec。

## 0 第一性原理与不变量

先不从“加一个 task 表”出发，而从约束推导：

1. **模型输出不是持久事实**：模型可以说“完成了”，但只有工具/环境证据可入账（[完成守卫与审计](verified-completion.md)）。因此任务状态必须由 harness 写，不能让模型文本直接改状态。
2. **单次 run 有硬上限**：`max_run_seconds`、token/费用、上下文窗口都会让一个长任务在中间停下。长任务必然由多个段组成，段之间需要一个稳定身份和状态，而不是靠 TUI 内存里的闭包。
3. **界面进程会退出**：TUI、CLI、worker 都可能是任务的一个视图或执行入口；任务不能绑在任一界面进程的生命周期上。
4. **未来有多个消费者**：TUI、CLI、后台 worker、eval/bench、子 Agent 都需要“任务”这一语义；如果每处各造一份 JSON，必然分叉。
5. **执行边界不变**：TaskRuntime 只保存/推进任务状态，**不执行工具、不审批、不碰工作区**；所有副作用仍走 `Agent` + `ApprovalGate` + 沙箱。

由此得到本切片的不变量：

| # | 不变量 |
|---|---|
| I1 | 任务状态只通过 `TaskStore`/`TaskRuntime` 的显式状态迁移写入；模型/界面不能直接改 meta |
| I2 | 任务身份持久化在会话目录内；进程崩溃、TUI 退出后仍可 `get/list` 到同一 `task_id` |
| I3 | 元数据写入原子（temp + `os.replace`）；事件 append-only，不允许原地改写历史 |
| I4 | 同一 task 同一时刻最多一个写者/活跃段；双开必须被拒绝 |
| I5 | 非法状态迁移被拒绝并返回明确错误，不静默吞掉 |
| I6 | 崩溃恢复只把“运行中但写者已死”的任务标为 `interrupted` 并等用户确认；**不自动重放已执行段** |
| I7 | 损坏的 meta 不阻断 `list`；`get` 明确报错；不静默删任务/事件 |
| I8 | 不改变现有 `run`/`/goal` 行为；本切片通过显式 API 使用，接入由下一 spec 完成 |

## 1 目标与非目标

**目标（P0 基础）**

- 定义稳定的 `TaskRecord` v1 schema：任务身份、`kind`、`mode`、状态、workspace、预算/审批声明、段账本引用。
- 实现 `TaskStore`：create / get / list / 状态迁移 / 段记录 / 事件追加 / 锁 / 陈旧检测。
- 任务目录布局、原子写、损坏跳过与并发规则。
- 为新代码提供纯 Python API（先不接协议/TUI）。
- 测试先行：新增 `tests/test_task_store.py`，不改变现有 1195 项回归的语义。

**非目标（后续 spec）**

- stdio JSONL 的 `run.task` / `task` 事件 / `--task-id`（`task-runtime-integration.md`）；
- worker、attach、supervisor、心跳、TUI 列表（02 后续）；
- GoalRecord、Plan、预算账本、审批收件箱、worktree（05/06/03/04/12）；
- 任务调度/队列/并行执行；本切片只定义状态与存储。

## 2 数据模型

### 2.1 TaskRecord（`meta.json`，schema_version=1）

```jsonc
{
  "schema_version": 1,
  "task_id": "t-20261103-142501-5f3a9c",   // UTC compact + 6 hex，创建后不可变
  "session_id": "20261103-142500-123456",  // 所属会话（目录名）
  "kind": "goal",                           // goal | run | plan | subagent
  "status": "running",                      // 见 §4
  "objective": "完成跨文件重构并跑通全量测试", // ≤4000 字符，用户/harness 输入
  "mode": "goal",                           // goal | act | plan | chat
  "parent_id": null,                        // 子任务指向父 task_id
  "goal_id": "g-20261103-142501",           // 可空；与 05 GoalRecord 对齐
  "workspace": "/abs/path",                 // 解析后的绝对路径
  "budget": {                               // 06 的声明侧；task runtime 只保存
    "max_run_tokens": 1000000,
    "max_goal_tokens": 0,                   // 0=不设 task 硬上限（global safety cap 仍单列）
    "max_wall_seconds": 0
  },
  "approval_policy": {                      // D3/D9
    "autonomy": "ask",                      // ask | edits | workspace | full
    "scope": "own",                         // 本 task 的 pre-auth capability 声明，后续扩展
    "capabilities": ["sandbox_exec"]        // 可为空；写/网络/浏览器能力由 09 定义
  },
  "segments": [],                           // SegmentRecord[]，append-only
  "result_ref": null,                       // 终态引用：rounds/round_NNN.json 或 plan/…
  "created_at": "2026-11-03T14:25:01Z",
  "updated_at": "2026-11-03T14:25:01Z"
}
```

约束：

- 未知字段：读者忽略、写者保留（前向兼容）；`schema_version` 不匹配拒绝。
- `task_id` 唯一；`workspace` 必须是绝对路径；`objective` 非空且 ≤4000 字符；`kind`/`mode`/`status` 必须来自枚举。
- `account_id`/`parent_id`/`goal_id` 只做引用，不在此切片校验目标存在性（父任务校验在 P1 接入）。
- 不存工具输出、命令正文、文件正文；只存引用与证据摘要。

### 2.2 SegmentRecord（嵌在 `segments` 中）

```jsonc
{
  "segment": 1,
  "run_id": "ab12cd34",
  "status": "running | stopped | done | failed | cancelled | interrupted",
  "stop_reason": "max_run_seconds",   // 可空；复用 AgentResult.stop_reason
  "round_ref": "rounds/round_003.json",
  "started_at": "…",
  "ended_at": "…"
}
```

段号从 1 递增且不可复用；`run_id` 一次 run 一个；`round_ref` 可空（run 未产生轮次记录时）。`segments` 只追加，终止段不得改写（只能追加新段）。

### 2.3 TaskEvent（`events.jsonl`，只追加）

```jsonc
{"schema_version":1,"task_id":"t-…","seq":1,"ts":"…","event":"task_created","actor":"harness","data":{}}
{"schema_version":1,"task_id":"t-…","seq":2,"ts":"…","event":"segment_started","actor":"harness","data":{"segment":1,"run_id":"ab12cd34"}}
```

P0 事件至少：`task_created`、`segment_started`、`segment_ended`、`task_paused`、`task_resumed`、`task_completed`、`task_failed`、`task_cancelled`、`task_interrupted`、`lock_acquired`、`lock_released`、`lock_stale_recovered`。
`seq` 由 store 在该 task 内单调递增；`actor` ∈ `user|harness|worker|tui`（P0 写 `harness` 为主）。事件只用于审计/恢复，不替代 meta 的当前状态。

## 3 存储布局与原子性

```
.vibe/sessions/<session_id>/
  meta.json
  events.jsonl
  state.json
  rounds/
  tasks/
    <task_id>/
      meta.json        # TaskRecord，原子替换
      events.jsonl     # append-only TaskEvent，一行一个
      lock             # 活跃写者锁（JSON：pid/host/created_at）；无则为未锁
```

规则：

- **tasks 根**：`SessionStore.root / "tasks"`；不新增外部目录；`.vibe/` 已 gitignore。
- **原子 meta**：写 `meta.json.tmp` → flush → `os.replace`；失败保留旧 meta，清理 tmp；启动时忽略残留 `.tmp`。
- **事件追加**：`open(..., "a")` 写一行 JSON，flush；不以多行 split；读时跳过损坏行并计数（不删）。
- **锁**：`O_CREAT|O_EXCL` 创建 `lock`，内容 `{pid, host, created_at}`；释放用 unlink。P0 不做心跳；`recover_stale()` 依据 pid 是否存活 + host 相同判断 stale（host 不同不自动回收，交人工）。
- **损坏**：`meta.json` JSON/字段非法 → `list` 跳过并在返回结果中带 `skipped` 计数（不静默）；`get` 抛 `TaskCorrupt`。`events.jsonl` 单行损坏跳过，不影响 meta。
- **路径安全**：`task_id` 必须匹配 `^t-\d{8}-\d{6}-[0-9a-f]{6}$`；读取时拒绝 `..`/符号链接；任务目录必须位于 `tasks_root` 下。

## 4 状态机

状态：`created | queued | running | paused | completed | failed | cancelled | interrupted`。

| 当前 | 允许迁移 | 触发 | 说明 |
|---|---|---|---|
| created | running, cancelled | start / user cancel | P0 可跳过 queued；queued 预留给 02 调度 |
| queued | running, cancelled | worker/scheduler（后续） | P0 不主动产生 |
| running | paused, completed, failed, cancelled, interrupted | 段结束/停止/崩溃 | `interrupted` 需 recover 或显式标记 |
| paused | running, cancelled, failed | resume / user cancel | resume 必须重新拿锁 |
| interrupted | running, paused, cancelled | 用户显式恢复 | 不自动迁移 |
| completed | — | 终态 | 不可再迁移；重复 complete 幂等返回 |
| failed | — | 终态 | 不可再迁移；重复 fail 幂等返回 |
| cancelled | — | 终态 | 不可再迁移；重复 cancel 幂等返回 |

规则：

- 非法迁移抛 `InvalidTaskTransition`，**不写 meta/事件**；
- 终态重复设置同一状态视为幂等成功（不重复写事件）；
- 每次迁移同时 append 事件（`task_*` / `segment_*`），meta 的 `updated_at` 更新；
- `pause(reason)` 必须来自 running；`resume` 只允许 paused/interrupted；`complete/fail/cancel` 的所有权由调用方声明，但 store 只校验状态；
- **P0 不自动续段**：`segment_ended` 后由调用方决定 `pause` 还是迁移终态。

## 5 API（P0）

```python
class TaskStore:
    def __init__(self, session_root: str | Path): ...
    def create(self, *, kind, objective, workspace, mode,
               budget=None, approval_policy=None, parent_id=None,
               goal_id=None, session_id=None) -> TaskRecord
    def get(self, task_id: str) -> TaskRecord          # 不存在 TaskNotFound；损坏 TaskCorrupt
    def list(self, *, status=None, kind=None) -> list[TaskRecord]
    def transition(self, task_id, target, *, reason=None, data=None,
                   actor="harness") -> TaskRecord
    def append_event(self, task_id, event, *, data=None, actor="harness") -> None
    def start_segment(self, task_id, *, run_id) -> SegmentRecord
    def end_segment(self, task_id, *, run_id, status, stop_reason=None, round_ref=None) -> SegmentRecord
    def acquire_lock(self, task_id) -> None            # running/paused 前置检查在 runtime 层
    def release_lock(self, task_id) -> None
    def recover_stale(self) -> list[TaskRecord]        # running + stale lock → interrupted
```

- `TaskRuntime`（P0 可为薄封装）负责：create 后 `running`、段开始/结束、按 AgentResult 映射迁移；具体映射在 `task-runtime-integration` spec 定义。
- `create` 只写 meta + `task_created` 事件，不拿锁、不启动执行。
- `get/list` 只读；`list` 按 `created_at` 升序，损坏项跳过并计数。
- `start_segment` 要求 status ∈ {created, running, paused} 且无活跃段；否则 `TaskBusy`/`InvalidTaskTransition`。
- `end_segment` 校验 `run_id` 是当前活跃段；写入 SegmentRecord 的终态；随后调用方决定 task 状态。
- `recover_stale` 只处理 running 且锁 stale 的任务：append `lock_stale_recovered` + `task_interrupted`，状态置 `interrupted`；**不执行、不重放**。
- 所有写方法在拿不到文件锁时可重试一次或抛 `TaskBusy`；不得静默并发写。

## 6 失败、崩溃与恢复

| 场景 | 行为 |
|---|---|
| 写 meta 时崩溃 | 旧 meta 有效；tmp 残留被忽略；下次写覆盖 tmp |
| 写事件时崩溃 | 最多一行不完整；读取跳过并计数；meta 不受影响 |
| run 中被 kill -9 | meta 仍为 running、lock 残留；下次启动 `recover_stale()` → interrupted + 事件；不自动重跑 |
| 同一 task 双开 | `acquire_lock` 失败 → `TaskBusy`；不创建第二段 |
| 未知/损坏 meta | list 跳过并计数；get 抛错；不删除文件 |
| 事件 seq 断裂 | 读取按 seq 排序；缺失只报告，不补写 |
| 磁盘满/权限 | 抛 `TaskIOError`；不改变旧 meta；调用方负责展示 |

恢复原则：**能从事件重放出“哪些段结束、最后状态是什么”，但绝不从事件重放执行**。

## 7 边界与兼容

- TaskStore 属 `app/runtime/`，只依赖标准库 + `core`；不 import `app.stdio`/TUI/审批实现。
- 不执行工具、不读工作区文件内容、不调用模型；`workspace` 只做路径记录/校验。
- 任务目录只存元数据与事件，不存正文/命令输出/模型消息；证据引用走 `round_ref`/`result_ref`。
- **兼容**：本切片不启用任何自动创建路径；`VIBE_TASK_RUNTIME` 的读取与接入在下一 spec。旧 session 没有 `tasks/` 时 `list()` 返回空；不迁移 `goal.json`。
- eval/bench/oneshot 不创建 task（下一 spec 明确 enforce）。
- `schema_version=1`；新增可选字段向前兼容，删除/改义字段必须升版本并提供迁移。

## 8 可观测性与证据

- 每个 task 的事件带 `task_id/seq/actor/event/ts`；以后 `/task` 与 07 的 `task_snapshot` 直接读它。
- `meta.json` 与 `events.jsonl` 可离线回答：任务何时创建、跑了哪些段、每段 stop_reason、最终状态与结果引用。
- 不在事件里写模型文本、命令输出或审批预览；需要时引用 round/session 文件。

## 9 测试计划（先写测试）

新增 `tests/test_task_store.py`：

1. create/get/list 往返；schema 字段完整；task_id 格式与唯一性；非法 kind/mode/objective/workspace 被拒。
2. 原子写：monkeypatch `os.replace` 失败，旧 meta 仍可读，tmp 清理。
3. 损坏 meta：list 跳过并计数，get 抛 `TaskCorrupt`；损坏 events 行跳过。
4. 状态机：所有合法迁移；非法迁移抛错且无写入；终态幂等；重复 complete 不重复事件。
5. 锁：双开 `TaskBusy`；release 后可再拿；stale lock（dead pid / 同 host）→ `recover_stale` → interrupted；异 host 不自动回收。
6. 段：start/end 正常；run_id 不匹配拒绝；段号递增；终止段不可改。
7. 路径安全：`task_id` 含 `..`/符号链接/越界路径拒绝。
8. 并发：两个任务并行 create/list 不互相破坏；同一 task 并发 start 只有一个成功。
9. 无回归：`./run_tests.sh -q` 现有 1195 项通过；不产生 `.vibe/tasks` 副作用（除测试临时目录）。

（崩溃注入可在测试里用子进程 `os._exit` 或伪造 lock 文件，避免依赖真实 kill。）

## 10 验收标准

- 新测试全部通过，全量回归通过；
- 给定一个临时 session 根，`TaskStore.create/get/list/transition` 可按本文行为工作；
- kill -9 模拟后，`recover_stale()` 把 running 任务置为 interrupted，事件可解释，且不执行任何动作；
- 双开被拒绝；损坏 meta 不拖垮 list；
- 现有交互路径（TUI/CLI/eval/bench）行为与 HEAD 一致（本切片不被任何路径自动调用）；
- `git diff` 只包含新 spec/实现/测试/索引更新，不改 Agent loop 与工具。

## 11 实现顺序与后续 spec

1. 写 `tests/test_task_store.py`（红）；
2. 实现 `app/runtime/tasks.py`（dataclass + 校验 + store + lock + recover）；
3. 更新 `docs/STRUCTURE.md`（`app/runtime/tasks.py`、`tests/test_task_store.py`）与 README 文档索引；
4. 全量回归；
5. 下一 spec：`task-runtime-integration.md`（stdio `run.task`、`--task-id`、TUI `/goal`、`/task` 最小命令）；随后 `process-supervision`（02）、`goal-record`（05）、`unattended-approval`（03）、`worktree-isolation`（04）。

## 12 风险与未决

- **锁没有心跳**：P0 用 pid+host 判断 stale，跨重启/跨 host 场景需 02 的 heartbeat 补充；误判时只允许人工 `recover`。
- **task_id 生成**：当前格式含时间与随机 hex；如果未来需要跨机可读，需重新评估（P0 本地单用户）。
- **meta 字段**：`budget/approval_policy` 只是声明，实际执行点由 06/03 决定；本 spec 不校验业务值范围。
- **与 GoalRecord 的边界**：`goal_id` 只是引用；GoalRecord 的 schema/双写由 05 决定。
- **API 形态**：P0 先 Python 内部类；是否暴露为 CLI/协议命令由 integration spec 决定。
- **是否支持 queued**：P0 保留状态但无调度器；如果 02 决定不用 queued，可向后兼容地不产生该状态。
