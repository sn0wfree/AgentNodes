"""A5 runtime: event emit + trace recording (A5)."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Default preview length for tool output in traces.
DEFAULT_PREVIEW_CHARS = 200


class Telemetry:
    """Event + trace sink extracted from AgentLoop._emit/_trace."""

    def __init__(
        self,
        *,
        on_event: Callable[[str, dict], Any] | None = None,
        trace_writer: Any | None = None,
    ) -> None:
        self._on_event = on_event
        self._trace_writer = trace_writer

    def emit(self, event_type: str, data: dict | None = None) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event_type, data or {})
        except Exception as exc:  # noqa: BLE001
            logger.debug("telemetry emit %s failed: %s", event_type, exc)

    def trace(self, entry: dict[str, Any]) -> None:
        if self._trace_writer is None:
            return
        try:
            self._trace_writer.write(entry)
        except Exception as exc:  # noqa: BLE001
            logger.debug("telemetry trace failed: %s", exc)

    def trace_and_emit(self, event_type: str, data: dict | None = None) -> None:
        payload = data or {}
        self.trace({"type": event_type, **payload})
        self.emit(event_type, payload)
