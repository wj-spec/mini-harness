# W5：关于你——个人偏好

日期：2026-10-23。状态：MVP 实现完成；真实使用验收待做。

## 目标

提供一个用户级、可查看、可确认/修正/停用/删除的个人偏好入口；只有**用户已确认**且**启用**的条目进入新任务，项目规则与当前明确指令优先。

边界：W5 不自动从聊天提取画像；先显式确认，验证价值后再考虑观察与提议机制。项目事实在 W4，个人偏好在 W5，二者分开管理。

## 存储

- 默认路径：`~/.vibe/user-profile.json`；可用 `VIBE_USER_PROFILE` 覆盖（Ink 与 Python 后端读取同一环境变量）。
- 格式：

```json
{
  "version": 1,
  "enabled": true,
  "entries": [
    {"text": "回答先给结论，再给细节", "source": "user", "status": "confirmed"},
    {"text": "可能偏好简短回复", "source": "observation", "status": "proposed"}
  ]
}
```

- `status`: `confirmed`（用户已确认，会注入）或 `proposed`（待确认观察，不注入）。
- 损坏、版本不匹配或缺失时按空偏好处理，不阻塞任务。

## 注入

- stdio 后端在**每次 run 前**读取；仅当会话 purpose 为 `user` 且 `enabled=true` 时，把已确认条目作为独立 `[USER PROFILE]` system 段注入（逐条带来源，最多 3000 字符）。
- 测试/基准会话（`--test-session`、fake provider 会话）不注入，避免污染对照和泄露画像。
- 当前明确指令优先于历史偏好；项目规则、安全与审批约束优先于个人偏好。

## 管理（Ink）

- `/about`：查看启用状态与条目（`[已确认]` / `[待确认]`、来源）；
- `/about add <文本>`：添加已确认偏好（来源 user）；
- `/about propose <文本>`：记录待确认观察（来源 observation，不注入）；
- `/about confirm <序号>`：把待确认观察转为已确认；
- `/about remove <序号>`：删除；
- `/about on|off`：启用/停用全部个人偏好。

## 验收

1. 新增“回答先给结论”等偏好，新会话任务中实际体现；
2. `/about propose` 的待确认观察不注入，`confirm` 后新会话生效；
3. `/about off` 后新会话不再使用；`remove` 后不再出现；
4. 项目规则与审批约束不被个人偏好覆盖（例如仍要求审批）；
5. 记录哪些重复说明/纠正因偏好而减少（W6 做三组对照）。

## 非目标

自动从对话推断并持久化、跨用户共享、替代项目背景、替代当前指令。
