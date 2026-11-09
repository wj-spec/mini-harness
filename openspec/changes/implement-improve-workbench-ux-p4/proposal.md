# P4：反馈驱动的计划修订

状态：实施中；同族版本提交、只读修订、后台锁内准备、Ink 反馈编辑及重开后的修订记录已接通。确定性跨层验证覆盖取消、重开、显式续做和原内容冲突；受控真实模型修订、Ghostty 真人与 Owner Review 待完成。P3 核心演示已获用户确认，新审批按钮、P2 真人和 P3 原生恢复仍待反馈。
来源：[路线图 P4](../../roadmaps/improve-workbench-ux/roadmap.md#p4--plan-反馈修订)、[设计决策 4](../../roadmaps/improve-workbench-ux/design.md#决策-4plan-审阅绑定版本批准并执行是一项可恢复操作)、[Plan UX S2](../../../docs/specs/plan-ux.md)。

## 问题与交付

启动本阶段时，request-changes 只把旧计划复制为带反馈的 draft，用户仍需另开规划任务；普通 submit_plan 又生成新 plan_id/v1，丢失同一计划的版本链。目标旅程是：在所见卡片输入反馈，确认后启动一次只读修订；模型读取原计划与反馈，提交同 plan_id 的 v+1 proposed，再由用户审阅批准。

1. 所有修改请求绑定 P3 ReviewRef，不重新猜测“最新计划”。普通草稿与反馈编辑分别保存，取消反馈不发送任务。
2. 修订 run 只开放现有 plan profile 工具。修订过程不写工作区、运行命令或自动执行新计划。
3. 原计划原样保留；只有模型成功提交并校验后才发布新版。新版不继承 approval、执行台账或验收结果；缺省业务字段保留原值，显式修改进入新审阅。
4. 重复确认、回执丢失和重开查询返回同一修订操作；失败/取消保留反馈，没有新 proposed 就不能显示“修订完成”。续做需显式动作，不因重连自动调用模型。
5. 修订中防止同族审批/执行竞争；新版本发布后旧准确 Ref 失效，旧管理入口也不能隐式回退执行旧 approved 版本。

复用既有 ToolRegistry/Plan 产物、PlanReview、TaskRuntime、JSONL 与 Ink 键盘 seam。按已授权的持续开发和先测试规则纵向推进，当前不扩展到通用问答平台或跨会话修订。

实现、失败恢复和验收分别见 [design](design.md)、[tasks](tasks.md)、[verification](verification.md)。工程接线与确定性验证已完成的范围、真实模型和真人待验收范围分开记录。
