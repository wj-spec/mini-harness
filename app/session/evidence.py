"""最近一轮 RoundRecord 的中文证据摘要。

纯函数、只读、容错：只渲染记录里已有的证据字段，不读取也不复制文件正文。
"""

from __future__ import annotations

EMPTY_TEXT = "当前会话还没有轮次记录。完成一次任务后再查看。"
MAX_FILE_CHANGES = 8
MAX_VERIFICATIONS = 5
MAX_AUDIT_ITEMS = 8
MAX_RECEIPT_CHARS = 600
MAX_FIELD_CHARS = 300


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _text(value, default: str = "未知") -> str:
    if value is None or value == "":
        return default
    return str(value)[:MAX_FIELD_CHARS]


def _raw_bool(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "null"


def _short_hash(value) -> str:
    if value is None or value == "":
        return "无"
    return str(value)[:8]


def _count_suffix(total: int, shown: int) -> str:
    if total > shown:
        return f"（共 {total} 条，仅显示前 {shown} 条）"
    return f"（{total} 条）"


def _completion_line(verified, completion) -> str:
    if verified is True:
        verified_text = "已验证"
    elif verified is False:
        verified_text = "未验证"
    elif verified is None:
        verified_text = "不适用"
    else:
        verified_text = f"未知（{_text(verified)}）"
    line = f"- 完成判定：{verified_text}（verified={_raw_bool(verified)}）"
    data = _as_dict(completion)
    if not data:
        return line + "；completion：未记录"
    line += f"；completion.allowed={_raw_bool(data.get('allowed'))}"
    codes = [str(code) for code in _as_list(data.get("codes"))]
    line += "；原因码：" + ("、".join(codes) if codes else "无")
    messages = [str(item) for item in _as_list(data.get("messages")) if str(item).strip()]
    if messages:
        line += "；说明：" + "；".join(messages)[:MAX_FIELD_CHARS]
    return line


def _audit_items(label: str, items: list) -> list[str]:
    if not items:
        return [f"  - {label}：无"]
    shown = [str(item)[:MAX_FIELD_CHARS] for item in items[:MAX_AUDIT_ITEMS]]
    header = f"  - {label}{_count_suffix(len(items), len(shown))}："
    return [header, *[f"    {item}" for item in shown]]


def _audit_lines(audit) -> list[str]:
    data = _as_dict(audit)
    if not data:
        return ["- 环境审计：未记录（守卫或审计未启用）"]
    header = (
        f"- 环境审计：status={_text(data.get('status'))}；"
        f"integrity={_text(data.get('integrity'))}；"
        f"verified={_raw_bool(data.get('verified'))}；"
        f"coverage={_text(data.get('coverage'))}；"
        f"改动文件 {_text(data.get('changed_files'), '0')} 个"
    )
    if data.get("contract_audit"):
        header += f"；contract_audit={_text(data.get('contract_audit'))}"
    lines = [
        header,
        *_audit_items("未归因改动", _as_list(data.get("unattributed"))),
        *_audit_items("发现", _as_list(data.get("findings"))),
    ]
    llm = _as_dict(data.get("llm"))
    if llm:
        lines.append(
            "- LLM 审计："
            f"status={_text(llm.get('status'))}；"
            f"integrity={_text(llm.get('integrity'))}；"
            f"contract={_text(llm.get('contract_audit'))}"
        )
    return lines


def _change_lines(task_state: dict) -> list[str]:
    changes = [item for item in _as_list(task_state.get("file_changes"))
               if isinstance(item, dict)]
    if not changes:
        return ["- 改动文件：无记录"]
    shown = changes[:MAX_FILE_CHANGES]
    lines = [f"- 改动文件{_count_suffix(len(changes), len(shown))}："]
    for change in shown:
        path = _text(change.get("path"))
        kind = _text(change.get("kind"))
        before = _short_hash(change.get("before_sha256"))
        after = _short_hash(change.get("after_sha256"))
        lines.append(f"  - {path} [{kind}]：before={before} → after={after}")
    return lines


def _verification_lines(task_state: dict) -> list[str]:
    verifications = [item for item in _as_list(task_state.get("verifications"))
                     if isinstance(item, dict)]
    if not verifications:
        return ["- 验证记录：无（未记录验证命令）"]
    shown = verifications[:MAX_VERIFICATIONS]
    lines = [f"- 验证记录{_count_suffix(len(verifications), len(shown))}："]
    for verification in shown:
        command = _text(verification.get("command"))
        status = _text(verification.get("status"))
        exit_code = _text(verification.get("exit_code"))
        lines.append(f"  - `{command}`：status={status}；exit={exit_code}")
    return lines


def _receipt_lines(receipt) -> list[str]:
    if not isinstance(receipt, str) or not receipt.strip():
        return ["- 回执摘要：无"]
    truncated = len(receipt) > MAX_RECEIPT_CHARS
    text = receipt[:MAX_RECEIPT_CHARS] + ("…" if truncated else "")
    header = "- 回执摘要（前 600 字符）：" if truncated else "- 回执摘要："
    return [header, *[f"  {line}" for line in text.splitlines()]]


def format_round_evidence(record: dict | None) -> str:
    """把最近一轮 RoundRecord 渲染成中文多行摘要；缺字段容错，不抛异常。"""
    if not isinstance(record, dict):
        return EMPTY_TEXT
    task_state = _as_dict(record.get("task_state"))
    usage = _as_dict(record.get("usage"))
    cost = record.get("cost_usd")
    cost_text = f"{cost:.4f}" if type(cost) in (int, float) else "未知"
    lines = [
        "最近一轮证据：",
        f"- 轮次：{_text(record.get('round_index'))}；"
        f"run_id：{_text(record.get('run_id'))}；"
        f"状态：{_text(record.get('status'))}；"
        f"步骤：{_text(record.get('steps'))}；"
        f"成本：${cost_text}",
    ]
    if record.get("stop_reason"):
        lines.append(f"- 停止原因：{_text(record.get('stop_reason'))}")
    if record.get("started_at") or record.get("ended_at"):
        lines.append(
            f"- 开始：{_text(record.get('started_at'))}；"
            f"结束：{_text(record.get('ended_at'))}"
        )
    if record.get("goal") or record.get("task_kind"):
        lines.append(
            f"- 目标：{_text(record.get('goal'))}；"
            f"任务类型：{_text(record.get('task_kind'))}"
        )
    if usage:
        lines.append(
            f"- 用量：prompt={_text(usage.get('prompt_tokens'), '0')} / "
            f"completion={_text(usage.get('completion_tokens'), '0')} / "
            f"total={_text(usage.get('total_tokens'), '0')} tokens"
        )
    lines.append(_completion_line(record.get("verified"), record.get("completion")))
    lines.extend(_audit_lines(record.get("audit")))
    lines.extend(_change_lines(task_state))
    lines.extend(_verification_lines(task_state))
    lines.extend(_receipt_lines(record.get("receipt")))
    return "\n".join(lines)
