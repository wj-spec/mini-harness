# 02 后台进程监督

> 状态：讨论稿（2026-11-02；2026-11-03 部分决策已确认，见 §9）；不是 spec，未承诺实现。
> 实现：P0a 监督基础（worker 状态/心跳/判死/`--tasks`）已实现，见 [process-supervision spec](../../specs/process-supervision.md)；worker/attach/TUI detach 见后续。
> 所属：长任务与后台任务 / 第 2 块（进程生命周期与恢复）
> 前置/相关：[01 任务运行时](01-task-runtime.md)、[05 跨段目标状态](05-goal-state.md)、[06 预算账本](06-budget-ledger.md)；相关规格：[goal-mode](../../specs/goal-mode.md)、[w2-w3-acceptance](../../specs/w2-w3-acceptance.md)

## 1 问题与范围

现在的长任务只有“前台一段一段跑”：TUI 进程是唯一的监督者，它 spawn Python 后端、持有管道、在退出时按进程组杀掉后端（tui/src/cli.tsx:117-144；tui/src/backend.ts:214-250）。一旦 TUI 关闭或崩溃，后端与所有工具进程一起消失，`/goal` 的下一段也就没人发起。

本文讨论：**谁托管 Python 后端进程、进程如何被观察/杀灭/重连，以及崩溃后如何判定状态**。范围：

- worker 进程的生命周期、pid/lock、心跳、日志、退出码；
- TUI 退出/崩溃后的后台延续与 attach 重连；
- 崩溃恢复的边界（恢复证据 vs. 自动重放）；
- 与审批通道、隔离/并发的接口点。

不在本文：任务模型与 API（01）、目标语义（05）、预算账本（06）、审批策略与收件箱（03）、worktree 隔离（04）、协议帧细节（07）。

## 2 现状与证据

**进程拓扑（现状）**

- TUI 用 `spawn('./run_vibe.sh', ['--stdio', ...])` 拉起后端，POSIX 上 `detached: true`，stdin/stdout/stderr 三根管道（tui/src/backend.ts:293-303）；cwd 固定为仓库根、继承环境（tui/src/backend-launch.ts:1-16）；`run_ink.sh` 先构建再 `exec node tui/dist/src/cli.js`（run_ink.sh:29-30）。
- TUI 是 supervisor：退出/信号 → `client.shutdown()` → 发 `shutdown` 帧 → 等待 4s → 进程组 SIGTERM → 500ms 后 SIGKILL（tui/src/backend.ts:34-35,214-250）；组信号用 `process.kill(-child.pid, ...)`（tui/src/backend.ts:160-177）。
- 后端侧：`shutdown` 帧只置 `stop`（app/stdio.py:682-684）；`serve()` 单连接循环（app/stdio.py:699,754-763），finally 里取消任务、关闭 MCP、恢复信号、发 `closed`（app/stdio.py:764-790）；`run_stdio` 用 `asyncio.run(serve(...))`（app/stdio.py:793-801）。
- stdin EOF 会让 `_stdin_frames` 结束，循环随之退出（app/stdio.py:85-113,754-763）；这是“TUI 崩溃后后端一般也会退出”的实际机制。

**恢复与日志（现状）**

