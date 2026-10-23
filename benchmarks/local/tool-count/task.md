# 修复：取消的 run 在 trace 里工具调用数记为 0

`tests/test_stdio.py::test_cancelled_run_records_observed_tool_count` 当前失败：run 被取消或异常结束时，`run_end.tool_calls` 没有记录已执行的工具数。

要求：
- 让该测试通过，不要修改测试；
- 正常结束的 trace 计数不能被改坏；
- 只做相关最小改动；
- 运行相关测试并报告命令、退出码与改动文件。
