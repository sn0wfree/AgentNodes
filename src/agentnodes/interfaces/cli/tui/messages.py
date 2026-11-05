"""Inter-widget Textual messages.

Textual widgets communicate via posted :class:`Message` instances. This
module centralises the message types so widgets / app / session layers
all import from one place.
"""
from __future__ import annotations

from typing import Any, Optional

from textual.message import Message


class WriteTranscript(Message):
    """Posted by anything that wants to push a Renderable into the chat log."""

    def __init__(self, content: Any) -> None:
        super().__init__()
        self.content = content


class WriteRail(Message):
    """Posted by the session to log an activity rail event."""

    def __init__(self, event: Any) -> None:
        super().__init__()
        self.event = event


class SynthesizeInput(Message):
    """Posted by sidebar clicks to push a slash command into the input bar."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class AgentStreamDelta(Message):
    """Posted by the LLM streaming layer with each token delta."""

    def __init__(self, delta: str) -> None:
        super().__init__()
        self.delta = delta


class AgentStreamDone(Message):
    """Posted when the LLM stream completes (or errors)."""

    def __init__(self, error: Optional[BaseException] = None) -> None:
        super().__init__()
        self.error = error


__all__ = [
    "WriteTranscript",
    "WriteRail",
    "SynthesizeInput",
    "AgentStreamDelta",
    "AgentStreamDone",
]
