# W1 切片：编码 Agent 评测

状态：内部回归与 SWE-bench Lite 开发集接入已实现。2026-10-21 已补齐协议、预算继承和操作任务结束约束；内部真实模型已完成创建、编辑、读取复制，独立评分 3/3。2026-10-21 22:45 公开仓库首题 `pylint-dev__astroid-1196` 官方评分 **resolved 1/1**，2026-10-23 已用 `doctor` 核对继承预算。固定前三题与整体环境验收未完成，W1 保持未验收。

## 检查与评测入口

| 入口 | 用途 | 评分依据 |
|---|---|---|
| `./run_eval.sh check-budget` | 使用确定性 fake 模型离线验证预算机制，不需要 API Key 或 Docker | 上下文边界、压缩继续、累计预算暂停的不变量 |
| `./run_vibe.sh --eval` | 项目自带 10 个小任务，快速检查工具调用和编码闭环 | 预设文件检查、命令退出状态、测试文件保护及 Agent 终态 |
| `./run_eval.sh run` | SWE-bench Lite 固定开发子集，接通真实仓库、补丁导出和独立评分 | 官方 `swebench==4.1.0` 在干净容器中应用补丁并测试 |

这些入口复用本项目 `Agent`、工具审批、上下文和预算实现。`check-budget` 固定使用 fake；其他入口的 `--fake` 使用脚本响应。确定性检查不能作为模型能力成绩。

## 预算层级与验收顺序

三种额度各自控制不同边界，压缩不会重置任务累计用量：

| 额度 | 默认值 | 控制行为 |
|---|---:|---|
| `context_window` | 1,000,000 | 单次请求的容量；历史接近容量时压缩，固定目标仍装不下时停止发送请求 |
| `compact_interval_tokens` | 200,000 | 上次压缩后累计消耗达到 80% 时本地压缩，并在同一 run 中继续；0 关闭周期触发 |
| `max_run_tokens` | 1,000,000 | 每题一次 run 的累计输入与输出；余额不足时先压缩重算，再无工具收尾或本地暂停；0 不设 token 上限 |

离线检查可以逐层运行，也可以一次执行全部层级：

```bash
./run_eval.sh check-budget
./run_eval.sh check-budget --level context
./run_eval.sh check-budget --level compaction
./run_eval.sh check-budget --level total
```

`--level` 默认为 `all`，依次执行三层；前一层未通过时，后续层记为 `skipped`。这组检查使用固定的小额度和脚本用量，不调用真实模型，也不启动 Docker。预期的 `stopped/token_budget` 表示暂停机制符合断言，不能计为编码任务完成或 SWE-bench `resolved`。真实模型即使正常触及预算，也仍需记录为该预算内未完成。

报告、审批审计与逐层轨迹保存在 `.vibe/benchmarks/budget-checks/<timestamp>/`。`context` 检查固定目标超窗时不发请求；`compaction` 在同一 run 中跨过两次周期压缩，实际读取、修改 `calc.py` 并运行原始测试；`total` 检查余额耗尽后写文件与命令均未审批、未执行，累计用量保留。

开发与验收按以下顺序推进；每次只根据当前阶段的失败补齐缺口：

| 阶段 | 命令 | 进入下一阶段的依据 |
|---|---|---|
| 1. 预算机制 | `./run_eval.sh check-budget` | 所有层级不变量通过，预算停止、工具副作用和协议配对符合预期 |
| 2. 内部编码任务 | `./run_vibe.sh --eval --eval-limit 1`，再用 `--eval-limit 3` | 真实模型完成小任务，正常结束且评分检查通过 |
| 3. 公开仓库首题 | `./run_eval.sh run --limit 1` | 在记录的配置下生成补丁，并通过官方独立评分 |
| 4. 固定开发子集 | `./run_eval.sh run --limit 3` | 逐题记录解决数、预算暂停、真实错误与消耗，核对环境差异 |

