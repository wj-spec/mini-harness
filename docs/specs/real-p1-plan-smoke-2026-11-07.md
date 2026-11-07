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

已实现硬 tool_gate：全部步骤完成后拒绝非计划工具（返回 tool_gate 错误）。
但真实多步重试仍出现完成 s2 后继续检查/未收敛、以及 ledger 状态与轨迹不一致，
说明真实模型/并发运行的收敛仍需继续验证；后续需更强的完成守卫与单一运行隔离。

已加“连续 2 次 tool_gate 后强制 done”的收敛兜底；真实重试仍出现模型未最终完成 s2
的情况（token/max_steps 停止），多步 P1 仍需继续验证。

## 3f. 干净工作区真实两步计划成功

新建一次性工作区与会话（避免旧 session 的失败轨迹污染），真实模型：

1. submit_plan 生成 2 步计划：
   - s1 读取 README；
   - s2 总结（depends_on=[s1]）；
2. 批准；
3. 新 Harness 执行：
   - 依次 start/read/complete s1；
   - 依次 start/complete s2；
4. run status=**done**，steps=6；
5. `execution.v1.json`：s1=done、s2=done。

这是真实模型 **多步计划执行**的干净成功样本。

结论修正：之前的“多步不收敛”主要受旧 session 中反复失败的 ledger/轨迹影响；
干净会话中多步可完成。仍建议用单一运行隔离/新会话做真实验收。

## 3g. 真实两步计划 + 验收命令成功

在干净工作区/会话中，对 2 步计划补充：

- confirmed criterion `ac1`；
- trusted command `true`；
- `VIBE_AUTONOMY=full`。

结果：

- 真实模型生成 2 步计划（s1→s2 依赖）；
- 批准后执行：s1=done、s2=done；
- run status=done，steps=8；
- `acceptance.v1.json` status=**true**。

端到端覆盖：多步 Plan 生成 → 批准 → 绑定执行 → step 证据 → 可信验收命令 → 完成。

## 3h. 真实审批路径（stdio approval 帧模拟 TUI）成功

不启用 `VIBE_AUTONOMY=full`，执行真实两步计划 + `true` 验收命令：

- 测试 Harness 监听 `approval_request` 并回送 `approval` 帧（decision=once），模拟 TUI 用户审批；
- 共 5 次审批；
- 结果：
  - run status=done，steps=14；
  - execution：s1=done、s2=done；
  - acceptance.v1.json status=**true**。

这验证了真实模型 + 真实审批帧（非自动放行）下的 Plan 全链路。
真实 TUI 本机仍受 pty 环境限制，人工验收按 TC-16 清单执行。

## 3i. 重复样本

同一自动化脚本（干净工作区、2 步计划 + `true` 验收）重复运行：

| 样本 | 模式 | 结果 | 审批 |
|---|---|---|---|
| 1 | autonomy full | done，s1/s2 done，acceptance=true | - |
| 2 | autonomy full | done，s1/s2 done，acceptance=true | - |
| 3 | autonomy full | done，s1/s2 done，acceptance=true | - |
| 4 | stdio approval 帧 | done，s1/s2 done，acceptance=true | 5 |
| 5 | stdio approval 帧 | done，s1/s2 done，acceptance=true | 3 |

即：干净会话下 3/3 多步+验收通过；审批帧路径 2/2 通过。
TUI 真实键路仍需人工在支持 pty 的终端执行。

## 3j. 跨模型样本（deepseek-chat）

用 `VIBE_MODEL=deepseek-chat` 覆盖默认模型：

- 干净两步计划 + `true` 验收（autonomy full）：run done、s1/s2 done、acceptance=true；
- 审批帧路径：run done、s1/s2 done、acceptance=true，审批 1 次。

即 deepseek-chat 在同样脚本下 1/1 多步验收 + 1/1 审批路径通过。
目前仅验证 deepseek-flash/chat 两个模型、单机单 key；更多 provider 仍待。

## 3k. 可复用验收脚本

已把上述流程固化为 `scripts/real_p1_acceptance.py`：

- 自动创建临时 git 工作区；
- 真实模型生成两步计划、批准、执行；
- 自动回送 approval 帧；
- 校验 steps/acceptance；
- 再跑一次真实 `/explore`，校验 `subagent_event.status=ok`；
- 输出 JSON 报告并写 `.vibe/real-p1-acceptance/`；
- 2026-11-07 实跑：ok=true，s1/s2 done，acceptance=true，审批 4 次，
  子 Agent status=ok（task t-20261107-042450-30ca97）。

