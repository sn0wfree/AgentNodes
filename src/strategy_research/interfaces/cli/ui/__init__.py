"""Public re-exports for ``cli.ui``.

Mirrors ``vibe-trading/cli/ui/__init__.py``.
"""

from __future__ import annotations

from strategy_research.interfaces.cli.ui.banner import print_banner
from strategy_research.interfaces.cli.ui.rail import RailRunDashboard, RailStep
from strategy_research.interfaces.cli.ui.transcript import (
    render_answer,
    render_elapsed_status,
    render_prompt_footer,
    render_recap,
)

__all__ = [
    "print_banner",
    "render_answer",
    "render_elapsed_status",
    "render_prompt_footer",
    "render_recap",
    "RailRunDashboard",
    "RailStep",
]
