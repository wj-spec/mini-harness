# 控制面 attach（TUI 连接运行中 worker，P0）

日期：2026-11-03。状态：TUI attach + controller/observer + 心跳租约 + capability 协商 + replay/live 锁 + `/attach`/`/attach now` + attach v2 帧（`task_snapshot_request`、`approval_list_request`、`approval_decide`）已实现；2026-11-06 人工验收 TC-01–09 通过（[记录](../specs/manual-acceptance-07-results.md)）；完整 v2 stdio 双栈/协议升级仍待后续。
对应讨论稿：[07 控制面与协议](../design/long-horizon/07-control-plane.md)（§9/§10 已确认）。
依赖：[process-supervision](process-supervision.md)、[task-runtime-integration](task-runtime-integration.md)。

## 0 第一性原理与不变量

1. **控制面不复制执行**：attach 只是把现有 JSONL v1 帧接到运行中的 worker socket，不另造任务状态/执行引擎。
2. **默认只读观察，显式控制**：P0 的 attach 中继允许转发 run/cancel/approval/steer/command；controller 权限由后续 v2/租约收紧，当前仅本机 socket 0600。
3. **回放 + live**：连接先回放 worker-events，再接 live；断线不影响 worker。
4. **TUI 复用同一 BackendClient**：attach 进程对 TUI 呈现为普通后端；无需另写 UI 分支。
5. **不改变前台路径**：默认 `--stdio` 启动不变；attach 是显式启动参数。

## 1 范围

**P0（本切片）**

- TUI CLI：`./run_ink.sh --attach-task <task_id> --session <session_id> [--fake ...]`；
- TUI BackendClient：attach 模式 spawn `./run_vibe.sh --attach --task-id … --session …`，不再 spawn `--stdio`；
- 复用 worker 的 socket 回放/live；TUI 的 run/cancel/approval/steer/command 均经 worker 中继；
- 测试：`backendSpawnArgs` 默认 stdio、attach 参数顺序；原有 TUI 测试不变。

**后续 P1**

- worker 协议 v2：`task_snapshot/task_event`、`approval_list/decide`、controller 租约/observer；
- TUI 启动自动发现运行中 worker 并提供 attach 选择；
- 断线重连/回放上限；
- v1/v2 双栈兼容窗口（07 §9）。

**非目标**：远程/多用户 attach、多主控制、跨机器、把 worker 状态复制到 TUI 内存。

## 2 使用

```bash
# 列出任务，找到 task_id / session
./run_vibe.sh --tasks
# 用 TUI 连接运行中的 worker
./run_ink.sh --attach-task <task_id> --session <session_id>
```

- attach 进程输出 worker 回放的事件（ready/history/task_state/…）与 live 帧；
- TUI 内的 `/task`、`/approvals`、审批、取消、steer 都经 worker 中继；
- TUI 关闭只断开 attach，不取消 worker（除非用户选择停止）。

## 3 实现点

- `tui/src/backend.ts`：
  - `AttachOptions {taskId, session}`；`backendSpawnArgs(args, attach)`；
  - `BackendOptions.attach`；spawn 改为按模式选择参数；
- `tui/src/cli.tsx`：
  - 解析 `--attach-task`（要求 `--session`），从普通 args 中移除；
  - `createBackendClient({args, attach})`；
  - `--list-sessions` 与 attach 互斥；
- Python `--attach` 已实现（`app/runtime/worker.py`）：socket 连接、回放、stdin 转发、stdout 输出。

## 4 边界与失败

| 场景 | 行为 |
|---|---|
| 无 socket/worker 不存在 | attach CLI 报错退出 1；TUI 收到后端退出错误 |
| worker 已结束 | socket 不存在；可用 `--tasks`/trace 查看历史 |
| TUI 退出 | 只断开 attach；worker 继续 |
| 多 TUI attach | P0 未强制 controller/observer；本机 socket 0600，后续 v2 收紧 |
| 协议不匹配 | 复用 v1 解析；旧/新版本不一致时按协议错误断开 |

## 5 测试

- `tui/tests/attach-args.test.ts`：`backendSpawnArgs` 默认 `--stdio`；attach 参数顺序；
- 全量 TUI 测试（attach 参数解析不破坏默认路径）；
- Python attach 已由 worker 测试/CLI 覆盖。

## 6 后续

1. TUI 启动任务提示增加“按 a attach”交互（自动 spawn）；  
2. v2 task/approval 帧与 controller 租约；  
3. attach 回放上限/缺口策略（07 §9）；  
4. TUI `/task` 从 attach 帧刷新。

## 7 风险与未决

- **controller 权限未实现**：P0 任何 attach 都可发控制帧；只适合本机单用户，v2 必须补租约；
- **回放重复**：attach 回放与 live 之间可能重复一帧；TUI 需幂等（现有事件渲染基本可接受）；
- **session 选择**：attach 需用户显式提供 session/task；自动发现后续。
