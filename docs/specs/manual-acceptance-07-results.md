# 07 attach 人工验收记录（进行中）

日期：2026-11-06。基线：`bdf428b` 之后的实现（v2 attach + TUI 修复）。
自动基线：Python **1286 passed / 13 skipped**；TUI **355/356**（当前环境 pty smoke 限制）。

## 环境

- macOS，真实交互终端；过程中曾遇到 pty/raw mode 与 TUI 重载竞争问题，已修复并通过直连 `node tui/dist/src/cli.js` 验证。
- 验收 workspace：`/private/tmp/vibe-acceptance`（git repo，`.vibe/` ignored）。

## 结果

| 用例 | 状态 | 证据 |
|---|---|---|
| TC-01 TUI 基线 | 通过 | TUI 打开、`/task` 可用、Ctrl+Q 退出 |
| TC-02 worker + attach | **通过** | worker 运行；`/attach now` 进入 attach；收到 task_snapshot；任务完成 |
| TC-03 审批流 | **通过** | attach 后出现 write_file 审批面板；`y` 后 `approvals.jsonl` 出现 `resolved`（decided_by=attach）；task=`completed`；worktree 内有 `.vibe/fake-demo.txt`，主工作区没有 |
| TC-04 observer 只读 | **通过** | observer 收到 attach_ok(mode=observe)+task_snapshot+approval_list；`cancel` 返回 `observer_readonly` |
| TC-05 controller 接管 | **通过** | probe 收到 attach_ok(mode=control)+task_snapshot；原 TUI 显示 `control_taken: another client took control`（截图证据） |
| TC-06 detach 后继续 | **通过** | attach TUI Ctrl+Q 后 worker 仍 running；attach 关闭不再取消任务 |
| TC-07 kill switch | **通过** | `--stop` 输出 `signalled=true`；worker.json=`killed`；task paused/cancelled；停止文件清理 |
| TC-08 merge + 后验 | **通过** | `merged=true`、`post_merge_verified=true`、`ACCEPTANCE_MERGE.txt` 在主工作区、merge commit 生成 |
| TC-09 非 git 拒绝 | **通过** | `--worker` 退出 3，stderr `non-git workspace`；无 worktree、无 workspace.lock |

### TC-03 关键证据

```text
task status: completed
segment 1: done
approval: resolved, decision=once, decided_by=attach, decider=user
worker: exited, exit_code=0
worktree file: .vibe/worktrees/<task>/.vibe/fake-demo.txt exists
main workspace file: absent
```

## 过程中修复并验证

- task-only session 无消息时 `--session` 被旧 CLI 拒绝 → 允许有 task 目录的显式 session；
- 中断后重启 worker 误报 `non-git workspace`（已有 worktree）→ 复用 worktree；
- `/attach now` 把 `now` 当 task id → 解析为命令标志；
- attach 回放丢弃审批请求（无 active run id）→ `task_snapshot` 带 `run_id`，TUI 恢复 `activeId`；
- TUI `/attach now` 新旧进程抢 stdin 导致 `read EIO` → 延迟启动新 TUI，并处理 stdin EIO；
- `run_ink.sh` 的 npm build 影响 raw mode → build 按需执行、stdin 隔离、node 子进程、`stty sane`。
- attach TUI 的 Ctrl+Q 仍在走停止任务逻辑 → attach 模式直接 detach；
- TUI 后端 shutdown 被 attach CLI 转发给 worker 子进程 → attach 关闭不再转发 shutdown；
- TUI `/attach now` 新旧进程抢 stdin → 延迟启动新 TUI，并处理 stdin EIO；
- attach 关闭仍会取消 worker（TUI shutdown 被转发）→ attach 模式 Ctrl+Q 直接 detach，worker/CLI 忽略 attach 的 shutdown；
- merge 首次因 scratch workspace 中 `.acceptance.env` 等未跟踪文件变 dirty → 清理后重新请求审批并合并成功；
- 已 resolved 的 merge approval 再次 `--approve-merge` 会报 `approval already resolved` → 重新请求一次 approval。

## 后续

按 [manual-acceptance-07-attach.md](manual-acceptance-07-attach.md) 继续 TC-04 → TC-09。

## 结论

TC-01–TC-09 全部通过（TC-08 在清理 scratch 未跟踪文件后完成；TC-09 使用 `/tmp/vibe-nongit`）。
07 P0 attach / v2 帧 / TUI 人工验收完成；完整 v2 stdio 双栈与自动 attach 仍属于后续范围。
