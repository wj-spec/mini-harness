# Design

## Context

动机见 proposal.md。现有 AgentResult 只有文本回执和 verified；计划验收可改写结论，files_changed 去重列表不能表示再次修改。

## Goals / Non-Goals

目标：新增结构化事实并统一呈现，保留证据；非目标：修改完成守卫、任务控制与配置。

## Decisions

决策与替代方案见 [decision-memo.md](decision-memo.md)。

### 数据与职责

~~~text
Agent 工具/变更/验证 → core outcome 事实 → stdio 计划验收与任务聚合
                                         ↓
                          done / rounds / inspection 持久快照
                                         ↓
                          Ink outcome 展示函数 → 正文/状态/检视
~~~

outcome v1 包含 files、omitted_files、verification（not_run/passed/failed/stale）、checks（命令摘要哈希/检查状态/修改序列）、mutation_seq、operations、tool_errors、denied、audit_warning、prior_unknown。字段只描述事实，不存凭据或完整命令。错误/停止使用 RunResult 的既有 status/stop_reason。计划验收单独在最终结果中补 plan_verification，不能用 Agent 的提前布尔值替代。

文件取 file_changes 增量；检查按同一命令最新结果并结合最后修改序列计算，失败不能被另一条成功检查掩盖。普通命令/外部操作没有 diff 也算操作；实际执行失败、拒绝与审计异常保留。core 不做 I/O。

stdio 合并修复尝试与同一 task 前一段的持久 inspection outcome；复用任务 segment/request 身份，旧证据缺失明确标记。新的用户任务不继承上一任务摘要。自动续段由真实调度方明确标记，在下一段开始后折叠前段摘要；最终摘要保留前段影响。前台和 worker 在确定续段后提供相同语义，原始回放记录保留。

Ink 新增纯展示函数；新字段缺失/未知版本保守降级，不解析自然语言推断成功。紧凑条通过原有变更/验证入口查看细节；只读成功把证据入口挂在回答，不生成独立卡。

## Risks / Trade-offs

- 旧/坏结构数据 → 不信任成功结论，保留原始记录。
- 多处状态不一致 → 同一展示函数并验证计划验收覆盖。
- 聚合丢失前段证据 → 以 task+segment 精确读取已有 inspection，缺失则标未知。
- 历史/接入重复 → 复用 request 身份去重，不按文本相等合并。
- 保存失败 → 继续可见的错误结论，不伪造持久化成功。

## Migration Plan

新增可选字段，原 receipt 保留；新旧读端兼容。回滚展示不删除证据。记录新字段到已有 rounds/inspections，不建第二份事件库。
