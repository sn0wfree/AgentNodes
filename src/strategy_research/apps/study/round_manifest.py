"""Study v2 round artifacts — pure functions (design §9).

Three per-round artifacts:
- manifest.json   machine handoff (next round's injection source)
- summary.md      human-readable single-round summary (template render)
- journal.md      append-only task archive (one line per round)

Two-phase write (design §9.4): phase 1 at round end (body), phase 2 after
review (review section overlay).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def round_dir(workspace_path: Path, study_id: str, round_num: int) -> Path:
    return (
        Path(workspace_path) / "study" / study_id / "rounds"
        / f"round_{round_num:04d}"
    )


def manifest_path(workspace_path: Path, study_id: str, round_num: int) -> Path:
    return round_dir(workspace_path, study_id, round_num) / "manifest.json"


def summary_path(workspace_path: Path, study_id: str, round_num: int) -> Path:
    return round_dir(workspace_path, study_id, round_num) / "summary.md"


def journal_path(workspace_path: Path, study_id: str) -> Path:
    return Path(workspace_path) / "study" / study_id / "journal.md"


# ── manifest body (phase 1) ────────────────────────────────────────────

# B3 conclusion labels — the research-strength axis, orthogonal to the
# keep/discard verdict (docs/phase1-research-discipline.md §4).
CONCLUSION_LABELS = frozenset({
    "effective",
    "weakly",
    "ineffective",
    "regime-dependent",
    "inconclusive",
})

REQUIRED_MANIFEST_FIELDS = (
    "hypothesis",
    "metrics",
    "verdict",
)


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Missing-field warnings for a manifest body (never raises).

    Gradual enforcement: callers log the returned lines; nothing
    fails a round for a missing contract field yet. Field paths match
    the actual manifest shape (hypothesis.levers etc. live nested).
    """
    problems: list[str] = []
    hyp = manifest.get("hypothesis") or {}
    if not isinstance(hyp, dict) or not hyp.get("text"):
        problems.append("missing required field: hypothesis.text")
    if not hyp.get("predicted_affected"):
        problems.append("missing required field: hypothesis.predicted_affected")
    if not manifest.get("metrics"):
        problems.append("missing required field: metrics")
    verdict = manifest.get("verdict") or {}
    if not isinstance(verdict, dict) or not verdict.get("decision"):
        problems.append("missing required field: verdict.decision")
    conclusion = manifest.get("conclusion") or "inconclusive"
    if conclusion not in CONCLUSION_LABELS:
        problems.append(f"conclusion {conclusion!r} not in CONCLUSION_LABELS")
    return problems


def build_manifest(
    *,
    round_num: int,
    inherited_from: str | None,
    adopted_run: str | None,
    run_name: str,
    hypothesis: str,
    levers: list[str],
    predicted_affected: list[str],
    strategy_changes: list[dict] | None,
    metrics: dict,
    prev_metrics: dict | None,
    baseline_metrics: dict | None,
    verdict: str,
    verdict_reason: str,
    gates: list[dict] | None,
    budget: dict,
    conclusion: str = "inconclusive",
    findings: list[dict] | None = None,
) -> dict[str, Any]:
    """Phase-1 manifest body (round/继承/假设/改动/metrics/verdict/gates/
    next/budget/conclusion/findings). The ``review`` section is filled in
    phase 2."""
    vs = _vs(metrics, prev_metrics)
    vs_base = _vs(metrics, baseline_metrics)
    return {
        "round": round_num,
        "inherited_from": inherited_from,
        "adopted_run": adopted_run,
        "run_name": run_name,
        "created_at": now_iso(),
        "hypothesis": {
            "text": hypothesis,
            "levers": levers,
            "predicted_affected": predicted_affected,
        },
        "strategy_changes": strategy_changes or [],
        "metrics": {
            **{k: v for k, v in metrics.items()
               if k in ("calmar", "sharpe", "max_dd", "ann_return", "turnover")},
            "vs_prev": vs,
            "vs_baseline": vs_base,
        },
        "verdict": {"decision": verdict, "reason": verdict_reason},
        "gates": gates or [],
        "next": {"suggested_focus": "", "open_questions": [], "blockers": []},
        "review": None,  # phase 2
        "budget": budget,
        "conclusion": conclusion,
        "findings": findings or [],
    }


def _vs(metrics: dict, base: dict | None) -> dict[str, str]:
    if not base:
        return {}
    out: dict[str, str] = {}
    for key in ("calmar", "sharpe", "max_dd", "ann_return"):
        cur, prev = metrics.get(key), base.get(key)
        if isinstance(cur, (int, float)) and isinstance(prev, (int, float)):
            out[key] = f"{cur - prev:+.3f}"
    return out


def save_manifest(manifest: dict, ws: Path, study_id: str, round_num: int) -> Path:
    import json
    p = manifest_path(ws, study_id, round_num)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    tmp.replace(p)
    return p


def load_manifest(ws: Path, study_id: str, round_num: int) -> dict | None:
    import json
    p = manifest_path(ws, study_id, round_num)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def overlay_review(manifest: dict, review: dict) -> dict:
    """Phase 2: attach review conclusions to the manifest."""
    manifest["review"] = review
    return manifest


# ── summary.md (template render, zero LLM tokens) ──────────────────────


