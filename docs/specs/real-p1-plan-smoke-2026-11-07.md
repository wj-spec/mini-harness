# 真实模型 Plan P1 冒烟记录（2026-11-07）

环境：仓库 `.env` 配置 `deepseek-flash`（OpenAI-compatible）。工作区为一次性 git 仓库。

## 1. 单 Agent 只读 smoke

```bash
VIBE_MAX_STEPS=4 VIBE_MAX_RUN_TOKENS=8000 \
  ./run_vibe.sh --task "读取 README.md，用一句话回答这个工作区是什么项目。只读，不修改文件。" \
  --workspace /tmp/mini-harness-real-smoke
```

结果：

- status=done，steps=2；
- tool_calls=2，tool_calls_ok=2（list_files/read_file）；
- usage prompt 3482 / completion 132 / total 3614；
- 模型/provider：deepseek-flash / openai；
- 未发生文件修改。

## 2. Plan 模式生成计划

通过 stdio Harness（真实 provider）发送：

```json
{"type":"run","text":"必须调用 submit_plan …","task":{"kind":"run","mode":"plan"}}
```

结果：

- status=done，steps=2；
- 生成 `plan.v1.json`，status=proposed；
- 真实模型输出 2 个步骤（s1 读 README，s2 总结），带 acceptance_criteria 与 verification_plan；
- 期间发现并修复 `submit_plan` 对模型友好格式的兼容问题：steps 为字符串/缺 step_id/title、
  acceptance_criteria 为字符串时，现会规范化为合法 Plan（`70644f6`）。

## 3. 批准并执行绑定计划

批准后发送 act run（绑定 `plan_id/plan_version`）：

- `plan_binding`：found=true executable=true step_tools=true；
- `execution.v1.json`：
  - s1 status=done，evidence read README.md ok=true；
  - s2 status=in_progress；
- 之后真实 provider 调用超过 300s 未返回，测试侧取消：run=cancelled、任务 paused；
- 未完成计划验收，**不算执行 P1 通过**。

## 3b. 单步计划真实执行成功（补充）

为避免多步计划在真实 provider 上长尾，另建一次性工作区做单步计划：

- Plan 模式真实模型调用 submit_plan，生成 1 步计划（s1 读 README），status=proposed；
- 批准后在新 Harness（空历史）执行绑定计划；
- 真实模型依次调用：
  - `start_plan_step s1`；
  - `read_file README.md`；
  - `complete_plan_step s1`；
- run status=done，`execution.v1.json` 中 s1=done；
- 这是**真实模型 Plan 生成 → 批准 → 绑定执行 → step 完成**的端到端成功样本。

随后给同一单步计划加 confirmed criterion + `true` 验收命令再执行：

- s1 仍完成；
- 但模型在完成步骤后的下一次 provider 调用超过 240s 未返回，测试取消；
- acceptance.v1.json 未生成，因此**验收命令真实端到端仍未通过**；
- 重试 deepseek-flash 与 deepseek-chat、并设置 `VIBE_LLM_MAX_OUTPUT_TOKENS=512` 后仍在
  完成 s1 后的下一次模型调用长时间无返回；已记录为 provider 长尾，需单独排查。

## 3c. 真实模型子 Agent（/explore）成功

在同一真实 provider 下发 `explore` command（prompt：读取 README，一句话说明用途，只读）：

- 子 Agent status=ok；
- 返回结构化摘要（README 两行内容 + 一句话说明用途）；
- `subagent_event` task_id=t-20261107-035103-c28ef8；
- 子 Agent 使用只读工具，不写工作区；
- 子 TaskRecord 创建成功。

这是真实模型 **只读子 Agent runner** 的端到端成功样本。

## 3d. 真实模型 Plan 全链路成功（单步 + 验收命令）

在同一单步计划上补充：

- confirmed criterion `ac1`；
- trusted verification command `true`（source=plan_approval）；
- 运行环境 `VIBE_AUTONOMY=full`（验收命令无需人工审批）。

结果：

- 真实模型生成 1 步计划并批准；
- 绑定执行后 s1=done，run status=done；
- `acceptance.v1.json` status=**true**；
- 端到端覆盖：Plan 生成 → 批准 → 绑定执行 → step 证据 → 可信验收命令 → 完成。

之前该用例“卡住”的根因：验收命令走 ApprovalGate；测试 Harness 未响应 approval_request，
不是 provider 流式读取问题。修复/缓解：

- `212d885` 给整轮验收加总超时，超时转 `stopped/acceptance_timeout`；
- 真实运行需在 TUI 审批或使用 `VIBE_AUTONOMY=full`（按风险理解）。

## 3e. 真实多步计划部分通过

对 2 步计划（s1 读 README、s2 总结）批准后执行：

- 真实模型完成 s1（read 证据）；
- s2 进入 in_progress；
- 模型在后续步骤中反复检查 ledger/README；
- 在 20k/60k token 预算下以 `token_budget` 停止，s2 未最终 done；
- 同一次执行轨迹中模型曾调用 complete_plan_step s2 成功，但随后继续探索并耗尽预算。

结论：多步真实执行的行为基本成立，但**任务完成前的收敛/预算控制仍不稳定**；
需改进提示/预算/进度守卫后再判定多步 P1。

已加 harness 提示：最后一步 `complete_plan_step` 成功时，工具结果返回
`all plan steps complete — stop calling tools and summarize now`。
真实重试中模型仍会继续检查 ledger/README（最多到 max_steps），说明仅靠工具提示
不足以强制收敛；后续需要完成守卫或硬性结束条件。

## 4. 结论与限制

- 已证明：真实模型可以走 `--task` 只读链路、可以生成结构化 proposed plan；
- 已证明：计划批准后绑定/门禁/step 工具在真实模型下可运行，s1 产生 read 证据；
- 已证明（单步）：真实模型能完成全部步骤并通过可信验收命令（acceptance=true）；
- 仍待：多步计划收敛、TUI 审批下的真实验收、更多样本；
- 本次仅 deepseek-flash、一次样本，成本显示 $0；
- 观察到一次真实 provider 长时间无返回（>300s），需要重跑/检查超时与重试；
- 下一步：重跑第 3 步（更强提示/更多步数/不同 provider），再按
  [Plan/SubAgent 人工验收](manual-acceptance-16-plan-subagent.md) 记录。
