# -*- coding: utf-8 -*-
"""会话库路径解析（L4）—— 单一事实源。

**为什么在 L4**：路径解析是「文件放哪」的技术决策，与会话语义无关。
放在 ``kernel/session/state/memory_manager`` 里会让所有想读这条路径的模块
（EventStore、web_session、CLI）都依赖会话模块，制造不必要的耦合。

**唯一事实源原则（保留原 docstring 的核心告诫）**：历史上
``sessions.db`` 与 ``quantnodes_agentnodes_user.db`` 分裂过——
两个模块各自拼路径，结果读到两个不同的库。所有消费者必须走这里。
"""
from __future__ import annotations

import os
from pathlib import Path

from agentnodes.foundation.config.paths import active_profile, session_db_filename
from agentnodes.foundation.config import settings as _settings

#: 统一会话库文件名（点号前缀 → 文件管理器里隐藏）。
#: 放在工作区目录，EventStore / web_session / SessionStore 读写同一个文件。
#:
#: **2026-11-09 路径统一**：原名 ``.quantnodes_agentnodes_session.db`` 带旧品牌。
#: 现在随 profile 变化——默认 ``.agentnodes_session.db``；
#: ``AN_DATA_PROFILE=quantnodes`` 时回到历史名（供 quantnodes 集成）。
#: 保留模块级常量是为了不打断既有 ``from ... import SESSION_DB_FILENAME``
#: 的调用方；**新代码请用** :func:`session_db_filename` **或**
#: :func:`resolve_session_db_path`（后者已按 profile 解析）。
SESSION_DB_FILENAME = session_db_filename()

#: 向后兼容别名（遗留代码可能引用 DEFAULT_DB_PATH）。
DEFAULT_DB_PATH = Path.home() / active_profile().home_dir_name / SESSION_DB_FILENAME


def resolve_session_db_path() -> Path:
    """Resolve the unified session DB path.

    Priority:
      1. ``AGENTNODES_SESSION_DB`` env var (explicit absolute path override)
      2. ``AGENTNODES_WORKSPACE`` env var / ``<filename>``
      3. current working dir / ``<filename>``
      4. ``~/.agentnodes`` 回退（profile 决定目录名与文件名）

    The parent directory is created if missing. Returns an absolute Path.

    This is the SINGLE source of truth for the session DB location —
    both ``EventStore.resolve_db_path`` and ``web_session._get_db_path``
    route through here so they can never diverge.

    **2026-11-09**：文件名改为**每调用解析**（不再读模块级常量），
    这样 ``AN_DATA_PROFILE`` 在进程启动后设置也能生效，
    且不会在 import 期因 env 未设而固化默认值。
    """
    # 1. Explicit override
    env_explicit = _settings.get_str("AGENTNODES_SESSION_DB")
    if env_explicit:
        p = Path(env_explicit)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    filename = session_db_filename()

    # 2/3. Workspace-relative (AGENTNODES_WORKSPACE or cwd)
    workspace_env = _settings.get_str("AGENTNODES_WORKSPACE")
    workspace = Path(workspace_env) if workspace_env else Path.cwd()
    p = (workspace / filename).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def resolve_db_path(override: Path | None = None) -> Path:
    """Resolve the session DB path (used by EventStore / MemoryManager).

    Priority: explicit ``override`` arg > ``resolve_session_db_path()``
    (which itself honors ``AGENTNODES_SESSION_DB`` / ``AGENTNODES_WORKSPACE`` /
    cwd / ``~/.agentnodes`` fallback).
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
