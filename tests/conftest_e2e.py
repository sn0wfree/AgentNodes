"""Shared fixtures for Web UI E2E tests.

启动一个真实的后端 (uvicorn) + 真实浏览器 (Playwright Chromium)。
前端构建产物 (webui/static/) 由前端构建产物提供服务。
后端使用 TEST_MODE=1 调用 chat router 脚本化路径,无需真实 LLM。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterator

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "webui" / "static"


# ────────────────────────── Port allocation ──────────────────────────


def _find_free_port() -> int:
    """Bind to port 0 → OS gives a free port, then close. Avoids races."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, timeout: float = 30.0, interval: float = 0.1) -> None:
    """Poll an HTTP endpoint until it responds or times out."""
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1.0)
            if r.status_code < 500:
                return
        except Exception as exc:
            last_err = exc
        time.sleep(interval)
    raise TimeoutError(
        f"Server at {url} did not become ready within {timeout}s. Last error: {last_err}"
    )


# ────────────────────────── Backend fixture ──────────────────────────


@pytest.fixture(scope="session")
def built_frontend() -> Path:
    """确保 webui/static/ 存在且含 E2E hooks (前端构建产物)。

    The E2E build must be produced with VITE_E2E=1 so main.tsx exposes
    ``window.__sessionStore`` etc. (gated by ``import.meta.env.VITE_E2E``).
    If the existing static dir lacks the hooks, rebuild with the flag.
    """
    def _has_e2e_hooks() -> bool:
        if not STATIC_DIR.exists():
            return False
        assets = STATIC_DIR / "assets"
        if not assets.is_dir():
            return False
        for js in assets.glob("*.js"):
            if b"__sessionStore" in js.read_bytes():
                return True
        return False

    if not _has_e2e_hooks():
        # Auto-build if missing or stale — saves a manual step in CI.
        print(f"\n[conftest] Building frontend (VITE_E2E=1) → {STATIC_DIR}...")
        subprocess.run(
            ["npm", "run", "build"],
            cwd=str(REPO_ROOT / "webui" / "frontend"),
            check=True,
            capture_output=True,
            env={**os.environ, "VITE_E2E": "1"},
        )
    assert STATIC_DIR.exists(), f"Frontend build missing: {STATIC_DIR}"
    assert (STATIC_DIR / "index.html").exists(), f"index.html missing in {STATIC_DIR}"
    assert _has_e2e_hooks(), "Frontend build lacks E2E hooks (VITE_E2E=1)"
    return STATIC_DIR


@pytest.fixture(scope="session")
def backend_server(built_frontend: Path) -> Iterator[dict]:
    """启动一个真实的 uvicorn 后端进程 (TEST_MODE=1)。

    Returns dict with:
      - base_url: e.g. http://127.0.0.1:18765
      - session: requests.Session() with default headers
    """
    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    # Isolated workspace: the backend would otherwise use the repo's
    # real session DB (cwd fallback), polluting tests with history rows.
    import tempfile
    ws = Path(tempfile.mkdtemp(prefix="e2e_workspace_"))

    env = os.environ.copy()
    env.update({
        "STRATEGY_RESEARCH_TEST_CHAT": "1",
        "STATIC_DIR": str(built_frontend),
        "CORS_ORIGINS": "*",
        "PYTHONUNBUFFERED": "1",
        "SR_WORKSPACE_PATH": str(ws),
        # E2E exercises the register → login → chat flow; registration is
        # disabled by default in production (auth.py opt-in switch).
        "SR_ALLOW_REGISTRATION": "1",
    })

    # Use the package's CLI entry point so we hit the same code path users would.
    cmd = [
        sys.executable, "-u", "-m", "uvicorn",
        "agentnodes.interfaces.api.app:create_app",
        "--factory",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "warning",
    ]

    print(f"\n[conftest] Starting backend: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    # P0 同款修复（2026-10-31）：PIPE 不排水会在缓冲填满后冻死服务端
    # 事件循环（logging 写 stdout 阻塞）。后台线程持续排水。
    def _drain() -> None:
        try:
            for _line in proc.stdout:  # type: ignore[union-attr]
                pass  # 丢弃（诊断时可改为收集）
        except Exception:
            pass

    threading.Thread(target=_drain, daemon=True).start()

    try:
        _wait_for_http(f"{base_url}/health", timeout=30.0)
        print(f"[conftest] Backend ready at {base_url}")

        yield {
            "base_url": base_url,
            "proc": proc,
        }
    finally:
        print("[conftest] Stopping backend...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


@pytest.fixture
def api_client(backend_server: dict) -> requests.Session:
    """Plain HTTP client (no auth) for backend API tests."""
    session = requests.Session()
    session.base_url = backend_server["base_url"]  # type: ignore[attr-defined]
    return session


@pytest.fixture(scope="session")
def browser():
    """共享 Playwright Chromium browser fixture (供 E2E + 视觉回归测试使用)。

    注意：scope="session" 让所有测试复用同一浏览器实例，加速测试。
    每个测试函数仍应该用 ``context`` fixture 创建独立 BrowserContext
    以避免 cookies / localStorage / viewport 互相污染。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def app_url(backend_server: dict) -> str:
    """Backend URL — named `app_url` to avoid pytest-base-url collision."""
    return backend_server["base_url"]
