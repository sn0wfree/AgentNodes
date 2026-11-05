"""Event domain types (P0-1 A2).

Sinking the EventV2 envelope into core lets ``core/agent/event_store``
depend on it without inverting the core/api layering. The single
definition lives in ``event_v2.py``; this package re-exports the public
surface so call sites can write ``from agentnodes.kernel.events
import EventV2`` if they prefer.
"""

from .event_v2 import EventType, EventV2, is_known_event_type

__all__ = ["EventType", "EventV2", "is_known_event_type"]
