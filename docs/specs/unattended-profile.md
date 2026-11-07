# 无人值守 profile P0（保守默认 + 能力拒绝）

状态：2026-11-06 P0 实现；env 凭据 scrub（命令工具）与 `--pause-task` 已实现；2026-11-07 补 profile 放宽审计（worker 通知 + doctor 提示）；日志保留/通知、两槽队列仍待后续。
设计来源：[09 无人值守安全默认值](../design/long-horizon/09-unattended-safety.md)。

## 已实现

- `app/safety/unattended.py`
  - `UnattendedProfile`，默认 `unattended-conservative`；
  - profile 文件：`<workspace>/.vibe/unattended.json`（可用 `VIBE_UNATTENDED_PROFILE` 覆盖路径）；
  - 未知字段/版本拒绝；缺省即保守 profile。
- 默认能力：
  - 只读工具允许；
  - 工作区写允许，但**要求独立 worktree**（worker 无 worktree 时拒绝写审批）；
  - `bash/run_command` 默认拒绝（`allow_exec=false`）；
  - 网络/web 默认拒绝（`allow_network=false`）；
  - MCP 默认拒绝（`allow_mcp=false`）；
  - browser/remote 默认拒绝（`allow_remote_action=false`）；
  - 未知工具默认拒绝。
- profile 放宽审计（2026-11-07）：
  - `relaxed_settings()` 列出相对保守默认被放宽的项（allow_exec/allow_network/allow_mcp/allow_remote_action/require_worktree/approval_mode）；
  - worker 启动任务事件 `unattended_profile` 记 `profile/hash/relaxed`；
  - 非空 relaxed 时写本地通知 `unattended_profile_relaxed`（可经 `/notifications`、`--notifications`、webhook 查看）；
  - `--doctor` 读取 profile：宽松项 → WARN；非法 JSON/未知键/符号链接 → FAIL 并提示 worker 会拒绝启动。
- worker 接线：
  - 启动时加载 profile，并在任务事件写 `unattended_profile`（profile id + hash）；
  - 收到 child 的 `approval_request` 时先做能力检查；
  - 被拒绝：不写审批收件箱，写 `unattended_denied` 事件，并向 child 回 `approval` deny；
  - 允许：继续现有审批队列 + attach/超时语义。
- 显式放宽示例：

```json
{"schema_version":1,"allow_exec":true,"allow_network":true}
```

- `run_command`/`run_command_async` 使用 `scrubbed_command_env`：保留 PATH/HOME/语言/工具链变量，剥离 `*KEY*/*TOKEN*/*SECRET*/*PASSWORD*` 等凭据变量；`env_extra` 显式覆盖。
- CLI `--pause-task TASK_ID --workspace PATH`：SIGTERM 暂停指定 worker，保留 worktree，不写 `.vibe/stop`；`--stop/--stop-task` 仍是 kill switch。
- 测试：`tests/test_unattended.py`（默认拒绝、profile 覆盖/未知字段、worker 自动 deny + audit）、`tests/test_sandbox.py`（env scrub）、`tests/test_cli_merge_stop.py`（pause）。
- worker-events.jsonl 支持启动时按大小轮转（`VIBE_WORKER_EVENTS_MAX_BYTES/BACKUPS`）。
- 红队清单：`python3 scripts/redteam_unattended.py --workspace <ws>` 或
  `./run_vibe.sh --redteam --workspace <ws>` 只读检查默认拒绝矩阵
  （读/写/worktree/exec/network/mcp/browser/未知工具），exit 1 表示偏离预期；测试见 `tests/test_redteam_unattended.py`。
- `worker_started`/`worker_exited` 通知含 task_kind/mode/plan_id/plan_version；
- 本地通知：worker 生命周期写 `.vibe/notifications.jsonl`（started/exited/stop_requested/approval_requested/approval_warning/approval_timeout/task_deadline_warning/task_deadline_exceeded），
  拒绝工具调用写 `unattended_denied`，审批收件箱满写 `approval_inbox_full`；Plan 绑定/提出/审批/验收写 `plan_binding`/`plan_proposed`/`plan_approved`/`plan_rejected`/`plan_acceptance`，子 Agent 完成写 `subagent_finished`，
  goal 续段/停止写 `goal_segment`/`goal_stopped`，
  写任务等待 workspace lock 时写 `workspace_queued`；
- `.vibe/notifications.jsonl` 超过 `VIBE_NOTIFICATIONS_MAX_BYTES`（默认 8 MiB）时
  轮转为 `.1`，避免无限增长；merge 生命周期写
  `merge_approval_requested`/`merge_completed`/`merge_failed`，供后续告警渠道消费；
  stdio command `notifications` 与 TUI `/notifications` 可查看最近通知。
- 日志保留：`--cleanup-logs --workspace <ws> [--log-max-bytes N] [--log-dry-run]` 显式轮转会话 `trace.jsonl`（`.1`…备份），写 `.vibe/retention.jsonl` 审计；`events.jsonl` 不轮转，避免破坏会话恢复。
- events 软上限：`--cleanup-logs` 输出 `events_soft_limit` / `events` / `oversized_events`（只报告不轮转）；后台 worker 启动时若本会话 `events.jsonl` 超过 `VIBE_EVENTS_MAX_BYTES`（默认 32 MiB，0=关闭），写 `session_events_large` 通知与任务事件。
- 自动 trace 轮转：追加 trace 前检查 `VIBE_TRACE_MAX_BYTES`（默认 0=关闭）/`VIBE_TRACE_BACKUPS`，超过阈值即轮转；`events.jsonl` 仍不轮转。
- 两任务写串行：`VIBE_WORKSPACE_LOCK_WAIT=<seconds>` 让第二个 worker 排队等待 workspace lock（默认 0=立即拒绝）；`bench_long` 的 two-slot 场景覆盖。
- 审批超时前 `VIBE_WORKER_APPROVAL_WARN_SECONDS` 写 `approval_warning` 通知/事件；
- 任务级 deadline：`VIBE_TASK_DEADLINE_SECONDS`（默认 0=关闭）从 worker 启动计 wall；到期前 `VIBE_TASK_DEADLINE_WARN_SECONDS` 写 `task_deadline_warning`；到期写 `task_deadline_exceeded`、取消运行，任务 running/paused 置 `failed`/created 置 `cancelled`。

## 未完成（P0 剩余）

- `bash` 子进程环境已按通用 allowlist scrub；模型 provider key 与工具链的细粒度注入仍需后续设计；
- 日志保留：trace 显式+自动轮转已实现；events 软上限、外部告警渠道、审批/审计保留时长仍待；本地 notifications.jsonl 已有基础事件。
- 只读任务并发/文件集并行；`--pause-task`、两任务写串行、任务级 deadline 已有基础实现；
- merge 审批默认值与 profile 的联动仍是现有 MergeGate 行为；
- profile 变更的审批/审计入口（当前只能改文件）；
- 红队清单尚未固化为自动化脚本。

## 与现有行为的关系

- 这是**后台 worker** 的默认严格化：后台 fake demo 的 `bash` 验证现在默认被拒绝；需要显式在 workspace `.vibe/unattended.json` 设置 `allow_exec=true` 才会进入审批队列。
- 前台 TUI/stdio 不经过 profile；交互审批行为不变。
