"""A5 runtime: model I/O boundary (chat / stream / async)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ModelIO:
    """Thin façade over OpenAICompatClient for AgentLoop (A5).

    Keeps provider/streaming details out of the control loop so tests
    can stub a single seam.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        return self._client

    def chat(self, messages: list[dict[str, Any]], tools=None, **overrides: Any) -> Any:
        return self._client.chat(messages, tools=tools, **overrides)

    async def achat(self, messages: list[dict[str, Any]], tools=None, **overrides: Any) -> Any:
        return await self._client.achat(messages, tools=tools, **overrides)

    def stream(self, messages: list[dict[str, Any]], tools=None, **overrides: Any) -> Any:
        return self._client.stream(messages, tools=tools, **overrides)

    def astream(self, messages: list[dict[str, Any]], tools=None, **overrides: Any) -> Any:
        return self._client.astream(messages, tools=tools, **overrides)
