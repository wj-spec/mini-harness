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

## 结论与建议

- 该任务规模下，两种方式都正确；delegate 额外增加 token 与延迟，未体现收益。
- 因此维持**默认关闭**（`VIBE_SUBAGENT_DELEGATE=0`），仅在更大/更脏的仓库调研场景试用。
- 下一步若要证明收益，应换更大任务（多文件、需要并行探索或上下文分区），并记录「主 Agent 上下文占用/正确性」而不只是 token；这属于需要预算与设计的评估，见 [open-decisions](open-decisions.md) 第 1 项。
- 复用：`VIBE_MODEL=deepseek-flash .venv/bin/python3 scripts/delegate_ab.py --runs 1`。
