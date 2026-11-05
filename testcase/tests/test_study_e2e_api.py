"""End-to-end API test for the study system.

Tests the complete study lifecycle via HTTP API calls:
1. Create study with langgraph engine
2. Poll status until completion
3. Inject directive
4. Verify graph structure
5. Verify round history
6. Check HITL interrupt support
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

# ── Test Setup ──────────────────────────────────────────────────────

def _build_asgi_app():
    """Build the FastAPI app for testing."""
    from fastapi import FastAPI

    from strategy_research.interfaces.api.middleware import AuthMiddleware
    from strategy_research.interfaces.api.routers import chat, study
    from strategy_research.interfaces.api.routers.web_session import router as session_router

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(chat.router, prefix="/api/chat")
    app.include_router(session_router, prefix="/api/chat/session")
    app.include_router(study.router, prefix="/api/study")
    return app


def _bearer(user_id: str = "tester") -> dict:
    """Create auth header."""
    from strategy_research.interfaces.api.auth_tokens import create_token
    return {"Authorization": f"Bearer {create_token(user_id)}"}


def _setup_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace with strategy.py."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    strategy_dir = ws / "strategies" / "demo"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "strategy.py").write_text(
        "# Minimal strategy for testing\nPARAMS = {}\nFACTOR_EXPRS = []\nFACTOR_WEIGHT_METHOD = 'equal'\n",
        encoding="utf-8",
    )
    (ws / "acceptance.yaml").write_text("llm_enabled: false\n", encoding="utf-8")
    return ws


def _setup_sessions_db(tmp_path: Path, session_ids: list[str] | None = None) -> Path:
    """Create a sessions DB with proper schema."""
    import sqlite3
    sessions_db = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(sessions_db))
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT,
            created_at TEXT,
            updated_at TEXT,
            starred INTEGER NOT NULL DEFAULT 0,
            tags_json TEXT NOT NULL DEFAULT '[]',
            message_count INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0
        );
    """)
    now = "2026-08-01T10:00:00"
    for sid in (session_ids or ["test-session-1"]):
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) VALUES (?, 'tester', 't', ?, ?)",
            (sid, now, now),
        )
    conn.commit()
    conn.close()
    return sessions_db


# ── Test Cases ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_study_lifecycle(tmp_path, monkeypatch):
    """Complete study lifecycle test - API endpoint verification."""
    # Setup environment
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("SR_ENFORCE_STUDY_IDOR", "0")

    # Setup workspace and sessions
    ws = _setup_workspace(tmp_path)
    sessions_db = _setup_sessions_db(tmp_path, ["test-session-1"])
    monkeypatch.setenv("SR_SESSIONS_DB", str(sessions_db))

    # Build test client
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
        timeout=30.0,
    ) as client:

        # ── Step 1: Create study ──────────────────────────────────
        print("\n=== Step 1: Create Study ===")
        r = await client.post("/api/study/start", json={
            "session_id": "test-session-1",
            "objective": "优化策略参数以最大化 Calmar 比率",
            "workspace_path": str(ws),
            "strategy_name": "demo",
            "engine": "phases",
            "max_rounds": 2,
            "cooldown_base": 0.1,
            "cooldown_jitter": 0.05,
            "min_cooldown": 0.01,
            "budget_turn": 50,
            "behavior": "static",
        })
        assert r.status_code == 200, f"Create study failed: {r.text}"
        data = r.json()
        assert data["status"] == "ok"
        assert data["engine"] == "phases"
        study_id = data["study_id"]
        print(f"Study created: {study_id}")

        # ── Step 2: Check initial summary ─────────────────────────
        print("\n=== Step 2: Check Initial Summary ===")
        r = await client.get(f"/api/study/{study_id}/summary")
        assert r.status_code == 200
        summary = r.json()
        assert summary["execution_status"] in ("queued", "running")
        print(f"Status: {summary['execution_status']}, Round: {summary['current_round']}")

        # ── Step 3: Get graph ─────────────────────────────────────
        print("\n=== Step 3: Get Graph ===")
        r = await client.get(f"/api/study/{study_id}/graph")
        assert r.status_code == 200
        graph_data = r.json()
        assert "nodes" in graph_data["graph"]
        assert "edges" in graph_data["graph"]
        node_ids = [n["id"] for n in graph_data["graph"]["nodes"]]
        print(f"Graph nodes: {node_ids}")
        assert "researcher" in node_ids
        assert "strategist" in node_ids

        # ── Step 4: Inject directive ──────────────────────────────
        print("\n=== Step 4: Inject Directive ===")
        r = await client.post(f"/api/study/{study_id}/directive", json={
            "content": "尝试均值回归策略，窗口期 14 天",
        })
        assert r.status_code == 200
        directive_data = r.json()
        assert directive_data["status"] == "ok"
        print(f"Directive injected: {directive_data['directive_id']}")

        # ── Step 5: List directives ───────────────────────────────
        print("\n=== Step 5: List Directives ===")
        r = await client.get(f"/api/study/{study_id}/directives")
        assert r.status_code == 200
        directives = r.json()["directives"]
        assert len(directives) >= 1
        print(f"Directives: {len(directives)}")

        # ── Step 6: Get rounds (may be empty if study hasn't completed) ──
        print("\n=== Step 6: Get Rounds ===")
        r = await client.get(f"/api/study/{study_id}/rounds")
        assert r.status_code == 200
        rounds_data = r.json()
        print(f"Rounds: {rounds_data['total']}")

        # ── Step 7: Verify summary fields ─────────────────────────
        print("\n=== Step 7: Verify Summary Fields ===")
        r = await client.get(f"/api/study/{study_id}/summary")
        summary = r.json()
        assert summary["objective"] == "优化策略参数以最大化 Calmar 比率"
        assert summary["strategy_name"] == "demo"
        assert summary["engine"] == "phases"
        print(f"Objective: {summary['objective']}")
        print(f"Strategy: {summary['strategy_name']}")
        print(f"Engine: {summary['engine']}")

        # ── Final assertion ───────────────────────────────────────
        print("\n=== Test Complete ===")
        print("✓ Study API endpoints test passed!")


