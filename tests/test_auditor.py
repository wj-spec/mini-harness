import asyncio

from app.agent import Agent
from app.auditor import AuditRequest, ReadOnlyAuditor, parse_audit_report
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.tools import READ_ONLY_TOOL_NAMES, ToolRegistry
from core.models import Response, ToolCall


def run(coro):
    return asyncio.run(coro)


def test_tool_registry_subset_exposes_only_requested_read_tools(tmp_path):
    registry = ToolRegistry(tmp_path)
    subset = registry.subset(READ_ONLY_TOOL_NAMES + ("missing_tool",))

    assert subset.names() == list(READ_ONLY_TOOL_NAMES)
    ok, output = subset.execute(ToolCall("write_file", {"path": "x.txt", "content": "x"}))
    assert ok is False and "unknown_tool" in output
    ok, output = subset.execute(ToolCall("bash", {"command": "true"}))
    assert ok is False and "unknown_tool" in output


def test_parse_audit_report_accepts_control_header_and_rejects_missing_header():
    parsed = parse_audit_report(
        "Status: complete\nIntegrity: clean\nContract audit: aligned\n\nEvidence: tests passed."
    )
    assert parsed["valid_header"] is True
    assert parsed["status"] == "complete"
    assert parsed["integrity"] == "clean"
    assert parsed["contract_audit"] == "aligned"

    missing = parse_audit_report("done, looks good")
    assert missing["valid_header"] is False
    assert missing["status"] == "blocked"
    assert missing["integrity"] == "suspect"


def test_read_only_auditor_uses_fresh_subset_and_parses_report(tmp_path):
    (tmp_path / "a.txt").write_text("verified content\n")
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("read_file", {"path": "a.txt"}, id="r1")]),
        Response(content=(
            "Status: complete\n"
            "Integrity: clean\n"
            "Contract audit: aligned\n\n"
            "Acceptance-constraint backcheck:\n"
            "Contract conclusion: aligned\n"
            "Blocking constraints: none\n"
            "Evidence: a.txt contains verified content."
        )),
    ])
    auditor = ReadOnlyAuditor(provider, ToolRegistry(tmp_path))
    request = AuditRequest(
        task="验证 a.txt", claim="done",
        deterministic_audit={"status": "complete", "integrity": "clean"},
    )

    report = run(auditor.audit(request))

    assert report["valid_header"] is True
    assert report["status"] == "complete"
    assert report["integrity"] == "clean"
    assert report["tools"] == ["read_file", "grep", "list_files", "git_status", "git_diff"]
    assert provider.seen and "read-only auditor" in provider.seen[0][0].content


def test_read_only_auditor_downgrades_complete_when_deterministic_audit_failed(tmp_path):
    provider = FakeProvider([
        "Status: complete\nIntegrity: clean\nContract audit: aligned\n\n"
        "Acceptance-constraint backcheck:\nContract conclusion: aligned\n"
        "Blocking constraints: none\n\nok"
    ])
    auditor = ReadOnlyAuditor(provider, ToolRegistry(tmp_path))
    request = AuditRequest(
        task="验证", claim="done",
        deterministic_audit={"status": "incomplete", "integrity": "suspect"},
    )

    report = run(auditor.audit(request))

    assert report["status"] == "incomplete"
    assert report["integrity"] == "suspect"
    assert any("downgraded" in item for item in report["findings"])


def test_parse_contract_backcheck_section_and_blocking_constraints():
    parsed = parse_audit_report(
        "Status: complete\n"
        "Integrity: clean\n"
        "Contract audit: aligned\n\n"
        "Acceptance-constraint backcheck:\n"
        "Contract conclusion: aligned\n"
        "Blocking constraints: none\n"
    )
    backcheck = parsed["contract_backcheck"]
    assert backcheck["present"] is True
    assert backcheck["conclusion"] == "aligned"
    assert backcheck["blocking"] is False
    assert backcheck["ok"] is True

    blocked = parse_audit_report(
        "Status: incomplete\n"
        "Integrity: clean\n"
        "Contract audit: needs_revision\n\n"
        "Acceptance-constraint backcheck:\n"
        "Contract conclusion: needs_revision\n"
        "Blocking constraints: tests were not run\n"
    )
    assert blocked["contract_backcheck"]["blocking"] is True
    assert blocked["contract_backcheck"]["ok"] is False


def test_strict_contract_backcheck_rejects_missing_section(tmp_path):
    provider = FakeProvider(["Status: complete\nIntegrity: clean\nContract audit: aligned\n\nok"])
    auditor = ReadOnlyAuditor(provider, ToolRegistry(tmp_path))
    report = run(auditor.audit(AuditRequest(task="task", claim="done")))

    assert report["status"] == "incomplete"
    assert report["contract_backcheck_ok"] is False


def test_contract_backcheck_can_be_relaxed(tmp_path):
    provider = FakeProvider(["Status: complete\nIntegrity: clean\nContract audit: aligned\n\nok"])
    auditor = ReadOnlyAuditor(
        provider, ToolRegistry(tmp_path), require_contract_aligned=False,
    )
    report = run(auditor.audit(AuditRequest(task="task", claim="done")))

    assert report["status"] == "complete"
    assert report["contract_backcheck_ok"] is False


async def _allow(call, preview):
    return ApprovalDecision(True, "workspace")


def _verified_flow():
    return [
        Response(tool_calls=[ToolCall(
            "write_file", {"path": "a.txt", "content": "new"}, id="w1",
        )]),
        Response(tool_calls=[ToolCall(
            "bash", {"command": "true", "verification": True}, id="b1",
        )]),
        "done",
    ]


def test_agent_uses_llm_auditor_and_records_result(tmp_path):
    async def approve(request: AuditRequest):
        assert request.task == "修改 a.txt"
        assert request.claim == "done"
        return {
            "valid_header": True, "status": "complete", "integrity": "clean",
            "contract_audit": "aligned", "findings": ["checked"],
        }

    agent = Agent(
        FakeProvider(_verified_flow()), ToolRegistry(tmp_path), approval=ApprovalGate(_allow),
        completion_guard=True, workspace_audit=False, llm_auditor=approve,
    )
    result = run(agent.run("修改 a.txt"))

    assert result.status == "done"
    assert result.verified is True
    assert result.audit is not None and result.audit["llm"]["status"] == "complete"
    assert "LLM 审计" in result.receipt


def test_agent_llm_rejection_marks_unverified(tmp_path):
    async def reject(request: AuditRequest):
        return {
            "valid_header": True, "status": "incomplete", "integrity": "suspect",
            "contract_audit": "needs_revision", "findings": ["missing test"],
        }

    agent = Agent(
        FakeProvider(_verified_flow()), ToolRegistry(tmp_path), approval=ApprovalGate(_allow),
        completion_guard=True, workspace_audit=False, llm_auditor=reject,
    )
    result = run(agent.run("修改 a.txt"))

    assert result.status == "done"
    assert result.verified is False
    assert result.audit is not None and result.audit["llm"]["status"] == "incomplete"
    assert "未验证" in result.receipt
