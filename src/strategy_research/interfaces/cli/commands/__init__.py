"""Public re-exports for ``cli.commands``.

Mirrors ``vibe-trading/cli/commands/__init__.py``.
"""

from __future__ import annotations

from strategy_research.interfaces.cli.commands.help import render_help_table
from strategy_research.interfaces.cli.commands.show import (
    cmd_pine,
    cmd_show,
    cmd_skill,
)
from strategy_research.interfaces.cli.commands.slash_chat import (
    cmd_clear,
    cmd_debug,
    cmd_journal,
    cmd_model,
    cmd_quit,
    cmd_shadow,
)
# slash_goal 已随 apps/goal 移除（2026-11-04 做减法批次 2）
from strategy_research.interfaces.cli.commands.slash_memory import (
    cmd_forget,
)
from strategy_research.interfaces.cli.commands.slash_memory import (
    cmd_list as memory_cmd_list,
)
from strategy_research.interfaces.cli.commands.slash_memory import (
    cmd_search as memory_cmd_search,
)
from strategy_research.interfaces.cli.commands.slash_memory import (
    cmd_show as memory_cmd_show,
)
from strategy_research.interfaces.cli.commands.slash_router import (
    SLASH_COMMANDS,
    Command,
    find_exact,
    match_commands,
)
from strategy_research.interfaces.cli.commands.slash_session import (
    cmd_export,
    cmd_history,
    cmd_search,
)

__all__ = [
    "SLASH_COMMANDS",
    "Command",
    "find_exact",
    "match_commands",
    "render_help_table",
    "cmd_export",
    "cmd_history",
    "cmd_search",
    "cmd_forget",
    "memory_cmd_list",
    "memory_cmd_search",
    "memory_cmd_show",
    "cmd_clear",
    "cmd_debug",
    "cmd_journal",
    "cmd_model",
    "cmd_quit",
    "cmd_shadow",
    "cmd_pine",
    "cmd_show",
    "cmd_skill",
]
