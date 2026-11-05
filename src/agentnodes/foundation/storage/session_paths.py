# -*- coding: utf-8 -*-
"""会话库路径解析（L4）—— 单一事实源。

**为什么在 L4**：路径解析是「文件放哪」的技术决策，与会话语义无关。
放在 ``kernel/agent/memory_manager`` 里会让所有想读这条路径的模块
（EventStore、web_session、CLI）都依赖会话模块，制造不必要的耦合。

**唯一事实源原则（保留原 docstring 的核心告诫）**：历史上
``sessions.db`` 与 ``quantnodes_agentnodes_user.db`` 分裂过——
两个模块各自拼路径，结果读到两个不同的库。所有消费者必须走这里。
"""
from __future__ import annotations

import os
from pathlib import Path

#: 统一会话库文件名（点号前缀 → 文件管理器里隐藏）。
#: 放在工作区目录，EventStore / web_session / SessionStore 读写同一个文件。
SESSION_DB_FILENAME = ".quantnodes_agentnodes_session.db"

#: 向后兼容别名（遗留代码可能引用 DEFAULT_DB_PATH）。
DEFAULT_DB_PATH = Path.home() / ".agentnodes" / SESSION_DB_FILENAME


def resolve_session_db_path() -> Path:
    """Resolve the unified session DB path.

    Priority:
      1. ``AN_SESSIONS_DB`` env var (explicit absolute path override)
      2. ``AN_WORKSPACE_PATH`` env var / ``<filename>``
      3. current working dir / ``<filename>``
      4. ``~/.quantnodes`` / ``<filename>`` (last-resort fallback)

    The parent directory is created if missing. Returns an absolute Path.

    This is the SINGLE source of truth for the session DB location —
    both ``EventStore.resolve_db_path`` and ``web_session._get_db_path``
    route through here so they can never diverge.
    """
    # 1. Explicit override
    env_explicit = os.environ.get("AN_SESSIONS_DB")
    if env_explicit:
        p = Path(env_explicit)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # 2/3. Workspace-relative (AN_WORKSPACE_PATH or cwd)
    workspace_env = os.environ.get("AN_WORKSPACE_PATH")
    workspace = Path(workspace_env) if workspace_env else Path.cwd()
    p = (workspace / SESSION_DB_FILENAME).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def resolve_db_path(override: Path | None = None) -> Path:
    """Resolve the session DB path (used by EventStore / MemoryManager).

    Priority: explicit ``override`` arg > ``resolve_session_db_path()``
    (which itself honors ``AN_SESSIONS_DB`` / ``AN_WORKSPACE_PATH`` /
    cwd / ``~/.quantnodes`` fallback).
    """
    if override is not None:
        return Path(override)
    return resolve_session_db_path()


__all__ = [
    "DEFAULT_DB_PATH",
    "SESSION_DB_FILENAME",
    "resolve_db_path",
    "resolve_session_db_path",
]
