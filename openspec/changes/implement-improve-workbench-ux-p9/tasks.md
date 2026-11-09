# P9 执行清单

- [x] 核对现有 ApprovalGate/PermissionPolicy//permissions 文本与审计结构；保存基线。
- [x] 后端只读概览：workspace/session grant 的稳定 id、工具与目标摘要、档位来源、持久规则与错误；`permissions`+args 结构化，不带 args 保留文本。
- [x] 后端逐条撤销：workspace / session；未知、已撤销、跨工作区 id 拒绝；回执带 revoked 与撤销后概览。
- [x] 能力协商 `permission-panel` 与 TypeScript 严格校验；旧后端回退文本结果。
- [x] TUI 面板：档位来源/有效期、授权列表、逐条撤销、剩余权限、持久策略只读说明、失败与迟到回执。
- [x] 先红后绿：Python/stdio/BackendClient/App 测试；完整回归通过；fake PTY 本沙箱不可用（PTY 环境失败与基线相同）。
- [x] 文档：verification、roadmap/MILESTONES/task-log/USAGE/验收清单同步；证据归档到 `.vibe/ux-p9-panel-2026-11-09/`。
- [x] 持久 user 策略规则撤销：稳定 ur-/pr- id、可撤销性展示、原子写回用户策略文件、project 规则拒绝、成功后重载并返回剩余概览。
- [x] TUI 持久规则撤销：user 规则纳入可撤销项、二次确认、剩余策略回执、Esc 取消；transport/实际 Python 覆盖。
- [ ] 项目策略写回、撤销独立审计、跨进程同步与真人终端验收。