- 会话级恢复已有：`restore()` 回放历史、提示“未自动续跑”（app/stdio.py:209-237）；`/resume` 显式发起新 run（app/stdio.py:274-280）；session 有 `repair_incomplete_tool_calls` 崩溃对账（app/session/store.py:392-420）。
- 后端 stderr 被 TUI 读成诊断，内存缓冲上限 8 KiB，不落盘（tui/src/backend.ts:26-28,97-151）；协议错误/退出异常会变成 `error` 帧（tui/src/backend.ts:316-343）。
- oneshot 路径：`--task` 构造 Agent 后 `asyncio.run` 一次，结束时写 telemetry 再退出（app/__main__.py:144-159；app/eval/oneshot.py:230-289），没有任何后台/重连概念。
- 现状不存在 worker 入口、pid 文件、锁、控制 socket 或 attach：CLI 参数只有一次性模式（app/__main__.py:69-95），协议请求类型只有 run/cancel/steer/command/approval/shutdown（app/stdio.py:598-603）。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 监督者单点 | TUI 退出 → 进程组 SIGTERM/SIGKILL（tui/src/backend.ts:214-250） | 长任务无法跨 TUI 生命周期；`/goal` 只在窗口开着时成立 |
| 无 attach/重连 | 每次 TUI 启动都 spawn 新后端（tui/src/backend.ts:293-303）；无控制 socket | 第二终端看不到正在跑的任务；崩溃后无法接管 |
| 无 pid/lock/心跳 | 无进程标识文件；只有 session 目录（app/session/store.py:24-56） | 重复启动、孤儿进程、崩溃残留无法判定 |
| 日志不留存 | stderr 进 TUI 内存缓冲，8 KiB 截断（tui/src/backend.ts:26-28） | 长任务事后无日志可查；崩溃原因丢失 |
| 通知缺失 | 只能在 TUI 里看 `notice`/`status` | 后台跑完后用户不知道结果 |
| 恢复语义靠人 | 只恢复会话并提示显式 `/resume`（app/stdio.py:209-237） | 任务级“哪一段中断、下一步该做什么”需要人工重建 |

## 4 设计选项与推荐

| 方案 | 进程由谁托管 | 退出后是否继续 | 改动面 | 风险 |
|---|---|---|---|---|
| A TUI 常驻 | TUI（现状） | 只有 TUI 不退出才行 | 最小：加“最小化/防退出” | 解决不了崩溃/关机；不是后台 |
| **B `python -m app --worker` + 任务目录 + attach（推荐）** | 独立 worker（本机用户会话内） | 是：TUI 只是客户端 | 中：新增 worker/attach、pid/锁/日志 | 需要定义 attach 协议与恢复规则（07/01 配合） |
| C OS 服务包装（launchd/systemd） | 操作系统 | 是，开机可恢复 | 大：安装/权限/日志/升级 | 对单用户本地工作台过重；可作 B 之后的包装层 |

**推荐 B**，分两步：

- **B0（P0）**：worker 就是现有 stdio 循环的“无 TUI 版”——自己把自己挂到任务目录上，stdin 从控制 socket 来，事件写 `events.jsonl` 与 `worker.log`；TUI 通过 attach 连 socket，能断线重连。
- **B1（P1）**：加 OS 服务包装（可选），把“登录后自动接管未完成任务”做成显式开关，而不是默认行为。

理由：复用既有 JSONL 协议与 `Agent`；不用新写一个执行引擎；01 的任务目录天然是 worker 的家；07 可以在同一 socket 上升级帧；失败时仍能退回前台模式。

## 5 最小实现切片

### 任务目录（与 01 共用）

```text
.vibe/sessions/<session>/
  tasks/<task_id>/
    meta.json        # TaskRecord（01），updated_at 兼作心跳
    worker.pid       # 独占创建；内容为 pid + start time
    worker.lock      # flock 或 O_EXCL；防双开
    ctrl.sock        # AF_UNIX JSONL v1 透传
    events.jsonl     # 追加协议事件（session 事件也写这里）
    worker.log       # stdout/stderr/诊断；大小上限 + 简单轮转
    trace.jsonl      # 既有 trace
```

### 生命周期（建议状态机）

```text
created → starting → running ⇄ waiting_approval
                 ↘ stopping → exited(0) / failed(nonzero) / killed(signal)
running → interrupted（worker 消失但无终态事件）
```