阶段 2–4 仅在用户显式运行时调用已配置模型。不会因未解决自动增加预算或重新发起评测；已有请求重试仍受配置与同一 run 预算限制。各阶段保留失败和暂停记录，不用离线机制通过替代真实任务验收。

## 内部回归

```bash
./run_vibe.sh --eval --fake --eval-limit 3
# 配置好真实模型后：
./run_vibe.sh --eval --eval-limit 3
```

`--eval-tasks path` 可以替换任务文件。运行记录写在工作区 `.vibe/evals/<fake|model>/<timestamp>/`，包含 `report.json`、`report.md`，每题目录中保留工作文件及 `.vibe/messages.jsonl`、`trace.jsonl`、`audit.jsonl`。

本轮补齐的评分约束：

- 使用实际 `Config` 中的时间、token、费用、重试和上下文预算；步数取任务与配置中有效上限的较小值。
- 必须 `status=done`，且所有评分检查通过，才计为通过；暂停、超时或异常不会因工作文件碰巧满足断言而通过。
- 命令评分任务的测试文件和显式 `protected_files` 在评分前后检查，修改或删除会失败。
- fake 与真实模型分目录。任务、模型提供方、端点、预算、工具策略等形成 fingerprint，只与相同 fingerprint 的上一轮计算 `delta`；时间戳含微秒，避免同秒运行覆盖。
- 关闭个人技能发现、MCP 和 `web_search`。内部任务仍是受信本地任务，shell 在宿主执行，网络没有操作系统隔离。

内部入口在总分后输出具体停止、运行错误与失败检查的原因。退出码为：全部通过 `0`；正常未完成、预算暂停或检查不通过 `1`；模型接口或执行异常 `2`。`status=done` 表示 Agent 循环正常结束，不能单独当作任务通过。

## 明确操作任务的结束约束

`Agent(require_tool_use=True)` 仅用于明确要求实际操作的任务；内部 eval 和 SWE-bench 入口均启用，普通问答默认 `False`。启用时，初始指令要求实际执行授权任务、遵守审批并根据观察结果交付。若本次 run 尚无工具请求，模型给出非空文本后正常停止，harness 在同一 run 的既有预算内只追加一次通用提醒；下一次仍无工具请求时，返回 `status=stopped`、`stop_reason=no_action`，保留模型最后的说明。

纠偏沿用原来的步数、时间、累计 token、费用和上下文限制，不增加额度、不清零用量；取消与预算停止优先。已经提出过工具请求（包括审批被拒绝）时不以此约束强制再试。工具请求只是最低执行证据，读取一次文件、无效工具调用或被拒绝的写入均不能证明任务完成，仍须通过独立检查或官方评分。

提醒内容不包含评分断言、期望文件内容、隐藏测试或检查失败反馈。评分在 Agent 返回后运行，不将 checker 答案回灌给 Agent 继续修复，也不因结果为 `0/N` 自动重新调用模型。

当前内部协议为 `internal-v3`，`metadata.agent.require_tool_use=true`；公开协议 `metadata.protocol_version=2`，并记录 `metadata.require_tool_use=true`。内部 fingerprint 包含协议与 Agent 配置，旧协议结果保留，不能与新协议计算同配置 `delta` 或混合作为可比成绩。

## 公开开发集协议

数据固定为 [SWE-bench Lite](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite) 的 `dev`，revision 为 `6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2`。完整开发集为 23 题；仓库中的 `evals/swebench-lite-dev.json` 仅记录固定数据来源、摘要和 10 个实例 ID，不保存标准补丁。

公开入口默认只选择清单首题，先完成一次真实修复。显式 `--limit 3` 扩展到固定的前三题：

1. `pylint-dev__astroid-1196`
2. `marshmallow-code__marshmallow-1359`
3. `sqlfluff__sqlfluff-1625`

