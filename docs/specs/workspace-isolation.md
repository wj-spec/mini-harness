# 工作区隔离（WorkspaceLock / WorktreeManager，P0）

日期：2026-11-03。状态：P0 基础 + P1 worker 接线 + MergeGate 已实现（worker 写任务 acquire workspace lock、git worktree、`--tools-root`；合并需 inbox 审批、漂移/脏工作区拦截、合并后 `post_merge_verified=false`）；CLI/TUI merge 入口、kill switch、非 git 写拒绝仍待后续。
对应讨论稿：[04 隔离与并发](../design/long-horizon/04-isolation-concurrency.md)（§9 已确认）。
依赖：[task-runtime](task-runtime.md)、[process-supervision](process-supervision.md)。

## 0 第一性原理与不变量

1. **同一 workspace 不能有两个写者**：单连接串行不够；跨进程需要外部锁。
2. **写任务应在独立 worktree**：主工作区只保留用户自己的改动；任务结束产出 base..branch 的 diff 供审查/合并。
3. **锁与 worktree 不同层**：workspace 锁防跨任务写冲突；worker.lock 防同一 task 双开；task lock 防同一 task 双段。
4. **非 git 工作区明确拒绝后台写**：P0 只读允许；不静默降级到共享工作区写。
5. **不自动合并/不自动解决冲突**：合并是审批事件；冲突交人工/父级。
6. **stale 只报告不静默删除**：缺失/孤儿 worktree 可发现，清理需显式。
7. **复用主环境**：worktree 不重新安装依赖；`.venv`/node_modules 等用绝对路径/链接（P1 接线，P0 只建目录）。

## 1 范围

**P0（本切片）**

- `app/runtime/workspace.py`：
  - `WorkspaceLock`：`.vibe/workspace.lock` O_EXCL，pid/host 判死回收；context manager；
  - `WorktreeManager`：git repo 检测、`create/list/stale/remove/diff_stat`、`.vibe/worktrees/<task_id>/meta.json`；
  - 非 git 工作区抛 `WorktreeError`；
- 测试与 spec；不改 ToolRegistry/worker/stdio。

**P1（后续）**

- worker 启动写任务前 acquire workspace lock、创建 worktree，并把它作为 ToolRegistry.root；
- 只读任务可并发；写任务排队；
- 合并门禁：`base..branch` diff + 验证证据 → 审批事件 → merge → 合并后重跑验证；
- 用户/外部编辑冲突：合并前重核 base，列出变动但不强制合并；
- workspace 级 kill switch（CLI/`.vibe/stop`）与 stale 清理提示。

**非目标**：容器运行时、并行写后自动合并、自动 rebase/冲突解决、多任务共享一个 worktree。

## 2 API

```python
acquire_workspace_lock(workspace, *, task_id, session_id) -> WorkspaceLock
lock.release()

class WorktreeManager:
    def __init__(workspace)                 # 非 git -> WorktreeError
    def create(task_id, session_id) -> Worktree   # branch vibe/<session>-<task>, base HEAD
    def list() -> list[Worktree]
    def stale() -> list[Worktree]           # active 但目录缺失
    def remove(task_id, *, keep_branch=False) -> None
    def diff_stat(task_id) -> str           # base..branch --stat
```

- `Worktree`：`task_id/session_id/path/branch/base_commit/status`；
- worktree 目录：`<repo>/.vibe/worktrees/<task_id>/`；sidecar meta 在 `.vibe/worktrees/<task_id>.meta.json`（不在工作树内，stale 可发现）；
- branch：`vibe/<session_id>-<task_id>`；
- 锁文件包含 pid/host/task/session/created_at；只做 write-mode 互斥，读任务不调用。

## 3 边界与失败

| 场景 | 行为 |
|---|---|
| 第二个写任务拿锁 | `WorkspaceBusy`，不绕过 |
| 锁持有者 pid 死亡 | 同 host 可回收；异 host 不自动 |
| 非 git 工作区 | `WorktreeError`；后台写任务拒绝（P1 接入） |
| worktree 已存在 | 报错，不覆盖 |
| 目录被删/孤儿 | `stale()` 报告，不自动删 |
| remove | `git worktree remove --force`，可选删分支；失败报错 |
| 主工作区用户改动 | 锁不覆盖用户；合并前需重新核对（P1） |

## 4 测试

- `tests/test_workspace.py`：锁互斥/陈旧回收；worktree 创建、隔离写、diff、remove；stale 报告；非 git 拒绝。
- 依赖本机 git；无 git 时 skip。
- 全量回归。

## 5 验收标准

- 两个写任务不能同时拿 workspace 锁；读任务不调用锁；
- worktree 内写入不出现在主工作区，`diff_stat` 能看到；
- remove 后目录与分支消失；
- 缺失目录被 `stale()` 报告且不自动删除；
- 非 git 工作区明确报错；
- 全量回归通过。

## 6 后续 P1

1. worker 接线：写任务 acquire lock + create worktree + ToolRegistry.root=worktree；
2. 合并门禁与审批（03）+ 合并后验证；
3. workspace kill switch / stale 清理提示；
4. 只读并发上限与写任务排队（09 资源 profile）。

## 7 风险与未决

- **worktree 工具链**：P0 只建目录；依赖复用（`.venv`/node_modules 链接）在 P1。
- **meta 位置**：meta 写工作树内会被 git 视为未跟踪；P1 可移到 `.vibe/worktrees/<task>/meta.json` 旁侧。
- **锁与用户编辑**：锁只约束 harness，不约束外部编辑器；合并前必须重核。
- **git 版本**：依赖 `git worktree`；旧 git 不支持时明确报错。
