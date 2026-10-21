# Coding Agent 公开评测集调研

核实日期：2026-10-21。只引用官方仓库、数据卡、官网与原始论文。本轮仅调研，没有下载题库或实现接入。

## 选择结论

本项目优先接 **SWE-bench Lite 的固定 10–20 题**，再扩展到 Verified；保留现有内部 eval 作为工具与执行流程回归。Lite/Verified 评的是“读取真实仓库、定位问题、改代码、运行测试”的完整闭环，更符合当前单 Agent 编码方向。小样本只用于项目迭代，不代表完整榜单分数。

| 公开评测 | 规模与任务形态 | 对本项目的用途 |
|---|---|---|
| SWE-bench Lite / Verified | Lite **test 300**（另有 dev 23）；Verified **500**。提供 GitHub issue 与修复前仓库，由补丁和测试判断是否解决问题 | 首选仓库级基线；先 Lite 小集，再跑更大的 Verified 集。[Lite 数据卡](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite/raw/main/README.md)、[Verified 数据卡](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified) |
| SWE-bench Multilingual | **300** 题、42 个仓库、9 种语言；延续仓库 issue 修复与 F2P/P2P 测试协议 | Python 流程稳定后检验跨语言工具能力。[官方介绍](https://www.swebench.com/multilingual.html) |
| SWE-bench Pro 公开集 | 当前 **V2 default 642** 题、11 个仓库；Harbor 格式和公开镜像，另有 HARD-51；原 v1 731 题保留。任务偏长程仓库修复 | 后期压力测试；务必固定版本，不能混用 V1/V2 分数。[官方仓库](https://github.com/scaleapi/SWE-bench_Pro-os)、[HF 版本数据卡](https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro/raw/main/README.md) |
| Terminal-Bench 2.0 | **89** 个终端任务，包括编码、调试、环境与系统操作；通过 Harbor 运行容器 | 后续验证命令工具和复杂终端工作流；范围超过纯编码。[原始论文](https://arxiv.org/abs/2601.11868)、[官方发布](https://www.tbench.ai/news/announcement-2-0) |
| Aider Polyglot | **225** 题、6 种语言，基于 Exercism 的代码编辑任务 | 可先用 Python 固定小集验证“读文件—编辑—测试”；用本项目 harness 跑出的结果不直接等同 Aider 排行榜。[官方说明](https://aider.chat/docs/leaderboards/) |
| EvalPlus：HumanEval+ / MBPP+ | HumanEval+ 延续 **164** 个 Python 函数生成题；MBPP+ v0.2.0 为 **378** 题（旧版 399）。主要增强测试覆盖 | 可作低成本模型编程能力基线，但不能代替仓库级 Agent 评测。[HumanEval 原始论文](https://arxiv.org/abs/2107.03374)、[EvalPlus 官方说明及版本变化](https://github.com/evalplus/evalplus) |

## 当前已有能力与接入缺口

根据 [建设方向](../roadmap.md)、[W1 验收](../MILESTONES.md) 和 `app/eval.py`，当前已有 10 个文件／小代码 JSON eval，支持 prompt、初始 files、checks、max_steps，报告已有 tokens、费用、耗时与步数。这可以验证 harness 回归，尚不等于标准仓库修复基准。

当前缺少基于 `base_commit` 的仓库准备、容器环境、正式补丁导出与独立评分；checks 仍在 Agent 可写的工作目录执行。公开基准接入前，应把评分器、隐藏测试和 gold patch 放在 Agent 无法读取或修改的位置，在干净环境应用生成补丁后评分。SWE-bench 官方已有 Docker 评测 harness；Pro V2 明确采用离线 Agent 阶段和新沙箱重评。[SWE-bench 官方仓库](https://github.com/SWE-bench/SWE-bench)、[Pro V2 协议](https://github.com/scaleapi/SWE-bench_Pro-os/blob/main/v2/README.md)

建议顺序：**内部回归 → 可选 Aider Python 小集 → Lite 固定 10–20 题 → Verified → 按需求选择 Multilingual、Terminal-Bench 或 Pro**。这只是后续接入建议，不改变 W1 真实任务验收状态。

## 评分与成本边界

- 主指标用真实测试得到的 resolved/pass rate；同时记录超时、环境失败、取消、token、费用、耗时和工具调用，环境失败不能静默算成模型失败或丢弃。
- 固定数据集版本、题目 ID、模型、prompt、工具权限、轮数／token／时间预算和重试规则；更换模型时沿用同一协议。保留逐题轨迹，少量重复运行观察波动。
- 代码基线默认关闭新接入的 `web_search`；公开 issue 的修复可能被搜到。联网能力另开评测，并明确标注允许网络。Pro V2 的标准协议本身要求 Agent 阶段离线。[Pro V2 协议](https://github.com/scaleapi/SWE-bench_Pro-os/blob/main/v2/README.md)
- **公开数据与本地 harness 可获取，不代表模型推理、容器算力、镜像存储或云平台免费**。开源工具许可与题目来源仓库许可分别核对；SWE-bench 和 Pro 的 harness 仓库标 MIT，EvalPlus 标 Apache-2.0，不由此推断所有原仓库代码都使用同一许可。[SWE-bench](https://github.com/SWE-bench/SWE-bench)、[Pro](https://github.com/scaleapi/SWE-bench_Pro-os)、[EvalPlus](https://github.com/evalplus/evalplus)

本文件没有承诺榜单成绩，也没有将自动评测替代审批拒绝、取消回收、恢复和结果真实性等本项目专属回归。
