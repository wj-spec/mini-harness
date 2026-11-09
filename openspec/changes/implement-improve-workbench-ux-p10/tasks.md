# P10 执行清单

- [x] 核对 entries/transcriptLines、轮次与错误可得的字段；确定本批以用户任务轮次为定位单位，rounds 账本深链单列。
- [x] `tool_call` 可选 `tier`（T0–T4）协议与 Python 事件；旧后端缺省不折叠。
- [x] ViewState：task 条目记录 runId 与终态，tool 条目记录 tier。
- [x] `/find <关键词>`、`/rounds`、`/errors` 结果面板与定位动作；复用回看/返回语义。
- [x] 折叠连续成功只读工具，原始调用与输出仍可选中/详情查看。
- [x] 先红后绿：纯函数/渲染/App 旅程测试；3000 条记录量级搜索 p95 < 100ms 实测。
- [x] 完整 Ink/Python 回归；文档 verification/roadmap/MILESTONES/task-log/USAGE/验收清单同步；证据归档 `.vibe/ux-p10-history-2026-11-09/`。
- [x] 跨会话检索：`search` 后端（用途隔离/分页/损坏计数）+ `/history` 面板 + 打开会话并携带 focus 定位；旧后端回退。
- [x] rounds 账本深链：`rounds_ledger` 后端（验证三态/费用/损坏计数）+ `/rounds` 账本面板与正文定位；不在正文时提示打开原会话；无能力回退正文轮次。
- [x] 跨会话索引/增量缓存（签名判断、只重建变化会话、删除剔除、损坏计数、消息上限截断）与搜索高亮（结果行 + 正文目标行）。
- [ ] 磁盘级持久索引/预热、账本到 P6 检查记录的精确跳转。
- [ ] 真人终端验收与后续批次。