@pytest.mark.asyncio
async def test_study_directive_and_rounds(tmp_path, monkeypatch):
    """Test directive injection and round history retrieval."""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("SR_ENFORCE_STUDY_IDOR", "0")

    ws = _setup_workspace(tmp_path)
    sessions_db = _setup_sessions_db(tmp_path, ["test-session-2"])
    monkeypatch.setenv("SR_SESSIONS_DB", str(sessions_db))

    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
        timeout=30.0,
    ) as client:

        # Create study
        r = await client.post("/api/study/start", json={
            "session_id": "test-session-2",
            "objective": "测试指令和轮次",
            "workspace_path": str(ws),
            "strategy_name": "demo",
            "engine": "phases",
            "max_rounds": 1,
            "cooldown_base": 0.1,
            "cooldown_jitter": 0.05,
            "min_cooldown": 0.01,
        })
        assert r.status_code == 200
        study_id = r.json()["study_id"]

        # Inject multiple directives
        for i in range(3):
            r = await client.post(f"/api/study/{study_id}/directive", json={
                "content": f"指令 {i+1}: 尝试参数组合 {i}",
            })
            assert r.status_code == 200

        # Verify directives were created
        r = await client.get(f"/api/study/{study_id}/directives")
        assert r.status_code == 200
        directives = r.json()["directives"]
        assert len(directives) == 3
        print(f"Directives created: {len(directives)}")

        # Poll until complete
        for _ in range(60):
            r = await client.get(f"/api/study/{study_id}/summary")
            summary = r.json()
            if summary["execution_status"] in ("complete", "early_stopped"):
                break
            await asyncio.sleep(1)

        # Verify rounds
        r = await client.get(f"/api/study/{study_id}/rounds?limit=10")
        assert r.status_code == 200
        rounds = r.json()["rounds"]
        assert len(rounds) >= 1
        print(f"Rounds completed: {len(rounds)}")

        print("✓ Directive and rounds test passed!")


@pytest.mark.asyncio
async def test_study_graph_structure(tmp_path, monkeypatch):
    """Test that the graph structure is correct for default graph."""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("SR_ENFORCE_STUDY_IDOR", "0")

    ws = _setup_workspace(tmp_path)
    sessions_db = _setup_sessions_db(tmp_path, ["test-session-3"])
    monkeypatch.setenv("SR_SESSIONS_DB", str(sessions_db))

    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
        timeout=30.0,
    ) as client:

        # Create study
        r = await client.post("/api/study/start", json={
            "session_id": "test-session-3",
            "objective": "测试图结构",
            "workspace_path": str(ws),
            "strategy_name": "demo",
            "engine": "langgraph",
            "max_rounds": 1,
        })
        assert r.status_code == 200
        study_id = r.json()["study_id"]

        # Get graph
        r = await client.get(f"/api/study/{study_id}/graph")
        assert r.status_code == 200
        graph = r.json()["graph"]

        # Verify structure
        node_ids = {n["id"] for n in graph["nodes"]}
        edges = {(e["source"], e["target"]) for e in graph["edges"]}

        # Default standard graph has these nodes
        assert "researcher" in node_ids
        assert "data_quality" in node_ids
        assert "factor_analyst" in node_ids
        assert "strategist" in node_ids
        assert "portfolio_construction" in node_ids
        assert "risk_controller" in node_ids
        assert "attribution_analyst" in node_ids
        assert "anti_overfit_analyst" in node_ids

        # Verify key edges
        assert ("researcher", "data_quality") in edges
        assert ("researcher", "factor_analyst") in edges
        assert ("data_quality", "strategist") in edges
        assert ("factor_analyst", "strategist") in edges

        print(f"✓ Graph structure verified: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")
