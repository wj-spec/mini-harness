# 后台进程监督（Worker / Supervisor）

日期：2026-11-03。状态：P0a 基础实现完成（worker 状态/心跳/判死/`--tasks`）；P0b worker/attach CLI 基础实现（单 segment、socket 回放、审批入收件箱、无决定超时 → task failed(approval_timeout)）；P0c TUI 退出提示、后台 worker 启动、启动任务提示 + `/task` 已实现（TUI 内 attach 重连 UI 后续）。
对应讨论稿：[02 后台进程监督](../design/long-horizon/02-process-supervision.md)（§9 已确认）、[01 任务运行时](../design/long-horizon/01-task-runtime.md)。
依赖：[task-runtime](task-runtime.md)（TaskRecord/TaskStore）、[task-runtime-integration](task-runtime-integration.md)（run.task/task_state）。

## 0 第一性原理与不变量

1. **TUI 不能是监督者**：界面进程退出/崩溃不应决定任务生死；监督者必须是独立进程或可独立恢复的状态。
2. **进程身份必须外置**：仅靠内存里的 `Popen` 无法回答“谁在跑、是否还活着、为什么退出”；需要 pid + 锁 + 心跳 + 日志。
3. **一个任务一个执行者**：同一 task 同时只有一个 worker/active segment（01 的锁）；这是审批归属、恢复和预算归属的前提。
4. **恢复不是重放**：崩溃后只把任务标为 `interrupted` 并让用户确认；绝不自动重跑已执行段。
5. **先观察后控制**：attach 默认只读；审批/取消需要显式 controller 身份（02 §9 D3）。
6. **日志可追溯且不无限增长**：按会话保留 + 软上限/轮转；清理必须显式并写审计。

不变量：

| # | 不变量 |
|---|---|
| K1 | worker 状态文件写在 `.vibe/sessions/<session>/tasks/<task_id>/`；不写工作区源码 |
| K2 | 同一 task 双开被 pid/lock 拒绝；第二个 worker 退出码可区分 |
| K3 | 心跳过期 + pid 不存在 → 判死；running 任务标 `interrupted`，不自动重跑 |
| K4 | worker.log 与 events.jsonl 只追加；轮转/清理显式记录 |
| K5 | attach 默认 observe；只有 controller 能审批/steer/cancel；observer 只读 |
| K6 | TUI 退出不等于取消：确认“转入后台”后 worker 继续；选择“停止并退出”才 cancel |
| K7 | 前台 `--stdio` 路径零回归，worker/attach 是新增入口 |

## 1 范围与阶段

**P0a（本切片）监督基础**

- worker 状态文件 schema/原子写/心跳/判死；
- `WorkerSupervisor.snapshot()` 只读视图 + `recover()` 陈旧标记；
- `--tasks` CLI：列出当前 workspace 最近会话的任务与 worker 状态；
- 日志布局与轮转约定（实现留给 P0b 的 worker）；
- 不启动真实 worker、不改 TUI。

**P0b（下一 spec/实现）worker 与 attach**

- `python -m app --worker --task-id ... --session ...`：独立进程执行一个 task segment；
- Unix socket attach：回放 events + live 帧；`--attach --mode observe|control`；
- 审批由在线 controller 应答；无 controller 时按 03 策略 fail closed；
- pid/lock/心跳/日志落地，双开/SIGTERM/SIGKILL 测试。

**P0c（再下一）TUI detach**

- TUI 关闭时提示“转入后台 / 等待完成 / 停止任务并退出 / 取消关闭”；
- TUI 启动检测到运行中任务时提示 attach/resume/cancel；
- TUI 通过 attach socket 连接（不改 v1 帧语义）。

**非目标（本切片及后续初期）**

- OS 服务（launchd/systemd）、远程/多用户 attach、多主控制、自动无限重启；
- worktree 隔离/审批收件箱/预算执行（03/04/06 各自切片）。

## 2 状态与文件

```
.vibe/sessions/<session>/tasks/<task_id>/
  meta.json          # 01 的 TaskRecord（状态/段账本）
  events.jsonl       # 01 的事件
  worker.json        # WorkerState（本 spec；原子替换）
  worker.pid         # 当前 worker pid + started_at（O_EXCL 创建）
  worker.lock        # 双开锁（O_EXCL；worker 退出时删除）
  worker.log         # stdout/stderr/诊断；按大小轮转
  ctrl.sock          # P0b 的 Unix socket（P0a 不创建）
```

### WorkerState（`worker.json`，schema_version=1）

```jsonc
{
  "schema_version": 1,
  "task_id": "t-…",
  "pid": 12345,
  "host": "hostname",
  "status": "starting | running | waiting_approval | stopping | exited | failed | killed | interrupted",
  "started_at": "…",
  "heartbeat_at": "…",
  "exit_code": null,
  "error": null
}
```

- `pid` 必须是本机进程；`host` 不同不自动回收（交人工）。
- 心跳：worker 运行期间每 `WORKER_HEARTBEAT_SECONDS`（默认 10s）更新 `heartbeat_at`；`WORKER_HEARTBEAT_TTL`（默认 30s）判死。
- `worker.pid`/`worker.lock` 用 `O_CREAT|O_EXCL`；两者任一存在且 pid 存活 → 双开拒绝。
- 退出：`exited` 只在 0 退出且任务已有终态/暂停；非 0 → `failed`；信号 → `killed`；判死 → `interrupted`。
- 日志：`worker.log` 轮转阈值 `WORKER_LOG_MAX_BYTES`（默认 64 MiB），保留 `WORKER_LOG_BACKUPS`（默认 3）；轮转写一条 event/审计。P0b 实现写入，P0a 定义契约。