- `worker.pid`/`worker.lock` 双重防双开；启动时若 pid 不存在或锁陈旧，先把旧任务标 `interrupted`，再由 01 决定 `resume`。
- 心跳：每 N 秒更新 `meta.updated_at`；supervisor/attach 以“心跳过期 + pid 不存在”判死。
- 退出：SIGTERM → 优雅收尾（发 `closed`、flush events）超时后 SIGKILL；kill 后目录仍可读，状态从 events 重建。

### 接口草案

```text
python -m app --worker --task-id <id> [--workspace PATH]  # 后台执行一个任务
python -m app --attach <id>                               # 连 ctrl.sock：先回放 events.jsonl，再收 live
python -m app --tasks [--status running|paused]           # 只读列表（01 的 list）
```

- attach 幂等：多个客户端只读回放 + 一个控制者（审批/取消）；控制权用 socket 上的一次性 token 约定，不做多主。
- P0 不做审批收件箱：worker 等待审批时事件写 `waiting_approval`，attach 后由在线客户端应答；无人 attach 时按 03 的“无人值守审批”策略处理（默认 fail closed）。

### 修改点

- 新增 `app/runtime/worker.py`（入口/锁/日志/socket）与 `app/runtime/supervise.py`（pid/心跳/判死，可纯函数化便于测试）。
- `app/stdio.serve()` 抽出“连接无关的循环”：事件写文件 + socket；TUI 仍是同一 JSONL（不改协议语义）。
- `app/__main__.py` 增加 `--worker/--attach/--tasks`；oneshot 未来可复用 worker 入口（P1）。
- `tui/src/backend.ts` 增加“连 socket”启动模式与断线重连（P1；P0 允许仅 CLI attach）。

### 测试点

- 双开：第二个 worker 启动被锁拒绝，退出码可区分。
- 心跳/判死：kill -9 worker 后 `--tasks` 显示 `interrupted` 而非 running；陈旧锁可被安全回收。
- attach 回放：断开期间事件不丢；重连后顺序与 events.jsonl 一致。
- TUI 退出不杀 worker：模拟 TUI `shutdown()` 后 worker 继续到段结束（用 fake provider）。
- 日志上限与轮转；SIGTERM 优雅；SIGKILL 后目录可重建状态。
- 既有回归：前台模式行为不变，`tests/test_stdio.py` 与 TUI 通信测试通过。

### 明确不做

- 不做多主 attach、不做远程网络控制、不做跨机器迁移；
- 不做自动无限重启（最多一次可配置重启，且必须在 01 的状态机里表达）；
- 不改审批/沙箱边界（03/04），不在 P0 引入 OS 服务安装。

## 6 与其它块的关系

- **01** 提供 task_id、任务目录与状态机；02 只负责“把某个 task 跑起来并看住它”。
- **05** 决定 worker 重启后从哪里继续：GoalRecord 的段账本是恢复输入；02 不解释目标。
- **06** 在 worker 退出/崩溃时仍要能结账：账本应由事件实时写入（06），而不是依赖进程优雅退出。
- **03/04/07/08**：审批通道要能被 attach 客户端应答；隔离决定 worker 的权限边界；07 定义 socket 上的帧；08 用 kill -9/断网/磁盘满等故障注入验收 02。

## 7 与子 Agent 的关系

**A 谁发起**——子任务由 01 的任务 API 创建：用户显式或父任务内部派生；子 Agent 不自行派生（不嵌套）。**B 能力边界**——M1 只读子 Agent 建议先在同进程运行（现有 app/session/auditor.py 形态，审批为 None），进程隔离收益不足以先付出 worker 成本；需要“独立 worktree 写”时再用独立 worker（02 的机制），共享工作区写不做。**C 结果可信度**——子进程的输出只是 claim；父进程回收时用事件/trace/证据复核，且子崩溃不能杀死父。

对 02 的要求：子任务节点必须有独立 pid/lock/日志/退出码（如果是独立进程），否则无法回答“它为什么没结果”。规格见 docs/specs/multi-agent-computer-use.md:284-340,402-427。