## 3l. 可复用脚本稳定性样本

`scripts/real_p1_acceptance.py`（Plan 多步 + 验收 + `/explore`）连续运行：

| 样本 | 结果 | 审批 |
|---|---|---|
| 1 | ok=true，s1/s2 done，acceptance=true，subagent ok | 4 |
| 2 | ok=true | 4 |
| 3 | **ok=false**：run stopped，s1 in_progress，s2 pending，7 次审批，subagent ok | 7 |
| 4 | ok=true | 4 |
| 5 | ok=true | 4 |
| 6 | ok=true | 3 |

即 5/6 通过；1 次失败表现为模型在 s1 阶段多次触发审批后停止。
脚本已增加 `stop_reason`、审批工具记录和 `--keep-on-failure`，便于后续定位失败样本。
计划/执行核心链路已有稳定成功样本，但真实模型 P1 仍非 100% 稳定。

修复后（步骤级工具门禁 + 下一步提示）连续 3 次脚本运行：
- ok=true，s1/s2 done，acceptance=true；
- 审批次数均为 1（仅验收 bash 命令）。
说明 read 步骤越界工具被拦截后，审批/探索噪声显著下降。

诊断增强：

- `start_plan_step` 也接受模型额外 evidence 参数（忽略），避免 TypeError；
- 脚本对 `run_status=error`（如 ConnectTimeout）自动重试一次；
- 修复后再跑 3 次：3/3 ok，s1/s2 done，acceptance=true，审批均 1 次；
- 脚本进一步对 error/stopped 自动重试一次，再跑 3 次仍 3/3 ok（审批均 1 次）；
- 最终修复后再追加 2 次样本：均 ok，s1/s2 done，acceptance=true，审批 1 次；
  即最终版本连续 5/5 通过。
- 真实验收命令 `grep -q "real-p1" README.md`：ok=true、acceptance=true；
- 真实失败命令 `grep -q "NOT_PRESENT" README.md`：ok=false、acceptance=false，
  stop_reason=`acceptance_acceptance_failed,v1`（失败路径可观测，且脚本不再对此重试）。

## 3m. 真实后台 goal worker 成功

真实 provider 下启动 detached worker（非 fake）：

- goal task：读取 README、一句话总结、只读；
- worker 执行一个 segment 后退出 0；
- TaskRecord status=completed，mode=goal；
- goal-record.json state=completed，segment status=done。

这验证了真实模型下的后台 worker + GoalRecord 链路；attach/审批键路仍待 TUI 人工验收。

## 3n. 真实后台 Plan worker 成功

真实 provider 下走 TUI `/plan background` 等价路径：

- 真实模型生成 2 步计划 → 批准 → `plan prepare-background` 创建 task；
- 启动 detached worker（真实 provider，`VIBE_AUTONOMY=full`+unattended allow_exec）；
- worker 完成计划步骤与验收命令；
- 结果：
  - worker exit 0；
  - TaskRecord status=completed，plan 绑定正确；
  - acceptance.v1.json status=**true**。

这是真实模型下 **后台 Plan 执行 + 验收** 的端到端成功样本。

该路径已固化到脚本：

```bash
VIBE_TASK_RUNTIME=1 ./.venv/bin/python3 scripts/real_p1_acceptance.py --background
```

连续 2 次：ok=true、worker exit 0、task completed、s1/s2 done、acceptance=true、
`/explore` ok；另用 deepseek-chat 后台路径 1/1 通过。

最终追加 2 次后台样本均 ok；后台路径累计 deepseek-flash 4/4、deepseek-chat 1/1。

## 3o. 真实 edit 步骤成功（含审批帧）

真实模型生成单步 edit 计划（文件范围 `out.txt`），批准后：

- 无 autonomy（默认 ask），脚本回送 approval 帧；
- 模型调用 start_plan_step → write_file out.txt=ok → complete_plan_step；
- 验收命令 `grep -q ok out.txt`；
- 结果：run done，out.txt=ok，ledger s1=done，acceptance=true，审批 **2 次**（写文件 + 验收命令）。

另在 `VIBE_AUTONOMY=full` 下同样通过（审批 0 次）。
这验证了真实模型 **edit 步骤 + 文件范围 + 审批 + 验收** 的完整路径。

