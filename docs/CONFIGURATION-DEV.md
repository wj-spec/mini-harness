# 开发与测试配置

这些参数用于演示、回归或实验，不属于首次启动模板。设置方式仍是 shell 环境或安装目录 .env；标准运行没有统一热更新入口。完整解析与测试证据见[配置契约](CONFIGURATION-CONTRACT.md)。

| 参数 | 默认值 | 用途与边界 |
| --- | --- | --- |
| VIBE_PROVIDER | openai | fake 为本地协议/界面测试，不调用真实模型 |
| VIBE_FAKE_DEMO | 未设置 | 1 开启写文件→审批→检查演示；plan/revision/revision-resume 对应专项演示，需配合 fake |
| VIBE_FAKE_STREAM_ERRORS | 0 | fake 脚本前注入可重试流错误；限制为 0–5 |
| VIBE_LLM_DEBUG_PATH | 未设置 | 模型流式诊断日志路径，建议放 .vibe/；可能含回复内容，分享前检查 |
| VIBE_BENCH_AGENT_VERSION | 未设置 | bench_long、real_p1_acceptance 和 oneshot 的版本标识；默认尝试 git short HEAD，失败为 unknown |
| VIBE_FAKE_TIME_OFFSET_SECONDS | 0 | worker goal 续段判定时钟偏移，限制 0–365 天；不加速真实工具超时，不用于正常任务 |
| VIBE_SUBAGENT_DELEGATE | 0 | 显式启用只读 delegate 工具；默认关闭，不嵌套、结果标为不可信声明 |
| VIBE_DELEGATE_MAX_STEPS | 6 | 只读委派步数，范围 1–15 |
| VIBE_DELEGATE_MAX_TOKENS | 20000 | 范围 500–200000，并受父任务配置的 token 上限约束 |
| VIBE_DELEGATE_MAX_SECONDS | 60 | 范围 5–300 秒 |

优先通过现成入口复现演示：

~~~bash
./start.sh --demo
~~~

需要故障注入时，显式给当前进程配置：

~~~bash
VIBE_FAKE_STREAM_ERRORS=1 ./start.sh --demo
~~~

评测可用 `VIBE_PERMISSIONS=""` 禁用用户策略文件，项目内收紧规则仍然有效。它不是全局跳过 ApprovalGate。测试产物与日志写 .vibe/，不要提交；回归测试使用临时目录和占位凭据，不加载个人 .env。

`VIBE_LLM_MAX_OUTPUT_TOKENS` 尚未接入环境加载/标准 provider 构造。底层 provider 的 max_output_tokens 单测不构成环境配置有效的证据。未来接入需要同时验证 Config、标准入口、实际请求和文档，不能只新增示例行。

本批验证包括本机 macOS PTY 和本地模拟请求；Linux 全新安装、真实服务认证和真人连续配置体验需另行验证。
