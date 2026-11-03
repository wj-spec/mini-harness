# 04 隔离与并发：单写者、worktree 与合并门禁

> 状态：讨论稿（2026-11-02；2026-11-03 部分决策已确认，见 §9）；不是 spec，未承诺实现。
> 所属：长任务与后台任务 / 第 4 块
> 前置/相关：[01 任务运行时](01-task-runtime.md)、[02 后台进程监督](02-process-supervision.md)、[03 无人值守审批](03-unattended-approval.md)、[07 控制面与协议](07-control-plane.md)、[09 无人值守安全默认值](09-unattended-safety.md)、[16 子 Agent 的有机结合](16-subagent-integration.md)；相关规格：[执行隔离评估](../../specs/execution-isolation.md)、[多 Agent 与 Computer Use](../../specs/multi-agent-computer-use.md)、[Git 只读工具](../../specs/git-readonly-tools.md)、[沙箱调研](../../research/sandbox-research-2026-10-25.md)


## 1 问题与范围

当前所有 run 共享同一个工作区：`ToolRegistry` 把 `workspace` resolve 成根，所有文件工具被限制在根内（`app/tools/registry.py:122,983-987`）；一次 stdio 连接只允许一个 active run（`app/stdio.py:633-634`），但**多个 Ink/后台进程可以同时打开同一工作区**，互相没有锁、没有分支、没有逐任务 diff。长任务/后台任务一多，就会出现：后台写到一半、用户手动改同一文件；两个任务各改一半、最终 diff 混在一起；任务“完成”后直接把改动留在主工作区，没有合并审查步骤。

本文讨论工作区隔离与并发：单写者规则、每个写任务一个 git worktree、只读任务并发、合并门禁、worktree 生命周期、资源上限与 kill switch。审批队列本身见 03，无人值守默认能力集见 09；本文不改变审批层级（T0–T4）也不实现容器运行时。

## 2 现状与证据

| 事实 | 现状 | 证据 |
|---|---|---|
| 所有工具共享一个工作区根 | `self.root = Path(workspace).resolve()`；路径解析越界即报错（bench 例外） | `app/tools/registry.py:122,983-987` |
| 单连接串行 | 同一连接第二个 run 返回 `busy` | `app/stdio.py:633-634` |
| 跨进程无锁 | 锁/租约/队列在代码中不存在；会话按时间戳分目录，两个进程可同时打开同一 workspace | `app/session/store.py:59-87`；`app/stdio.py:633-634` |
| 无 worktree | `git worktree` 只出现在 M2 设计里；侧栏的 `worktree` 是 git status porcelain 字段，不是功能 | `docs/specs/multi-agent-computer-use.md:373`；`tui/src/sidebar/git.ts:11` |
| 隔离是可选沙箱 | 默认关闭；启用后用 macOS Seatbelt 或 Linux bwrap 限制写与网络 | `app/config.py:55,117`；`app/safety/sandbox.py:55-60,118-176` |
| 冲突只能事后发现 | 读 hash/`_assert_fresh` 防本进程拿过期内容写；环境审计把外部改动记为 `suspect`，但不阻止 | `app/tools/registry.py:819-826`；`app/session/audit.py:164-200,265-299` |
| bash 是任意写通道 | 审批后 bash 可在工作区外写/删，黑名单只是护栏 | `docs/specs/execution-isolation.md:15-22` |
| 进程组可回收 | `start_new_session` + killpg；TUI 退出时对后端组 SIGTERM→SIGKILL | `app/safety/sandbox.py:268-372`；`tui/src/backend.ts:160-177,214-249` |
| 资源上限是 per-run | `max_run_seconds/max_run_tokens/max_run_cost_usd` 只约束单次 run | `app/config.py:22-35`；`app/runtime/agent.py:1895-1896` |
| 容器只用于评测 | 内部 eval 用 Docker、公开跑分用 Harbor 任务容器；不是本地运行隔离 | `app/eval/container.py:1-30`；`docs/specs/harbor-benchmark.md:22,52-54` |
| 备份/产物共享 | `.vibe/backups`、`.vibe/offload` 等都在同一 workspace 的 `.vibe/` 下 | `app/tools/registry.py:828-836`；`app/session/store.py:59-87` |
| `/goal` 段是串行的 | 资源停止后才开下一段，不会同时开两个段 | `tui/src/goal.ts:101-130` |

