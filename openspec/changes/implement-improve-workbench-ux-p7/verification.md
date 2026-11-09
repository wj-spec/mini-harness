# P7 验证记录

状态：实施中，未整体验收。前一目标增量完成 P7 进程内控制，本批推进会话持久恢复；两批基线与证据分别保留，全部已有改动保持。

## 持久待办与未知发送恢复（2026-11-09）

本批基线 `.vibe/ux-p7-persistence-2026-11-09/baseline-manifest.json` 为 638 文件；最终 640 文件，新增 followup-store 与 followup-persistence.test。修改范围为 7 份 Ink 源码/测试及配套文档；既有 Python 后端、ApprovalGate、协议消息形状和执行器未变。BackendClient 的 run 增加可选预分配请求 ID，沿用原请求协议。

行为与边界：

- 添加、编辑、撤回先原子保存，再确认成功。待办属于会话，attach 按任务分域；同会话只有一个写窗口，其他窗口只读，原窗口关闭后点击“接入”，仍默认暂停。
- 发送前保存队首正文与同一请求 ID，匹配 done 后移除追踪记录。中断、发送异常或终态落盘失败都保留待核对，不自动放回队列重发；旧终态不能清理新请求。
- “历史”可核对原请求身份；“移出”有确认说明，只移出追踪，不停止或重发原任务。当前正在执行的记录不能移出。没有自动对账或自动补发。
- 坏文件、非普通文件、范围不符、目录被替换均拒绝覆盖。失败保留添加草稿和编辑；正常退出保留已存待办，未存编辑退出前提示。未提供 session_path 的旧后端保留“仅本次窗口”兼容行为。
- 上限为 100 条、单条 64 KiB、总文件 256 KiB。原子替换应对进程中断；不宣称机器断电时的 fsync 级耐久性，也不把本地文件校验当安全隔离。

测试、评审与失败证据（目录同上）：

1. `reopen-red.log` 复现重开丢队列；`ownership-red.log` 复现只读窗口仍显示编辑/继续；`send-red.log` 复现请求身份未沿用与中断无追踪。原始红证据和各轮绿证据保留。
2. `unsaved-exit-red.log` 复现已存队列中的未保存编辑直接退出；现在退出提示与取消退出中文光标都覆盖。
3. `review-red.log` 复现 error 后追踪仍被当成运行中、会话目录替换后误写。修复后 `review-green.log` 相关 56/56，通过公开 App 和实际 BackendClient；自审见 `review.md`。
4. `tui-full.log` 为修复前 668/668；最终冻结 7 份源码后 `tui-full-final.log` 为 **670/670**，共新增 12 条 App 恢复旅程与 1 条请求身份传输测试。没有把较早全量代替最终版本。
5. `python-full.xml` 为 **1919 项，1915 passed / 4 skipped**，0 错误/失败；最终核对 core/app/Python tests 与全量开始时的哈希一致，既有条件跳过和 tar 弃用警告保留。
6. `native-final.log` / `native-verified.json` / 原始 ANSI：实际 Ink 与 Python，在独立临时 Git 中完成拒绝、待办保存、正常退出重开、双窗口只读/接入、发送后 SIGKILL、重开不重发、32×12 核对/确认移出、下一条仅执行一次。`native-inspections.json` 核对运行身份和实际目标。第一次脚本在窄屏上用连续字符串匹配换行文案失败；`native-wrap-assertion.*` 保留，按可见换行修正断言后通过，没有为此改产品行为。
7. `persistence-manifest.json`、`persistence-increment.patch` 与 `source-review.patch` 绑定基线、最终源码和证据；文档链接和限定范围核验通过，未 stage 或提交。

本批完成工程验证；Ghostty 真人完整旅程、Owner Review 仍待完成。P7 继续实施中，整体目标保持 active。此前 P4 真实模型及其他阶段真人/原生验收不因本批转为完成。

以下为前一增量的历史证据，其“仅本次窗口”描述属于当时实现。

## 进程内后续消息控制（2026-11-09）

本批 8 份 Ink 源码/测试（新增 use-followups、followup-panel 和 followup-app.test），后端、协议、ApprovalGate 与执行器保持。

