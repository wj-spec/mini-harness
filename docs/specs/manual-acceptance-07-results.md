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
| TC-04 observer 只读 | 待测 | — |
| TC-05 controller 接管 | 待测 | — |
| TC-06 detach 后继续 | 待测 | — |
| TC-07 kill switch | 待测 | — |
| TC-08 merge + 后验 | 待测 | — |
| TC-09 非 git 拒绝 | 待测 | — |

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

## 后续

按 [manual-acceptance-07-attach.md](manual-acceptance-07-attach.md) 继续 TC-04 → TC-09。
