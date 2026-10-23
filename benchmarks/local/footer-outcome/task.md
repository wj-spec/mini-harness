# 修复：Ink 底部状态行重复显示运行结果

`tui/tests/app.test.tsx` 中的 `footer shows one outcome label for finished, cancelled and stopped runs` 当前失败：运行结束后底部会重复结果标签（如 `本轮结束 · 本轮结束`、`已取消 · 已取消`）。

要求：
- 让该测试通过，不要修改测试；
- 停止原因（如 `token_budget`）仍要显示，不能因去重而丢失；
- 只改 Ink 实现，最小改动；
- 运行 `npm --prefix tui test` 并报告命令、退出码与改动文件。
