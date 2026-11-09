# P1：输入与审批控制

状态：代码、自动验证与真人核心交互通过，缩放扩展场景及 Owner 学习记录待补，见 [验证记录](verification.md)。来源：[工作台 UX 路线图 P1](../../roadmaps/improve-workbench-ux/roadmap.md#p1--输入与审批控制)。

目标：用户的文字只有在显式进入审批动作区后才可成为审批选择；命令未受理不丢草稿，阻塞动作可发现。
范围：U01/U02/U03，以及 U07 的 reset 准确文案；保持后端 ApprovalGate 权威。
本次不做 Plan 新审阅卡、命令菜单重构、完整权限管理、侧栏联动或框架迁移。

公共验证边界沿用已批准路线图：Ink 键盘/粘贴输入 → 可见草稿/焦点/提示及 BackendClient 请求；Python stdio → permissions reset 输出及有效权限。
遵循项目先失败测试、实现、评审、核验顺序。已有未提交 Plan 改动已另存基线，不能作为本次成果。
