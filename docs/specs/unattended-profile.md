# 无人值守 profile P0（保守默认 + 能力拒绝）

状态：2026-11-06 P0 实现；env 凭据 scrub（命令工具）与 `--pause-task` 已实现；日志保留/通知、两槽队列仍待后续。
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
- 两任务写串行：`VIBE_WORKSPACE_LOCK_WAIT=<seconds>` 让第二个 worker 排队等待 workspace lock（默认 0=立即拒绝）；`bench_long` 的 two-slot 场景覆盖。

## 未完成（P0 剩余）

- `bash` 子进程环境已按通用 allowlist scrub；模型 provider key 与工具链的细粒度注入仍需后续设计；
- 日志保留/轮转/告警；
- 任务级 deadline 独立配置；只读任务并发/文件集并行；`--pause-task` 与两任务写串行已有基础实现；
- merge 审批默认值与 profile 的联动仍是现有 MergeGate 行为；
- profile 变更的审批/审计入口（当前只能改文件）；
- 红队清单尚未固化为自动化脚本。

## 与现有行为的关系

- 这是**后台 worker** 的默认严格化：后台 fake demo 的 `bash` 验证现在默认被拒绝；需要显式在 workspace `.vibe/unattended.json` 设置 `allow_exec=true` 才会进入审批队列。
- 前台 TUI/stdio 不经过 profile；交互审批行为不变。
