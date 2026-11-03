"""WebUI surface — CLI serve entry only (legacy HTMX /webui removed)."""

from __future__ import annotations


class TestCLI:
    def test_webui_serve_help(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "strategy_research.interfaces.cli", "webui", "serve", "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0
        assert "--host" in result.stdout
        assert "--port" in result.stdout
