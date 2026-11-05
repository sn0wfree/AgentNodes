"""Pydantic v2 response/request schemas for the chat HTTP API.

Single source of truth for wire shapes. Frontend types are generated
from ``/openapi.json`` via openapi-typescript.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

# ── request models ───────────────────────────────────────────────────


class ChatMessageRequest(BaseModel):
    session_id: str
    content: str
    images: Optional[list[str]] = None
    agent_id: Optional[str] = None
    mode: Optional[str] = None
    model: Optional[str] = None
    thinking: Optional[str] = None


class CancelRequest(BaseModel):
    session_id: str
    attempt_id: Optional[str] = None


# ── response models ─────────────────────────────────────────────────


class SendMessageResponse(BaseModel):
    message_id: str
    user_message_id: str
    assistant_message_id: str
    event_id: str
    status: str = "queued"
    attempt_id: Optional[str] = None


class ChatCancelResponse(BaseModel):
    status: str
    session_id: str
    attempt_id: Optional[str] = None


class ChatQueueResumeResponse(BaseModel):
    ok: bool
    session_id: str


class ChatAttemptsResponse(BaseModel):
    attempts: list[ChatAttemptItem]


class ChatAttemptItem(BaseModel):
    attempt_id: str
    message_id: str
    status: str
    prompt: str
    created_at: str
    error: str = ""


class ChatPersonasResponse(BaseModel):
    personas: list[ChatPersonaItem]


class ChatPersonaItem(BaseModel):
    id: str
    name: str
    description: str
    system_prompt: Optional[str] = None
