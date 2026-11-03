"""Public re-exports for the interactive REPL layer."""

from __future__ import annotations

from strategy_research.interfaces.cli.interactive.completer import SlashCompleter
from strategy_research.interfaces.cli.interactive.main import (
    InteractiveContext,
    dispatch_slash,
    process_turn,
)
from strategy_research.interfaces.cli.interactive.main import (
    main as interactive_main,
)
from strategy_research.interfaces.cli.onboard import (
    BACK,
    CANCEL,
    PROVIDERS,
    TIMEOUT_CHOICES,
    Provider,
    is_onboarded,
    run_onboarding,
)

__all__ = [
    "SlashCompleter",
    "InteractiveContext",
    "dispatch_slash",
    "interactive_main",
    "process_turn",
    "BACK",
    "CANCEL",
    "PROVIDERS",
    "TIMEOUT_CHOICES",
    "Provider",
    "is_onboarded",
    "run_onboarding",
]