## 3 Supervisor API（P0a）

```python
@dataclass
class WorkerState: ...

def read_worker_state(task_dir) -> WorkerState | None
def write_worker_state(task_dir, state) -> None      # temp + os.replace
def heartbeat(task_dir, *, now=None) -> WorkerState  # 更新 heartbeat_at
def pid_alive(pid) -> bool
def is_stale(state, *, ttl_seconds=30, now=None) -> bool

class WorkerSupervisor:
    def __init__(self, session_root: Path)
    def snapshot(self) -> list[WorkerSnapshot]       # TaskRecord + WorkerState|None
    def recover(self) -> list[TaskRecord]            # stale worker + TaskStore.recover_stale
```

`WorkerSnapshot`：`task_id/kind/status/objective/segments/worker_status/pid/heartbeat_at/stale`。
`recover()` 规则：

1. 先调用 `TaskStore.recover_stale()`（running + stale task lock → interrupted，01 已有）；
2. 对 worker state 为 running/waiting/starting 且 stale 的，标 `interrupted` 并追加 task event（`worker_interrupted`）；
3. 不执行、不重跑；返回被中断的任务列表；
4. host 不同不自动回收，只在 snapshot 标 `foreign_host=true`。

## 4 `--tasks` CLI

```text
vibe --tasks [--workspace PATH] [--fake]
```

- 选择该 workspace 最近一个 `session`（`--fake`/`--test-session` 用 test purpose，否则 user），不创建新 session；
- 输出表：`TASK_ID KIND STATUS SEGMENTS WORKER PID HEARTBEAT OBJECTIVE`；
- 无 session/无 task 时输出 `no tasks under <path>`，退出 0；
- `WORKER` 列：`-`（无 worker.json）/`running`/`interrupted`/`failed` 等；`STALE` 列/标记用 `!`；
- 只读：不调用 `recover()`、不改任务；用户可用后续 `--tasks --recover`（P0b）显式恢复；
- 与 `--stdio/--eval/--task/--list-sessions` 互斥；参数错误退出 2。

## 5 失败与恢复

| 场景 | 行为 |
|---|---|
| 第二个 worker 启动 | 锁/pid 已存在 → 拒绝，退出码 3（P0b） |
| worker 被 kill -9 | 心跳过期 + pid 不存在 → snapshot 标 stale；`--tasks` 显示；显式 recover 后 interrupted |
| worker 正常退出 | status=exited/failed，保留 worker.json/log；任务状态由集成层已写 |
| TUI 退出 | P0c：确认后 worker 继续；不确认则取消 |
| 心跳过期但 pid 存活 | 不判死（可能暂停/卡住）；显示 stale 但标 `pid_alive=true`，交用户 |
| 日志超限 | 轮转 `worker.log.1..N`；写事件；不删除最新日志 |
| 清理 | 只允许显式 `--tasks purge`（P0c 再定）；记录审计 |

## 6 安全与边界

- supervisor 只读任务/worker 元数据，不执行工具、不审批、不读工作区内容；
- attach/worker 的审批仍走 `ApprovalGate`；P0a 不新增审批路径；
- worker 进程必须以本机用户运行；不引入网络端口；ctrl.sock 权限 `0600`、仅当前用户；
- worker 状态/log 不写模型消息、命令输出或密钥；日志若含敏感输出需由未来脱敏策略处理（与 03 对齐）。

## 7 测试计划（P0a）

- `tests/test_supervise.py`：state 原子写/读取；心跳；pid_live；stale（pid 死/心跳旧/host 不同）；snapshot；recover stale worker；损坏 worker.json 不阻断其它任务。
- `tests/test_cli_tasks.py`：`--tasks` 输出/无任务/无 session/互斥参数；不创建新 session；不改变状态。
- 不启动真实 worker；worker/attach 测试在 P0b。
- 全量回归。

## 8 验收标准（P0a）

- `--tasks` 能列出最近 session 的 task/segment/worker 状态，损坏 worker.json 只影响该项；
- 心跳/判死函数可直接测试；P0a 不产生进程；
- 现有 `--stdio`/`--eval`/`--task`/TUI 行为不变；
- 全量 Python 回归通过。

## 9 后续

1. P0b spec/实现：worker 入口 + ctrl.sock + attach 回放/控制 + 日志轮转；
2. P0c：TUI 关闭提示/启动检测/attach；
3. 之后 05 GoalRecord、03 审批收件箱、04 worktree。

## 10 风险与未决

- **worker 入口如何复用 Agent/stdin**：P0b 决定是包 `serve()` 还是抽出连接无关循环；当前 pre-spec 建议包 `serve()` + socket 队列。
- **无人 attach 时的审批**：默认 fail closed；P0b 需与 03 的 deadline 语义对齐。
- **心跳与任务锁关系**：P0a 只做 worker.json 心跳；P0b 让 task lock 与 worker 心跳共用生命周期。
- **`--tasks` 的 session 选择**：P0a 取最新；多 session/跨 workspace 后续再说。
- **日志脱敏**：P0a 只定义轮转；P0b 记录前做最小 secret 屏蔽，完整策略随 03/09。

## 写队列状态（P0 补充）

- worker 在 workspace lock 等待时任务进入 `queued`；超时未获取锁 → `failed`，
  worker 退出 3；两任务写串行场景见 `bench_long.py`。