- 点击“后续 N”或无参数 `/followup` 打开管理，逐条阅读完整正文，编辑保存/取消、撤回与继续都有按钮；方向键、滚轮、Tab/Enter 可补充操作。
- 查看即暂停自动发送，返回不会恢复自动执行。管理页有独立编辑器，普通中文草稿和光标保持，Ctrl+C 始终指向当前运行；审批到达提示返回处理。
- done 非完成、verified=false、completion.allowed=false、等待计划决策及未确认的实际验证失败会保留并暂停。正常完成按 FIFO 推进；重复/迟到终态不影响另一当前运行。发送同步失败保留，断线可以读/改但不能继续。
- 退出明确提示待办仅本次窗口；运行中保留原等待结束、停止与后台选项。取消退出保留未保存编辑。未实现持久恢复，P7 不标完成。

评审与失败证据：

1. `first-red.log` 证明验收失败后仍自动发送；公开 App 完整旅程先红后绿。
2. `edit-exit-red.log` 证明取消退出后未保存编辑丢失；保持挂载并停用隐藏输入后修复。`exit-options-red.log` 证明待办提示遮掉运行中原退出选项，现已保留。
3. 相关范围首轮 212/214：旧取消丢弃断言按新契约更新；审批测试在 Ctrl+C 后仍期待允许按钮，与原停止行为矛盾，改为核对正在停止、审批已清除。第二轮仅后一夹具失败，原日志保留。
4. 实际终端前两次分别误匹配启动提示中的“就绪”、误把单次工具拒绝视为整轮结束；改用标题就绪徽标及真正终态。第三次暴露产品缺口：验证 exit=1、verified=null/allowed=true 仍自动续发。`pty-verification-red.log`、原始 ANSI、失败 inspection 记录和 `verification-red.log` 保留；修复为结合当前运行的验证失败事实，明确 verified=true 可覆盖已修复失败，其他运行不串入。
5. 先前完整 Ink 654/654 只覆盖修复前 11 条新增旅程，不能替代实际终端发现；现新增共 13 条 App 回归，`verification-fixed.log` 13/13 通过，最终又补 1 条快速授权点击回归，共新增 14 条；最终结果见下方。

6. 修正后的实际 PTY 又复现取消退出使主草稿光标回到末尾：原退出 UI 卸载主 Composer。`draft-exit-red.log` 与 `pty-draft-cursor-red.log` 留证；保持主 Composer 挂载、隐藏时停用输入后修复。
7. 第二次完整 Ink 为 655/656，快速切换授权菜单时点击“拒绝”却命中旧的 session 区域；`rapid-red.log` 缩短为等待可见按钮后立即点击，稳定复现过时命中。共享 ActionButtons 在 layout effect 同步坐标，审批鼠标入口也只接受当前可见动作。`rapid-fixed.log` 4/4、八组并发各 4 项全部通过；没有修改 ApprovalGate 或扩大授权规则。

最终验证：

- `tui-full-final.log`：完整 Ink **657/657**，0 失败；`final-scenarios.log` 后续控制 **13/13**，另 1 条审批快速点击新增回归随全量通过。
- `followup-pty-verified.json` / 原始 ANSI：实际 Ink/Python 拒绝写入、允许验证后得到 exit=1，待办暂停；编辑保存、取消退出、中文光标、32×12 动作和显式继续通过。`completed-inspections.json` 只有两条运行记录，第二条目标正是编辑后的正文；点击继续之前只有第一条，没有管理动作触发的隐藏运行。
- 最终 8 份源码/测试冻结后完成全量和实际终端复验；前两次全量与所有实际失败记录均保留。完整 Python 已完成 1915 passed / 4 skipped，后端及 Python 测试源码保持；既有条件跳过和 tar 弃用警告保留。最终源码冻结、差异和证据摘要为 `control-manifest.json` 与 `control-increment.patch`，存于 `.vibe/ux-p7-control-2026-11-09/`；自审见 review.md，未 stage 或提交。

归档核对 631→638 文件，本批仅 8 份源码/测试与 12 份文档变化；362 份范围外受保护文件保持，292 个本地链接/锚点有效。文档测试 5 项通过，`git diff --check` 清洁。

持久保存、重开默认暂停、多实例隔离、发送后断线的未知状态，以及 Ghostty 真人完整旅程/Owner Review 继续待完成。工具 PTY 使用 vibe-pty 与临时 Git 仓库，全部 fake，不替代原生 Ghostty GUI 或真实模型证据。
