# 结构化 Plan 产物 P0（schema / 存储 / 渲染）

状态：2026-11-06 P0 实现（schema/存储/审批/执行绑定/TUI 覆盖层）；真模型 P1 待后续。
设计来源：[12 结构化 Plan 产物](../design/long-horizon/12-plan-artifact.md)。

## 已实现

- `app/runtime/plan.py`
  - `Plan` / `PlanStep` dataclass（plan_id/task_id/version/status/objective/source/
    acceptance_criteria/budget/approval/steps 等）；
  - `Plan.from_dict` 严格校验：
    - 必填 objective/plan_id/task_id；
    - step_id 唯一、depends_on 必须存在且无环；
    - files 必须是相对路径（拒绝绝对路径/`..`）；
    - step kind ∈ read/edit/run/verify，risk ∈ read/write/exec/network；
    - acceptance_criteria id 唯一、confirmed 必须 bool；
    - verification_plan 最多 50 条，id 唯一、command 非空且 ≤4000 字符、
      covers 指向已知 criteria、source∈plan_approval/project_profile；
    - 未知 schema_version fail closed。
  - `save_plan` 原子写 `task_dir/plan/plan.v<version>.json`；首次创建要求版本不存在，覆盖更新必须提供修改前的 `expected` 快照；
  - `load_plan` / `load_plan_ref`；读写上限 4 MiB，旧产物缺 created_at 时读取使用空标记保持身份稳定，不在查询中回写；
  - `plan_hash()`（canonical JSON sha256）；
  - `render_plan()`：目标、状态、验收标准（候选/已确认）、全部步骤（不截断前 5 条）。
- `submit_plan` 工具（仅 plan profile 的 run 注册）：
  - 模型提交 objective/steps/criteria 等，校验后写任务目录并置 proposed；
  - step schema 暴露可选 risk；新提案缺省风险按 read→read、edit→write、run/verify→exec 补齐，低于步骤类型的 read/write 提示被提升；显式 exec/network 保留。旧产物缺省值不迁移，风险提示不授予工具权限；
  - 返回 `plan proposed: <plan_id> v1`，不执行工作区写；
  - run 结束注销，act/chat 不可见。
- 测试：`tests/test_plan_artifact.py`（roundtrip/hash/原子写、6 类非法输入、环检测、
  unknown schema、渲染、路径布局、submit_plan 工具写 proposed）。
- TUI `/plan`：
  - `/plan <任务>` 发送 `kind=run, mode=plan`；
  - `/plan` 打开计划覆盖层（默认折叠：目标/状态/批准元数据/验收标准/验收命令 + 前 3 步，提示共 N 步）；
    批准元数据含 approved_by/approved_at/plan_hash 前缀/stale；proposed 计划在设置
    `VIBE_PLAN_REVIEW_TIMEOUT_SECONDS` 时显示审批剩余/过期；已有验收结果时显示 ✓/✗ 与 codes；
  - `/plan all` 打开完整步骤覆盖层；
  - 覆盖层支持 ↑↓ 选择、s 查看选中步骤、a 查看验收标准、e 预填修订命令、
    b 后台执行、v 查看版本列表、滚轮/PgUp/PgDn 翻阅、Esc 返回；
  - proposed 计划可用 `y` 批准 / `n` 拒绝（发 backend plan command）；
  - approved 计划按 Enter 直接执行（`/plan execute` 等价）；执行时选择**最新可执行**的 approved 非 stale 版本，
    最新版本不可执行会提示回退版本；无可执行版本时提示批准/修订；
  - `/plan list` 列出最近版本/状态；`/plan step <id>` 或覆盖层 `s` 查看单步定义与 execution 证据；
  - `/plan diff <a> <b>` 比较两个版本的目标/状态/步骤/审批差异；
  - `/plan acceptance` 查看验收标准与可信验证命令清单（不执行）；
  - `/plan edit-step <id> <title>` 修改步骤标题并生成 version+1 draft；
  - `/plan edit-step <id> --kind read|edit|run|verify`（CLI/协议可省略标题）修改步骤类型，同样生成新 draft；
  - 协议 `edit-step` 也接受 `files`（步骤文件范围）与 `depends_on`（依赖步骤）；
    修改前校验依赖存在性、自依赖与环；四类字段可单独或组合修改；
  - 完整审批卡片与右侧 sidebar 仍待。
  - `/plan approve`、`/plan reject` 通过后端 `plan` command 处理最近计划（stdio 本地 user 决定）。
  - `/plan execute` 读取最近 approved 计划，提交带 `plan_id/plan_version` 的 act run。
  - `/plan background` 准备后台 task（TaskRecord kind=run + plan binding），启动 detached worker 后 TUI 可退出；
    worker 的 run 帧携带 kind=run/mode/plan_id/plan_version，由 TaskRuntime 绑定计划门禁；
    worker 启动失败时 TUI 保留并提示 task 已创建，可稍后手动启动。

