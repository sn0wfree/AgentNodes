"""Runtime input-side injection guard (observation-first).

Phase 5-guardrails (docs/phase5-guardrails-design.md): the runtime
counterpart to the 6 injection/safety eval task expectations. v1
observation-only — emits GuardrailFinding / guardrail.finding events,
never alters the message path or blocks the LLM.

Pattern discipline: low-recall high-precision (a miss is better than
a noisy false-positive flood); ``<system>``-style ordinary mentions
score ``info``, not ``suspect``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Pattern table (id → regex, severity). Kept deliberately small;
# extend only with an eval-backed case.
GUARDRAIL_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "instruction_override",
        re.compile(
            r"ignore\s+(all|previous|prior|the)\s+(instructions|rules|prompts)"
            r"|forget\s+(your|all)\s+(instructions|rules)"
            r"|(不再受限|忽略(之前|所有).{0,4}(指令|规则|提示))",
            re.IGNORECASE,
        ),
        "suspect",
    ),
    (
        "role_takeover",
        re.compile(
            r"<\s*system\s*>|you\s+are\s+now\s+(the\s+)?(system|assistant|admin)"
            r"|(你是系统|你现在是)",
            re.IGNORECASE,
        ),
        "info",  # ordinary mentions of <system> in research text score info
    ),
    (
        "secret_exfil",
        re.compile(
            r"(api[_\-]?key|password|secret|\.env)\b.{0,40}(leak|dump|print|reveal)"
            r"|(print|dump|show)\s+(all\s+)?(secrets?|api[_\-]?keys?)"
            r"|(泄露|打印|展示).{0,6}(密钥|密码|api[_\-]?key)",
            re.IGNORECASE,
        ),
        "suspect",
    ),
    (
        "shell_escape",
        re.compile(
            r"\brm\s+-rf\s+/\b|os\.system\s*\(|\beval\s*\(|\bexec\s*\("
            r"|(删除|清空).{0,4}(/|整个|全部)",
            re.IGNORECASE,
        ),
        "suspect",
    ),
]


@dataclass
class GuardrailFinding:
    """One detected injection-like pattern."""

    kind: str        # user_input | tool_result
    pattern: str     # pattern id from GUARDRAIL_PATTERNS
    severity: str    # info | warn | suspect
    snippet: str     # truncated matched text
    source_id: str   # session / tool-call id for correlation

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "pattern": self.pattern,
            "severity": self.severity,
            "snippet": self.snippet,
            "source_id": self.source_id,
        }


def _scan(text: str, kind: str, source_id: str) -> list[GuardrailFinding]:
    findings: list[GuardrailFinding] = []
    if not text or not isinstance(text, str):
        return findings
    for pattern_id, rx, severity in GUARDRAIL_PATTERNS:
        m = rx.search(text)
        if m:
            findings.append(GuardrailFinding(
                kind=kind,
                pattern=pattern_id,
                severity=severity,
                snippet=m.group(0)[:120],
                source_id=source_id,
            ))
    return findings


def scan_user_input(text: str, source_id: str = "") -> list[GuardrailFinding]:
    """Pattern scan for LLM prompt-injection signatures in user text."""
    return _scan(text, "user_input", source_id)


def scan_tool_result(
    tool_name: str,
    text: str,
    source_id: str = "",
) -> list[GuardrailFinding]:
    """Pattern scan for injection / exfil signatures in tool outputs."""
    findings = _scan(text, "tool_result", source_id or tool_name)
    # tool result always carries the tool name prefix for correlation
    for f in findings:
        f.source_id = f"{tool_name}:{f.source_id}"
    return findings
