"""Tests for cli/commands/server.py — serve commands.

验证：
- cmd_webui_serve 不会立即崩溃，调用 uvicorn.run() 时使用正确的参数
- cmd_api_serve 类似
- cmd_mcp_list_tools 列出已注册工具
- LLM 配置状态在 banner 中正确反映
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ────────────────────────── cmd_webui_serve ──────────────────────────


class TestWebUIServe:
    """`cmd_webui_serve` — top-level `serve` 命令核心逻辑。

    实际启动 uvicorn.run() 会阻塞，所以 mock 掉。
    """

    @pytest.fixture
    def mock_uvicorn(self):
        with patch("uvicorn.run") as m:
            yield m

    @pytest.fixture
    def mock_app_factory(self):
        """mock create_app + configure_from_env.

        A third patch targeted ``agentnodes.webui.routes.router``,
        but that module was deleted in the W3 structure cleanup (see
        api/app.py:240) — the webui router is mounted inside create_app()
        now, and the mocked ``router`` was never asserted. It only kept
        working because serial collection order let some other test make
        the attribute resolvable; under xdist it raised AttributeError.
        """
        with patch("agentnodes.interfaces.api.app.create_app") as create, \
             patch("agentnodes.interfaces.api.app.configure_from_env") as config:
            fake_app = MagicMock()
            create.return_value = fake_app
            config.return_value = {
                "static_dir": "/tmp/static",
                "cors_origins": ["*"],
            }
            yield create, config, fake_app

    def test_runs_uvicorn_with_args(self, mock_uvicorn, mock_app_factory):
        """uvicorn.run() 被调用时 host/port 来自 args。"""
        from agentnodes.interfaces.cli.commands.server import cmd_webui_serve

        create, config, fake_app = mock_app_factory
        args = argparse.Namespace(
            host="0.0.0.0",
            port=9999,
            reload=True,
            workspace="/tmp/ws",
            static_dir=None,
            goal_db=None,
            hypotheses_path=None,
        )

        rc = cmd_webui_serve(args)

        assert rc == 0
        mock_uvicorn.assert_called_once()
        kwargs = mock_uvicorn.call_args.kwargs
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == 9999
        assert kwargs["reload"] is True

    def test_mounts_webui_router(self, mock_uvicorn, mock_app_factory):
        """webui router is now mounted inside create_app() (survives --reload)."""
        from agentnodes.interfaces.cli.commands.server import cmd_webui_serve

        create, config, fake_app = mock_app_factory
        args = argparse.Namespace(
            host="127.0.0.1", port=8766, reload=False,
            workspace=".", static_dir=None,
            goal_db=None, hypotheses_path=None,
        )

        cmd_webui_serve(args)

        # webui router is mounted inside create_app, so just verify it was called
        create.assert_called_once()

    def test_static_dir_from_env(self, mock_uvicorn, mock_app_factory):
        """无 args.static_dir 时从 env_config 取。"""
        from agentnodes.interfaces.cli.commands.server import cmd_webui_serve

        create, config, fake_app = mock_app_factory
        config.return_value = {"static_dir": "/env/static", "cors_origins": ["*"]}
        args = argparse.Namespace(
            host="127.0.0.1", port=8766, reload=False,
            workspace=".", static_dir=None,
            goal_db=None, hypotheses_path=None,
        )

        cmd_webui_serve(args)

        create.assert_called_once()
        kwargs = create.call_args.kwargs
        assert kwargs["static_dir"] == "/env/static"

    def test_static_dir_overrides_env(self, mock_uvicorn, mock_app_factory):
        """args.static_dir 优先级高于 env。"""
        from agentnodes.interfaces.cli.commands.server import cmd_webui_serve

        create, config, fake_app = mock_app_factory
        config.return_value = {"static_dir": "/env/static", "cors_origins": ["*"]}
        args = argparse.Namespace(
            host="127.0.0.1", port=8766, reload=False,
            workspace=".", static_dir="/cli/static",
            goal_db=None, hypotheses_path=None,
        )

        cmd_webui_serve(args)

        kwargs = create.call_args.kwargs
        assert kwargs["static_dir"] == "/cli/static"

    def test_banner_shows_configured_llm(self, mock_uvicorn, mock_app_factory, capsys, tmp_path, monkeypatch):
        """已配置 LLM 时 banner 显示 ✓。"""
        from agentnodes.interfaces.cli import llm_config_check
        from agentnodes.interfaces.cli.commands.server import cmd_webui_serve

        create, config, fake_app = mock_app_factory
        qdir = tmp_path / ".quantnodes"
        qdir.mkdir()
        monkeypatch.setattr(llm_config_check, "QUANTNODES_DIR", qdir)
        monkeypatch.setattr(llm_config_check, "LLM_JSON_PATH", qdir / "llm.json")
        monkeypatch.setattr(llm_config_check, "DOTENV_PATH", qdir / ".env")
        for k in ("OPENAI_API_KEY", "LLM_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        (qdir / "llm.json").write_text(
            json.dumps({"llm": {"provider": "openai", "model": "gpt-4", "api_key": "k"}}),
            encoding="utf-8",
        )

        args = argparse.Namespace(
            host="127.0.0.1", port=8766, reload=False,
            workspace=".", static_dir=None,
            goal_db=None, hypotheses_path=None,
        )

        cmd_webui_serve(args)

        out = capsys.readouterr().out
        assert "openai" in out
        assert "gpt-4" in out
        assert "✓" in out

    def test_banner_warns_when_unconfigured(self, mock_uvicorn, mock_app_factory, capsys, monkeypatch):
        """未配置 LLM 时 banner 显示 ⚠。"""
        from agentnodes.interfaces.cli import llm_config_check
        from agentnodes.interfaces.cli.commands.server import cmd_webui_serve

        create, config, fake_app = mock_app_factory
        # 重定向到一个不存在的目录
        monkeypatch.setattr(llm_config_check, "QUANTNODES_DIR", Path("/nonexistent"))
        monkeypatch.setattr(llm_config_check, "LLM_JSON_PATH", Path("/nonexistent/llm.json"))
        monkeypatch.setattr(llm_config_check, "DOTENV_PATH", Path("/nonexistent/.env"))
        for k in ("OPENAI_API_KEY", "LLM_API_KEY"):
            monkeypatch.delenv(k, raising=False)

        args = argparse.Namespace(
            host="127.0.0.1", port=8766, reload=False,
            workspace=".", static_dir=None,
            goal_db=None, hypotheses_path=None,
        )

        cmd_webui_serve(args)

        out = capsys.readouterr().out
        assert "⚠" in out or "LLM 配置未完成" in out


# ────────────────────────── cmd_api_serve ──────────────────────────


class TestAPIServe:
    @pytest.fixture
    def mock_uvicorn(self):
        with patch("uvicorn.run") as m:
            yield m

    def test_runs_uvicorn(self, mock_uvicorn):
        from agentnodes.interfaces.cli.commands.server import cmd_api_serve

        args = argparse.Namespace(
            host="0.0.0.0",
            port=8765,
            reload=False,
            workspace=".",
            goal_db=None,
            hypotheses_path=None,
        )

        rc = cmd_api_serve(args)

        assert rc == 0
        mock_uvicorn.assert_called_once()
        kwargs = mock_uvicorn.call_args.kwargs
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == 8765


class TestFrontendDevServe:
    def test_runs_frontend_with_backend_proxy_and_stops_backend_on_failure(self, monkeypatch, capsys):
        from agentnodes.interfaces.cli import _build_parser
        from agentnodes.interfaces.cli.commands import merged

        parser, _ = _build_parser()
        args = parser.parse_args([
            "serve", "--frontend-dev", "--port", "9876",
            "--frontend-port", "5174", "--an-workspace", ".",
        ])
        backend = MagicMock()
        backend.poll.return_value = None
        frontend = MagicMock()
        frontend.poll.return_value = 2
        popen = MagicMock(side_effect=[backend, frontend])
        monkeypatch.setattr(merged.subprocess, "Popen", popen)
        monkeypatch.setattr(merged.shutil, "which", lambda name: "node")
        monkeypatch.setattr(merged.Path, "is_file", lambda self: True)

        assert merged.dispatch_serve(args) != 0

        backend_args = popen.call_args_list[0].args[0]
        frontend_call = popen.call_args_list[1]
        assert backend_args[:4] == [merged.sys.executable, "-m", "agentnodes.interfaces.cli", "serve"]
        assert "--frontend-dev" not in backend_args
        assert frontend_call.kwargs["env"]["API_TARGET"] == "http://127.0.0.1:9876"
        assert "--strictPort" in frontend_call.args[0]
        assert "5174" in frontend_call.args[0]
        backend.terminate.assert_called_once()
        backend.wait.assert_called_once()
        assert "退出" in capsys.readouterr().err

    @pytest.mark.parametrize("conflict", ["--api-only", "--reload"])
    def test_rejects_incompatible_flags(self, conflict, capsys):
        from agentnodes.interfaces.cli import _build_parser
        from agentnodes.interfaces.cli.commands.merged import dispatch_serve

        parser, _ = _build_parser()
        args = parser.parse_args(["serve", "--frontend-dev", conflict])
        assert dispatch_serve(args) != 0
        assert conflict in capsys.readouterr().err

    def test_missing_node_is_reported_without_starting_backend(self, monkeypatch, capsys):
        from agentnodes.interfaces.cli import _build_parser
        from agentnodes.interfaces.cli.commands import merged

        parser, _ = _build_parser()
        args = parser.parse_args(["serve", "--frontend-dev"])
        popen = MagicMock()
        monkeypatch.setattr(merged.subprocess, "Popen", popen)
        monkeypatch.setattr(merged.shutil, "which", lambda name: None)
        monkeypatch.setattr(merged.Path, "is_file", lambda self: True)

        assert merged.dispatch_serve(args) != 0
        popen.assert_not_called()
        assert "Node" in capsys.readouterr().err

    def test_interrupt_stops_both_started_processes(self, monkeypatch):
        from agentnodes.interfaces.cli import _build_parser
        from agentnodes.interfaces.cli.commands import merged

        parser, _ = _build_parser()
        args = parser.parse_args(["serve", "--frontend-dev"])
        backend, frontend = MagicMock(), MagicMock()
        backend.poll.return_value = None
        frontend.poll.return_value = None
        monkeypatch.setattr(merged.subprocess, "Popen", MagicMock(side_effect=[backend, frontend]))
        monkeypatch.setattr(merged.shutil, "which", lambda name: "node")
        monkeypatch.setattr(merged.Path, "is_file", lambda self: True)
        monkeypatch.setattr(merged.time, "sleep", MagicMock(side_effect=KeyboardInterrupt))

        assert merged.dispatch_serve(args) == 0
        backend.terminate.assert_called_once()
        frontend.terminate.assert_called_once()

    def test_interrupt_reaps_real_child_processes(self, monkeypatch):
        import subprocess
        import sys

        from agentnodes.interfaces.cli import _build_parser
        from agentnodes.interfaces.cli.commands import merged

        parser, _ = _build_parser()
        args = parser.parse_args(["serve", "--frontend-dev"])
        original_popen = subprocess.Popen
        children = []

        def start_child(*args, **kwargs):
            child = original_popen([sys.executable, "-c", "import time; time.sleep(15)"])
            children.append(child)
            return child

        monkeypatch.setattr(merged.subprocess, "Popen", start_child)
        monkeypatch.setattr(merged.shutil, "which", lambda name: "node")
        monkeypatch.setattr(merged.Path, "is_file", lambda self: True)
        monkeypatch.setattr(merged.time, "sleep", MagicMock(side_effect=KeyboardInterrupt))

        try:
            assert merged.dispatch_serve(args) == 0
            assert len(children) == 2
            assert all(child.poll() is not None for child in children)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait()

    def test_frontend_start_error_stops_backend(self, monkeypatch, capsys):
        from agentnodes.interfaces.cli import _build_parser
        from agentnodes.interfaces.cli.commands import merged

        parser, _ = _build_parser()
        args = parser.parse_args(["serve", "--frontend-dev"])
        backend = MagicMock()
        backend.poll.return_value = None
        monkeypatch.setattr(merged.subprocess, "Popen", MagicMock(side_effect=[backend, OSError("spawn failed")]))
        monkeypatch.setattr(merged.shutil, "which", lambda name: "node")
        monkeypatch.setattr(merged.Path, "is_file", lambda self: True)

        assert merged.dispatch_serve(args) != 0
        backend.terminate.assert_called_once()
        assert "spawn failed" in capsys.readouterr().err

    def test_rejects_same_frontend_and_backend_port(self, capsys):
        from agentnodes.interfaces.cli import _build_parser
        from agentnodes.interfaces.cli.commands.merged import dispatch_serve

        parser, _ = _build_parser()
        args = parser.parse_args(["serve", "--frontend-dev", "--frontend-port", "8766"])
        assert dispatch_serve(args) != 0
        assert "相同端口" in capsys.readouterr().err


# ────────────────────────── cmd_mcp_list_tools ──────────────────────────


class TestMCPListTools:
    def test_lists_tools(self, capsys):
        """打印已注册的工具列表。"""
        from agentnodes.interfaces.cli.commands.server import cmd_mcp_list_tools

        fake_server = MagicMock()
        fake_server.list_tools.return_value = [
            {
                "name": "calc_alpha",
                "description": "Calculate alpha factor",
                "inputSchema": {"properties": {"x": {"type": "number"}}},
            },
            {
                "name": "noop",
                "description": "no-op",
                "inputSchema": {"properties": {}},
            },
        ]

        with patch("agentnodes.interfaces.mcp.MCPServer", return_value=fake_server):
            args = argparse.Namespace()
            rc = cmd_mcp_list_tools(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "calc_alpha" in out
        assert "noop" in out
        assert "共 2 个" in out

    def test_handles_no_properties(self, capsys):
        """inputSchema 无 properties 时应正常显示 'no params'。"""
        from agentnodes.interfaces.cli.commands.server import cmd_mcp_list_tools

        fake_server = MagicMock()
        fake_server.list_tools.return_value = [
            {
                "name": "empty",
                "description": "no params",
                "inputSchema": {},  # no properties
            },
        ]

        with patch("agentnodes.interfaces.mcp.MCPServer", return_value=fake_server):
            args = argparse.Namespace()
            rc = cmd_mcp_list_tools(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "no params" in out
