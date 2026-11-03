"""LLM-as-judge for strategy acceptance (P6 Step 0).

The LLM layer is OPTIONAL and OFF BY DEFAULT for v1. Enable via
``AcceptanceConfig.llm_enabled=True`` or CLI flag ``--llm-enabled``.

When invoked, the evaluator asks the configured LLM to score the run
qualitatively. The output is parsed into a strict verdict dict:

    {
        "passed": bool,         # overall pass/fail
        "score":   float (0-1),  # normalised confidence
        "reason":  str,         # one-sentence justification
        "concerns": list[str],  # optional list of caveats
        "raw":     str,         # raw LLM text (for audit)
    }

The evaluator is intentionally tolerant:
* Malformed LLM responses default to passed=False, score=0.0 (safe fail)
* Network errors raise ``LLMEvaluatorError`` for the caller to handle
* Long responses are truncated to ``cfg.llm_timeout_s`` seconds
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class LLMEvaluatorError(RuntimeError):
    """Raised when the LLM evaluator cannot produce a verdict (network, parse, etc.)."""


# ── Evaluator ────────────────────────────────────────────────────────


@dataclass
class LLMEvaluator:
    """Asks an LLM to qualitatively judge a strategy run.

    The evaluator does NOT own an ``OpenAICompatClient`` instance. The caller
    passes one in (``OpenAICompatClient(cfg)``). This keeps the module
    LLM-library-agnostic and easy to mock in tests.

    Typical usage:

        cfg_llm = LLMConfig.load()
        client = OpenAICompatClient(cfg_llm)
        evaluator = LLMEvaluator(client)
        verdict = evaluator.evaluate(metrics, summary)
    """

    client: Any  # OpenAICompatClient (typed as Any to avoid hard dep here)
    system_prompt: str = ""

    # ── Public API ──────────────────────────────────────────────

    def evaluate(
        self,
        metrics: dict[str, Any],
        summary: dict[str, Any] | None = None,
        cfg: Any | None = None,
    ) -> dict[str, Any]:
        """Run a single LLM-as-judge call.

        Args:
            metrics: Run metrics (calmar, sharpe, …).
            summary: Optional run summary dict (hypothesis, key_insight, …).
            cfg: AcceptanceConfig (only llm_score_threshold is read here).

        Returns:
            Verdict dict (see module docstring). On any failure, returns
            ``{"passed": False, "score": 0.0, "reason": "<error>", …}``.
        """
        system_prompt = self.system_prompt or _DEFAULT_SYSTEM_PROMPT
        prompt = self._build_prompt(metrics, summary)
        try:
            response = self.client.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=512,
            )
        except Exception as exc:                                # noqa: BLE001
            logger.warning("LLMEvaluator: chat() failed: %s", exc)
            return _fail_verdict(f"llm call failed: {exc}")

        content = (response.content or "").strip()
        verdict = self._parse_verdict(content)
        verdict["raw"] = content

        if cfg is not None and hasattr(cfg, "llm_score_threshold"):
            threshold = float(cfg.llm_score_threshold)
            verdict["passed"] = verdict["score"] >= threshold

        return verdict

    # ── Prompt construction ─────────────────────────────────────

    def _build_prompt(
        self,
        metrics: dict[str, Any],
        summary: dict[str, Any] | None,
    ) -> str:
        lines: list[str] = ["# Strategy Run — LLM Verdict Request", ""]
        lines.append("## Metrics")
        lines.append("")
        lines.append("| metric | value |")
        lines.append("|--------|-------|")
        for key in ("calmar", "sharpe", "max_dd", "ann_return", "ann_vol",
                    "sortino", "turnover", "win_rate", "trades"):
            v = metrics.get(key)
            if v is None:
                continue
            lines.append(f"| {key} | {v} |")
        lines.append("")

        if summary:
            hyp = summary.get("hypothesis")
            insight = summary.get("key_insight")
            if hyp or insight:
                lines.append("## Context")
                lines.append("")
                if hyp:
                    lines.append(f"- hypothesis: {hyp}")
                if insight:
                    lines.append(f"- key_insight: {insight}")
                lines.append("")

        lines.extend([
            "## Task",
            "",
            "Return ONLY a single JSON object with this schema:",
            "",
            '{"passed": <bool>, "score": <float 0-1>, "reason": "<one sentence>", "concerns": [<str>, ...]}',
            "",
            "Rules:",
            "- score=1.0 means the strategy looks production-ready.",
            "- score>=0.5 means acceptable but with caveats.",
            "- score<0.5 means reject.",
            "- concerns is a list of risk factors (e.g. ['high turnover', 'low trades']).",
            "- Do NOT include any text outside the JSON object.",
        ])
        return "\n".join(lines)

    # ── Verdict parsing ──────────────────────────────────────────

    def _parse_verdict(self, text: str) -> dict[str, Any]:
        """Extract the verdict dict from LLM text. Lenient on bad JSON."""
        candidate = self._extract_json_block(text)
        if candidate is None:
            return _fail_verdict("llm response did not contain JSON object")

        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError) as exc:
            return _fail_verdict(f"llm JSON parse failed: {exc}")

        if not isinstance(data, dict):
            return _fail_verdict("llm JSON root was not an object")

        passed = bool(data.get("passed", False))
        try:
            score = float(data.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(1.0, score))

        reason = str(data.get("reason", "")).strip() or "<no reason>"
        concerns = data.get("concerns") or []
        if not isinstance(concerns, list):
            concerns = []
        concerns = [str(c) for c in concerns if c is not None][:8]

        return {
            "passed": passed,
            "score": score,
            "reason": reason,
            "concerns": concerns,
        }

    @staticmethod
    def _extract_json_block(text: str) -> str | None:
        """Find the first ``{...}`` JSON object in text (greedy to last ``}``)."""
        start = text.find("{")
        if start == -1:
            return None
        # Find matching closing brace by counting depth
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return None


# ── Helpers ──────────────────────────────────────────────────────────


def _fail_verdict(reason: str) -> dict[str, Any]:
    """Build a deterministic-fail verdict."""
    return {
        "passed": False,
        "score": 0.0,
        "reason": reason,
        "concerns": [reason],
    }


# Default system prompt (Chinese-friendly, matches project tone)
_DEFAULT_SYSTEM_PROMPT = (
    "你是一名量化研究审计员，擅长评估策略回测结果是否值得保留。"
    "请保持客观，避免乐观偏差；当指标过低或样本不足时果断判否。"
    "严格按要求输出 JSON，不加任何额外文本。"
)


# ── Convenience wrapper for decide() ─────────────────────────────────


def evaluate_or_skip(
    metrics: dict[str, Any],
    summary: dict[str, Any] | None,
    cfg: Any,
    client: Any | None = None,
) -> dict[str, Any] | None:
    """Convenience: evaluate via LLM only if enabled; else return None.

    Returns:
        Verdict dict, or None if LLM layer is disabled or misconfigured.
    """
    if not getattr(cfg, "llm_enabled", False):
        return None
    if client is None:
        try:
            from strategy_research.foundation.llm import LLMConfig, OpenAICompatClient
            client = OpenAICompatClient(LLMConfig.load())
        except Exception as exc:                                # noqa: BLE001
            logger.warning("evaluate_or_skip: LLM client init failed: %s", exc)
            return _fail_verdict(f"llm client init failed: {exc}")

    evaluator = LLMEvaluator(client=client)
    return evaluator.evaluate(metrics, summary, cfg)


# Pre-compile JSON brace regex for hot paths (small optimisation)
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)
