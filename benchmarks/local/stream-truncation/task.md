# 修复：截断的工具调用仍会被执行

`tests/test_provider.py` 中两个测试当前失败：

- `test_incomplete_tool_calls_are_not_executable`：参数被截断（非法 JSON）的工具调用仍进入可执行列表；
- `test_stream_without_done_or_finish_reason_is_truncated`：没有 `[DONE]` 也没有 `finish_reason` 的流被当成正常结束。

要求：
- 让这两个测试通过，不要修改测试；
- 不完整接收的工具调用绝不能进入可执行列表；异常断流要明确标记为截断；
- 正常结束（有 `[DONE]` 或明确 `finish_reason`）的流行为不能被改坏；
- 只做相关最小改动；
- 运行相关测试并报告命令、退出码与改动文件。