结论：当前并发模型只保证“一个事件循环里、一次连接内不出两个 run”，不保证“一个工作区里不出两个写者”。已有的隔离与回收手段（可选沙箱、进程组 kill、审计发现未归因改动）是**检测和兜底**，不是并发隔离。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 无工作区锁 | 两个进程/两个任务可同时写同一文件，后写覆盖先写；唯一的串行只在单连接内（`app/stdio.py:633-634`；`app/session/store.py:59-87`） | 丢失改动、diff 不可解释、审计只能事后标 suspect |
| 无任务级隔离 | 所有任务都在主工作区（`app/tools/registry.py:122`），用户手动编辑与 Agent 改动混在一起 | 无法回答“这次任务改了哪些文件”，也无法回退单个任务 |
| 无 worktree/branch 生命周期 | 没有创建、命名、base commit、清理、stale 处理（仅 M2 设计提到，`docs/specs/multi-agent-computer-use.md:373`） | 后台任务无法独立演进，多个任务互相污染 |
| 无合并门禁 | 任务 done 后改动直接生效；git 只有只读 `status/diff`，无 merge 工具（`app/tools/registry.py:524,600`） | 无人值守下的改动直接进入主工作区，只有完成守卫做质量兜底 |
| 并发限制跨进程失效 | `busy` 只在一个连接内有效（`app/stdio.py:633-634`） | 多 Ink/后台 runner 可叠加多个写者 |
| kill switch 只作用于本进程 | cancel 只停当前 run（`app/stdio.py:202-207`）；后端退出杀自己的进程组（`app/stdio.py:764-790`） | 多个后台任务无法一键全停 |
| 资源没有 workspace 级上限 | token/时间/费用是 per-run（`app/config.py:22-35`） | 多任务成本/进程数可叠加，夜间跑飞 |
| git 工具只读、无 commit/merge | 只能 `git status/diff`（`app/tools/registry.py:524,600`）；要提交/合并只能 bash（经审批） | 合并门禁没有一等公民接口 |

## 4 设计选项与推荐

| 方案 | 做法 | 成本 | 收益 | 风险 | 结论 |
|---|---|---|---|---|---|
| A workspace 锁 | `.vibe/workspace.lock`（pid/session/task/mode/时间），写任务开始时获取，超时/陈旧可回收 | 低 | 立即消除“两个写者同时写”；读任务不受影响 | 只串行化，不隔离 diff；用户手动编辑/外部进程不受锁约束 | P0 兜底，所有写任务至少用它 |
| B 每任务 git worktree + branch | 每个写任务在 `.vibe/worktrees/<task_id>` 建独立 worktree 与分支，base commit 固定；任务只在其中写；完成后出 diff/测试证据，经合并门禁再应用到主工作区 | 中 | 真正隔离；逐任务 diff 可审查；可丢弃/重试；多任务可并行准备（合并仍串行） | 非 git 工作区不可用；依赖安装/工具链每 worktree 一份成本；合并冲突需处理 | **后台/长任务推荐**，与 A 组合 |
| C 每任务容器 | 每任务一个容器挂载独立副本 | 高 | 最强隔离（网络/文件/进程） | 重；macOS 本地体验差；模型/凭据分发复杂；与本地 diff 工作流脱节 | 推迟；评测已用容器，运行时不引入 |

推荐组合：**A 作为所有写者的最小互斥，B 作为后台写任务的默认隔离，C 继续只用于评测**。只读任务（read_file/grep/list_files/git_status/git_diff 子集）可以共享主工作区并发；任何可能写文件或执行任意命令的任务先按“写者”处理，串行进入 worktree。只读并发上限仍由 09 的资源 profile 约束。

