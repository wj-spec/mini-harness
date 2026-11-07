# 只读子 Agent 委派 A/B（2026-11-07）

目的：验证 P2 opt-in 只读 `delegate` 是否在「多文件调研」任务上带来收益。脚本：[`scripts/delegate_ab.py`](../../scripts/delegate_ab.py)。

## 方法

- 临时 git 仓库 3 个文件：`README.md`、`docs/design.md`（标记 ZEBRA）、`notes.txt`（标记 FALCON）；
- 任务：输出 `RESULT=<项目描述>|<设计标记>|<运维标记>`，要求优先用 delegate（该条件）；
- 两个条件：`single`（不注入 `VIBE_SUBAGENT_DELEGATE`）与 `delegate`（=1，主 Agent 获得只读 delegate）；
- 指标：status、steps、tokens、耗时、`RESULT` 正确性（同时含 widget/ZEBRA/FALCON）、delegate 调用次数；
- 每条件 1 轮，deepseek-flash 与 deepseek-chat 各一次。

## 结果（2026-11-07，`--runs 1`）

| 模型 | 条件 | 正确 | steps | tokens | 耗时 | delegate 调用 |
|---|---|---|---|---|---|---|
| flash | single | ✅ | 3 | 7591 | 4.2s | 0 |
| flash | delegate | ✅ | 3 | 8989（+18%） | 10.6s（+152%） | 1 |
| chat | single | ✅ | 3 | 7279 | 3.7s | 0 |
| chat | delegate | ✅ | 4 | 11120（+53%） | 8.2s（+122%） | 1 |

## 更大任务（digest，12 个模块文件 × ~28 行）

`--task digest`：`mods/module_00..11.md`，要求输出 12 项摘要 + `RESULT=4|9`（alice 模块数、最大 PRIORITY）。

| 模型 | 条件 | 正确 | steps | 主 Agent tokens | 含子 Agent 有效 tokens | 耗时 |
|---|---|---|---|---|---|---|
| flash | single | ✅ | 6 | 35699 | 35699 | 12.9s |
| flash | delegate | ✅ | 6 | 37926（+6%） | 57998（+62%） | 23.7s |
| chat | single | ✅ | 6 | 26562 | 26562 | — |
| chat | delegate | ✅ | 5 | 21091（−21%） | 36770（+38%） | — |

## 结论与建议

- 小任务（survey）：两种都正确，delegate 总 token 更高（+18%/+53%），无收益。
- 较大任务（digest）：两种都正确；delegate 在 chat 上把**主 Agent 上下文减少 21%**、少用 1 步，
  但**总 token 仍高 38%**；flash 上主 Agent token 还略增。即「上下文隔离」可能降低主 loop 负担，但整体成本上升。
- 结论：维持**默认关闭**（`VIBE_SUBAGENT_DELEGATE=0`）；delegate 更适合「超大仓库/上下文吃紧」而非一般任务。
- 是否投入更多预算扩大样本（更多轮、更多仓库形态），见 [open-decisions](open-decisions.md) 第 1 项。
- 复用：`VIBE_MODEL=deepseek-flash .venv/bin/python3 scripts/delegate_ab.py --task digest --runs 1`；
  加 `--markdown-out report.md` 可另存 summary 表格。
