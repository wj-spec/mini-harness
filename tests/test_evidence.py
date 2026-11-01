import json

from app.evidence import format_round_evidence

EMPTY = "当前会话还没有轮次记录。完成一次任务后再查看。"

AFTER_SHA = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
BEFORE_SHA = "aaaa1111bbbb2222cccc3333dddd4444eeee5555ffff6666aaaa7777bbbb8888"


def _change(path="notes.txt", kind="create", before=None, after=AFTER_SHA, **extra):
    change = {
        "path": path,
        "kind": kind,
        "before_sha256": before,
        "after_sha256": after,
        "before_status": "missing" if before is None else "present",
        "after_status": "present",
        "size": 6,
        "step": 1,
        "tool": "write_file",
        "tool_call_id": "w1",
        "approval_scope": "once",
        "at": "2026-10-31T05:08:57.218357+00:00",
    }
    change.update(extra)
    return change


def _verification(command="test -f notes.txt", status="succeeded", exit_code=0, **extra):
    verification = {
        "command": command,
        "status": status,
        "exit_code": exit_code,
        "ok": exit_code == 0,
        "output_digest": "5c7be382e6604ef750c7ab7de407fb8dec1fb34d64e22dcbb08fcf12dad59186",
        "step": 2,
        "mutation_seq": 1,
        "at": "2026-10-31T05:08:57.405796+00:00",
    }
    verification.update(extra)
    return verification


def _record(**overrides):
    record = {
        "schema_version": 1,
        "round_index": 1,
        "run_id": "3364077c",
        "goal": "创建 notes.txt 并验证",
        "task_kind": "action",
        "status": "done",
        "stop_reason": "",
        "steps": 3,
        "usage": {"prompt_tokens": 6691, "completion_tokens": 56, "total_tokens": 6747},
        "cost_usd": 0.0,
        "started_at": "2026-10-31T05:08:57.343679+00:00",
        "ended_at": "2026-10-31T05:08:57.418426+00:00",
        "verified": True,
        "completion": {"allowed": True, "codes": [], "messages": []},
        "audit": {
            "schema_version": 1,
            "status": "complete",
            "integrity": "clean",
            "verified": True,
            "findings": [],
            "unattributed": [],
            "changed_files": 1,
            "coverage": "full",
            "contract_audit": "unknown",
        },
        "receipt": (
            "[运行结束] status=done steps=3 tokens=6747 cost=$0.0000\n"
            "[任务验收] 已验证（有环境证据）；仍需你核对是否满足原始需求\n"
            "完成判定：通过"
        ),
        "task_state": {
            "goal": "创建 notes.txt 并验证",
            "files_changed": ["notes.txt"],
            "file_changes": [_change()],
            "verifications": [_verification()],
            "mutation_seq": 1,
            "last_verification_seq": 1,
            "last_verification_ok": True,
        },
    }
    record.update(overrides)
    return record


def test_no_record_returns_explicit_message():
    assert format_round_evidence(None) == EMPTY


def test_non_dict_record_is_tolerated_like_no_record():
    assert format_round_evidence("not a record") == EMPTY


def test_full_record_covers_round_completion_audit_changes_verifications_receipt():
    text = format_round_evidence(_record())

    assert "最近一轮证据" in text
    # 轮次基本字段
    assert "3364077c" in text
    assert "1" in text and "3" in text
    assert "2026-10-31T05:08:57" in text
    assert "创建 notes.txt 并验证" in text
    # 完成判定与 completion
    assert "完成判定" in text and "已验证" in text and "verified=true" in text
    assert "completion.allowed=true" in text
    assert "原因码：无" in text
    # 环境审计
    assert "环境审计" in text
    assert "status=complete" in text
    assert "integrity=clean" in text
    assert "coverage=full" in text
    assert "改动文件 1 个" in text
    assert "未归因改动：无" in text
    assert "发现：无" in text
    # 改动文件：路径 + 前 8 位 hash + kind
    assert "notes.txt" in text
    assert "[create]" in text
    assert AFTER_SHA[:8] in text
    assert "before=无" in text
    # 验证记录
    assert "test -f notes.txt" in text
    assert "status=succeeded" in text
    assert "exit=0" in text
    # receipt 摘要
    assert "回执摘要" in text
    assert "[运行结束]" in text
    assert "完成判定：通过" in text


