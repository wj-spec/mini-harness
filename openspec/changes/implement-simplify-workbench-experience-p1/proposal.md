# Proposal

## Why

每轮 Agent 结束都出现独立回执，打断连续对话；前端基于文本推断验收，与后端计划验收可能不一致。落实[体验简化阶段 1](../../roadmaps/simplify-workbench-experience/roadmap.md)，让重要结果可见、普通对话安静。

## What Changes

- 增加可版本化的结果事实，覆盖文件变更、验证、工具异常、拒绝与影响待核对。
- 对话只在有改动、操作影响或异常时显示紧凑摘要，证据保留在既有检视入口。
- 状态区、检视、历史与 attach 统一结果语义；旧记录保守读取。
- 自动续段聚合任务结果，不重复显示每段终态卡。

## Capabilities

### New Capabilities

- run-outcome-presentation: 基于结构事实的运行结果分层及证据可达性。

### Modified Capabilities

无；openspec list --specs 返回 No specs found。

## Impact

core/task 纯结果逻辑，Agent 及 stdio/worker 编排、轮次/检视持久化，Ink 协议与展示。无新增运行依赖，不改变 ApprovalGate、完成守卫、预算或模型执行策略。
