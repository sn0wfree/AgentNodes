# -*- coding: utf-8 -*-
"""导航侧边框调整（R-014 sidebar-navigation-redesign）：入口撤销但 API 保留。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webui" / "frontend" / "src"))


def test_dag_frontend_entries_removed():
    """DAG 编辑器的 5 处前端入口全部撤销（路由/API 仍保留）。"""
    src = ROOT / "webui" / "frontend" / "src"
    icon_nav = (src / "components/layout/IconNav.tsx").read_text(encoding="utf-8")
    assert "'/dag'" not in icon_nav and "编排" not in icon_nav
    monitor = (src / "pages/MonitorPage.tsx").read_text(encoding="utf-8")
    assert 'to="/dag"' not in monitor
    palette = (src / "components/common/CommandPalette.tsx").read_text(encoding="utf-8")
    assert "navigate('/dag')" not in palette
    shortcuts = (src / "hooks/useKeyboardShortcuts.ts").read_text(encoding="utf-8")
    assert "navigate('/dag')" not in shortcuts


def test_trace_frontend_entry_present():
    """Trace 独立侧栏入口与页面存在。"""
    src = ROOT / "webui" / "frontend" / "src"
    icon_nav = (src / "components/layout/IconNav.tsx").read_text(encoding="utf-8")
    assert "'/trace'" in icon_nav and "Trace" in icon_nav
    assert (src / "pages/TracePage.tsx").is_file()
    app = (src / "App.tsx").read_text(encoding="utf-8")
    assert 'path="/trace"' in app


def test_dag_api_still_mounted():
    """DAG 后端契约仍在（24 条路由由 app.py 挂载），隐藏 UI 不删 API。"""
    app = (ROOT / "src/agentnodes/interfaces/api/app.py").read_text(encoding="utf-8")
    assert 'workflow.router, prefix="/api/goal/workflow"' in app
    wf = (ROOT / "src/agentnodes/interfaces/api/routers/workflow.py").read_text(encoding="utf-8")
    for route in ["/start", "/status", "/pause", "/resume", "/definitions",
                  "/approve", "/orchestrate/session", "/run/{run_id}/status"]:
        assert f'"{route}"' in wf
