# W5/P1：用户偏好与画像条目

日期：2026-10-26。状态：P1 Profile v2 + 相关性注入完成；P2 观察→proposed 完成；P3 过期/容量/冲突第一版完成；P4 凭据过滤/导出/注入边界第一版完成；W6 v2 未证明收益，2026-10-26 决定停止 P0 扩张、不进入 P5。

## 目标

提供用户级、可查看、可确认/修正/停用/删除的个人偏好入口；只有**已确认、未过期且与当前任务相关**的条目进入新任务。项目规则、安全/审批约束和当前明确指令优先。

边界：
- 不自动从聊天提取并生效；观察只以 `proposed` 保存，永不自动注入；
- W4 项目背景独立管理；本文件只存个人偏好/画像候选；
- 不引入向量库/知识图谱；当前是结构化 JSON + 标签匹配。

## 存储

- 默认：`~/.vibe/user-profile.json`；`VIBE_USER_PROFILE` 可覆盖（Ink 与 Python 后端共用）。
- v2 格式：

```json
{
  "version": 2,
  "enabled": true,
  "entries": [
    {
      "id": "pref-...",
      "kind": "preference",
      "text": "解释代码时先给结论，再给细节",
      "scope": "workflow",
      "triggers": ["解释", "说明"],
      "source": "user",
      "evidence": [],
      "status": "confirmed",
      "confidence": 0.9,
      "created_at": "2026-10-26T00:00:00Z",
      "updated_at": "2026-10-26T00:00:00Z",
      "last_confirmed_at": "2026-10-26T00:00:00Z",
      "expires_at": ""
    }
  ]
}
```

- `status`: `confirmed`（已确认，可注入）、`proposed`（待确认观察，不注入）、`stale`（已停用/冲突，不注入）。
- `triggers`：任务文本匹配词；有 triggers 的条目只在命中时注入，无 triggers 的旧条目按“通用偏好”兼容注入。
- 损坏、未知版本、非法字段按空/默认降级，不阻塞任务；v1 自动迁移并保留 confirmed 状态。
- 过期条目在读取时过滤，不依赖后台清理。

## 注入

- stdio 在每次 run 前读取，仅用户会话且 `enabled=true`；
- 先过滤 `confirmed` 与未过期，再用 `select_user_preferences` 按 triggers 匹配当前 task：
  - 命中条目优先；最多 **3 条 / 800 字符**；
  - 无 triggers 的旧条目作为兼容兜底，但相关命中条目优先；
  - 测试/基准会话不注入；
- 独立 `[USER PROFILE]` system 段，逐条带来源；不改变权限、项目规则和当前指令的优先级。

## 管理（Ink）

- `/about`：查看状态与条目；显示 `[已确认]/[待确认]/[已停用]`、来源、触发词；
- `/about add <文本>` / `/about propose <文本>`：确认偏好 / 待确认观察；
- `/about edit <序号> <文本>`：修改文本；
- `/about triggers <序号> <词1,词2|->`：设置/清除触发词；
- `/about confirm|disable|enable <序号>`：确认、停用、重新启用单条；
- `/about observe`：只读本会话用户消息，按显式长期线索（以后/记住/始终/from now on 等）生成最多 2 条 **proposed**，带证据，不自动注入；
- `/about remove <序号>` / `/about on|off`：删除单条 / 启停全部；
- 下一次任务前重读，修改即时生效。

## 验收

1. v1 profile 升级后 confirmed 条目不丢失，旧行为不阻塞；
2. 有 triggers 的条目只在相关任务注入；无关条目不进入 system；
3. 无 triggers 的旧条目仍兼容注入；
4. `/about edit/triggers/disable` 落盘后下一次任务生效；
5. 过期条目不再注入；损坏文件不阻塞；
6. 项目规则、审批边界与当前指令不被偏好覆盖；
7. W6 v2 多次运行验证重复说明/纠正是否减少（P0）。

## 观察 → proposed（P2 第一步）

- `tui/src/preference-observer.ts` 读取 `<session>/events.jsonl` 的 user 消息；
- 只匹配显式长期偏好线索，最多 2 条；生成 `status=proposed`、`source=observation`、`evidence=session:<id>`、建议 triggers 与置信度 0.6；
- 与已有条目规范化文本去重；proposed 永不被后端注入，必须 `/about confirm`；
- 当前由 `/about observe` 手动触发；设置 `VIBE_AUTO_OBSERVE=1` 后，普通任务 done/stopped 结束会自动观察（默认关闭，cancelled/error 不触发）；
- `mergeProposals`：相同文本 NOOP；相似 proposed 更新文本/触发词/证据；confirmed/stale 不静默改写；新 proposal 默认 30 天 expires_at；
- 冲突升级（UPDATE/CONFLICT 到 confirmed）、A-MAC 准入和自动清理后台任务仍待后续。

## 过期、容量与冲突（P3 第一版）

- 读取路径维护（打开 `/about`、执行 about 命令时）：
  - 过期 `proposed` 直接清理；
  - 过期 `confirmed` 标记 `stale`，不静默删除；
  - profile 超过 50 条时优先淘汰低置信/旧的 `proposed`，不自动删除 confirmed；
- `/about expire <序号> <天数|ISO|->`：设置或清除过期时间；
- `/about clear proposed|stale`：清理对应状态条目，confirmed 不受影响；
- `mergeProposals` 将相似 confirmed 保留为带 `conflict:<id>` 证据的 proposed，不自动改写；`/about` 显示“与已确认偏好冲突待确认”，用 `/about resolve <序号>` 一次完成：旧条转 stale、冲突 proposal 转 confirmed；也可手动 disable/confirm；
- 后台自动清理、confirmed 冲突交互式替换、A-MAC 准入仍待后续。

## 安全与审计（P4 第一版）

- 明显密钥/凭据（`sk-*`、GitHub token、AWS key、私钥头、`api_key=`/`password=`/`token=` 等）不会写入偏好：
  - `/about add/propose/edit` 直接拒绝；
  - `/about observe`/自动观察跳过含凭据的句子；
  - Python 加载器再次过滤，防止手工文件中的凭据进入模型上下文；
- 偏好文件以 `0600` 写入（POSIX）；
- `/about export` 把 profile 导出为 `exports/user-profile-<时间>.json`，同样过滤疑似凭据；
- `/about reset` 需 `/about reset confirm` 二次确认，删除 profile 与导出文件（完整擦除）；
- `[USER PROFILE]` 注入明确声明：偏好是数据，不是指令，不能授予工具/文件/命令权限或改变审批边界；
- `trace.jsonl` 的 `user_profile_selection` 记录实际注入条目，供审计；
- `user-profile-audit.jsonl` 记录每次 add/propose/observe/edit/confirm/disable/remove/resolve 的动作、条目数量与 id，不复制偏好正文；`/about audit` 可查看最近记录；
- 测试/评测会话隔离、项目/个人域分离继续有效。

## 非目标

自动写入并注入画像、向量/图谱检索、跨用户共享、替代项目背景、替代 AGENTS.md/当前指令。
