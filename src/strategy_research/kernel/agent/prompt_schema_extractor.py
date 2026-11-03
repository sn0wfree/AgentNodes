"""Extract JSON output schemas from agent prompt markdown files.

Reads ``.prompts/<role>.md``, locates the output JSON block (under
``## 输出`` / ``## 输出格式`` / ``## 输出 Schema``), and returns a
structured ``AgentSchema`` describing each field's label, type, role as
core/non-core, and optional enum/format hints.

The parser handles two JSON presentation styles found in the prompt
files:

  1. **Fenced** — ``\\`\\`\\`json ... \\`\\`\\```
  2. **Raw** — bare ``{ ... }`` blocks (no language tag)

Field annotations are inline ``# @hint`` comments placed on the JSON
line *above* the field definition.  Recognized hints:

  ``@label: <text>``    — human-readable field name (Chinese preferred)
  ``@core: true|false`` — whether to show by default (vs. collapsible)
  ``@type: <type>``     — explicit type override
  ``@enum: {JSON}``     — enum value mapping
  ``@format: <fmt>``    — display format hint
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── Section detection ──────────────────────────────────────────────────

# Pattern: heading line starting with ## that mentions output/schema
_OUTPUT_SECTION_RE = re.compile(
    r"^##\s+(输出|输出格式|输出 Schema|输出格式（严格 JSON）)",
    re.MULTILINE,
)

# Fenced JSON block: ```json ... ``` or ``` ... ```
_FENCED_JSON_RE = re.compile(
    r"```(?:json)?\s*\n(\{[\s\S]*?\})\n```",
    re.MULTILINE,
)

# @hint pattern: # @label: X @type: Y @core: true
# Value is non-greedy and stops at the next " @key:" or end-of-line so a
# single line can carry multiple hints.
_HINT_RE = re.compile(r"@(\w+):\s*(.*?)(?=\s+@\w+:|$)")

# ── Data classes ───────────────────────────────────────────────────────

@dataclass
class FieldHints:
    """Metadata hints attached to a JSON field via ``# @hint`` comments."""
    label: str | None = None
    type: str | None = None            # bool|number|string|enum|array|object|weights_table
    enum_values: Dict[str, str] | None = None   # {raw_value: display_label}
    format: str | None = None          # percentage|currency|comma_separated
    core: bool | None = None           # True = show by default
    description: str | None = None     # tooltip text


@dataclass
class AgentSchema:
    """Structured representation of an agent's JSON output schema."""
    role: str
    fields: List[str]                   # ordered list of field names
    field_hints: Dict[str, FieldHints]  # field_name → hints
    action_field: str | None = None     # 'action' if action-type output
    action_enum: List[str] | None = None


# ── Core field heuristic ───────────────────────────────────────────────

# Fields that are decisional / conclusion-like and should be shown by default.
_DEFAULT_CORE_FIELDS = {
    "action", "verdict", "risk_passed", "risk_rating", "overfit_passed",
    "hypothesis", "reason", "recommendation", "status", "level",
    "method", "weighted_score", "analysis",
}

# Fields that are data/detail-like and should be collapsible.
_DEFAULT_NON_CORE_FIELDS = {
    "weights", "risk_contributions", "var_95", "cvar_95", "max_drawdown",
    "stress_results", "tail_risk", "thresholds", "config_audit",
    "evidence_contribution", "human_guidance_observed", "decision_blockers",
    "open_required_items", "thresholds_breached", "predicted_affected",
    "avoid_actions", "bias_check", "context_notes",
    "methods_passed", "metrics", "suggestions",
}


def _is_core_field(name: str, hints: FieldHints | None) -> bool:
    """Determine if a field should be shown by default."""
    if hints and hints.core is not None:
        return hints.core
    if name in _DEFAULT_CORE_FIELDS:
        return True
    if name in _DEFAULT_NON_CORE_FIELDS:
        return False
    # Unknown field: use type heuristic
    if hints and hints.type:
        if hints.type in ("bool", "enum"):
            return True  # decisional
    return False  # default: collapsible


# ── JSON block extraction ──────────────────────────────────────────────

def _find_output_section(text: str) -> tuple[int, int]:
    """Find the line range of the output/schema section.

    Returns (start_line, end_line) where start_line is the heading line
    and end_line is the next ``## `` heading or EOF.
    """
    m = _OUTPUT_SECTION_RE.search(text)
    if not m:
        return -1, -1
    start = m.start()
    # Find next ## heading after the match
    next_heading = re.search(r"^## ", text[m.end():], re.MULTILINE)
    if next_heading:
        end = m.end() + next_heading.start()
    else:
        end = len(text)
    return start, end


def _strip_hint_comments(text: str) -> str:
    """Strip ``# @…`` comment lines from the JSON candidate.

    JSON itself does not allow comments, but our hint convention places
    ``# @label: x`` etc. on their own line ABOVE the field. Removing those
    lines before ``json.loads`` makes the candidate valid JSON.

    Only strips lines whose entire (stripped) content starts with
    ``# @`` — protects against accidental ``#`` characters in string values
    that happen to be on their own line.
    """
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("# @"):
            continue
        lines.append(line)
    return "\n".join(lines)


