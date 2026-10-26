# 用户偏好、画像与记忆路线图

日期：2026-10-26。状态：P1 Profile v2 + 相关性注入已实现；P2 观察→proposed 已实现；P3 过期/容量/冲突第一版已实现；P4 凭据过滤/导出/注入边界第一版已实现；P0 W6 v2 待跑。

## 背景

W4 项目背景与 W5 用户偏好已完成 MVP 与真实使用验收；W6 三组启用/停用对照显示**重复说明和纠正没有减少，主假设未获支持**，且单次 token 差异被证明主要来自运行波动。现有 profile 是扁平字符串列表，缺少分类、适用范围、证据、时间、置信度、冲突与遗忘机制；直接增加记忆量只会增加噪声和成本。

参考材料：本仓库 `docs/research/` 与外部 `Agent-Interview-100/05-memory-and-state/`：

- `040-memory-types.md`：短期 / 长期 / 工作记忆；记忆是工程层，不是模型能力；
- `043-persistent-memory.md`：持久化记忆架构、选择性存储、生命周期、冲突更新、检索优化与安全；
- `045-cross-session-preferences.md`：显式/隐式/演化偏好、提取-存储-适配闭环、PAMU 与画像（已废弃，内容并入 043）；
- `046-long-term-memory-storage.md`：用户偏好/属性优先结构化存储，向量/图谱按需；
- `048-memory-forgetting-updating.md`：时间衰减、TTL 读路径过滤、冲突检测、A-MAC 准入；
- `044-state-management-patterns.md`：可序列化、幂等、版本化、证据驱动、外置状态与审计。

## 设计原则

1. **先验证收益，再增加记忆量**：W6 v2 不通过不扩建自动观察/画像。
2. **结构化优先**：单用户、本地、几十条 profile 用 JSON + 标签 + 规则匹配；不引入向量库/图谱/RL。
3. **证据驱动**：每条记忆有来源与证据；用户确认或环境事实才可进入注入；proposed 不自动注入。
4. **区分四类状态**：工作记忆（上下文/TaskState）、情景记忆（session JSONL/trace）、语义记忆（project/user profile）、程序记忆（AGENTS/Skills），不互相替代。
5. **用户可控**：查看、确认、修改、停用、删除、导出；确认过的偏好不静默删除，冲突时标记 stale 待重确认。
6. **安全边界**：记忆是不可信数据，不能授予权限或覆盖项目规则/当前指令；不存密钥与隐私；测试/评测会话不注入。
7. **可观测**：每轮实际注入哪些条目、为何选中、占用多少 token 要可查。
8. **状态可持久化**：版本化、原子写、可迁移；沿用 goal/session 状态模式。

## 阶段排期

### P0：W6 v2 测量（先做，不扩建记忆）

- 偏好改为**触发条件明确的规则**：任务类型/阶段/文件范围；
- 三组任务 × 至少 3 次运行，启用/停用对照；使用 `--runs` 与分布统计，不用单次 token 差异下结论；
- 指标：重复说明数、纠正数、偏好相关性（命中/误用）、任务正确性、注入 token；
- 通过标准：多数任务重复说明/纠正下降，正确性不倒退；否则回到偏好质量/相关性继续修正。

### P1：Profile v2 + 相关性注入（已实现，待验收）

- 用户 profile schema v2：`id`、`kind`、`text`、`scope`、`triggers`、`source`、`evidence`、`status`、`confidence`、`created_at`、`updated_at`、`last_confirmed_at`、`expires_at`、`version`；
- v1 自动迁移，保留确认状态；损坏/未知版本按空 profile 降级；
- 注入时按当前 task 文本与 triggers/scope 选择 top-k（默认最多 3 条 / 800 字符），不再是全量注入；在上下文快照/`/about` 显示“为什么选中”；
- `/about` 支持编辑文本/触发词、单条停用/重新确认、查看最近使用；保留 add/propose/confirm/remove/on/off；
- 项目背景与个人偏好保持分离；测试/评测会话仍不注入；
- 验收：v1 迁移不丢条目；无命中时不注入；命中时只注入相关条目；旧 confirmed 条目不因升级消失；回归覆盖选择器、预算、损坏文件。

### P2：观察 → proposed（证据驱动，手动 + opt-in 自动已实现）