## 3p. 真实验收失败自动修复（repair 回灌）

`scripts/real_p1_acceptance.py --repair`（deepseek-flash，默认 ask + approval 帧）：

1. 真实模型生成两步只读计划 → 批准；
2. 注入 confirmed criterion + 会失败的验收命令 `test -f real-p1-repair-marker.txt`；
3. 执行：s1/s2 完成后首次验收失败（marker 不存在）；
4. harness 触发修复回灌（`VIBE_ACCEPTANCE_REPAIR_ATTEMPTS=1`）：
   - 模型收到失败命令与输出，创建 `real-p1-repair-marker.txt`；
   - 修复门禁允许读/写（只读计划无 edit 文件范围限制）；
5. 重新验收通过：run **done**、s1/s2 done、`acceptance.v1.json=true`、`plan_repair` 通知 1 次；
6. 审批 3 次（首次验收 bash / 修复写入 write_file / 二次验收 bash）。

样本：

| 样本 | 结果 | 修复次数 | marker | 备注 |
|---|---|---|---|---|
| 1 | ok，run done，acceptance=true | 1 | 已创建 | 首次实现通过 |
| 2 | run error（ConnectTimeout） | 0 | 未创建 | provider 偶发长尾 |
| 3 | ok（加 provider 错误重试后） | 1 | 已创建 | 重试修复生效 |
| 4 | ok（加 provider 错误重试后） | 1 | 已创建 | 连续 2/2 |

即：修复回灌链路在真实模型下可端到端通过；修复实现对 provider 错误补了一次重试后连续 2/2。

## 3q. 真实 worktree → merge → 合并后重验

`scripts/real_p1_acceptance.py --merge`（deepseek-flash）：

1. 真实模型生成 edit 计划（s1 读 README；s2 写 `merge-marker.txt` 内容 `merge-ok`）；
2. 后台 worker 在独立 worktree 执行，s1/s2 done，worktree 内验收 `grep -q merge-ok merge-marker.txt` 通过；
3. `--merge-task` 请求合并审批 → `--approve-merge` 合并；
4. 合并后使用计划绑定的可信验收命令在主工作区重验；
5. 结果：task completed、`merged=true`、`post_merge_verified=true`、`merge_completed.verified=true`、
   主工作区 marker 内容正确。

连续 **2/2** 通过。

**发现并修复的问题**：首跑时 worktree 执行完成、acceptance=true，但任务停在 `paused(unverified_completion)`——
Agent 完成守卫只看到“文件改动未被模型自己的验证覆盖”，而计划验收是 harness 执行的可信验证。
修复：绑定计划验收通过时把 run 标为 `verified=True`（`app/stdio.py`），任务按 completed 收口；
新增回归测试（edit 计划 + 可信验收 → task completed）。

## 3r. 一条命令跑完四种真实链路（`--suite`）

`scripts/real_p1_acceptance.py --suite` 依次执行：

1. `foreground`：真实两步计划 + 验收（stdio 审批帧）；
2. `background`：`plan prepare-background` + detached worker（worktree）；
3. `queue`：`--prepare-plan-task` + `--run-plan-queue --queue-dry-run` 预演 + 串行 drain；
4. `goal`：真实 goal run + 已确认 criteria 可信验收（`goal_acceptance`，run verified/completed）；
5. `repair`：验收失败 → 自动修复回灌 → 重新验收（marker 写 `.vibe/`，避免弄脏 suite 后续合并）；
6. `merge`：edit 计划在 worktree 执行 → 合并审批 → 合并后计划重验。

deepseek-flash 一次 suite 结果（2026-11-07）：

| 模式 | 结果 |
|---|---|
| foreground | run done、s1/s2 done、acceptance=true |
| background | task completed、s1/s2 done、acceptance=true |
| queue | dry-run executable=true、queue completed=1、task completed、acceptance=true |
| goal | run done、verified=true、task completed、goal_acceptance |
| repair | run done、repair 1 次、acceptance=true |
| merge | task completed、merged=true、post_merge_verified=true |

聚合 `ok=true`。单模式异常不阻断后续；任一失败最终 exit 1。

稳定性样本：`--suite-runs N`（1-5）可连续跑 N 轮 suite（每轮独立 git 工作区），
输出 `runs`/`passed_runs`/`ok`，用于扩大真实模型样本。