### 4.1 worktree 生命周期（建议）

1. **创建**：确认 workspace 是 git 仓库（可用 `git rev-parse --show-toplevel`）；记录 `base_commit=HEAD`；创建 `.vibe/worktrees/<task_id>/` 与分支 `vibe/<session_id>-<task_id>`；写入 `meta.json`（`task_id/session_id/base_commit/branch/created_at/mode/status`）。`.vibe/` 已 gitignore（`.gitignore:1`），worktree 放在其下不污染主仓库。
2. **写入**：任务的 `ToolRegistry.root` 指向 worktree；`bash` 的 cwd 也限制在 worktree；主工作区只保留用户自己的改动。
3. **结束/清理**：`done/stopped/cancelled` 后不自动删未合并分支；合并成功或用户显式丢弃后 `git worktree remove` + `git branch -D`；启动时扫描 `git worktree list --porcelain` 与 `.vibe/worktrees/*/meta.json`，把缺失/孤立/超时的标 `stale`，只报告不静默删除。
4. **不自动 rebase/merge**：base commit 变化或主工作区有冲突时交人工/父级处理。

### 4.2 合并门禁（建议）

- 任务结束先生成证据包：`base_commit..branch` 的 diff 摘要、改动文件、验证命令与退出码、`verified`/审计结论（复用 `core/task/rounds.py` 的轮次记录与 `app/session/evidence.py`）。
- 合并动作是一次**审批事件**（走 03 的审批队列，`origin=parent|user`，`tool=merge_worktree`，预览显示 diff 摘要与测试结果）；无批准不落主工作区。
- 批准后可用 `git merge --no-ff` 或 `git apply`；冲突进入人工/父 Agent 处理，不自动解决。
- 合并后的验证（至少重跑测试）仍是 required；合并本身不等于 verified。

### 4.3 单写者与资源规则（建议）

- **单写者（P0/P1）**：同一 workspace 同时最多一个“写任务”（含 `bash`/写文件/MCP risk≠read）；读任务可并发 N 个，上限由 profile 定。按 §9，文件集级并行写是后续实验项，不作为首版默认。
- **锁语义**：锁文件记录 `pid/task_id/mode/created_at`；进程死亡或心跳超时后可回收；获取失败时任务进入排队/暂停，不自动绕过。
- **kill switch**：任务级 cancel 已有；新增 workspace 级“停止所有任务”信号（文件或 CLI），先 SIGTERM 再 SIGKILL 进程组，并标记所有运行中任务 `cancelled`（不回滚已写内容）。

## 5 最小实现切片

**P0（worktree 创建/清理 + 单写者锁）**

- `WorkspaceLock`：`.vibe/workspace.lock` 原子创建（O_EXCL），内容 `{pid,session_id,task_id,mode,created_at}`；释放/陈旧回收；读任务 `mode=read` 不进锁。它与 02 的 `worker.lock` 不同层：后者防同一 task 双开，前者做 workspace 写互斥。
- `WorktreeManager`：`create(task_id, session_id)`、`remove(task_id)`、`list_stale()`；`meta.json` 字段如 §4.1；非 git 工作区明确拒绝后台写任务（回退到 A + 只读/人工）。
- 接口草案（伪代码，不贴实现）：
  ```text
  acquire_lock(workspace, task_id, mode) -> Lock
  create_worktree(workspace, task_id, session_id) -> Worktree{path,branch,base_commit}
  finish_worktree(task_id, action=keep|remove) -> DiffSummary
  ```
- 接入点：`ToolRegistry` 构造时传入 `root=worktree_path`（现有 `app/tools/registry.py:122` 已支持任意 root，无需改工具边界）；worktree 路径、branch、base_commit 记入 01 的 `TaskRecord`（`workspace` 字段）与任务目录 `meta.json`（`01-task-runtime.md:70-99`）。

**P1（合并门禁 + stale/清理 + 全局 kill）**