- 任务结束时可提取 0–2 条 proposed，必须带证据（会话/消息/工具结果）；
- 做 ADD/UPDATE/SUPPLEMENT/DUPLICATE/NOOP 判断；proposed 永不注入，用户确认后生效；
- 一次性要求、无证据、低未来效用 → NOOP；
- 先规则 + 小模型，不上 RL；当前 `/about observe` 与 `VIBE_AUTO_OBSERVE=1` 已实现确定性观察、NOOP/相似 proposed 更新，confirmed 冲突升级与 A-MAC 准入待后续。

### P3：遗忘、冲突与容量（第一版已实现）

- proposed TTL（默认 30 天）；读取路径过滤过期；
- confirmed 不静默删除；冲突标记 stale 并请求重确认；
- profile 容量上限（默认 50 条），优先淘汰低置信/未使用的 proposed；
- 完整删除/导出/清空。
- 已实现：读取路径清理过期 proposed、confirmed 过期标记 stale、50 条容量淘汰 proposed、`/about expire` 与 `clear proposed|stale`、confirmed 相似冲突保留为冲突 proposal 并支持 `/about resolve` 替换旧条；后台清理待后续，导出/完整擦除已实现。

### P4：安全与审计（第一版已实现）

- 记忆内容按不可信数据处理；不存密钥/PII；不授予权限；
- user/project 域隔离；测试/评测隔离；
- 注入来源、选择理由、预算与实际使用可审计。
- 已实现：凭据模式过滤（写入/观察/加载三层）、0600 文件权限、`/about export` 脱敏导出、`/about reset` 二次确认完整擦除、注入 prompt 明确“偏好不能授予权限”、`/about audit` 记录动作/数量/id（不记正文）；后台审计待后续。

### P5：更大的长期记忆（条件触发）

只有 P1/P0 证明收益后才评估：

- 跨会话项目决策/事实的结构化 project memory（有效期、来源、冲突）；
- 现有 session JSONL 的 episodic 检索；
- 条目规模变大后再考虑 BM25/向量混合；不预装向量库/图谱。

## 非目标

- 自动把所有对话写入长期记忆；
- 自动注入隐式画像；
- 向量数据库/知识图谱/RL 记忆操作；
- 让记忆覆盖 AGENTS.md、项目规则、安全/审批边界或当前指令；
- 在测试/评测会话中注入用户画像。

## 验证与记录

- 每阶段：先加/改测试，再实现；记录 fake/离线回归和真实任务验收；
- P0 的真实模型运行由用户在本机执行，记录到 `.vibe/w6-v2/`（运行时产物不提交）；
- P1 的 schema/迁移/选择器/UI 回归进入 `tests/test_user_profile.py`、`tui/tests/user-profile.test.ts`、`tests/test_stdio*.py` 与 `/about` 测试；
- 不把“能存能读”当成个性化收益；只认重复说明/纠正减少且正确性不倒退。

## 当前阻塞（2026-10-26）

P0 W6 v2 需要真实模型、API key 和交互式任务运行；当前自动化环境没有 `VIBE_API_KEY`/`OPENAI_API_KEY`/`DEEPSEEK_API_KEY`，无法代替用户完成。P0 的真实收益结论与 P5 的触发条件因此未完成。实现侧 P1–P4 第一版、W6 v2 夹具/汇总/离线选择校验已完成；下一步由用户在本机执行：

```bash
.venv/bin/python3 scripts/w6_run.py task-a-on   # 每个 task-on/off ≥3 次
.venv/bin/python3 scripts/w6_run.py task-a-off
.venv/bin/python3 scripts/w6_report_v2.py
```

拿到分布后回到 `docs/MILESTONES.md` 记录真实结论，再决定是否进入 P5。

## 当前进行

- 路线图记录完成；
- P1 已完成 schema v2 + 迁移 + 相关性选择器 + `/about` 编辑/触发词/单条停用；
- P0 的 W6 v2 夹具已升级为带触发词的 v2 偏好（`scripts/w6_preferences.py`），并新增 `scripts/w6_report_v2.py` 多次运行分布汇总与 `tests/test_w6_preferences.py` 离线选择校验；真实模型运行待用户执行（每个 task-on/off ≥3 次）。