清单按固定顺序选择，`--limit N` 可调整数量；初版默认值为 3，当前默认值为 1。这 10 题没有全部完成环境验证；开发子集结果也不能写成完整 SWE-bench Lite 排行榜分数。

下载固定 revision 的 Parquet，检查大小 `119551` 字节和 SHA-256 `8312f321838051849d2fa7c6ca071244733a3e90bb517ff6ca8186f722199b5c` 后读取。数据摘要不匹配、缓存被改动或清单任务缺失会报错。完整评分字段只存控制侧 `.vibe/benchmarks/cache/` 和运行目录中的 `dataset.json`。

## 安装与运行

```bash
uv sync --extra test --extra eval
./run_eval.sh doctor
./run_eval.sh prepare --limit 1
```

`doctor` 显示模型凭据是否配置、Docker 是否可用、评分器版本、数据 revision 和实际生效的 `budget`，不显示密钥。`prepare` 只下载与校验数据，不调用模型，也不拉取 Docker 镜像。

Docker daemon 需要运行。若使用 Colima，可按自己的实例指定 socket，例如 `export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"`，也可通过 `--docker-host` 指定；入口不会切换全局 Docker context。执行与评分都使用官方 Linux x86_64 镜像，M 系列 Mac 需要支持 amd64 模拟。镜像可能较大，首次运行需要下载，后续复用缓存。

先检查接线和评分环境：

```bash
./run_eval.sh run --fake --limit 1 --skip-grade
./run_eval.sh validate --limit 1
```

fake 只执行查看工作目录、Git 状态等固定动作，不尝试修复。`--skip-grade` 保留 `ungraded`，退出码为 1，不能据此认定评分失败或修复成功。`validate` 仅选第一题，检查空补丁应为 `0/1`、官方参考补丁应为 `1/1`；参考补丁只经过评分器，不会交给 Agent。

使用本机 `VIBE_API_KEY`、`VIBE_MODEL`、`VIBE_BASE_URL` 配置运行真实评测：

```bash
./run_eval.sh doctor
./run_eval.sh run --limit 1
# 首题完成后，再运行固定 3 题：
./run_eval.sh run --limit 3
# 需要显式覆盖本机配置时：
./run_eval.sh run --limit 1 --max-steps 50 --max-tokens 1000000 --max-seconds 600
# 对已有运行补评分或重评分：
./run_eval.sh grade --run-dir .vibe/benchmarks/swebench-lite-dev/model/<timestamp>
```

`run` 与 `doctor` 默认继承 `Config.from_env()`，环境变量优先于代码默认值；仅显式传入的 `--max-steps`、`--max-tokens`、`--max-seconds` 覆盖对应配置。未设置环境变量时，每题默认累计 1,000,000 token、600 秒，`max_steps=0` 表示不设轮数上限。`--max-tokens 0` 表示不设累计 token 上限，时间必须大于 0；单请求容量、费用、停滞检测和审批规则仍各自生效。先用 `doctor` 查看实际预算，再进行真实运行。

token 是累计各轮 prompt 与 completion，重复发送的历史再次计入，与单请求上下文容量不同。价格与费用上限沿用本机配置；价格为 0 表示未配置，不能据此认定调用免费。预算在请求前估算、响应后按实际 usage 检查，最终一次响应可能超过余额。真实 `run` 默认自动评分，`--skip-grade` 可只生成补丁；每次评分使用新的 run ID，避免官方评分缓存复用旧补丁。`run` 每次创建新工作区，此入口暂不支持从旧评测 `/resume`。

公开入口退出码：全部实例解决或离线预算机制检查通过为 0；正常未解决、预算暂停或未评分为 1；真实接口、执行环境、评分、依赖或配置错误为 2。具体原因以报告中的状态和日志为准；离线检查的退出 0 仅说明机制符合预期。

## 执行与信息边界