## 8 开放问题与验收标准

待用户拍板：

1. worker 的默认归属：每个 task 一个进程，还是每个 session 一个常驻 worker 跑多任务？（推荐 task 一进程，隔离简单）

回答：给我解释两个设计的区别。对于项目能力的限制等

2. TUI 退出后的默认行为：继续后台跑，还是提示“任务将转入后台”并需要确认？

回答：提示“任务将转入后台”并需要确认

3. attach 的控制权：只容许一个控制者是否足够？是否需要“只读观察者”模式？

回答：结合产品，给出你的建议

4. 崩溃后自动恢复的边界：只恢复状态等待 `/resume`，还是允许自动重跑“未开始”的段？

回答：检测到有正在运行的任务，弹出提示吧 让用户确认。

5. 日志与事件保留期：按会话保留，还是按大小轮转？（涉及隐私与磁盘）

回答：按照会话保留吧， 方便后续trace。

验收条件（建议）：

- 启动后台任务后关闭 TUI：进程继续，事件持续写入；重开 attach 能看到进度并可取消。
- kill -9 TUI 不杀 worker；kill -9 worker 后任务状态为 `interrupted`，不会出现“假 running”。
- 双开被拒绝；陈旧 pid/锁可自动回收；worker.log 有可定位的退出原因。
- 前台模式零回归；无孤儿进程（退出后进程组检查为空）。

## 9 决策记录（2026-11-03）

1. **worker 归属（Q1 解释与建议）**：

| 维度 | 每 task 一个 worker | 每 session 常驻 worker |
|---|---|---|
| 隔离 | 进程级；任务崩溃/被杀/超预算互不影响 | 共享进程；一个任务卡死或崩溃会拖垮全部 |
| 审批归属 | 一 task 一套连接/收件箱/审计，归因简单 | 多任务共用连接，需要把 ApprovalGate 改造成多租户 |
| 启动成本 | 每任务一次启动 + import + MCP/技能初始化（可通过懒加载/缓存缓解） | 一次启动复用；但需要并发调度、资源配额与状态复用 |
| 与现状契合 | 贴合当前“一连接一 active run”“一个 backend 一个 Agent”的语义（`app/stdio.py:633-642`） | 需要把 Agent loop、sandbox 代理、MCP 生命周期与事件协议改成多 run 并发；当前没有这些原语 |
| 恢复/爆炸半径 | 单任务失败只影响自身 | 整 worker 崩溃影响所有任务；恢复要重建多任务状态 |
| 结论 | **P0/P1 采用**；另设 per-workspace supervisor 只做 registry/健康检查/attach 入口，不执行任务 | 推迟；等短任务很多、启动成本成为瓶颈时再评估 warm pool |

2. **TUI 退出（Q2，已确认）**：存在运行中任务时提示“任务将转入后台”并需确认；确认后 worker 继续，TUI 断开不取消；选择“停止任务并退出”走 cancel 语义，已写内容不回滚但记录审计。
3. **attach 控制权（Q3，已确认）**：**一个 controller + 多个只读 observer**。attach 时声明 `mode=control|observe`；controller 通过租约/心跳持有，只有 controller 能审批、steer、cancel；observer 可看事件与审批列表但不能决定。controller 失效后需显式 `--take-control` 或用户确认才能接管；接管写 `control_taken` 事件与审计，避免两个终端同时批准。
4. **崩溃恢复（Q4，已确认）**：启动/attach 时检测到“运行中”任务，弹出提示让用户确认（attach/恢复到暂停/resume/cancel）；不自动重跑已执行段；仅对“尚未开始或尚未执行动作”的段允许用户确认后重试。
5. **日志保留（Q5，已确认）**：按会话保留，便于后续 trace；但必须有软上限与显式清理：会话 events/trace 可按大小轮转或压缩，审批/审计记录保留更久；删除/清理必须显式操作并记录，不静默丢弃。