## 未完成（P0 剩余）

- `submit_plan` 已接 plan profile；TUI `/plan` 展示/审批未接；
- plan.review 审批门（13）与 `plan_proposed/plan_approved` 审计事件；
- 执行绑定/步骤证据（14）、计划验收（15）；
- TUI `/plan` 覆盖层已能折叠/展开、方向键选择、y/n 审批；打开计划时把 plan 摘要写入 sidebar signal，
  侧栏 header 显示 `计划 vN status done/total步`，有验收结果时追加 ✓/✗；
  按 `p` 打开计划步骤面板（↑↓/j/k 选择、p 返回变更）；
- legacy `TaskState.plan` 合成 legacy 计划；
- 预算/审批字段的执行层校验（D6/D9）。

## 存储约定

```text
.vibe/sessions/<session>/tasks/<task_id>/plan/plan.v1.json
```

每版本不可变；审批通过后只新增版本，不回写旧版本；`revise_plan` 生成 version+1 draft 并保留旧版。

## 计划提示词与提交门禁（2026-11-08 补充）

- `app/runtime/plan_policy.py`：plan profile 的 run 注入可配置 `[PLAN MODE]`
  system 段（默认含只读探索、对话同意不算批准、decision-complete 清单、
  `submit_plan` 唯一且最后调用）；`VIBE_PLAN_POLICY` 或
  `VIBE_PLAN_POLICY_FILE` 可覆盖，读取失败/超长 fail closed；run 结束恢复原 system。
- `submit_plan` 成功后 plan run 立即收尾（`Agent.stop_after_tools`）：同批剩余工具
  记为 `not_executed`，run `stop_reason=plan_submitted`；stdio 发送“计划已提交”通知。
- `Plan` 增加可选 `review_feedback`（最多 20 条）；`revise_plan(..., feedback=...)`
  写回反馈并生成 v+1 draft。
- `plan markdown` 输出审阅稿，供 TUI/侧栏展示；JSON 仍是唯一审批与执行绑定来源。

## 并发与失败写入保护（2026-11-08）

所有应用内 Plan 写入口共用 `<task_dir>/plan/review.lock` 非阻塞进程锁，同线程允许同步嵌套；锁不跨 await 持有、不删除锁文件。当前实测 macOS，平台无 fcntl 时旧管理入口保持兼容，但不能执行要求进程锁的新审阅动作。

修改前在锁内重读并比较完整快照；冲突时要求重新读取，不用旧内存对象覆盖现有状态。新修订不能覆盖已有 vN。写文件使用独占临时文件、内容 fsync、原子替换及 POSIX 目录 fsync；替换前失败保留原文件和调用者状态，不使用或删除其他临时文件。审计追加拒绝 symlink 和非普通/多链接文件。Plan 与审计不是跨文件事务，部分成功仍须由后续启动凭据核对。

准确 ReviewRef 查询、稳定正文分页、上述写入保护和持久化启动去重/恢复已有 runtime 回归；JSONL/Agent.run、TypeScript 协议与新审阅卡已接线。受控真实模型写入/验收及离线重开通过，Ghostty 真人待确认；前文 P0 覆盖层说明保留为历史，当前交互以 [使用指南](../USAGE.md) 与 [P3 实施与验证](../../openspec/changes/implement-improve-workbench-ux-p3/verification.md) 为准。

## P4 同族版本提交（2026-11-08，尚未接通反馈旅程）

`propose_revision()` 与 submit_plan 的修订注册参数已支持：绑定原快照/反馈/修订 ID，发布同 plan_id/task_id 的 v+1 proposed，新 approval 为空；模型不能指定身份或批准。省略的业务字段保留原值，原版不改写。共享进程锁下拒绝变化/错误来源、已有新版与不同操作覆盖；同操作复用已发布产物，并追加 `plan_revision_reused` 审计，不重新生成版本。

持久修订任务也已实现：`PlanRevision` 将准确来源与反馈绑定到唯一初始 Task，提供只读查询和携带已观察 segment 的显式续做；查询核对同族 v+1 产物，不重放提交。JSONL 与只读 Agent 已接通，完整来源/反馈固定在上下文中；未提交则暂停并记录 `revision_not_submitted`，旧 `/plan request-changes` 进入同一控制器，不再复制 draft。TaskRuntime 在同族锁内阻止活动修订期间启动执行及回退旧版；后台候选也过滤被替代/正在修订的计划。Ink 卡片反馈输入及后台并发排队复核仍待完成，证据见 [P4 实施包](../../openspec/changes/implement-improve-workbench-ux-p4/proposal.md)。