模型请求在宿主发送，API Key 不注入执行容器；官方评分子进程还会移除模型 API Key 等凭据。受测 Agent 只获得 issue 文本及各题的工作目录，不通过工具提供标准补丁、评分测试补丁、完整数据缓存和控制侧报告。

各题从官方镜像导出 `/testbed` 到独立工作目录，核对 `base_commit` 后开始运行。官方镜像可能在该提交上增加一次名为 `SWE-bench` 的安装提交；仅在父提交精确匹配时接受，并记录实际 `repository_head` 和镜像摘要。运行前必须没有待导出补丁，避免把镜像自带改动算成模型成果。`bash` 在 `--network none` 的容器中执行，激活镜像内 `testbed` 环境；限定 CPU、内存和进程数。`web_search`、技能发现、个人技能和 MCP 均不启用。

文件工具仍在宿主操作容器共享的工作目录，现有路径与符号链接检查提供静态边界，不能当作抵御恶意程序、符号链接竞态或 TOCTOU 的强沙箱。无网约束针对执行容器，不能概括成整个宿主与评分基础设施均无网络。

写文件、命令执行和评测准备、导出、评分均经过 `ApprovalGate`；评测入口采用固定范围内自动批准并审计的策略。自动批准不等于取消审批记录，也不等于操作系统隔离。

Agent 导出的 Git diff 写成官方格式 `predictions.jsonl`：`instance_id`、`model_name_or_path`、`model_patch`。官方评分器使用独立干净容器，应用模型补丁后运行评分脚本；受测工作区里的测试结果不能代替官方评分。

## 报告与失败分类

公开评测默认写入 `.vibe/benchmarks/swebench-lite-dev/<scripted|model>/<timestamp>/`：

- `metadata.json`：数据版本、模型、工具策略、预算、价格、源码摘要和选中任务。
- `report.json`、`report.md`：每题状态、步数、用量、费用与耗时。
- `predictions.jsonl`、每题 `model.patch`：待评分补丁；每题还有消息和 trace。
- `audit.jsonl`：准备、执行和评分审批记录。
- `grade/<run-id>/`：评分器日志、官方汇总及每题测试输出。

`resolved`、`unresolved` 记录官方结论；`environment_error`、`grading_error` 表示环境或评分过程异常；空补丁在评分后记为未解决。未评分时 `resolved=null`，不能展示成已通过。分析模型能力时，应同时查看环境失败数量及每题日志。

`agent_status` 独立记录 Agent 的终态，模型协议错误保存在 `inference_error`；CLI 和 Markdown 报告会列出错误或停止原因。旧报告可能只有 `text` 保存模型错误，即使 `error=null` 也不能认定运行正常。

汇总分别展示解决、正常未解决、预算暂停与真实错误。`token_budget` 等预算停止属于正常停止；HTTP 协议错误、容器或评分异常保留错误分类，不把所有 `0/N` 都展示成同一种错误。独立评分结论仍保留，预算机制检查不参与公开成功率。

## 已有验证证据（2026-10-21）