def test_verified_false_and_null_are_rendered_distinctly():
    unverified = format_round_evidence(_record(
        verified=False,
        completion={"allowed": False, "codes": ["no_verification"],
                    "messages": ["有文件改动，但没有记录到验证命令"]},
        audit=None,
    ))
    assert "未验证" in unverified and "verified=false" in unverified
    assert "completion.allowed=false" in unverified
    assert "no_verification" in unverified
    assert "有文件改动，但没有记录到验证命令" in unverified
    assert "环境审计：未记录" in unverified

    not_applicable = format_round_evidence(_record(verified=None, completion=None, audit=None))
    assert "verified=null" in not_applicable
    assert "completion：未记录" in not_applicable


def test_hashes_are_truncated_to_eight_characters():
    text = format_round_evidence(_record(
        task_state={
            "file_changes": [_change(before=BEFORE_SHA, after=AFTER_SHA)],
            "verifications": [],
        },
    ))
    assert BEFORE_SHA[:8] in text and AFTER_SHA[:8] in text
    assert BEFORE_SHA not in text
    assert AFTER_SHA not in text


def test_does_not_render_file_body_or_raw_output():
    text = format_round_evidence(_record(
        task_state={
            "file_changes": [_change(content="SECRET-FILE-BODY", output="SECRET-ECHO")],
            "verifications": [_verification(output="SECRET-COMMAND-OUTPUT")],
        },
        receipt="回执不含正文",
    ))
    assert "SECRET-FILE-BODY" not in text
    assert "SECRET-ECHO" not in text
    assert "SECRET-COMMAND-OUTPUT" not in text


def test_missing_fields_are_tolerated_without_raising():
    assert "最近一轮证据" in format_round_evidence({})
    partial = format_round_evidence({"run_id": "abc"})
    assert "abc" in partial and "最近一轮证据" in partial

    malformed = {
        "round_index": "第一轮",
        "run_id": 42,
        "status": None,
        "steps": "x",
        "usage": "bad",
        "cost_usd": "bad",
        "verified": None,
        "completion": None,
        "audit": None,
        "receipt": None,
        "task_state": {"file_changes": "bad", "verifications": 3},
    }
    text = format_round_evidence(malformed)
    assert "第一轮" in text
    assert "42" in text
    assert "改动文件：无记录" in text
    assert "验证记录：无" in text
    assert "回执摘要：无" in text

    weird_verified = format_round_evidence({"verified": "unknown"})
    assert "unknown" in weird_verified


def test_changes_and_verifications_are_capped():
    changes = [_change(path=f"file-{index}.txt") for index in range(10)]
    verifications = [_verification(command=f"cmd-{index}") for index in range(6)]
    text = format_round_evidence(_record(task_state={
        "file_changes": changes,
        "verifications": verifications,
    }))
    assert "file-7.txt" in text
    assert "file-8.txt" not in text
    assert "file-9.txt" not in text
    assert "共 10 条" in text
    assert "cmd-4" in text
    assert "cmd-5" not in text
    assert "共 6 条" in text


def test_receipt_is_truncated_to_600_characters():
    receipt = "A" * 600 + "TAIL-MARKER"
    text = format_round_evidence(_record(receipt=receipt))
    assert "A" * 600 in text
    assert "TAIL-MARKER" not in text


def test_json_serializable_unicode_text():
    text = format_round_evidence(_record(task_state={"file_changes": [], "verifications": []}))
    assert isinstance(text, str)
    json.dumps(text, ensure_ascii=False)
    assert "\x00" not in text
