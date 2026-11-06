"""Claim validator — structural check that assistant claims trace to tool results.

L2 of the truthfulness work (see docs/archive/undated/claim-validation-badge-design.md).

Design:
- Pure function ``validate_claims``: no IO, easy to unit test.
- Extracts "metric claims" (metric keyword + nearby number) from the
  assistant's final answer text, then cross-references those numbers
  against the actual tool-result strings from the same conversation.
- Numbers that cannot be found in any tool result are flagged as
  ``unverified`` so the UI can surface a 🟡/🔴 verifiability badge.
- Never blocks or rewrites the model output by itself; ``strict`` mode
  only appends a soft warning suffix (decided at the call site).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 指标关键词（EN + CN）——标记附近数字为「断言」。
#:
#: **批次 4d：本表默认空，业务域自己注册**。
#:
#: 原表是量化专属（sharpe / calmar / ic / 回撤 / 夏普 ...）。它的问题：
#: 换一个业务域（法律条款金额、医学剂量、代码行数）就得改 kernel。
#:
#: **判据**：「夏普比率」这个词能用于法律域吗？不能 → 它是业务内容。
#:
#: 业务域通过 ``register_metric_keywords()`` 注册自己的词表；
#: ``/`` 量化域的注册在 ``apps/agent_plugins/quant_claims.py``。
DEFAULT_METRIC_KEYWORDS: list[str] = []

#: 业务域注册的额外关键词（进程级）。
_REGISTERED_KEYWORDS: list[str] = []


def register_metric_keywords(words: list[str]) -> None:
    """注册业务域的指标关键词（累加，幂等按词去重）。"""
    for w in words:
        w = str(w)
        if w and w not in _REGISTERED_KEYWORDS:
            _REGISTERED_KEYWORDS.append(w)


def active_metric_keywords() -> list[str]:
    """当前生效的关键词 = 默认（空） + 已注册。"""
    return list(DEFAULT_METRIC_KEYWORDS) + list(_REGISTERED_KEYWORDS)


def clear_registered_keywords() -> None:
    """清空业务注册（测试用）。"""
    _REGISTERED_KEYWORDS.clear()

# Maximum character window around a metric keyword to look for its value.
_KEYWORD_WINDOW = 30

# Number literal: optional minus, digits with optional fraction, optional %.
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?%?")

# Extract every number (as string) from a text blob.
_ALL_NUMBERS_RE = re.compile(r"-?\d+\.?\d*%?")

# Normalize a numeric literal to a canonical float string. Percent
# values are converted to their decimal form ("15%" → "0.15") so claims
# and tool results compare on the same scale.
def _normalize_number(value: str) -> str:
    s = value.strip()
    is_pct = s.endswith("%")
    if is_pct:
        s = s.rstrip("%")
    try:
        num = float(s) / 100.0 if is_pct else float(s)
        return f"{num:.6f}".rstrip("0").rstrip(".")
    except ValueError:
        return value.strip()


def _claim_detail(
    unverified: list[str], total_claims: int, confidence: float,
) -> str:
    if total_claims == 0:
        return "未检测到指标数字，无需验证"
    if not unverified:
        return f"全部 {total_claims} 个指标数字均可在工具返回值中找到"
    return (
        f"{len(unverified)} 个数字未在工具返回值中找到"
        f"（置信度 {confidence:.0%}）: {', '.join(unverified)}"
    )


@dataclass(frozen=True)
class ClaimValidationResult:
    """Outcome of validating claims in one assistant answer."""

    ok: bool
    total_claims: int
    verified: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    confidence: float = 1.0
    detail: str = ""


def validate_claims(
    assistant_text: str,
    tool_result_texts: list[str],
    metric_keywords: list[str] | None = None,
    tolerance: float = 1e-3,
) -> ClaimValidationResult:
    """Validate metric claims in ``assistant_text`` against tool results.

    Args:
        assistant_text: The final assistant answer (user-facing text).
        tool_result_texts: Content strings of all ``role == "tool"``
            messages from the same conversation (history + current turn).
        metric_keywords: Override the keyword list (defaults to
            ``DEFAULT_METRIC_KEYWORDS``).
        tolerance: Absolute float tolerance when matching a claim value
            to a value found in a tool result.

    Returns:
        A ``ClaimValidationResult``. ``total_claims == 0`` yields
        ``ok=True`` (nothing to verify → UI stays quiet).
    """
    keywords = (
        metric_keywords if metric_keywords is not None
        else active_metric_keywords()
    )
    text = assistant_text or ""

    # 1. Locate metric claims: for each keyword occurrence, look for a
    #    number AFTER the keyword first (typical "sharpe 1.5"), then
    #    fall back to the number just BEFORE it ("1.5 的 sharpe").
    claims: list[str] = []
    lower = text.lower()
    for kw in keywords:
        kw_lower = kw.lower()
        start = 0
        while True:
            idx = lower.find(kw_lower, start)
            if idx == -1:
                break
            kw_end = idx + len(kw_lower)
            after = text[kw_end:kw_end + _KEYWORD_WINDOW]
            m = _NUMBER_RE.search(after)
            if not m:
                before = text[max(0, idx - _KEYWORD_WINDOW):idx]
                m = _NUMBER_RE.search(before)
            if m:
                claims.append(f"{kw}={m.group(0)}")
            start = kw_end

    if not claims:
        return ClaimValidationResult(
            ok=True,
            total_claims=0,
            verified=[],
            unverified=[],
            confidence=1.0,
            detail=_claim_detail([], 0, 1.0),
        )

    # 2. Collect every number present in tool results (normalized).
    tool_numbers: set[str] = set()
    for tr in tool_result_texts:
        for n in _ALL_NUMBERS_RE.findall(tr or ""):
            tool_numbers.add(_normalize_number(n))

    # 3. Classify each claim by numeric proximity to tool-result values.
    verified: list[str] = []
    unverified: list[str] = []
    for claim in claims:
        value_raw = claim.split("=", 1)[1]
        try:
            # Percent claims are compared in decimal (15% → 0.15).
            value = float(value_raw.rstrip("%")) / (100.0 if value_raw.endswith("%") else 1.0)
        except ValueError:
            unverified.append(claim)
            continue
        found = False
        for tn in tool_numbers:
            try:
                if abs(value - float(tn)) <= tolerance:
                    found = True
                    break
            except ValueError:
                continue
        if found:
            verified.append(claim)
        else:
            unverified.append(claim)

    total = len(claims)
    confidence = len(verified) / total if total else 1.0
    ok = not unverified

    return ClaimValidationResult(
        ok=ok,
        total_claims=total,
        verified=verified,
        unverified=unverified,
        confidence=round(confidence, 4),
        detail=_claim_detail(unverified, total, confidence),
    )


__all__ = [
    "ClaimValidationResult",
    "DEFAULT_METRIC_KEYWORDS",
    "validate_claims",
]