稳定性实测（2026-11-07，deepseek-flash）：`--suite --suite-runs 2` → **passed_runs=2**、
两轮各 6/6（合计 12/12 阶段），未出现跨轮状态污染。
跨模型稳定性（2026-11-07，deepseek-chat）：同样 `--suite --suite-runs 2` → **passed_runs=2**、
两轮各 6/6（合计 12/12 阶段）。

跨模型重复（2026-11-07）：suite 扩展为六种模式后，deepseek-flash 与 deepseek-chat 各跑一次，**均 6/6 通过**（新增 `goal` 完成验收模式）。

复跑（2026-11-07，queue/priority/trace/profile 审批门等改动合入后）：deepseek-flash 与 deepseek-chat 再次各跑一次，**均 6/6 通过**；同轮 Python 1526/13、TUI 396/397、长任务 fake 14/14。

复跑（2026-11-07，TUI /queue、/priority、webhook 重试等改动后）：deepseek-flash `--suite` 六模式 **6/6**；同轮一键验收 Python 1573/13、TUI 405/406、bench 14/14、doctor/redteam 通过。

**计划生成重试（2026-11-07）**：deepseek-chat 偶发一次运行只输出文字、不调用 `submit_plan`，
导致 repair 模式在 `generate` 阶段失败（`no plan.v1.json`）。脚本现对计划生成自动重试一次
（同一会话、第二段提示明确要求调用 `submit_plan`）；复跑 chat `--repair` 通过、suite 六模式 6/6。
同轮基线：Python 1558/13、TUI 398/399。

**chat 首跑暴露并修复的三个问题**：

1. **后台 plan 只发 objective**：deepseek-chat 后台任务只输出文字、不调用 step 工具 → `stopped/plan_incomplete`。
   修复：绑定 plan 的 run 在输入后追加 `[已批准计划]`（目标/步骤/start-complete 协议），后台与前台一致；
2. **修复轮与只读约束冲突**：chat 认为创建 marker 违反计划 `只读` 约束而拒绝修复。
   修复：repair prompt 明确修复轮以验收命令为准、允许创建/修改文件；脚本在 repair 模式清理冲突约束；
3. **纯分析步骤无法完成**：chat 在 s2（总结）找不到可自动记录的工具证据，两次 `complete_plan_step` 被拒后
   `no_progress` 停止。修复：`complete_plan_step` 接受模型提供的 `evidence`/`evidence_refs` 并记为 manual 证据。

## 4. 结论与限制

- 已证明：真实模型可以走 `--task` 只读链路、可以生成结构化 proposed plan；
- 已证明：计划批准后绑定/门禁/step 工具在真实模型下可运行，s1 产生 read 证据；
- 已证明（单步）：真实模型能完成全部步骤并通过可信验收命令（acceptance=true）；
- 已证明（干净会话）：真实模型两步计划全部完成，并通过可信验收命令（acceptance=true）；
- 已证明：stdio approval 帧模拟审批下的多步+验收全链路（5 次审批）；
- 已证明：干净会话 3/3、多步+验收 acceptance=true，审批帧路径 2/2；
- 已证明：deepseek-flash 3/3 多步验收、2/2 审批；deepseek-chat 1/1 多步验收、1/1 审批；
- 脚本稳定性：6 次中 5 次通过；失败样本表现为 s1 阶段多次审批后停止；
- 已证明：真实 edit 计划步骤（文件范围/审批/验收）通过；
- 已证明：真实后台 goal worker 完成；真实后台 Plan worker 完成计划+验收；
- 已证明（2026-11-07）：真实验收失败 → 自动修复回灌 → 重新验收通过（`--repair`，修复后连续 2/2）；
- 已证明（2026-11-07）：后台 worktree 执行 → 合并审批 → 合并后按计划重验（`--merge`，连续 2/2）；
- 已证明（2026-11-07）：`--suite` 一条命令跑通 foreground/background/repair/merge 四种真实链路；
- 仍待：真实 TUI 人工键路（pty 环境）、更多 provider/更大样本；
- 本次仅 deepseek-flash、一次样本，成本显示 $0；
- 观察到一次真实 provider 长时间无返回（>300s），需要重跑/检查超时与重试；
- 下一步：重跑第 3 步（更强提示/更多步数/不同 provider），再按
  [Plan/SubAgent 人工验收](manual-acceptance-16-plan-subagent.md) 记录。