def render_round_markdown(manifest: dict, objective: str = "") -> str:
    """Single-round summary — pure template rendering (design §9.2)."""
    r = manifest["round"]
    hyp = manifest.get("hypothesis") or {}
    verdict = manifest.get("verdict") or {}
    gates = manifest.get("gates") or []
    metrics = manifest.get("metrics") or {}
    changes = manifest.get("strategy_changes") or []
    next_ = manifest.get("next") or {}
    review = manifest.get("review")

    lines = [
        f"# Round {r} 总结 · {manifest.get('created_at', '')}",
        "",
        f"**目标**：{objective or '-'}",
        "",
        "## 本轮做了什么",
        "",
        f"- 假设：{hyp.get('text', '-')}",
        f"- 杠杆：{', '.join(hyp.get('levers') or []) or '-'}",
        f"- 预期影响：{', '.join(hyp.get('predicted_affected') or []) or '-'}",
        "",
        "## 本轮修改",
        "",
    ]
    if changes:
        lines.append("| 项 | 旧值 | 新值 |")
        lines.append("|---|---|---|")
        for ch in changes:
            lines.append(f"| {ch.get('param', '?')} | {ch.get('old', '')} | {ch.get('new', '')} |")
    else:
        lines.append("- 无修改")
    lines += [
        "",
        "## 结果",
        "",
        f"- Calmar: {metrics.get('calmar', '-')}（vs 上轮 {metrics.get('vs_prev', {}).get('calmar', '-')} / vs baseline {metrics.get('vs_baseline', {}).get('calmar', '-')}）",
        f"- Sharpe: {metrics.get('sharpe', '-')}",
        f"- MaxDD: {metrics.get('max_dd', '-')}",
        "",
        "## 裁决",
        "",
    ]
    if verdict.get("decision") == "keep":
        lines.append(f"- ✅ keep：{verdict.get('reason', '')}")
    else:
        lines.append(f"- ❌ discard：{verdict.get('reason', '')}")
    lines += [
        "",
        "## 人类判断点",
        "",
    ]
    if gates:
        for g in gates:
            lines.append(f"- {g.get('id', '?')}: {g.get('result', '?')}"
                         f"{'（enforced）' if g.get('enforced') else ''}")
    else:
        lines.append("- 无 gates 命中")
    lines += [
        "",
        "## 下一轮建议",
        "",
        f"- 建议焦点：{next_.get('suggested_focus') or (review or {}).get('next_focus') or '-'}",
    ]
    if review:
        lines.append(f"- 评审偏离度：{review.get('deviation', '-')}")
    lines.append("")
    return "\n".join(lines)


# ── journal.md (append-only archive) ───────────────────────────────────


def _journal_header(objective: str) -> str:
    return (
        "# 研究轮次归档（journal）\n"
        "\n"
        f"目标：{objective}\n"
        "\n"
        "每轮一行摘要（追加式）。discard 轮带否决原因。\n"
        "\n"
    )


def append_journal_md(
    ws: Path,
    study_id: str,
    manifest: dict,
    objective: str,
) -> Path:
    """Append one line per round; initialize the header on first write."""
    p = journal_path(ws, study_id)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_journal_header(objective), encoding="utf-8")

    r = manifest["round"]
    verdict = manifest.get("verdict") or {}
    decision = verdict.get("decision", "?")
    hyp = manifest.get("hypothesis") or {}
    metrics = manifest.get("metrics") or {}
    changes = manifest.get("strategy_changes") or []
    change_summary = ", ".join(
        f"{c.get('param', '?')}" for c in changes
    ) or "无改动"

    if decision == "keep":
        line = (
            f"## Round {r} [keep ✓] 假设：{hyp.get('text', '-')[:60]} · "
            f"改动：{change_summary} · "
            f"Calmar {metrics.get('calmar', '-')} "
            f"· [summary.md](rounds/round_{r:04d}/summary.md)"
        )
    else:
        line = (
            f"## Round {r} [discard ❌] 否决：{verdict.get('reason', '')[:80]} · "
            f"假设：{hyp.get('text', '-')[:60]} · "
            f"Calmar {metrics.get('calmar', '-')} "
            f"· [summary.md](rounds/round_{r:04d}/summary.md)"
        )
    with p.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return p


# ── inheritance chain (design §8.2) ────────────────────────────────────


def resolve_adopted_run(
    *,
    keep_run_dir: str | None,
    round_num: int,
    verdict: str,
    discard_streak: int,
    max_discard_streak: int,
) -> tuple[str, str, bool]:
    """Decide the next round's starting strategy source.

    Returns (adopted_run, inherited_from, stop_by_streak):
    - keep     → keep the current run (adopted_run updated later)
    - discard  → roll back: keep last keep-run (or baseline)
    - discard streak >= max → stop (stagnation_discard_streak)

    ``keep_run_dir`` is the CURRENT round's run dir when verdict=keep.
    """
    if verdict == "keep":
        return f"rounds/round_{round_num:04d}/run_0001", f"rounds/round_{round_num:04d}/run_0001", False
    if discard_streak + 1 >= max_discard_streak:
        return keep_run_dir or "baseline", keep_run_dir or "baseline", True
    return keep_run_dir or "baseline", keep_run_dir or "baseline", False


def resolve_adopted_run_for_start(keep_run_dir: str | None) -> str:
    """Round-start source: the last keep run, else baseline."""
    return keep_run_dir or "baseline"
