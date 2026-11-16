---
schema_version: 1
change: implement-simplify-workbench-experience-p1
branch: codex/receipt-ux-p1
generated_at: 2026-11-13T08:03:39.178938+00:00
decision_hash: c72164a6ed1b
---

# 决策纪要

## 目标态

问答与成功只读保持安静，修改与异常紧凑可查；实时、历史、接入和自动续段使用一致事实。

## 拍板决策

- D1 扩展 AgentResult.outcome 和既有轮次/检视快照；保留原 receipt 作为详情，不再用文本推断新结果。拒绝只改标题，因为无法解决计划验收与多个消费者漂移。
- D2 用实际变更记录、验证序列、工具执行/拒绝及审计构造事实。未验证、验证过期、工具异常、未知影响优先可见；不依赖 classify_task。
- D3 UI 使用同一个纯展示函数，输出摘要、语义色与证据入口；历史无字段保守显示“历史运行记录”并可查看原始文本。
- D4 自动续段只更新进度，最终结果汇总同一任务先前影响。前段检查仅在同一任务中明确覆盖后才能更新，无法确认的异常保留。
- D5 本次仅实现 roadmap 阶段 1。配置与引导不混入，已有工作区改动保留。用户本轮明确要求推进，继续完成实现与验证，不另行要求重复批准或自动提交。

## 承重约束

- C1 用户 2026-11-13 明确“好的 那么让我们推进吧”，承接已经给出的五阶段方案，从阶段 1 开始。
- C2 权威消费者：全仓 rg receipt/verified 定位 core/task/state.py、rounds.py，app/runtime/agent.py、inspection.py、worker.py，app/stdio.py，tui/src/protocol.ts、view-state.ts、workbench.tsx、agent-status.tsx、inspection.ts、sidebar/verification-content.ts。新结果路径均改；旧原始 receipt 保留为证据，完成守卫运行控制不改。
- C3 core/README.md 和 app/README.md 规定纯 core、app 编排、Ink 展示分层；AGENTS.md 要求测试先行与 ApprovalGate。
- C4 app/stdio.py _plan_result 在 Agent 结束后覆盖 verified；最终结构事实需同步计划验收，不能先锁定成功。
- C5 core/task/state.py files_changed 是去重列表；同文件跨轮修改不能仅靠该列表切片，需使用 file_changes 增量。
- C6 tui/src/app.tsx handleGoalDone 与 app/runtime/worker.py _handle_done 各自决定续段；必须在展示前知道续段状态，不能猜停止原因即认定自动续段。

## 接受的边角

旧记录无法补造结构事实；保留折叠原始记录并明确证据不足。未知工具副作用采用保守提示，不做 shell 意图分析。

## 三镜代价

结构化方案增加协议与测试维护量（系统），减少重复与误导结论（用户），统一消费函数减少后续文案改动分叉（开发）。文本过滤改动小但长期漂移更多。优先事实可靠，再减少界面干扰，最后控制迁移成本。