- 合并命令作为受审批工具（03 队列），预览含 diff stat + 验证结果；合并后重跑验证并追加 round。
- stale worktree 报告、孤儿分支清理提示、workspace 级 kill switch。
- 并发调度：读任务可并发；写任务排队；达到 workspace 资源上限时暂停排队而非继续启动。

**测试点**：两个写任务不能同时拿锁；读任务可并发；worktree 创建后写不会出现在主工作区；diff 只在任务分支；未审批的 merge 被拒；进程死亡后锁可回收；stale worktree 被发现且不自动删除；非 git 工作区后台写任务被明确拒绝。

**明确不做**：运行时容器隔离、并行写代码后自动合并、自动解决冲突、自动 rebase、多个任务共享一个 worktree 并发写。

## 6 与其它块的关系

- **01 任务运行时**：worktree 属于任务身份的一部分，`task_id`、任务目录、`TaskRecord.workspace` 由 01 定义；04 只负责其创建/清理/合并。
- **02 后台进程监督**：后台 worker 以 worktree 为 root 启动；`worker.lock` 防双开、`WorkspaceLock` 防 workspace 层多写，两者不同层。
- **07 控制面与协议**：`task_snapshot` 可携带 worktree 路径/分支/base commit 供 attach 查看（`07-control-plane.md:63`）；合并动作走 07 的任务控制帧 + 03 的审批。
- **03 无人值守审批**：合并门禁、锁冲突的人工裁决都复用审批队列；03 解决“谁来批”，04 解决“在哪写、写完怎么合”。
- **09 无人值守安全默认值**：09 把“后台写任务必须 worktree + 沙箱”写进默认 profile，并定义 kill switch 与资源上限；04 提供其实现落点。
- 与评测：`app/eval/container.py` 与 Harbor 容器是**评测隔离**，不能替代本地运行隔离；不要把评测容器当作后台任务默认（`docs/specs/execution-isolation.md:24-26`）。

## 7 与子 Agent 的关系

| 维度 | 当前/近期 | 未来约束 |
|---|---|---|
| A 谁发起 | 父 Agent 工具调用、用户显式；子 Agent 尚未实现 | 子 Agent 不能自行创建共享工作区写任务；由父创建 worktree 并分配 |
| B 能力边界 | M1 只读子 Agent 无写工具（`docs/specs/multi-agent-computer-use.md:305-308`） | M2 可写子 Agent 一子一 worktree、子只能改自己的 worktree；共享工作区写不做（`docs/specs/multi-agent-computer-use.md:373,557`） |
| C 结果可信度 | 子报告是 untrusted claim，父用测试/证据复核（`docs/specs/multi-agent-computer-use.md:348`） | 父接收 diff，运行测试，串行合并；冲突由父/人工处理（`docs/specs/multi-agent-computer-use.md:373`）；子不能审批合并 |

子 Agent 作为任务节点的发起/能力/可信度统一结论见 [16 子 Agent 的有机结合](16-subagent-integration.md)（`16-subagent-integration.md:138-150`）；04 只负责其 worktree 隔离与合并门禁。

## 8 开放问题与验收标准

**待拍板**

1. 非 git 工作区怎么办：后台写任务直接拒绝，还是降级为“锁 + 副本目录 + 人工合并”？

回答：还有好的解决方案么？

2. 合并由谁批准：父 Agent 生成证据后用户最终批准，还是允许父按策略自动合并“已验证 + 只读审阅通过”的 diff？

回答：生成证据后用户最终批准，还是允许父按策略自动合并“已验证。合并后再次验证

3. worktree 的工具链成本：是否在每个 worktree 复用主环境（如 `UV_PROJECT_ENVIRONMENT`/node_modules 链接），还是接受独立安装？这直接决定后台任务成本。

回答： 复用主环境

4. 单写者粒度：按 workspace 串行，还是按“目录/文件集”允许无交集并行（更复杂，先不做）？

回答：按“目录/文件集”允许无交集并行

5. 用户手动编辑与后台任务的冲突：锁只约束 harness，用户/外部编辑器不受约束——是否需要在合并前强制重新核对基线与冲突？

