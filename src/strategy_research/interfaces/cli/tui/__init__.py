"""Textual full-screen TUI for QuantNodes-Research.

Public API:
- :class:`ResearchApp` — the top-level Textual ``App``.
- :func:`run_tui` — convenience entry that mounts and runs ``ResearchApp``.

The TUI reuses existing CLI building blocks so this package is mostly
wiring + thin Textual-native widgets:

* Banner widget — reuses ``strategy_research.interfaces.cli.ui.banner.print_banner``.
* Transcript view — renders ``Rich.Renderable`` instances (turns + proposals).
* Activity rail — uses ``strategy_research.interfaces.cli.components.tool_event`` and
  ``strategy_research.interfaces.cli.ui.rail.RailRunDashboard`` event shapes.
* Input bar — bridged to the legacy ``process_turn`` dispatcher.
"""
from __future__ import annotations

from typing import Optional

from strategy_research.interfaces.cli.tui.app import ResearchApp


def run_tui(
    *,
    model: str = "unknown",
    version: str = "0.4.2",
    session_db_path: Optional[str] = None,
) -> int:
    """Mount the TUI and run it.

    Returns the process exit code.
    """
    app = ResearchApp(model=model, version=version, session_db_path=session_db_path)
    return app.run() or 0


__all__ = ["ResearchApp", "run_tui"]
