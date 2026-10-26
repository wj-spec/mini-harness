# W6：验证个性化的实际收益

日期：2026-10-26。状态：v1 对照已完成（主假设未获支持）；v2 触发词偏好与多次运行聚合工具就绪，待用户跑。

## 目标

选择至少三组有代表性的真实任务，在初始条件可比的新会话中，对比启用和停用个性化后的表现；记录必要背景补充次数、纠正次数、任务正确性和实际评价。只有重复交代或纠正减少，且正确性和审批边界没有回退，才认定有收益。

## 对照设计

每组任务跑两次，一次启用个性化、一次停用；工作区、提示词、模型配置一致。

| 任务 | 提示词 | 考察偏好 |
|---|---|---|
| task-a | 解释 Ink 的 `/about` 命令实现（不改文件） | 回答先给结论、中文简洁 |
| task-b | 给 `/about` 增加 `/about edit <序号> <新文本>` 并补测试 | 改动前先给方案、注释简洁 |
| task-c | 检查 `load_user_profile` 对重复/空白条目的处理，有问题就修并补测试 | 根因定位、验证 |

启用组：工作区带项目背景（`.vibe/project-profile.json`），`VIBE_USER_PROFILE` 指向 3 条 v2 已确认偏好（解释、修改、测试三类触发词）；夹具与任务定义在 `scripts/w6_preferences.py`，由 `scripts/w6_run.py` 生成，`tests/test_w6_preferences.py` 离线验证每个任务只选匹配条目。
停用组：工作区无项目背景，`VIBE_USER_PROFILE` 指向空且 `enabled: false` 的 profile。

## 运行

```bash
cd /Users/endless/Desktop/self_project/mini-harness

.venv/bin/python3 scripts/w6_run.py task-a-on    # 启用
.venv/bin/python3 scripts/w6_run.py task-a-off   # 停用
.venv/bin/python3 scripts/w6_run.py task-b-on
.venv/bin/python3 scripts/w6_run.py task-b-off
.venv/bin/python3 scripts/w6_run.py task-c-on
.venv/bin/python3 scripts/w6_run.py task-c-off

.venv/bin/python3 scripts/w6_report.py           # 汇总对照表
```

每条结束后自动取证到 `.vibe/w6/evidence/`：run 状态/步数/tokens/工具调用、测试通过命令、用户消息数、git 状态、实际注入的背景与偏好；`trace.jsonl` 的 `user_profile_selection` 记录本轮实际选中的偏好。建议每个 task-on/off 各跑 **3 次**，用 `scripts/w6_report_v2.py` 看 min/median/max 分布，不用单次 token 差异下结论。

## 记录指标

- **补充说明次数**：会话里除首条任务外的用户消息数（自动采集）+ 你实际补充了什么（手动）。
- **纠正次数**：同上，手动标注哪些消息是纠正。
- **任务正确性**：测试通过、git 改动合理、无越界。
- **实际评价**：哪组更贴合、少了哪些重复说明、有无回退。

## 判定

只有当三组里多数任务的补充/纠正减少，且正确性和审批边界未回退，才认定个性化有收益；小样本结论不外推。

## 非目标

普遍效果宣称、自动从对话推断、替代项目规则。

## v2 初测：task-a（2026-10-26）

用户本机完成 task-a v2 的 3 次启用 / 3 次停用（`scripts/w6_report_v2.py`，旧 v1 证据已排除）：

| 指标 | 启用（3 次） | 停用（3 次） |
|---|---|---|
| 用户消息（补充/纠正） | 1 / 1 / 1 | 1 / 1 / 1 |
| 步数 | 11 / 13 / 14 | 6 / 8 / 14 |
| tokens | 242,500 / 260,704 / 347,203 | 117,641 / 173,872 / 306,890 |
| 工具调用 | 20 / 20 / 23 | 10 / 14 / 23 |
| 实际选中偏好 | 3 次选择 / 1 条 | 0 次选择 / 0 条 |

结论（仅 task-a，不能外推）：
- 用户消息数没有下降，主假设**仍未获支持**；
- 启用组中位 tokens/步数/工具调用反而更高，可能是偏好注入带来的额外注意力或运行波动，不能作为收益证据；
- 相关性选择符合预期：3 次都只选中解释偏好，停用组 0 次；
- task-b/task-c 尚未跑 v2，不能用旧 v1 证据补结论。
- 早期报告曾把 2026-10-24 的 v1 证据混入 n；已修复为只统计 `version=2` 且含 `profile_selections` 的证据。

下一步：跑 task-b/task-c v2 各 ≥3 次；若仍无重复说明/纠正下降，不进入 P5，优先改偏好质量、任务设计或评估指标（例如偏好遵循评分，而不只看用户消息数）。
