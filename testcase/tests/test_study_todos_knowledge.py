"""Tests for Study todos and knowledge API endpoints."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _create_study_with_files(tmp_path, study_id="test-study"):
    """Create a study with todos.md and knowledge.md files."""
    ws = tmp_path / "workspace"
    study_dir = ws / "study" / study_id
    study_dir.mkdir(parents=True, exist_ok=True)

    # Create todos.md
    (study_dir / "todos.md").write_text("# Todos\n- [ ] Task 1\n- [x] Task 2\n")

    # Create knowledge.md
    (study_dir / "knowledge.md").write_text("# Knowledge\n- Finding 1\n- Finding 2\n")

    return ws, study_dir


class TestStudyTodosEndpoint:
    def test_todos_returns_content(self, tmp_path):
        """GET /{id}/todos should return todos.md content."""
        ws, study_dir = _create_study_with_files(tmp_path)

        from strategy_research.interfaces.api.routers.study import study_todos
        from fastapi import Request

        study = SimpleNamespace(
            study_id="test-study",
            workspace_path=str(ws),
        )

        request = MagicMock(spec=Request)
        request.state.user_id = "test-user"

        with patch("strategy_research.interfaces.api.routers.study._owned_study", return_value=study):
            result = asyncio.run(study_todos(request, "test-study"))

        assert result["status"] == "ok"
        assert result["study_id"] == "test-study"
        assert "Task 1" in result["todos"]

    def test_todos_returns_empty_when_not_exists(self, tmp_path):
        """GET /{id}/todos should return empty string when file doesn't exist."""
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True, exist_ok=True)

        from strategy_research.interfaces.api.routers.study import study_todos
        from fastapi import Request

        study = SimpleNamespace(
            study_id="test-study",
            workspace_path=str(ws),
        )

        request = MagicMock(spec=Request)
        request.state.user_id = "test-user"

        with patch("strategy_research.interfaces.api.routers.study._owned_study", return_value=study):
            result = asyncio.run(study_todos(request, "test-study"))

        assert result["status"] == "ok"
        assert result["todos"] == ""


class TestStudyKnowledgeEndpoint:
    def test_knowledge_returns_content(self, tmp_path):
        """GET /{id}/knowledge should return knowledge.md content."""
        ws, study_dir = _create_study_with_files(tmp_path)

        from strategy_research.interfaces.api.routers.study import study_knowledge
        from fastapi import Request

        study = SimpleNamespace(
            study_id="test-study",
            workspace_path=str(ws),
        )

        request = MagicMock(spec=Request)
        request.state.user_id = "test-user"

        with patch("strategy_research.interfaces.api.routers.study._owned_study", return_value=study):
            result = asyncio.run(study_knowledge(request, "test-study"))

        assert result["status"] == "ok"
        assert result["study_id"] == "test-study"
        assert "Finding 1" in result["knowledge"]

    def test_knowledge_returns_empty_when_not_exists(self, tmp_path):
        """GET /{id}/knowledge should return empty string when file doesn't exist."""
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True, exist_ok=True)

        from strategy_research.interfaces.api.routers.study import study_knowledge
        from fastapi import Request

        study = SimpleNamespace(
            study_id="test-study",
            workspace_path=str(ws),
        )

        request = MagicMock(spec=Request)
        request.state.user_id = "test-user"

        with patch("strategy_research.interfaces.api.routers.study._owned_study", return_value=study):
            result = asyncio.run(study_knowledge(request, "test-study"))

        assert result["status"] == "ok"
        assert result["knowledge"] == ""


# Helper for async tests
import asyncio
from types import SimpleNamespace