_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _strip_trailing_commas(text: str) -> str:
    """Remove trailing commas before ``}`` or ``]`` (JS-style → JSON).

    Prompt examples sometimes use trailing commas (legal in JS, not in
    JSON). Stripping them is safe as long as they appear outside string
    values, which is the convention for object/array literals.
    """
    # Walk through the string, skipping over string literals
    out = []
    i = 0
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        # Detect ", ]" or ", }" (possibly with whitespace / newline between
        # the comma and the closer).
        if ch == "," and i + 1 < len(text):
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j < len(text) and text[j] in "}]":
                # Skip the comma and whitespace, keep the closer
                i = j
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _extract_json_blocks(section_text: str) -> List[str]:
    """Extract JSON blocks from a section — both fenced and raw.

    Priority: fenced first (more explicit), then raw blocks. For raw
    blocks the candidate is scanned: ``# @hint`` comment lines are
    stripped, trailing commas are removed (prompt examples sometimes use
    JS-style trailing commas), and the result is parsed with
    ``json.loads``. A balanced JSON object is only accepted if it parses
    cleanly to a dict. This rejects prose sentences like
    "{ 开头,以 } 结尾。" that happen to contain a balanced brace pair.
    """
    def _normalize(candidate: str) -> str:
        return _strip_trailing_commas(_strip_hint_comments(candidate))

    blocks = []
    # 1. Fenced blocks (```json ... ``` or ``` ... ```) — strip comments
    for m in _FENCED_JSON_RE.finditer(section_text):
        candidate = _normalize(m.group(1))
        try:
            json.loads(candidate)
            blocks.append(candidate)
        except json.JSONDecodeError:
            pass
    # 2. Raw blocks (bare { ... } not inside fences)
    if not blocks:
        idx = 0
        while idx < len(section_text):
            start = section_text.find("{", idx)
            if start == -1:
                break
            # Skip fenced regions
            fence_before = section_text.rfind("```", 0, start)
            if fence_before > 0:
                fence_close = section_text.find("```", fence_before + 3)
                if fence_close > start:
                    idx = start + 1
                    continue
            # Find balanced end
            end = _find_balanced_end(section_text, start)
            if end > start:
                candidate = _normalize(section_text[start:end + 1])
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        blocks.append(candidate)
                except json.JSONDecodeError:
                    pass
                idx = end + 1
            else:
                idx = start + 1
    return blocks


def _find_balanced_end(text: str, start_idx: int) -> int:
    """Find matching closing brace via depth counting, respecting strings."""
    depth = 0
    i = start_idx
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


# ── Hint parsing ───────────────────────────────────────────────────────

def _parse_hints_from_line(line: str) -> Dict[str, str]:
    """Parse ``# @key: value`` pairs from a single line.

    Returns a dict like ``{"label": "是否通过", "type": "bool"}``.
    """
    hints = {}
    for m in _HINT_RE.finditer(line):
        key, value = m.group(1), m.group(2).strip()
        hints[key] = value
    return hints


def _build_field_hints(raw_hints: Dict[str, str]) -> FieldHints:
    """Convert raw hint strings into a typed FieldHints."""
    h = FieldHints()

    if "label" in raw_hints:
        h.label = raw_hints["label"]

    if "type" in raw_hints:
        h.type = raw_hints["type"]

    if "format" in raw_hints:
        h.format = raw_hints["format"]

    if "description" in raw_hints:
        h.description = raw_hints["description"]

    if "core" in raw_hints:
        h.core = raw_hints["core"].lower() in ("true", "1", "yes")

    if "enum" in raw_hints:
        try:
            h.enum_values = json.loads(raw_hints["enum"])
        except (json.JSONDecodeError, TypeError):
            h.enum_values = None

    return h


def _infer_type(value: Any) -> str:
    """Infer field type from a sample value."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) or isinstance(value, float):
        return "number"
    if isinstance(value, list):
        if not value:
            return "array"
        if all(isinstance(x, str) for x in value):
            return "array"
        if all(isinstance(x, dict) for x in value):
            return "array"
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, str):
        # Check for common enum patterns
        if "|" in value and len(value) < 100:
            return "enum"
        return "string"
    return "string"


# ── Main API ───────────────────────────────────────────────────────────

def _read_prompt_text(role: str, prompt_dir: Path | None) -> str | None:
    """Load the role's prompt markdown; None when missing or unreadable."""
    if prompt_dir is None:
        prompt_dir = (
            Path(__file__).parent.parent.parent / "templates" / ".prompts"
        )
    prompt_path = prompt_dir / f"{role}.md"
    if not prompt_path.exists():
        logger.debug("No prompt file for role=%s at %s", role, prompt_path)
        return None
    try:
        return prompt_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read prompt for role=%s: %s", role, exc)
        return None


