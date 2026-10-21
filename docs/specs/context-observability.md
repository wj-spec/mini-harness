# 上下文分区快照与回放

日期：2026-10-21。服务于 W1–W3 的运行可见性；不提升真实任务验收状态。设计依据见 [调研](../research/context-partition-observability-2026-10-21.md)。

## 行为

- 每次配置了 ContextBuilder 的模型调用，在最终 messages/tools 确定后记录 request 快照；重试有独立 request_id。纠偏与无工具收尾必须如实反映。
- 主动压缩前后各记录快照，以 phase 和 reason 区分工作上下文与实际请求。快照只使用 system/memory/tools/history 四个互斥 token 分区，组数与摘要层数留在原压缩事件。
- 每个快照记录估算方法、窗口、输入容量、名义/有效额度、输出与安全预留、剩余量、逐区变化；分区之和等于总量，块之和等于分区。
- system 可展开基础指令、项目规则、skills、TaskState；history 可展开消息正文、工具调用参数、工具结果、摘要和引用；tools 为工具定义。当前 memory 为 0。
- 块记录内容身份、来源、hash、字符数和估算 token，不重复保存正文。稳定顺序的增量归因保证拼接片段及工具 JSON 封装可对账。
- Textual 与 Ink 展示估算占用、分区明细、本轮有界历史及压缩变化；`/context` 查看与切换快照。累计 usage 单独展示。
- 规范快照沿已有 trace 落 `.vibe/sessions/.../trace.jsonl`，Ink 通过独立事件接收；旧 context 回调作为兼容信息，不再决定新版实时用量。

## 接口

纯 core 的 `build_context_snapshot` 接受实际 messages、tool_specs、BudgetPolicy 或旧式预算，以及可选的精确 system 来源片段；不访问文件、不调用模型、不依赖 app/TUI/网络。

Agent 在快照上附加 run/request/snapshot ID、步骤、尝试次数、时间、阶段、原因和相对上次快照的变化；llm_start/llm_end 携带同一 request_id。界面记录上限为 100 张，完整历史保存在日志。每张界面快照最多 256 块，优先保留 token 占用大的块，其余按分区合并并注明数量；不改变原始 trace 或分区总计，避免观测事件超过 stdio 帧上限。

## 失败与兼容

- 没有 ContextBuilder 时不虚构窗口；没有快照时显示暂无数据。
- 内容来源片段与实际 system 不符时按完整 system 计数，不猜来源；同内容重复块保留独立身份。
- 超过容量时显示超额且剩余为 0，不截掉块来伪造未超额；原有执行预算与审批不变。
- 不合法的协议快照按现有协议错误路径处理；旧后端未发新事件时原有界面仍可用。
- 不增加模型调用；不接第三方平台、不改变默认入口、不把估算称为供应商精确计数。
- 快照在线程中计算，任务总时间包含计算时间；计算后和模型启动前重查取消与运行时限。LLM 超时从实际调用开始计；没有 trace 消费者时不构建内容块。

## 验证

先失败再实现：纯快照计数和块归因；Agent 实际发送内容/纠偏/收尾/重试/压缩的对账；stdio 落盘与事件；Textual 与 Ink 的交互回放、重置和无数据兼容。完成后运行 `./run_tests.sh -q` 与 Ink 测试，并记录确定性 smoke；真实模型体验另行验收。

### 工程证据（2026-10-21）

- Python 全量回归通过；本地 HTTP 模拟测试在允许 loopback 端口绑定的环境运行，首次沙箱内的端口权限失败不作为产品失败或通过证据。
- Ink `npm test` 144 项通过，包括真实 Python fake stdio 快照；80×24、120×40 的 PTY 回放、来源、跟随与退出检查保存在 `.vibe/context-observability-ink/`。
- Textual `./run_vibe.sh --fake` 启动、提交、打开 `/context` 与退出通过；80×24、36×18 的实际 fake 请求与弹窗记录保存在 `.vibe/context-observability-textual/`。
- `--fake --eval --eval-limit 3` 为 0/3：入口的固定文本 fake provider 不执行默认 eval 的编码任务，不作为本功能通过证据。真实模型任务与用户实际体验尚未验收。
- 没有切换默认入口，没有安装或接入第三方观测平台；原有审批、工具执行与任务预算保持独立。
