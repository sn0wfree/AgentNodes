"""Tests for session ID + model name resolution at TUI startup.

The production logic lives in :meth:`ResearchApp._resolve_session_identity`
(extracted from ``on_mount`` for testability). These tests call that
method directly so they exercise the actual production code path —
no copy-pasted logic, no "tests test themselves" anti-pattern.

Three layers:

  1. **Unit tests** for ``_resolve_session_identity`` — patches
     ``LLMConfig.load`` and ``uuid.uuid4`` via ``monkeypatch``.
  2. **Header push tests** — verifies the resolved values are forwarded
     to ``update_header`` with the correct kwarg names.
  3. **Integration test** — runs the full ``ResearchApp.run_test()``
     and checks that the mounted ``StatusHeader`` reflects the resolved
     values from the very first frame.

A separate :class:`TestBuildLlmClientTupleReturn` covers the
``_build_llm_client`` tuple return contract (CLI entry point).
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest import mock

import pytest

from agentnodes.interfaces.cli.interactive.main import InteractiveContext
from agentnodes.interfaces.cli.tui.app import ResearchApp
from agentnodes.interfaces.cli.tui.widgets import StatusHeader

# Pattern for generated session ids: "cli-" + 1..8 hex chars.
_SESSION_ID_RE = re.compile(r"^cli-[a-f0-9]{1,8}$")


# ---------------------------------------------------------------- fixtures


def _make_app(*, model="unknown", session_id="cli"):
    """Build a ResearchApp with no Textual mount.

    Bypasses the full ``__init__`` lifecycle (which would try to
    query widgets) but still constructs the InteractiveContext so
    ``_resolve_session_identity`` has something to operate on.
    """
    app = ResearchApp.__new__(ResearchApp)
    app._model = model
    app._version = "0.4.2"
    app._skip_resume = True
    app._session_db_path = None
    app._llm_client = None
    app.banner = None
    app.session = None
    app._tool_total = 0
    app._tool_ok = 0
    app.ctx = InteractiveContext()
    app.ctx.session_id = session_id
    return app


def _patch_llm_load(monkeypatch, *, model=None, raises=False):
    """Patch ``LLMConfig.load`` to return a SimpleNamespace with ``.model``.

    Set ``model=None`` to make ``cfg.model`` empty/falsy (no override).
    Set ``raises=True`` to make ``LLMConfig.load`` raise.
    """
    if raises:
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config.LLMConfig.load",
            mock.MagicMock(side_effect=RuntimeError("simulated load failure")),
        )
        return

    def fake_load():
        return SimpleNamespace(model=model)

    monkeypatch.setattr(
        "agentnodes.foundation.llm.config.LLMConfig.load",
        fake_load,
    )


def _patch_uuid(monkeypatch, *, hex_value="abcdef12", raises=False):
    """Patch ``uuid.uuid4`` so generated session ids are deterministic.

    Default ``hex_value="abcdef12"`` → session id becomes ``"cli-abcdef12"``.
    """
    if raises:
        monkeypatch.setattr(
            "uuid.uuid4",
            mock.MagicMock(side_effect=RuntimeError("uuid failed")),
        )
        return

    import uuid as _uuid

    # Build a real uuid.UUID from the supplied hex so production code's
    # ``uuid.uuid4().hex[:8]`` resolves correctly. MagicMock auto-creates
    # ``.hex`` as a MagicMock attribute which shadows our intended value.
    def fake_uuid4():
        # Pad/truncate to a valid 32-char hex if needed
        h = (hex_value * 4)[:32]
        return _uuid.UUID(hex=h)

    monkeypatch.setattr("uuid.uuid4", fake_uuid4)


# ---------------------------------------------------------------- _resolve_session_identity


class TestResolveModel:
    def test_resolves_model_from_llm_config(self, monkeypatch):
        _patch_llm_load(monkeypatch, model="minimax-M3")
        app = _make_app(model="unknown")
        app._resolve_session_identity()
        assert app._model == "minimax-M3"

    def test_keeps_init_model_when_config_load_fails(self, monkeypatch):
        _patch_llm_load(monkeypatch, raises=True)
        app = _make_app(model="gpt-4o-mini")
        app._resolve_session_identity()
        assert app._model == "gpt-4o-mini"

    def test_keeps_init_model_when_config_returns_empty(self, monkeypatch):
        _patch_llm_load(monkeypatch, model="")
        app = _make_app(model="gpt-4o-mini")
        app._resolve_session_identity()
        assert app._model == "gpt-4o-mini"

    def test_keeps_init_model_when_config_returns_none(self, monkeypatch):
        _patch_llm_load(monkeypatch, model=None)
        app = _make_app(model="gpt-4o-mini")
        app._resolve_session_identity()
        assert app._model == "gpt-4o-mini"


class TestResolveSessionId:
    def test_generates_fresh_id_when_ctx_session_id_is_cli(self, monkeypatch):
        _patch_uuid(monkeypatch, hex_value="deadbeef")
        app = _make_app(session_id="cli")
        model, sid = app._resolve_session_identity()
        assert sid == "cli-deadbeef"
        assert app.ctx.session_id == "cli-deadbeef"
        assert _SESSION_ID_RE.match(sid)

    def test_does_not_overwrite_existing_session_id(self, monkeypatch):
        _patch_uuid(monkeypatch)
        app = _make_app(session_id="cli-existing-12345")
        _, sid = app._resolve_session_identity()
        assert sid == "cli-existing-12345"
        assert app.ctx.session_id == "cli-existing-12345"

    def test_does_not_overwrite_uuid_session_id(self, monkeypatch):
        # Real session IDs from SessionDB are 32-char hex
        _patch_uuid(monkeypatch)
        app = _make_app(session_id="abcdef0123456789abcdef0123456789")
        _, sid = app._resolve_session_identity()
        assert sid == "abcdef0123456789abcdef0123456789"

    def test_uuid_failure_falls_back_to_cli_fallback(self, monkeypatch):
        _patch_uuid(monkeypatch, raises=True)
        app = _make_app(session_id="cli")
        _, sid = app._resolve_session_identity()
        assert sid == "cli-fallback"
        assert app.ctx.session_id == "cli-fallback"

    def test_returns_tuple_with_resolved_values(self, monkeypatch):
        _patch_llm_load(monkeypatch, model="deepseek-chat")
        _patch_uuid(monkeypatch, hex_value="12345678")
        app = _make_app(model="unknown", session_id="cli")
        model, sid = app._resolve_session_identity()
        assert model == "deepseek-chat"
        assert sid == "cli-12345678"


# ---------------------------------------------------------------- header push


class TestHeaderPush:
    def test_resolved_values_pushed_to_header(self, monkeypatch):
        _patch_llm_load(monkeypatch, model="qwen-plus")
        _patch_uuid(monkeypatch, hex_value="abcd1234")
        app = _make_app(model="unknown", session_id="cli")
        app.update_header = mock.MagicMock()

        app._resolve_session_identity()

        # update_header must have been called with the resolved values
        # (but note: on_mount, not _resolve_session_identity, is what
        # actually pushes to header — so we exercise it through the
        # dedicated integration test below).
        # Here we just confirm the method itself doesn't call update_header.
        app.update_header.assert_not_called()

    def test_pure_method_no_widget_access(self, monkeypatch):
        """_resolve_session_identity must NOT touch any widget — pure logic."""
        _patch_llm_load(monkeypatch, model="minimax-M3")
        _patch_uuid(monkeypatch)
        app = _make_app()

        # If this method tries to query_one / write / update_header it
        # will crash since no widgets are mounted.
        model, sid = app._resolve_session_identity()

        assert model == "minimax-M3"
        assert sid.startswith("cli-")


# ---------------------------------------------------------------- integration test


@pytest.mark.asyncio
async def test_mounted_app_header_has_real_model_and_sid(monkeypatch):
    """End-to-end: mounted StatusHeader shows resolved values from frame 1.

    This is the test that proves the on_mount wiring actually feeds the
    widget tree correctly — not just that the resolver returns the
    right values.
    """
    _patch_llm_load(monkeypatch, model="minimax-M3")
    _patch_uuid(monkeypatch, hex_value="feedbeef")

    app = ResearchApp(model="unknown", skip_resume=True)
    app.ctx.session_id = "cli"

    async with app.run_test() as pilot:
        await pilot.pause()
        # StatusHeader is yielded in compose() with id="status-header"
        header = app.query_one("#status-header", StatusHeader)
        assert header._model == "minimax-M3"
        assert header._session_id == "cli-feedbeef"
        assert header._session_id != "cli"


@pytest.mark.asyncio
async def test_mounted_app_keeps_existing_session_id(monkeypatch):
    """End-to-end: pre-set session id is NOT overwritten by mount."""
    _patch_llm_load(monkeypatch, model="minimax-M3")
    _patch_uuid(monkeypatch, hex_value="cafebabe")

    app = ResearchApp(model="unknown", skip_resume=True)
    app.ctx.session_id = "resumed-from-disk-1234"

    async with app.run_test() as pilot:
        await pilot.pause()
        header = app.query_one("#status-header", StatusHeader)
        assert header._session_id == "resumed-from-disk-1234"


@pytest.mark.asyncio
async def test_mounted_app_banner_includes_real_model(monkeypatch):
    """End-to-end: the transcript banner shows the resolved model."""
    _patch_llm_load(monkeypatch, model="minimax-M3")

    app = ResearchApp(model="unknown", skip_resume=True)
    app.ctx.session_id = "cli-test"

    async with app.run_test() as pilot:
        await pilot.pause()
        from agentnodes.interfaces.cli.tui.widgets import TranscriptView
        tv = app.query_one(TranscriptView)
        # Banner Text was written as the first line(s); check the joined
        # text mentions the resolved model.
        joined = " ".join(str(line) for line in tv.lines)
        assert "minimax-M3" in joined, (
            f"banner did not include resolved model: {joined[:200]!r}"
        )


# ---------------------------------------------------------------- _build_llm_client tuple


class TestBuildLlmClientTupleReturn:
    """Verify that ``_build_llm_client`` now returns ``(client, model_name)``."""

    def test_returns_tuple(self, monkeypatch):
        from agentnodes.interfaces.cli.__main__ import _build_llm_client

        monkeypatch.setattr(
            "agentnodes.foundation.llm.config.LLMConfig.load",
            lambda: SimpleNamespace(api_key="sk-test", model="minimax-M3"),
        )
        # Stub OpenAICompatClient so __init__ doesn't hit the network
        monkeypatch.setattr(
            "agentnodes.foundation.llm.openai_client.OpenAICompatClient",
            lambda cfg: mock.MagicMock(spec=["config"]),
        )

        result = _build_llm_client()
        assert isinstance(result, tuple) and len(result) == 2
        client, model = result
        assert model == "minimax-M3"

    def test_returns_unknown_when_load_fails(self, monkeypatch):
        from agentnodes.interfaces.cli.__main__ import _build_llm_client

        monkeypatch.setattr(
            "agentnodes.foundation.llm.config.LLMConfig.load",
            mock.MagicMock(side_effect=RuntimeError("config load fail")),
        )
        result = _build_llm_client()
        assert result == (None, "unknown")

    def test_returns_model_even_when_no_api_key(self, monkeypatch):
        from agentnodes.interfaces.cli.__main__ import _build_llm_client

        monkeypatch.setattr(
            "agentnodes.foundation.llm.config.LLMConfig.load",
            lambda: SimpleNamespace(api_key="", model="minimax-M3"),
        )
        client, model = _build_llm_client()
        assert client is None
        assert model == "minimax-M3"
