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

## 4. 结论与限制

- 已证明：真实模型可以走 `--task` 只读链路、可以生成结构化 proposed plan；
- 已证明：计划批准后绑定/门禁/step 工具在真实模型下可运行，s1 产生 read 证据；
- 未证明：真实模型能稳定完成全部步骤、通过验收命令、完成 Plan 端到端；
- 本次仅 deepseek-flash、一次样本，成本显示 $0；
- 观察到一次真实 provider 长时间无返回（>300s），需要重跑/检查超时与重试；
- 下一步：重跑第 3 步（更强提示/更多步数/不同 provider），再按
  [Plan/SubAgent 人工验收](manual-acceptance-16-plan-subagent.md) 记录。
