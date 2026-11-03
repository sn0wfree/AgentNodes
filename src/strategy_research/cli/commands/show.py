"""``/show <run_id>``, ``/pine <run_id>``, ``/skill`` — run inspection.

Implements thin command shims that delegate to the existing run artifacts
filesystem. Reuses :mod:`cli.theme` for the shared console.

* :func:`cmd_show(run_id)` — read ``runs/run_<id>/summary.json``.
* :func:`cmd_pine(run_id)` — read the same and emit a minimal TradingView
  Pine Script stub.
* :func:`cmd_skill()` — list bundled skills via ``SkillsLoader``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel

from strategy_research.cli.theme import get_console


def _resolve_console(console: Any = None) -> Console:
    return console or get_console()


def _resolve_workspace() -> Path:
    """Default workspace — overridable via env ``STRATEGY_RESEARCH_WORKSPACE``."""
    import os
    ws = os.environ.get("STRATEGY_RESEARCH_WORKSPACE")
    if ws:
        return Path(ws)
    return Path.cwd()


def _locate_run(run_id: str) -> Optional[Path]:
    """Find the run directory for ``run_id`` (e.g. ``0001`` or ``run_0001``)."""
    runs_root = _resolve_workspace() / "strategies"
    if not runs_root.exists():
        return None
    norm = run_id.strip()
    if not norm.startswith("run_"):
        norm = f"run_{int(norm):04d}"
    for strat in runs_root.iterdir():
        candidate = strat / "runs" / norm
        if candidate.is_dir():
            return candidate
    return None


def cmd_show(run_id: str, *, console: Any = None) -> int:
    """``/show <run_id>`` — print run summary."""
    console = _resolve_console(console)
    run_dir = _locate_run(run_id)
    if run_dir is None:
        console.print(f"[red]Run not found:[/red] {run_id}")
        return 1

    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        console.print(f"[red]summary.json not found in {run_dir}[/red]")
        return 1

    data = json.loads(summary_path.read_text(encoding="utf-8"))
    body = json.dumps(data, indent=2, ensure_ascii=False)
    console.print(Panel(body, title=str(run_dir.name), border_style="blue"))
    return 0


def cmd_pine(run_id: str, *, console: Any = None) -> int:
    """``/pine <run_id>`` — emit a minimal TradingView Pine Script v5 stub."""
    console = _resolve_console(console)
    run_dir = _locate_run(run_id)
    if run_dir is None:
        console.print(f"[red]Run not found:[/red] {run_id}")
        return 1

    summary_path = run_dir / "summary.json"
    name = "Strategy"
    if summary_path.exists():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            name = data.get("name") or data.get("round") or name
        except (json.JSONDecodeError, OSError):
            pass

    pine = (
        "//@version=5\n"
        f"strategy(\"{name}\", overlay=true)\n"
        "\n"
        "// TODO: replace the body with your signal logic.\n"
        "longCondition = close > ta.sma(close, 20)\n"
        "if (longCondition)\n"
        "    strategy.entry(\"Long\", strategy.long)\n"
    )
    console.print(Panel(pine, title=f"Pine v5 — {run_id}", border_style="green"))
    return 0


def cmd_skill(*, console: Any = None) -> int:
    """``/skill`` — list bundled + user skills via ``SkillsLoader``."""
    console = _resolve_console(console)
    try:
        from strategy_research.kernel.skills import SkillsLoader
        loader = SkillsLoader()
        skills = loader.list() if hasattr(loader, "list") else []
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/skill failed:[/red] {exc}")
        return 1

    if not skills:
        console.print("[yellow]No skills registered.[/yellow]")
        return 0

    for skill in skills:
        name = getattr(skill, "name", str(skill))
        desc = getattr(skill, "description", "")
        console.print(f"• {name} — {desc}")
    return 0


# Slash-router entrypoints
def run_show(ctx: Any = None, *args: str) -> int:
    """Show the current agent/strategy context."""
    if not args:
        return cmd_skill()
    return cmd_show(args[0])


def run_pine(ctx: Any = None, *args: str) -> int:
    """Pine-level context dump (dev helper)."""
    if not args:
        return cmd_skill()
    return cmd_pine(args[0])


def run_skill(ctx: Any = None, *args: str) -> int:
    """Show available skill(s)."""
    return cmd_skill()


__all__ = ["cmd_show", "cmd_pine", "cmd_skill", "run_show", "run_pine", "run_skill"]
