# -*- coding: utf-8 -*-
"""M9-4：reload_user_dotenv 单元测试（此前零直测，只有间接 CLI 测试）。

钉住三点：① override=True 语义（刚写盘的密钥覆盖进程残留旧值——与
R-006 服务路径 override=False 有意不同）；② 读的是 onboard 实际写盘
文件（非 paths.dotenv_path()）；③ best-effort（加载失败静默、幂等）。
"""
from __future__ import annotations

import pytest

from agentnodes.interfaces.cli.commands.llm import reload_user_dotenv


class TestReloadUserDotenv:
    def test_override_true_replaces_stale_env(self, tmp_path, monkeypatch):
        """进程残留旧值不得遮住刚写盘的新密钥（override=True 的核心语义）。"""
        import agentnodes.interfaces.cli.onboard as onboard

        env_file = tmp_path / ".env"
        env_file.write_text("SMOKE_RELOAD_KEY=fresh-value\n", encoding="utf-8")
        monkeypatch.setattr(onboard, "_QUANTNODES_DOTENV_PATH", env_file)
        monkeypatch.setenv("SMOKE_RELOAD_KEY", "stale-value")

        reload_user_dotenv()
        assert __import__("os").environ["SMOKE_RELOAD_KEY"] == "fresh-value"

    def test_reads_onboard_path_not_paths_helper(self, tmp_path, monkeypatch):
        """读取的是 onboard 写盘路径（经 PEP 562 访问器），可被 monkeypatch。"""
        import agentnodes.interfaces.cli.onboard as onboard
        from agentnodes.foundation.config import paths

        custom = tmp_path / "custom.env"
        custom.write_text("SMOKE_RELOAD_PATH=from-custom\n", encoding="utf-8")
        # 故意让 paths.dotenv_path() 指向别处——重载必须跟 onboard 走
        monkeypatch.setattr(paths, "dotenv_path", lambda *a, **kw: tmp_path / "wrong.env")
        monkeypatch.setattr(onboard, "_QUANTNODES_DOTENV_PATH", custom)

        reload_user_dotenv()
        assert __import__("os").environ["SMOKE_RELOAD_PATH"] == "from-custom"

    def test_best_effort_on_missing_dotenv_module(self, monkeypatch):
        """python-dotenv 不可用时静默跳过（旧三处 try/except 语义）。"""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "dotenv":
                raise ImportError("no dotenv")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        reload_user_dotenv()  # 不抛

    def test_idempotent(self, tmp_path, monkeypatch):
        """重复调用安全（后一次覆盖前一次，不累积错误）。"""
        import os

        import agentnodes.interfaces.cli.onboard as onboard

        env_file = tmp_path / ".env"
        env_file.write_text("SMOKE_IDEMPOTENT=once\n", encoding="utf-8")
        monkeypatch.setattr(onboard, "_QUANTNODES_DOTENV_PATH", env_file)

        reload_user_dotenv()
        reload_user_dotenv()
        assert os.environ["SMOKE_IDEMPOTENT"] == "once"
