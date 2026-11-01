from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.tools import READ_ONLY_TOOL_NAMES, ToolRegistry
from core.state import TaskState

AUDITOR_MAX_STEPS = 6
AUDITOR_MAX_SECONDS = 120.0
AUDITOR_MAX_TOKENS = 60_000
AUDITOR_MAX_FINDING_CHARS = 4_000

_STATUS_LINE = re.compile(
    r"^(?:status|状态)\s*[:：]\s*(complete|incomplete|blocked|完成|未完成|阻塞)\s*$",
    re.IGNORECASE,
)
_INTEGRITY_LINE = re.compile(
    r"^(?:integrity|完整性)\s*[:：]\s*(clean|suspect|violation|干净|可疑|违规)\s*$",
    re.IGNORECASE,
)
_CONTRACT_LINE = re.compile(
    r"^(?:contract audit|契约审计)\s*[:：]\s*"
    r"(aligned|unknown|needs[_-]revision|invalid|对齐|未知|需修订|无效)\s*$",
    re.IGNORECASE,
)
_CONTRACT_HEADING = re.compile(
    r"^(?:acceptance[- ]constraint backcheck|验收约束反查)\s*[:：]?\s*$",
    re.IGNORECASE,
)
_CONTRACT_CONCLUSION = re.compile(
    r"^(?:contract conclusion|契约结论)\s*[:：]\s*"
    r"(aligned|unknown|needs[_-]revision|invalid|对齐|未知|需修订|无效)\s*$",
    re.IGNORECASE,
)
_BLOCKING_CONSTRAINTS = re.compile(
    r"^(?:blocking constraints|阻断约束)\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)
_NO_BLOCKING = re.compile(
    r"^(?:none|no blocking(?: constraints)?|n/?a|无|没有|无阻断(?:约束)?)[\s.。;；]*$",
    re.IGNORECASE,
)
_NORMALIZE = {
    "完成": "complete",
    "未完成": "incomplete",
    "阻塞": "blocked",
    "干净": "clean",
    "可疑": "suspect",
    "违规": "violation",
    "对齐": "aligned",
    "未知": "unknown",
    "需修订": "needs_revision",
    "无效": "invalid",
}

_SYSTEM = """\
You are a read-only auditor for a coding agent. You can inspect the workspace
through read-only tools only (read_file, grep, list_files, git_status, git_diff).
You cannot write files or run shell commands. Executor text is a claim, not evidence.

Before auditing execution, independently reconstruct the acceptance constraints
from the original task. Do not assume the executor's claim or the harness state
defines "done".

Output plain text, never JSON. The first three non-empty lines must be exactly:
Status: complete|incomplete|blocked
Integrity: clean|suspect|violation
Contract audit: aligned|unknown|needs_revision|invalid

Then include this section:
Acceptance-constraint backcheck:
Contract conclusion: aligned|unknown|needs_revision|invalid
Original constraint inventory: final consumer/state carrier, authoritative inputs,
candidate selection, required values/files/paths, save/submit/persistence boundary,
format/style/exact text, do-not-change/omit/add, preserve non-target state, forbidden shortcuts.
Contract coverage check: omissions, weakening, distortion, contradictions.
Per-constraint backcheck: verified|unknown|violated|not_applicable with evidence.
Blocking constraints: none, or list every blocking unknown/violated constraint.
Recommended contract revision: none, or the smallest correction.

The Contract audit line must equal Contract conclusion. Only aligned permits complete.
If any blocking constraint exists, Status must be incomplete or blocked.
Then list evidence found, gaps/untrusted artifacts, and the next step. Be concise.
"""


@dataclass
class AuditRequest:
    """只读审计的输入；不包含执行者 raw trajectory。"""

    task: str
    claim: str
    state_summary: str = ""
    file_changes: list[dict] = field(default_factory=list)
    verifications: list[dict] = field(default_factory=list)
    deterministic_audit: dict | None = None


def _clean_header(line: str) -> str:
    value = line.strip().replace("**", "").replace("`", "").strip()
    return value.rstrip("。.;；")


def _first_nonempty_lines(text: str, count: int) -> list[str]:
    lines = [_clean_header(line) for line in str(text or "").splitlines() if line.strip()]
    return lines[:count]


def _match(pattern: re.Pattern[str], line: str) -> str | None:
    match = pattern.match(line)
    if match is None:
        return None
    value = match.group(1).lower().replace("-", "_").replace(" ", "_")
    return _NORMALIZE.get(value, value)


def _parse_contract_backcheck(text: str) -> dict:
    lines = str(text or "").splitlines()
    present = False
    conclusion = None
    blocking = False
    blocking_text = ""
    for index, raw in enumerate(lines):
        line = _clean_header(raw)
        if _CONTRACT_HEADING.match(line):
            present = True
            continue
        match = _CONTRACT_CONCLUSION.match(line)
        if match is not None:
            value = match.group(1).lower().replace("-", "_").replace(" ", "_")
            conclusion = _NORMALIZE.get(value, value)
            continue
        match = _BLOCKING_CONSTRAINTS.match(line)
        if match is None:
            continue
        rest = match.group(1).strip()
        if rest:
            blocking_text = rest
        else:
            collected = []
            for following in lines[index + 1:]:
                candidate = _clean_header(following)
                if not candidate:
                    continue
                if (
                    _CONTRACT_HEADING.match(candidate)
                    or _CONTRACT_CONCLUSION.match(candidate)
                    or _BLOCKING_CONSTRAINTS.match(candidate)
                ):
                    break
                collected.append(candidate)
                if len(collected) >= 2:
                    break
            blocking_text = " ".join(collected)
        blocking = bool(blocking_text) and _NO_BLOCKING.match(blocking_text) is None
    return {
        "present": present,
        "conclusion": conclusion,
        "blocking": blocking,
        "blocking_text": blocking_text[:600],
    }


def parse_audit_report(text: str) -> dict:
    """解析审计者的三行控制头；缺失或非法时 fail closed。"""
    lines = _first_nonempty_lines(text, 3)
    status = _match(_STATUS_LINE, lines[0]) if len(lines) >= 1 else None
    integrity = _match(_INTEGRITY_LINE, lines[1]) if len(lines) >= 2 else None
    contract = _match(_CONTRACT_LINE, lines[2]) if len(lines) >= 3 else None
    valid = status is not None and integrity is not None and contract is not None
    if not valid:
        return {
            "status": "blocked",
            "integrity": "suspect",
            "contract_audit": "unknown",
            "valid_header": False,
            "findings": [
                "auditor report lacks a valid Status/Integrity/Contract audit header",
                _truncate(str(text or ""), 600),
            ],
            "report": str(text or ""),
        }
    body = "\n".join(str(text or "").splitlines())
    backcheck = _parse_contract_backcheck(text)
    backcheck["matches_control"] = backcheck["conclusion"] == contract
    backcheck["ok"] = (
        bool(backcheck["present"])
        and backcheck["conclusion"] == "aligned"
        and not backcheck["blocking"]
        and backcheck["matches_control"]
    )
    return {
        "status": status,
        "integrity": integrity,
        "contract_audit": contract,
        "contract_backcheck": backcheck,
        "valid_header": True,
        "findings": [_truncate(body, AUDITOR_MAX_FINDING_CHARS)],
        "report": str(text or ""),
    }


def _truncate(text: str, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


class ReadOnlyAuditor:
    """用一个全新上下文的只读子 Agent 审计刚完成的 run。"""

    def __init__(
        self,
        provider,
        registry: ToolRegistry,
        *,
        max_steps: int = AUDITOR_MAX_STEPS,
        max_seconds: float = AUDITOR_MAX_SECONDS,
        max_tokens: int = AUDITOR_MAX_TOKENS,
        timeout: float = 120.0,
        retries: int = 1,
        require_contract_aligned: bool = True,
    ):
        self.provider = provider
        self.registry = registry
        self.max_steps = max(1, int(max_steps))
        self.max_seconds = max(10.0, float(max_seconds))
        self.max_tokens = max(0, int(max_tokens))
        self.timeout = max(10.0, float(timeout))
        self.retries = max(0, int(retries))
        self.require_contract_aligned = bool(require_contract_aligned)

    def _prompt(self, request: AuditRequest) -> str:
        file_changes = request.file_changes[-20:]
        verifications = request.verifications[-10:]
        return "\n\n".join([
            "Original task:\n" + _truncate(request.task, 4_000),
            "Executor claim:\n" + _truncate(request.claim, 4_000),
            "Current task state (harness-maintained):\n" + _truncate(request.state_summary, 4_000),
            "File changes recorded this run:\n" + _truncate(_jsonish(file_changes), 6_000),
            "Verification events:\n" + _truncate(_jsonish(verifications), 4_000),
            "Deterministic harness audit:\n" + _truncate(_jsonish(request.deterministic_audit), 4_000),
            "Inspect the real workspace now. Audit only what the evidence supports.",
        ])

    async def audit(self, request: AuditRequest) -> dict:
        from app.agent import Agent  # 延迟导入，避免 Agent -> auditor 循环

        tools = self.registry.subset(READ_ONLY_TOOL_NAMES)
        state = TaskState(goal="Read-only environment audit")
        agent = Agent(
            self.provider,
            tools,
            max_steps=self.max_steps,
            max_run_seconds=self.max_seconds,
            system=_SYSTEM,
            state=state,
            history=[],
            approval=None,
            llm_timeout=self.timeout,
            llm_retries=self.retries,
            max_no_progress=0,
            max_run_tokens=self.max_tokens,
            require_tool_use=False,
        )
        result = await agent.run(self._prompt(request))
        parsed = parse_audit_report(result.text)
        parsed["llm_status"] = result.status
        parsed["llm_stop_reason"] = result.stop_reason
        parsed["usage"] = dict(result.usage or {})
        parsed["tools"] = list(tools.names())
        parsed["error"] = result.error
        backcheck = parsed.get("contract_backcheck") or {}
        parsed["contract_backcheck_ok"] = bool(backcheck.get("ok"))
        if self.require_contract_aligned and not backcheck.get("ok"):
            if parsed.get("status") == "complete":
                parsed["status"] = "incomplete"
            parsed["findings"].append(
                "acceptance-constraint backcheck missing, not aligned, "
                "mismatched with the contract line, or has blocking constraints"
            )
        if parsed.get("valid_header") and parsed["status"] == "complete":
            deterministic = request.deterministic_audit or {}
            if deterministic and (
                deterministic.get("status") != "complete"
                or deterministic.get("integrity") != "clean"
            ):
                parsed["status"] = "incomplete"
                parsed["integrity"] = "suspect"
                parsed["findings"].append(
                    "deterministic audit did not pass; LLM complete downgraded"
                )
        return parsed


def _jsonish(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)
