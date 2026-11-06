# 审批收件箱（ApprovalInbox，P0）

日期：2026-11-03。状态：P0 基础 + P1 的 worker 接线、stdio/TUI `/approvals` 只读列表已实现（approval_request→inbox、attach 决定→resolve、无决定超时 expire + task failed(approval_timeout)）；TUI 收件箱决定 UI 仍待后续。
对应讨论稿：[03 无人值守审批](../design/long-horizon/03-unattended-approval.md)（§9 已确认）。
依赖：[task-runtime](task-runtime.md)、[process-supervision](process-supervision.md)。

## 0 第一性原理与不变量

1. **审批是异步事实，不是连接状态**：请求不能只存在于当前 socket 的 Future；断开/重启后必须能列出、决定或过期。
2. **一次批准只执行一次**：`approval_id` 一次性；迟到/重复决定返回 mismatch，不产生动作。
3. **归属可审计**：必须能回答 `task_id`、`requester`、`decider`、`tier`、策略哈希、预览 hash。
4. **预览默认只存引用**：正文可能含代码/路径/秘密；默认存 `preview_ref + preview_hash`，需要落正文时脱敏并限 8KB。
5. **过期由 TaskRuntime 结算**：required 审批过期 → 任务 `failed(approval_timeout)`，动作不执行；收件箱只提供事件与状态。
6. **不改 ApprovalGate 语义**：审批矩阵/scope/T0–T4 仍由 `ApprovalGate` 判定；收件箱只改变“等待的位置”。

## 1 范围

**P0（本切片）**

- `app/runtime/approvals.py`：`<session>/tasks/<task_id>/approvals.jsonl` append-only；
- `submit/list/pending/resolve/expire`；一次性 `approval_id`；请求/解析/过期事件；
- 每任务 pending 上限 `VIBE_APPROVAL_MAX_PENDING`（默认 100，范围 1–1000），超限 fail closed；
- 归属字段：`requester/decider`、`task_id/session_id/agent_id/subagent_id/delegation_id`、`tier/decisions/policy_hash/project_policy_hash`；
- 预览：默认 hash + ref；可选脱敏正文 ≤8KB；确定性 secret/HOME 过滤；
- 损坏行跳过；task 目录隔离。

**P1（后续）**

- `_Connection.approve` 在后台/无连接模式写入收件箱并等待；
- worker 审批接线：`approval_request` → inbox；attach 决定 → resolve；无 controller → 超时 expire + task failed；
- 协议 `approval_list/approval_decide`（07）、TUI `/approvals` 收件箱；
- task 级 deadline 独立于 `max_run_seconds`；清理责任归 TaskRuntime（终态/预算过期时 expire）。

**非目标**：全自动模式；子 Agent 独立审批权；跨机器审批；改写 ApprovalGate 矩阵。

## 2 记录格式（`approvals.jsonl`）

```jsonc
{"schema_version":1,"type":"requested","approval_id":"a-…","task_id":"t-…","session_id":"…",
 "tool":"bash","tier":"exec","decisions":["once","session","workspace","deny"],
 "requester":"parent","preview_hash":"…","preview_ref":"messages.jsonl#step=7",
 "preview_redacted":null,"expires_at":null,
 "agent_id":"parent","subagent_id":null,"delegation_id":null,
 "policy_hash":null,"project_policy_hash":null,"created_at":"…"}
{"schema_version":1,"type":"resolved","approval_id":"a-…","task_id":"t-…",
 "decision":"once","scope":"once","decided_by":"user","decider":"user","decided_at":"…"}
{"schema_version":1,"type":"expired","approval_id":"a-…","task_id":"t-…",
 "decided_by":"system","decider":"auto","terminal_reason":"approval_timeout","decided_at":"…"}
```

- 一条逻辑审批 = `requested` + 最多一个 `resolved/expired/cancelled`；`items()` 折叠为最新状态；
- `status` 不在文件中持久化，由事件序列派生（`pending/resolved/expired/cancelled`）；
- `preview_redacted` 只在 `include_preview=True` 时写入，且已脱敏、截断 8KB；
- 不写命令输出/模型文本全文/密钥；`preview_ref` 指向会话消息/trace 偏移。

## 3 API

```python
class ApprovalInbox:
    def submit(task_id, *, approval_id=None, tool, tier=None, decisions=None,
               requester="parent", preview="", preview_ref=None, expires_at=None,
               session_id=None, agent_id=None, subagent_id=None, delegation_id=None,
               policy_hash=None, project_policy_hash=None, include_preview=False) -> dict
    def items(task_id=None) -> list[dict]       # 折叠后状态
    def pending(task_id=None) -> list[dict]
    def resolve(task_id, approval_id, *, decision, scope="once",
                decided_by="user", decider="user") -> dict
    def expire(task_id, *, reason="approval_timeout") -> list[dict]
```

- `resolve` 对已解析/过期/不存在 → `ApprovalMismatch`/`ApprovalNotFound`；
- `expire` 对所有 pending 写 `expired`（幂等：第二次无 pending 返回空）；
- `redact_preview` 确定性替换 secret/HOME；测试覆盖。

## 4 失败与边界

| 场景 | 行为 |
|---|---|
| 重复/迟到 approval_id | `ApprovalMismatch`，不写 second resolution |
| 损坏行 | 跳过；pending 仍可读 |
| 进程崩溃 | requested 保留；恢复后 `pending()` 可列出；不自动执行 |
| task 终态仍有 pending | 由 TaskRuntime 调用 `expire` 清理（P1）；P0 API 支持 |
| 预览含 secret | 默认不落正文；显式落正文时脱敏 |
| 多 task | 每个 task 独立文件；`pending(None)` 跨 task 汇总 |

## 5 测试

- `tests/test_approvals.py`：submit/items/pending、resolve 一次性、mismatch/notfound、expire 幂等、损坏行、脱敏、8KB 截断、task 隔离。
- 后续 P1 接 worker/stdio 后补集成测试。

## 6 验收标准

- pending 请求在进程重启后可列出（文件存在）；
- 同一 approval_id 只能 resolve 一次；错误/迟到决定不产生动作；
- expire 后 pending 清空且条目状态正确；
- 预览默认无正文；显式正文已脱敏且 ≤8KB；
- 全量回归通过。

## 7 后续 P1

1. worker 接线：`approval_request` → inbox；attach 决定 → resolve；无 controller → expire + task failed；
2. `_Connection.approve` 后台模式等待 inbox future；
3. 协议/TUI 收件箱；
4. TaskRuntime 清理责任：终态/预算过期/崩溃恢复时 expire。

## 8 风险与未决

- **approval_id 生成**：P0 可客户端生成；服务端需校验/幂等（P1）。
- **预览正文**：脱敏规则是确定性的但可能误伤/漏检；正文默认不落盘。
- **多客户端**：controller/observer 权限由 02/07 约束，收件箱只存事实。
- **任务级 deadline**：字段已有 `expires_at`，执行点 P1 与 03 对齐。