def _find_output_json_blocks(text: str, role: str) -> tuple[list[str], str]:
    """First output section that yields JSON blocks → (blocks, section_text).

    A prompt may declare SEVERAL output sections (e.g. "## 输出格式（严格
    JSON）" with rules only, then a later "## 输出" carrying the actual
    JSON example) — iterate all matches and use the first section that
    yields a JSON block.
    """
    blocks: List[str] = []
    section = ""
    for m in _OUTPUT_SECTION_RE.finditer(text):
        next_heading = re.search(r"^## ", text[m.end():], re.MULTILINE)
        end = m.end() + next_heading.start() if next_heading else len(text)
        candidate = text[m.start():end]
        found = _extract_json_blocks(candidate)
        if found:
            blocks = found
            section = candidate
            break
    if not blocks:
        logger.debug("No JSON blocks found in any output section for role=%s", role)
    return blocks, section


def _build_field_map(
    main_obj: dict, section: str,
) -> tuple[List[str], Dict[str, FieldHints]]:
    """Field order + per-field hints, with type/enum/core inference."""
    hints_by_key = _collect_hints(section, main_obj)
    fields: List[str] = []
    field_hints: Dict[str, FieldHints] = {}
    for key in main_obj.keys():
        fields.append(key)
        raw = hints_by_key.get(key, {})
        hints = _build_field_hints(raw)

        # Infer type from sample value if not explicitly set
        if not hints.type:
            hints.type = _infer_type(main_obj[key])

        # Auto-derive enum_values from "A | B | C" sample strings when no
        # explicit @enum annotation exists (values map to themselves; the
        # frontend or a later @enum hint supplies display labels).
        if not hints.enum_values and hints.type == "enum":
            val = main_obj[key]
            if isinstance(val, str) and "|" in val:
                parts = [v.strip() for v in val.split("|") if v.strip()]
                if parts:
                    hints.enum_values = {p: p for p in parts}

        hints.core = _is_core_field(key, hints)
        field_hints[key] = hints
    return fields, field_hints


def _detect_action(main_obj: dict) -> tuple[str | None, List[str] | None]:
    """Action field name + parsed enum labels, if the sample has one."""
    if "action" not in main_obj:
        return None, None
    val = main_obj["action"]
    if isinstance(val, str) and "|" in val:
        return "action", [v.strip() for v in val.split("|")]
    return "action", None


def extract_agent_schema(role: str, prompt_dir: Path | None = None) -> AgentSchema | None:
    """Extract JSON output schema from an agent's prompt file.

    Args:
        role: Agent role identifier (e.g. ``"researcher"``)
        prompt_dir: Path to ``templates/.prompts/`` directory.
            If None, uses the default relative to this file.

    Returns:
        AgentSchema with field metadata, or None if the prompt has no
        parseable JSON output section.
    """
    text = _read_prompt_text(role, prompt_dir)
    if text is None:
        return None

    blocks, section = _find_output_json_blocks(text, role)
    if not blocks:
        return None

    try:
        main_obj = json.loads(blocks[0])
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON block for role=%s", role)
        return None
    if not isinstance(main_obj, dict):
        return None

    fields, field_hints = _build_field_map(main_obj, section)
    action_field, action_enum = _detect_action(main_obj)
    return AgentSchema(
        role=role,
        fields=fields,
        field_hints=field_hints,
        action_field=action_field,
        action_enum=action_enum,
    )


def _collect_hints(section: str, main_obj: dict) -> Dict[str, Dict[str, str]]:
    """Walk the output section and collect # @hint comments keyed by field name.

    Strategy: scan lines before each top-level JSON field key and collect
    any ``# @key: value`` annotations found.
    """
    hints: Dict[str, Dict[str, str]] = {}
    pending_hints: Dict[str, str] = {}

    for line in section.splitlines():
        stripped = line.strip()
        # Collect hint lines
        parsed = _parse_hints_from_line(stripped)
        if parsed:
            pending_hints.update(parsed)
            continue

        # Detect JSON field lines: ``"key": value,`` or ``"key": {``
        field_match = re.match(r'\s*"(\w+)"\s*:', stripped)
        if field_match and pending_hints:
            key = field_match.group(1)
            if key in main_obj:
                hints[key] = dict(pending_hints)
            pending_hints = {}

    return hints


# ── Module-level helpers for external callers ──────────────────────────

def load_all_schemas(prompt_dir: Path | None = None) -> Dict[str, AgentSchema]:
    """Load schemas for all agents with prompt files in the directory.

    Returns ``{role: schema}`` for all successfully parsed roles.
    """
    if prompt_dir is None:
        prompt_dir = (
            Path(__file__).parent.parent.parent / "templates" / ".prompts"
        )

    results: Dict[str, AgentSchema] = {}
    if not prompt_dir.exists():
        return results

    for md_path in sorted(prompt_dir.glob("*.md")):
        role = md_path.stem
        # Skip private/common files
        if role.startswith("_"):
            continue
        schema = extract_agent_schema(role, prompt_dir)
        if schema:
            results[role] = schema

    return results
