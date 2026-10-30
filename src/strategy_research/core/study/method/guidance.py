"""Study v2 guidance — human decision points (design §13).

guidance.md is a single file per task: YAML frontmatter (gates hard rules)
+ markdown body (rules/preferences injected to the LLM every round).

Two-layer load (§13.1): per-task ``study/<id>/guidance.md`` wins, else the
global template ``study/guidance.md``. Zero-LLM, pure functions.

Gates hard check (§13.3): before the verdict, ``enforce: true`` gates are
compared against the round's metrics; violations force ``verdict=discard``
and are recorded in the manifest under ``gates[]``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SUPPORTED_OPS = {">=", "<=", ">", "<", "=="}


@dataclass
class Gate:
    """One hard rule from the guidance frontmatter."""

    id: str
    metric: str
    op: str = ">="
    value: float = 0.0
    enforce: bool = True
    action: str = "reject"
    applies_to: list[str] | None = None  # None = all agents

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "metric": self.metric,
            "op": self.op,
            "value": self.value,
            "enforce": self.enforce,
            "action": self.action,
            "applies_to": self.applies_to,
        }


@dataclass
class Guidance:
    """Parsed guidance for one study (or the global template)."""

    source: Path | None = None
    gates: list[Gate] = field(default_factory=list)
    body: str = ""
    task_scope: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.body.strip() or self.gates)


def _coerce_gate(raw: Any) -> Gate | None:
    if not isinstance(raw, dict):
        return None
    gate_id = str(raw.get("id") or "").strip()
    metric = str(raw.get("metric") or "").strip()
    if not gate_id or not metric:
        return None
    op = str(raw.get("op") or ">=")
    if op not in _SUPPORTED_OPS:
        logger.warning("guidance gate %s: unsupported op '%s', skipped", gate_id, op)
        return None
    try:
        value = float(raw.get("value"))
    except (TypeError, ValueError):
        logger.warning("guidance gate %s: non-numeric value %r, skipped", gate_id, raw.get("value"))
        return None
    applies = raw.get("applies_to")
    return Gate(
        id=gate_id,
        metric=metric,
        op=op,
        value=value,
        enforce=bool(raw.get("enforce", True)),
        action=str(raw.get("action") or "reject"),
        applies_to=[str(a) for a in applies] if isinstance(applies, list) else None,
    )


def parse_guidance(text: str) -> tuple[list[Gate], str]:
    """Split guidance.md text into (gates, body).

    Frontmatter is a ``---`` fenced YAML block at the very top. Missing or
    malformed frontmatter degrades to zero gates with the full text as body.
    """
    gates: list[Gate] = []
    if text.startswith("---"):
        yaml_block, body = _split_frontmatter(text)
        if yaml_block:
            try:
                import yaml

                data = yaml.safe_load(yaml_block) or {}
                if isinstance(data, dict):
                    for raw in data.get("gates") or []:
                        gate = _coerce_gate(raw)
                        if gate is not None:
                            gates.append(gate)
                else:
                    logger.warning("guidance frontmatter is not a mapping; gates ignored")
            except Exception as exc:  # noqa: BLE001
                logger.warning("guidance frontmatter parse failed: %s", exc)
            return gates, body
    return gates, text


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split ``---`` fenced YAML frontmatter from the body (raw text)."""
    if text.startswith("---"):
        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
        if m:
            return m.group(1), m.group(2)
    return "", text


