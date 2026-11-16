# 阶段 5.B：目标总预算与前后台恢复验证

日期：2026-11-13。状态：工程实现与自动验证完成；阶段 5.C MCP 生命周期尚未实施，真人终端体验另列。

## 交付行为

/settings 中按 g 切换单段和目标分组，目标分组编辑总段数、总时长、总 token 和估算总费用；分组草稿独立保留。保存仅写当前组，经过 ApprovalGate、版本复核、原子替换与 0600 权限；应用只影响未来新目标，进程环境覆盖优先。

TaskRecord 的 goal_limits 固定目标预算，created_at 为总时长起点，BudgetLedger 为累计用量。TaskRuntime 在创建新段前统一检查，普通 /resume、指定 goal 续做和 worker 子后端均受约束。达到上限后手动恢复不重置预算；拒绝不新增段或模型请求。worker 收到前置拒绝会结束，不再等待不存在的 done。

前台 task_state 对齐后端预算和段前累计用量；重启从匹配任务和账本恢复，不自动运行。旧目标优先迁移 taskId/objective 匹配的 goal.json；旧 GoalRecord 的已知非零预算可迁移，缺失项与含义不明的零值沿用启动默认并提示。已有快照损坏则拒绝续做。

## 验证证据

| 检查 | 结果 | 本地证据 |
| --- | --- | --- |
| Python 目标设置专项 | 17 项；数值边界、保存/拒绝/文件漂移、字段保留、环境覆盖、快照不可变、段数/时长/token/费用、历史迁移、损坏拒绝、前置拒绝无模型调用 | tests/test_goal_settings.py；.vibe/settings-p5b-goal-final.log |
| runtime/worker/stdio 关联回归 | 103 项通过；其中早期沙箱内 worker socket 测试失败，放到可绑定本机 socket 的环境后通过 | .vibe/settings-p5b-runtime2.log |
| Ink 目标与设置专项 | 18 项；手动恢复上限、恢复后台累计用量、g 分组、双草稿、分组保存、32×12 布局 | .vibe/settings-p5b-ink2.log |
| App 旅程补验 | 2 项；后端预算对齐后不自动越限、拒绝后解除活动态并可新建目标 | .vibe/settings-p5b-app.log |
| 真实 worker + 子后端 | 前台已用尽 1 段，后台环境改为 999 段仍返回 task_budget_exceeded；段数保持 1，无模型文本，退出 0 | tests/test_worker.py::test_real_worker_respects_exhausted_foreground_goal_snapshot；.vibe/settings-p5b-worker-real.log |
| 真实 PTY | 80×24：指引→设置→g 目标预算→总段数改 3→审批保存→应用→原草稿返回→启动 goal→写入与检查两次审批→完成退出 0；任务快照为 3，虚构 Key 保留且不回显 | .vibe/settings-p5b-demo-verified.log、settings-p5b-demo-terminal.log |
| Python 最终全量 | 2039 passed / 4 skipped，退出 0；1 条既有 tarfile DeprecationWarning | .vibe/settings-p5b-python-delivery.log |
| Ink build + 全量 | 790/790，0 failed / 0 skipped，退出 0 | .vibe/settings-p5b-build-full.log、settings-p5b-ink-full.log |
| 文档与规格 | OpenSpec strict、链接/锚点、diff 空白检查 | .vibe/settings-p5b-checks.log |

## 失败与修正记录

- 首次专项因新模块缺失收集失败；实现初期字段校验插入错误位置导致 UnboundLocalError，修正后关联回归通过。旧 payload 精确字段测试按新增 goal_budget 更新。
- 旧 GoalRecord 默认零值可能取消启动 token/费用保护，新增测试先失败（settings-p5b-legacy-red.log），修正迁移规则后 17 项专项通过。
- 早期 Python 全量 2038 项通过；加入历史零值回归后，一次完整运行暴露既有代理测试假设一次 recv 就能得到响应体。证据只收到 HTTP 头，断言未收到 hello。修正测试为读取到连接结束，不改代理实现，代理 9 项专项和最终全量通过。失败日志 settings-p5b-python-final.log 保留，最终日志另存。
- Ink 全量对应最终前端构建；最后的兼容修正与测试修正只涉及 Python，无重复前端改动。

## 实际限制

- 总预算在段开始前检查，已开始的一段仍受单段限制；累计 token/费用可能在一段内越过总额。总时长包含暂停和审批等待。费用依赖单价，不能替代服务商硬额度。
- 本次没有修改用户真实 .env，没有真实 Key、付费请求或远程 MCP 操作。PTY 使用隔离目录与虚构凭据。
- 已有进程的默认值不跨进程热同步；交接中的目标按不可变任务预算继续。不存在可信历史值时无法还原未知预算，使用固定启动值并明确提示。
- macOS 自动 PTY 不代替真人终端和跨平台体验。阶段 5.C 仍需独立实现 MCP 生命周期与失败回退。
- 未提交、推送或归档。