回答：是要核对，但是针对于用户/外部编辑器的部分，指出变动的部分。不要强制合并。

6. 全局 kill switch 的载体与权限：文件、CLI 还是 TUI 按钮；谁能触发；触发后任务状态如何记录？

回答：cli？  你给出建议。


**验收条件（P0/P1）**

- 同一 workspace 两个写任务不会同时写：第二个排队或明确拒绝；读任务可并发。
- 后台写任务的改动只出现在其 worktree；任务结束可生成 `base..branch` 的 diff 与验证证据。
- 无审批不合并；合并后主工作区仍通过测试，合并被记录在 round/audit。
- 进程崩溃/被 kill 后，锁与 worktree 不导致永久死锁；stale 可发现、可人工清理。
- workspace 级 kill switch 能在限定时间内终止所有本 harness 进程组，并留下状态记录。

## 9 决策记录（2026-11-03）

1. **非 git 工作区（Q1，已确认 2026-11-03）**：

| 方案 | 做法 | 优点 | 代价/风险 | 结论 |
|---|---|---|---|---|
| A 拒绝后台写 | 非 git 工作区只允许只读后台任务；写任务提示 `git init` | 最简单、边界清晰 | 用户必须初始化 git | **P0 采用** |
| B 影子副本 + 自动 git | 复制工作区到 `.vibe/tasks/<id>/ws`（macOS 可用 `cp -c`/APFS clone），在副本内 `git init` 并提交基线，任务只改副本，结束后生成 diff 供人工审查/应用 | 不改用户工作区，复用 worktree/diff/验证工具 | 磁盘/复制成本；忽略规则、符号链接、未跟踪大文件、外部编辑漂移要处理 | **P1 可选，推荐** |
| C 文件级备份 + 手工 diff | 只备份即将修改的文件，结束时按 hash 生成 diff | 无需 git | 重命名/删除/目录级变化不可靠，容易漏 | 不推荐 |

**已确认（2026-11-03）**：P0 只读允许、后台写拒绝并给 `git init` 指引；P1 做 B（带大小上限与忽略规则，明确“只产出 diff，不自动合并”），绝不静默改主工作区。
2. **合并批准（Q2，已确认 2026-11-03）**：默认**用户最终批准**；仅当任务 `verified=true`、只读审阅通过、diff 无冲突、且**合并后重跑验证通过**时，才允许按策略自动合并（opt-in，默认关闭）。无论谁批准，合并后都必须重跑验证；冲突不自动解决、不强制合并。
3. **worktree 工具链（Q3，已确认）**：复用主环境——`.venv`/`UV_PROJECT_ENVIRONMENT` 绝对路径、`node_modules` 符号链接、共享只读依赖缓存；worktree 内只写项目文件。限制：单写者下才安全，并发任务需避免同时写同一缓存/构建目录；lockfile 与依赖变更触发重新安装并记录。
4. **单写者粒度（Q4，已确认 2026-11-03）**：采用分期——P0/P1 仍按 workspace 单写者（写任务串行），只读任务可并发；P2 再评估“声明文件集 + 路径锁”的写并行，且必须限制在独立 worktree 或声明无交集的文件集，执行类命令/测试仍串行；共享工作区默认不并行写。
5. **外部编辑冲突（Q5，已确认）**：合并前强制重新核对 base 与当前主工作区；把用户/外部编辑器的变动逐项列出（文件/行/diff），**指出但不强制合并**；有冲突则暂停等用户决定，并重跑验证。
6. **kill switch（Q6，已确认 2026-11-03）**：主入口用 **CLI**：`vibe task stop <task_id>` 与 `vibe task stop --workspace <path>`（`--all` 可停本机全部），同时留 `.vibe/stop` 信号文件兜底给无终端场景；TUI 按钮调用同一实现，不另造路径。权限限本机用户；worker 在 step/工具边界检查信号，先 SIGTERM 进程组、宽限后 SIGKILL，任务状态写 `cancelled` 并记录审计。
