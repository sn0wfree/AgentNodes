"""Phase 3 tests — round artifacts / manifest / diff / adopt endpoints.

Creates a minimal study dir tree (rounds/round_XXXX/run_XXXX/strategy.py
+ manifest.json + summary.md) and exercises the read-only detail
endpoints plus the non-destructive adopt.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest


def _build_asgi_app():
    from fastapi import FastAPI

    from strategy_research.api.middleware import AuthMiddleware
    from strategy_research.api.routers import chat, study
    from strategy_research.api.routers.web_session import router as session_router

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(chat.router, prefix="/api/chat")
    app.include_router(session_router, prefix="/api/chat/session")
    app.include_router(study.router, prefix="/api/study")
    return app


def _bearer(user_id: str = "tester") -> dict:
    from strategy_research.api.auth_tokens import create_token

    return {"Authorization": f"Bearer {create_token(user_id)}"}


@pytest.fixture
def _study_tree(tmp_path: Path, monkeypatch):
    """Seed a study row + round dir tree on disk."""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    sessions_db = tmp_path / "sessions.db"
    monkeypatch.setenv("SR_SESSIONS_DB", str(sessions_db))
    import sqlite3
    conn = sqlite3.connect(str(sessions_db))
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT,
          created_at TEXT, updated_at TEXT, starred INTEGER NOT NULL DEFAULT 0,
          tags_json TEXT NOT NULL DEFAULT '[]', message_count INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, updated_at) "
        "VALUES ('sess-1', 'tester', '2026-08-01T10:00:00', '2026-08-01T10:00:00')"
    )
    conn.commit()
    conn.close()

    from strategy_research.apps.study import StudyStore

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="sess-1",
        goal_id=None,
        objective="test objective",
        workspace_path=str(tmp_path),
        strategy_name="demo",
    )
    study_id = rec.study_id

    # Round 3 dir tree: manifest + summary + run_0001/strategy.py + config.yaml
    rd = tmp_path / "study" / study_id / "rounds" / "round_0003"
    run = rd / "run_0001"
    run.mkdir(parents=True)
    (run / "strategy.py").write_text(
        "PARAMS = {'top_n': 20}\nFACTOR_EXPRS = ['mom(20)']\n",
        encoding="utf-8",
    )
    (run / "config.yaml").write_text("top_n: 20\n", encoding="utf-8")
    manifest = {
        "round": 3,
        "run_name": "run_0001",
        "hypothesis": {"text": "momentum works", "levers": ["mom"], "predicted_affected": []},
        "strategy_changes": [{"what": "top_n 10→20"}],
        "metrics": {"calmar": 1.2, "vs_prev": {"calmar": "+0.2"}},
        "verdict": {"decision": "keep", "reason": "beat baseline"},
        "next": {"suggested_focus": "try reversal", "open_questions": ["window?"], "blockers": []},
        "review": {"summary": "ok"},
    }
    (rd / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (rd / "summary.md").write_text("# Round 3\nmomentum won\n", encoding="utf-8")

    # Round 2 dir (for diff against)
    rd2 = tmp_path / "study" / study_id / "rounds" / "round_0002"
    (rd2 / "run_0001").mkdir(parents=True)
    (rd2 / "run_0001" / "strategy.py").write_text(
        "PARAMS = {'top_n': 10}\nFACTOR_EXPRS = ['mom(10)']\n",
        encoding="utf-8",
    )
    (rd2 / "manifest.json").write_text(json.dumps({"round": 2}), encoding="utf-8")
    return study_id, tmp_path


@pytest.mark.asyncio
async def test_round_artifacts_lists_files(_study_tree):
    study_id, _ = _study_tree
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get(f"/api/study/{study_id}/rounds/3/artifacts")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        paths = [a["path"] for a in body["artifacts"]]
        assert "manifest.json" in paths
        assert "summary.md" in paths
        assert any(x.replace("\\", "/").endswith("run_0001/strategy.py") for x in paths)
        assert any(x.replace("\\", "/").endswith("run_0001/config.yaml") for x in paths)
        assert all(a["size"] > 0 for a in body["artifacts"])


@pytest.mark.asyncio
async def test_round_artifacts_404_missing_round(_study_tree):
    study_id, _ = _study_tree
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get(f"/api/study/{study_id}/rounds/99/artifacts")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_round_manifest_returns_json(_study_tree):
    study_id, _ = _study_tree
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get(f"/api/study/{study_id}/rounds/3/manifest")
        assert r.status_code == 200
        m = r.json()["manifest"]
        assert m["round"] == 3
        assert m["verdict"]["decision"] == "keep"
        assert m["next"]["suggested_focus"] == "try reversal"


@pytest.mark.asyncio
async def test_round_diff_between_rounds(_study_tree):
    study_id, _ = _study_tree
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get(f"/api/study/{study_id}/rounds/3/diff?against=2")
        assert r.status_code == 200
        body = r.json()
        kinds = {line["kind"] for line in body["diff"]}
        assert kinds >= {"add", "del"}
        assert body["stats"]["adds"] >= 1 and body["stats"]["dels"] >= 1


@pytest.mark.asyncio
async def test_round_diff_against_baseline_404_when_missing(_study_tree):
    study_id, _ = _study_tree
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        # against=0 → baseline; not seeded → 404
        r = await client.get(f"/api/study/{study_id}/rounds/3/diff?against=0")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_adopt_copies_strategy_to_study_baseline(_study_tree):
    study_id, tmp_path = _study_tree
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post(f"/api/study/{study_id}/rounds/3/adopt")
        assert r.status_code == 200
        assert r.json()["adopted_run_dir"].endswith("run_0001")
    adopted = tmp_path / "study" / study_id / "baseline" / "strategy.py"
    assert adopted.exists()
    assert "top_n': 20" in adopted.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_adopt_404_when_run_missing(_study_tree):
    study_id, _ = _study_tree
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post(f"/api/study/{study_id}/rounds/99/adopt")
        assert r.status_code == 404