def compose_guidance_text(
    workspace: Path,
    *,
    guidance_file: str | None = None,
    gates_file: str | None = None,
) -> str | None:
    """Compose the single guidance.md text from CLI input sources (§17.1).

    ``gates_file`` provides the YAML frontmatter and ``guidance_file`` the
    markdown body; each falls back to the global template's corresponding
    part when omitted. Both files must resolve inside the workspace
    (security: no arbitrary file reads). Returns None when no content.
    """
    ws = Path(workspace).resolve()
    template = ws / "study" / "guidance.md"
    tpl_text = template.read_text(encoding="utf-8") if template.exists() else ""
    tpl_yaml, tpl_body = _split_frontmatter(tpl_text)

    yaml_part = _read_workspace_file(ws, gates_file) if gates_file else tpl_yaml
    body_part = _read_workspace_file(ws, guidance_file) if guidance_file else tpl_body

    yaml_part = yaml_part.strip()
    if not yaml_part and not body_part.strip():
        return None
    out = ""
    if yaml_part:
        out += f"---\n{yaml_part}\n---\n"
    out += body_part
    return out


def _read_workspace_file(workspace: Path, rel: str) -> str:
    """Read a workspace-relative file, rejecting paths outside the workspace."""
    target = (workspace / rel).resolve()
    if not (target == workspace or workspace in target.parents):
        raise ValueError(f"path outside workspace: {rel}")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"file not found: {rel}")
    return target.read_text(encoding="utf-8")


def load_guidance(workspace: Path, study_id: str | None = None) -> Guidance:
    """Two-layer load (design §13.1): task file wins, else global template.

    Returns an empty :class:`Guidance` when neither file exists.
    """
    ws = Path(workspace)
    task_file = (ws / "study" / study_id / "guidance.md") if study_id else None
    global_file = ws / "study" / "guidance.md"
    source: Path | None = None
    task_scope = False
    if task_file is not None and task_file.exists():
        source, task_scope = task_file, True
    elif global_file.exists():
        source, task_scope = global_file, False
    if source is None:
        return Guidance()
    gates, body = parse_guidance(source.read_text(encoding="utf-8"))
    return Guidance(source=source, gates=gates, body=body, task_scope=task_scope)


def render_guidance_section(guidance: Guidance, agent_name: str | None = None) -> str:
    """Render the ``## 人类判断点`` section injected into the agent prompt.

    ``applies_to`` gate filtering: a gate listed for specific agents is only
    shown when ``agent_name`` matches; gates without a list apply to all.
    """
    if not guidance.has_content:
        return ""
    parts = ["## 人类判断点", ""]
    gates = [
        g for g in guidance.gates
        if agent_name is None or g.applies_to is None or agent_name in g.applies_to
    ]
    if gates:
        parts.append("### 硬性规则（违反将被强制否决）")
        for g in gates:
            flag = "enforce" if g.enforce else "warn"
            parts.append(f"- [{flag}] {g.id}: {g.metric} {g.op} {g.value}（action: {g.action}）")
        parts.append("")
    body = guidance.body.strip()
    if body:
        parts.append(body)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def check_violations(gates: list[Gate], metrics: dict[str, Any]) -> tuple[list[dict], list[str]]:
    """Evaluate enforce:true gates against the round metrics (§13.3).

    Returns:
        (violations, skipped): violations are dicts ready for the manifest
        ``gates[]`` field (``enforced: true, result: "violated"``); skipped
        lists the gate ids whose metric is missing from this round (skip +
        warn, never a false kill).
    """
    violations: list[dict[str, Any]] = []
    skipped: list[str] = []
    for g in gates:
        if not g.enforce:
            continue
        actual = metrics.get(g.metric)
        if actual is None:
            skipped.append(g.id)
            continue
        try:
            a, v = float(actual), float(g.value)
        except (TypeError, ValueError):
            skipped.append(g.id)
            continue
        if not _op_holds(a, g.op, v):
            violations.append({
                "id": g.id,
                "metric": g.metric,
                "op": g.op,
                "value": g.value,
                "actual": actual,
                "enforced": True,
                "result": "violated",
            })
    return violations, skipped


def _op_holds(a: float, op: str, v: float) -> bool:
    """True when the gate requirement (``a op v``) is satisfied."""
    if op == ">=":
        return a >= v
    if op == "<=":
        return a <= v
    if op == ">":
        return a > v
    if op == "<":
        return a < v
    return a == v
