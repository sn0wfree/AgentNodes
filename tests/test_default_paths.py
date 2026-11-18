"""数据目录统一（2026-11-09）· 默认 `.agentnodes` + profile 可切换。

**背景**：批次 9 只迁移了主数据目录，实测仍有 8 处在用旧品牌
（``description=docs/design/data-dir-unification-20261109.md``）。

**用户的两个决定**：
1. 默认用 ``.agentnodes``；但**保留 quantnodes 兼容模式**
   （"未来这个项目将会被 quantnodes 使用"）。
2. 旧路径数据**硬切换**（不回退读取）。

本文件钉住这两条，外加"profile 拼错必须抛错"（否则切换失效是静默的）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentnodes.foundation.config import paths
from agentnodes.foundation.storage import session_paths


@pytest.fixture
def clean_env(monkeypatch):
    """清掉所有路径相关环境变量，避免宿主环境影响断言。

    **批 E-2**：旧名不再被读取（只有 4 个"仍然生效"的历史名例外），
    所以清单里要清的是**规范名** + 仍然生效的历史名。
    """
    for var in (
        # 规范名
        "AGENTNODES_PROFILE", "AGENTNODES_DATA_DIR", "AGENTNODES_SESSION_DB",
        "AGENTNODES_WORKSPACE", "AGENTNODES_CHECKPOINTS_DIR", "AGENTNODES_WORKFLOWS_DIR",
        "AGENTNODES_MODEL_CATALOG_DIR",
        # 仍然生效的历史名（settings.LEGACY_LIVE_ENVS）
        "AN_DATA_PROFILE",
        "STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR",
        # 历史遗留的 checkpoint / workflows 目录名
        "STRATEGY_RESEARCH_CHECKPOINT_DIR",
        "STRATEGY_RESEARCH_WORKFLOWS_DIR",
    ):
        monkeypatch.delenv(var, raising=False)


# ── 默认 profile ─────────────────────────────────────────────────────


def test_default_profile_is_agentnodes(clean_env):
    """**用户要求**：默认必须用 `.agentnodes`。"""
    prof = paths.active_profile()
    assert prof.name == "agentnodes"
    assert prof.home_dir_name == ".agentnodes"
    assert prof.session_db_name == ".agentnodes_session.db"


def test_default_data_dir_is_under_dot_agentnodes(clean_env):
    assert paths.data_dir() == Path.home() / ".agentnodes"


def test_default_session_db_name(clean_env):
    assert paths.session_db_filename() == ".agentnodes_session.db"


# ── quantnodes 兼容模式 ──────────────────────────────────────────────


def test_quantnodes_profile_restores_legacy_names(clean_env, monkeypatch):
    """**用户要求**：启动参数指定 quantnodes 时可用原始命名。

    这是"未来被 quantnodes 使用"的接入点。
    """
    monkeypatch.setenv("AN_DATA_PROFILE", "quantnodes")
    prof = paths.active_profile()
    assert prof.home_dir_name == ".quantnodes"
    assert prof.session_db_name == ".quantnodes_agentnodes_session.db"
    assert paths.data_dir() == Path.home() / ".quantnodes"


def test_unknown_profile_raises(clean_env, monkeypatch):
    """拼错必须抛错——静默降级会让"以为切了其实没切"一直藏着。"""
    monkeypatch.setenv("AN_DATA_PROFILE", "quantnode")  # 少个 s
    with pytest.raises(ValueError, match="unknown data profile"):
        paths.active_profile()


def test_explicit_profile_arg_overrides_env(clean_env, monkeypatch):
    monkeypatch.setenv("AN_DATA_PROFILE", "quantnodes")
    assert paths.active_profile("agentnodes").name == "agentnodes"


# ── 环境变量覆盖（"除非启动参数中有另外设定"）────────────────────────


def test_data_dir_env_overrides_profile(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTNODES_DATA_DIR", str(tmp_path))
    assert paths.data_dir() == tmp_path


def test_subdir_env_override(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTNODES_CHECKPOINTS_DIR", str(tmp_path / "ckpt"))
    got = paths.data_subdir("checkpoints", env_var="AGENTNODES_CHECKPOINTS_DIR")
    assert got == tmp_path / "ckpt"


def test_subdir_falls_back_to_data_dir(clean_env, monkeypatch):
    got = paths.data_subdir("workflows", env_var="AGENTNODES_WORKFLOWS_DIR")
    assert got == Path.home() / ".agentnodes" / "workflows"


def test_sessions_db_env_override(clean_env, monkeypatch, tmp_path):
    target = tmp_path / "custom.db"
    monkeypatch.setenv("AGENTNODES_SESSION_DB", str(target))
    assert session_paths.resolve_session_db_path() == target


def test_workspace_session_db_uses_profile_name(clean_env, monkeypatch, tmp_path):
    """会话库落在 workspace，文件名必须随 profile。"""
    monkeypatch.setenv("AGENTNODES_WORKSPACE", str(tmp_path))
    got = session_paths.resolve_session_db_path()
    assert got.name == ".agentnodes_session.db", got
    assert got.parent == tmp_path.resolve()


def test_workspace_session_db_quantnodes_profile(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTNODES_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("AN_DATA_PROFILE", "quantnodes")
    got = session_paths.resolve_session_db_path()
    assert got.name == ".quantnodes_agentnodes_session.db", got


# ── 硬切换：不再读旧路径 ─────────────────────────────────────────────


def test_no_quantnodes_research_anywhere(clean_env):
    """所有默认路径都不得落在 `~/.quantnodes-research` 之下。"""
    legacy = Path.home() / ".quantnodes-research"
    for name in ("checkpoints", "workflows", "genome", "platform"):
        got = paths.data_subdir(name)
        assert legacy not in got.parents, f"{name} 仍在旧路径: {got}"
        assert got.is_relative_to(Path.home() / ".agentnodes"), got


def test_legacy_dirs_are_read_only_references(clean_env):
    """LEGACY_DIRS 仅作为迁移来源存在，不被 data_dir 使用。"""
    assert Path.home() / ".quantnodes" in paths.LEGACY_DIRS
    assert Path.home() / ".quantnodes-research" in paths.LEGACY_DIRS
    assert paths.data_dir() not in paths.LEGACY_DIRS


# ── 下游模块确实用了 paths（不是各自硬编码）──────────────────────────


def test_sqlite_uses_paths_module(clean_env):
    from agentnodes.foundation.storage import sqlite

    assert sqlite.DEFAULT_DATA_DIR == paths.data_dir()
    assert sqlite.LEGACY_DATA_DIR == paths.LEGACY_DIRS[0]


def test_goal_checkpoint_dir_uses_paths(clean_env):
    from agentnodes.apps.goal import checkpoint_store

    assert checkpoint_store._DEFAULT_CHECKPOINT_DIR == paths.data_subdir("checkpoints")


def test_goal_workflow_dir_uses_paths(clean_env):
    from agentnodes.apps.goal import workflow_config

    assert workflow_config._USER_WORKFLOWS_DIR == paths.data_subdir("workflows")


def test_slash_goal_delegates_to_paths(clean_env):
    """slash_goal 原先复制了一份路径拼法（重复定义）——现在必须同源。"""
    from agentnodes.apps.goal import workflow_config
    from agentnodes.interfaces.cli.commands import slash_goal

    assert slash_goal._user_workflows_dir() == workflow_config._USER_WORKFLOWS_DIR
    assert slash_goal._checkpoint_base_dir() == paths.data_subdir("checkpoints")
