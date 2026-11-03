"""Research-proposal intercept — user picks a numbered choice, LLM never sees it.

Mirrors ``vibe-trading/cli/main.py``'s mandate.commit intercept. When the
agent emits a multi-choice proposal (e.g. swarm parameter tweaks, factor
additions), the user types ``1`` / ``2`` / ``3`` to accept one and the
choice is consumed by :func:`capture_pick` — never forwarded to the LLM.

Public API:

* :class:`Proposal` — dataclass with ``title``, ``choices``, ``payload``.
* :func:`make_proposal` — factory. ``choices`` may be dicts or strings.
* :func:`capture_pick(input_text, proposal)` — returns the picked choice or
  ``None`` if input is not a valid pick.
* :func:`has_pending_proposal` — return ``True`` iff ctx has a pending proposal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence, Union


@dataclass
class Proposal:
    """A pending user-facing proposal emitted by the agent/slave swarm.

    Attributes:
        title: Human-readable title shown above the choices.
        choices: Ordered sequence of choices. Each may be a string
            (label only) or a dict ``{"label": ..., "payload": ...}``.
        payload: Optional outer payload returned by ``capture_pick``
            alongside the chosen entry.
    """

    title: str
    choices: Sequence[Union[str, dict]]
    payload: Any = None

    def choice_labels(self) -> list[str]:
        """Extract the user-facing labels of every choice."""
        labels: list[str] = []
        for c in self.choices:
            if isinstance(c, dict):
                labels.append(str(c.get("label", c.get("text", ""))))
            else:
                labels.append(str(c))
        return labels


def make_proposal(
    title: str,
    choices: Sequence[Union[str, dict]],
    *,
    payload: Any = None,
) -> Proposal:
    """Factory: build a :class:`Proposal` from raw inputs."""
    return Proposal(title=title, choices=list(choices), payload=payload)


def is_pick(input_text: str, proposal: Optional[Proposal] = None) -> bool:
    """Return ``True`` iff ``input_text`` is a valid 1-based pick against ``proposal``.

    A bare integer string is a valid pick only when ``proposal`` is set and
    the integer is in ``1..len(choices)``.
    """
    if proposal is None:
        return False
    text = input_text.strip()
    if not text.isdigit():
        return False
    n = int(text)
    return 1 <= n <= len(proposal.choices)


def capture_pick(
    input_text: str, proposal: Optional[Proposal]
) -> Optional[dict[str, Any]]:
    """Consume ``input_text`` as a numbered pick against ``proposal``.

    Returns a dict ``{"index": n, "label": ..., "payload": <inner>}`` on
    success, or ``None`` if ``input_text`` is not a valid pick.

    When ``proposal.payload`` is set, it is returned alongside the
    choice-level payload so callers can correlate the pick with the
    outer context.
    """
    if proposal is None:
        return None
    if not is_pick(input_text, proposal):
        return None
    n = int(input_text.strip())
    chosen = proposal.choices[n - 1]
    if isinstance(chosen, dict):
        label = str(chosen.get("label", chosen.get("text", "")))
        inner = chosen.get("payload")
    else:
        label = str(chosen)
        inner = None
    return {
        "index": n,
        "label": label,
        "payload": inner,
        "context": proposal.payload,
    }


def has_pending_proposal(ctx: Any) -> bool:
    """Return ``True`` iff ``ctx.pending_proposal`` is set."""
    return bool(getattr(ctx, "pending_proposal", None))


__all__ = [
    "Proposal",
    "make_proposal",
    "is_pick",
    "capture_pick",
    "has_pending_proposal",
]
