"""StructuredOutputParser — 4-layer degradation JSON parser for tool_call arguments.

Ensures LLM tool_call arguments are always parseable (worst case: None + errors).
Never raises ParseError — degrades gracefully through 4 layers.

Layer 1: Strict JSON (with markdown fence extraction)
Layer 2: Repair (trailing comma, single quotes)
Layer 3: Regex field extraction
Layer 4: Return None + errors
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Structured output parse result."""

    data: dict[str, Any] | None  # None if all layers failed
    errors: list[str] = field(default_factory=list)
    source: str = "failed"  # "strict" | "repaired" | "regex" | "failed"


# Markdown code fence
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)

# Regex patterns for Layer 2 repair
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


class StructuredOutputParser:
    """4-layer degradation JSON parser for tool_call arguments.

    Ensures parsing never fails with an exception — always returns
    either parsed data or None + error list.
    """

    def parse(
        self,
        raw_args: str | Any,
        schema: dict[str, str] | None = None,
    ) -> ParseResult:
        """Parse raw_args with 4-layer degradation.

        Args:
            raw_args: Raw JSON string from LLM tool_call (or already-parsed dict).
            schema: Optional field schema for Layer 3 regex extraction.
                    e.g. {"name": "string", "count": "number", "active": "boolean"}

        Returns:
            ParseResult with data/errors/source
        """
        # Fast path: already a dict
        if isinstance(raw_args, dict):
            return ParseResult(data=dict(raw_args), source="strict")

        # Non-string input → Layer 4
        if not isinstance(raw_args, str) or not raw_args.strip():
            return ParseResult(
                data=None,
                errors=["empty or non-string input"],
                source="failed",
            )

        s = raw_args.strip()

        # Layer 1: Strict JSON (try fence extraction first, then raw)
        result = self._layer1_strict(s)
        if result is not None:
            return ParseResult(data=result, source="strict")

        # Layer 2: Repair
        result = self._layer2_repair(s)
        if result is not None:
            return ParseResult(data=result, source="repaired")

        # Layer 3: Regex extraction (requires schema)
        if schema:
            result = self._layer3_regex(s, schema)
            if result is not None:
                return ParseResult(data=result, source="regex")

        # Layer 4: All failed
        return ParseResult(
            data=None,
            errors=[f"failed to parse tool arguments: {s[:120]}..."],
            source="failed",
        )

    def _layer1_strict(self, s: str) -> dict[str, Any] | None:
        """Layer 1: Strict JSON parsing (fence extraction + text stripping)."""
        # Try direct parse first
        result = self._try_parse_json(s)
        if result is not None:
            return result

        # Try markdown fence extraction
        m = _JSON_FENCE_RE.search(s)
        if m:
            result = self._try_parse_json(m.group(1).strip())
            if result is not None:
                return result

        # Strip surrounding non-JSON text: extract first `{` ... last `}`.
        # LLMs sometimes prefix tool args with prose ("Output: ...") or
        # wrap them with trailing text.
        start = s.find("{")
        end = s.rfind("}")
        if 0 <= start < end:
            result = self._try_parse_json(s[start:end + 1])
            if result is not None:
                return result

        return None

    def _layer2_repair(self, s: str) -> dict[str, Any] | None:
        """Layer 2: Repair common JSON issues."""
        repaired = s

        # Remove trailing commas: ,] → ] and ,} → }
        repaired = _TRAILING_COMMA_RE.sub(r"\1", repaired)

        # Convert single quotes to double quotes (simple replacement)
        if "'" in repaired:
            repaired = self._repair_single_quotes(repaired)

        # Try parsing after repair
        if repaired != s:
            result = self._try_parse_json(repaired)
            if result is not None:
                return result

        return None

    def _repair_single_quotes(self, s: str) -> str:
        """Convert single-quoted JSON to double-quoted."""
        result = []
        i = 0
        in_string = False
        string_char = None

        while i < len(s):
            c = s[i]

            if in_string:
                if c == "\\" and i + 1 < len(s):
                    result.append(c)
                    result.append(s[i + 1])
                    i += 2
                    continue
                if c == string_char:
                    in_string = False
                    result.append('"')
                    i += 1
                    continue
                result.append(c)
                i += 1
                continue

            # Not in string
            if c == '"':
                in_string = True
                string_char = '"'
                result.append(c)
                i += 1
                continue
            if c == "'":
                in_string = True
                string_char = "'"
                result.append('"')
                i += 1
                continue

            result.append(c)
            i += 1

        return "".join(result)

    def _layer3_regex(
        self,
        s: str,
        schema: dict[str, str],
    ) -> dict[str, Any] | None:
        """Layer 3: Regex field extraction based on schema."""
        extracted: dict[str, Any] = {}

        for field_name, field_type in schema.items():
            if field_type == "string":
                # Try JSON-style: "field": "value"
                m = re.search(rf'"{re.escape(field_name)}"\s*:\s*"([^"]*)"', s)
                if m:
                    extracted[field_name] = m.group(1)
                    continue
                # Try YAML-style: field: 'value' or field: "value"
                m = re.search(rf"{re.escape(field_name)}\s*:\s*['\"]([^'\"]*)['\"]", s)
                if m:
                    extracted[field_name] = m.group(1)

            elif field_type == "number":
                # Try JSON-style: "field": 123
                m = re.search(rf'"{re.escape(field_name)}"\s*:\s*(\d+(?:\.\d+)?)', s)
                if m:
                    val = m.group(1)
                    extracted[field_name] = int(val) if "." not in val else float(val)
                    continue
                # Try YAML-style: field: 123
                m = re.search(rf"{re.escape(field_name)}\s*:\s*(\d+(?:\.\d+)?)", s)
                if m:
                    val = m.group(1)
                    extracted[field_name] = int(val) if "." not in val else float(val)

            elif field_type == "boolean":
                # Try JSON-style: "field": true/false
                m = re.search(rf'"{re.escape(field_name)}"\s*:\s*(true|false)', s)
                if m:
                    extracted[field_name] = m.group(1) == "true"
                    continue
                # Try YAML-style: field: true/false
                m = re.search(rf"{re.escape(field_name)}\s*:\s*(true|false)", s)
                if m:
                    extracted[field_name] = m.group(1) == "true"

        return extracted if extracted else None

    def _try_parse_json(self, s: str) -> dict[str, Any] | None:
        """Try to parse a string as JSON dict."""
        try:
            result = json.loads(s)
            if isinstance(result, dict):
                return result
            return {"value": result}
        except (json.JSONDecodeError, TypeError):
            return None


# Module-level singleton
_parser: StructuredOutputParser | None = None


def get_parser() -> StructuredOutputParser:
    """Get the singleton StructuredOutputParser instance."""
    global _parser
    if _parser is None:
        _parser = StructuredOutputParser()
    return _parser
