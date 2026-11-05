"""Thinking-verb pool for the spinner/working indicator.

Used by :mod:`cli.components.working_indicator` to show a different verb
per turn so the user sees variety in the spinner label.
"""

from __future__ import annotations

import random
from typing import Optional

THINKING_VERBS: tuple[str, ...] = (
    "Pondering",
    "Analyzing",
    "Reasoning",
    "Investigating",
    "Synthesizing",
    "Cross-checking",
)


def pick_thinking_verb(*, seed: Optional[int] = None) -> str:
    """Return a single verb, suffixed with ``…`` to match UX strings.

    Args:
        seed: Optional seed for deterministic testing. Without a seed the
            pick is uniformly random from :data:`THINKING_VERBS`.
    """
    pool = THINKING_VERBS
    if seed is None:
        verb = random.choice(pool)
    else:
        verb = random.Random(seed).choice(pool)
    return f"{verb}…"


__all__ = ["THINKING_VERBS", "pick_thinking_verb"]