- 评测接入时的全量 Python 回归基线为 **804 项通过**，无失败或跳过；记录：`.vibe/eval-final-tests.xml` 与 `.vibe/eval-final-tests.log`。
- 首轮协议修复后的完整 Python 回归 **845 项通过**，无失败或跳过；记录：`.vibe/reasoning-fix-tests.xml` 与 `.vibe/reasoning-fix-tests.log`。这些使用本地模拟模型，不等于真实任务修复通过。
- 本轮预算分层接入后的完整 Python 回归 **875 项通过**，无失败或跳过；记录：`.vibe/budget-layers-tests.xml` 与 `.vibe/budget-layers-tests.log`。覆盖配置继承、暂停与错误分类、离线分层入口和既有 Agent 行为。
- 预算分层入口已实际运行 `./run_eval.sh check-budget`，**3/3 层通过**；报告：`.vibe/benchmarks/budget-checks/20261021-133905-502317/report.json`。固定目标超窗时零请求；同一 run 两次周期压缩后，实际 `read_file`、`edit_file` 和验证命令完成小修复，原测试未变、退出码为 0；总预算层同时阻止写入与命令。此次使用 fake，仅验证预算与工具机制。
- 已实际下载固定 revision 的官方 Parquet，大小和 SHA-256 均匹配，读取 23 题，清单 10 个实例均存在。
- 已在实际 Docker 环境跑通脚本模型的命令工具、审批、补丁导出与独立评分；脚本未尝试修复，官方评分如实为 `0/1`。记录：`.vibe/benchmarks/swebench-lite-dev/scripted/20261021-164113-019939/`。
- `pylint-dev__astroid-1196` 官方评分自检通过：空补丁 `0/1`，参考补丁 `1/1`。本地证据：`.vibe/benchmarks/grader-validation/20261021-163635-265007/validation.json`。
- 首次选择的 `pvlib__pvlib-python-1072` 参考补丁未通过：镜像中 NumPy 2.0.2 移除了项目引用的 `np.Inf`，测试在导入时失败。失败证据保留于 `.vibe/benchmarks/grader-validation/20261021-163317-597515/`；该题已移到清单最后，未删除失败记录，也没有把环境故障记为模型能力不足。
- 首轮真实模型为 `deepseek-flash`，运行记录保留于 `.vibe/benchmarks/swebench-lite-dev/model/20261021-164901-333616/`。累计消耗 95,917 token 后，为满足下一请求预算触发压缩；这个数是多轮 prompt 与 completion 合计，不是单次上下文长度。压缩产生的 assistant 摘要缺少 DeepSeek 要求回传的 `reasoning_content`，后续请求 HTTP 400 中断；导出空补丁，官方评分器跳过测试。报告的 `0/1` 记录未解决事实，不能据此判断模型修复能力。
- 修复保留流式 reasoning 协议元数据，工具回灌、session/checkpoint 和磁盘 offload 均保持该字段；DeepSeek 的合成 assistant 摘要补空字段。reasoning 计入预算和上下文快照，不展示为正文或进入摘要。本地 HTTP 回放覆盖工具往返、预算压缩、跨用户轮、模型别名恢复、断流、超时和取消；持久化、压缩和预算回归通过。原失败记录及诊断保留，尚未重新完成真实模型修复任务。协议依据：[DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)。
- 第二轮真实模型记录：`.vibe/benchmarks/swebench-lite-dev/model/20261021-170835-513176/`。11 次请求全部成功，压缩后未重现 HTTP 400；执行 16 条只读 bash，Python reproducer 重现原始 `AttributeError`，未修改文件、未运行测试。累计 100711 tokens（输入 96954、输出 3757，均为服务端 usage），第 11 次响应后触发 `token_budget`，该次返回的搜索工具未执行。空补丁使官方评分器再次跳过测试；本轮结论是当前 Agent 在 10 万累计预算内未完成。当时公开入口写死每题 50 步、100000 token、600 秒，覆盖本机配置；现已改为继承 `Config`，原报告与独立诊断均保留。
- 内部真实首题记录：`.vibe/evals/model/20261021-135000-933796/`。`deepseek-flash` 在第 1 步以 `finish_reason=stop` 返回 `I'll create the file.`，累计 1111 tokens（输入 1104、输出 7），没有工具请求，审批审计为空，`hello.txt` 不存在。原 Agent 将非空文本结束标为 `done`，独立 `file_equals` 正确判失败；这轮不是预算耗尽或 HTTP 错误，原报告不改写。
- 操作任务结束约束已用本地 fake 与 HTTP MockTransport 回放验证：复现原始意向回复后继续，经审批实际创建文件；一次纠偏后仍未请求工具则停为 `no_action`；写入错误内容仍由独立检查判失败，检查答案不回灌。这些回放是确定性工程证据，真实结果另行记录。
- 结束约束修复后完整 Python 回归 **889 项通过**，无失败或跳过；记录：`.vibe/action-completion-tests.xml` 与 `.vibe/action-completion-tests.log`。预算分层 CLI 再次 **3/3 通过**，报告 `.vibe/benchmarks/budget-checks/20261021-140140-838346/report.json`。
- 内部真实首题重跑 **1/1 通过**：`.vibe/evals/model/20261021-142446-747494/`，模型 `deepseek-flash`，协议 `internal-v3`。3 步约 3.48 秒，输入 4042 + 输出 299 = 4341 tokens；直接调用 `write_file` 后执行 bash 核对，均有 `ApprovalGate` 审计，验证命令真实退出 0。独立 `file_equals` 通过，另核对 `hello.txt` 精确为 11 字节 `hello world`；没有触发 `action_required`。这是文件操作首题通过，尚不等于代码修复或公开仓库任务验收。
- 内部真实前三题 **3/3 通过**：`.vibe/evals/model/20261021-143720-292084/`，模型 `deepseek-flash`，协议 `internal-v3`。`create_file` 用 8100 tokens，`edit_file` 用 5339，`read_copy` 用 7234；合计 20673 tokens、14 步、约 17.25 秒。实际字节分别核对为 `hello world`、`hi\n`，以及 `secret.txt` 与 `answer.txt` 同为 `42\n`。写入、编辑、命令均有审批记录，执行的内容核对与复制比较命令均退出 0；三题均正常结束，无纠偏、预算暂停或运行错误。此次覆盖基础文件操作，不代替代码修复或公开仓库评分。
- **公开仓库首题通过（2026-10-21 22:45）**：`.vibe/benchmarks/swebench-lite-dev/model/20261021-224536-308814/`，模型 `deepseek-flash`，公开协议 v2，官方 `swebench==4.1.0` 评分 **resolved 1/1**（无空补丁、无错误）。Agent 24 步、323,577 tokens（输入 312,967、输出 10,610）、约 149 秒，正常 `done`；trace 保留 28 条命令执行记录，`model.patch` 与官方评分目录一并保留。此前两轮空补丁与 HTTP 400 记录不回写。
- **预算继承核对（2026-10-23）**：`./run_eval.sh doctor` 输出 `.vibe/w0/doctor.json`，确认每题继承 `max_run_tokens=1,000,000`、`max_steps=0`、`max_run_seconds=600`、`compact_interval_tokens=200,000`；对比旧入口写死的 50 步 / 100,000 tokens，两轮旧失败分别停在 95,917 与 100,711 tokens。本次核对环境没有 API key 与 Docker（`api_key_configured=false`、`docker_ready=false`），因此没有重跑首题，旧报告保持原始记录。
- 固定前三题及清单 10 题没有整体完成环境验收。

## 后续验收

- [x] 完成离线预算层级检查，保留逐层证据。
- [x] 真实模型通过内部首题，保留实际配置、写入与验证结果。
- [x] 扩大到内部 3 题，核对创建、编辑和读取复制的闭环。
- [x] 用 `doctor` 核对继承的本机预算，保持模型与首题不变运行 `./run_eval.sh run --limit 1`，记录相对旧入口默认值的变化，并完成实际修复与独立评分。证据见上方首题记录与 2026-10-23 doctor 输出。
- [ ] 逐题完成固定前三题的参考补丁自检，再显式用 `--limit 3` 扩大评测，单独记录镜像环境故障。
- [ ] 在相同协议下比较 harness 改动前后成功率、耗时、用量与失败原因。

官方接口依据：[v4.1.0 评分入口](https://github.com/SWE-bench/SWE-bench/blob/v4.1.0/swebench/harness/run_evaluation.py)、[镜像命名](https://github.com/SWE-bench/SWE-bench/blob/v4.1.0/swebench/harness/test_spec/test_spec.py)、[固定数据 revision](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite/commit/6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2)。
