"""用户主目录沙箱（阶段 4 测试隔离）。

问题（实测）：28+ 个模块解析 ``Path.home()``——其中 13 个是 **import 时求值**
的模块级常量（memory_manager.DEFAULT_DB_PATH、memory/fts5.MEMORY_FTS5_DB、
quantnodes_bridge.CONFIG_PATH 等）。任何只重定向 ``HOME`` 的测试在 Windows 上
都拦不住 ``Path.home()``（它读 ``USERPROFILE``/``HOMEDRIVE+HOMEPATH``）——
曾有一个测试长期覆写宿主的 ``~/.quantnodes/jwt_secret``。

方案：conftest **顶部**（任何 agentnodes 模块 import 之前）调用
``apply()``，把整个进程的 home 解析指向会话沙箱。import 时常量、运行时
解析、env 直读三条路全部落在沙箱。

需要真实 home 的测试（live 套件读宿主 llm.json/.env）用 ``restore_real()``
显式豁免——豁免是显式动作，而不是默认。
"""
from __future__ import annotations

import os
from pathlib import Path

# 真实主目录：必须在重定向之前记录。
REAL_HOME = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())

SANDBOX_HOME = Path(__file__).resolve().parent / "_user_home_sandbox"

# env 变量名：Windows Path.home() 读 USERPROFILE（3.8+ 不再看 HOME），
# POSIX 读 HOME；另有代码直读 HOME（model_catalog）。
_HOME_VARS = ("USERPROFILE", "HOME")


def apply() -> None:
    """进程级重定向（幂等）。必须在任何 agentnodes import 之前调用。"""
    SANDBOX_HOME.mkdir(parents=True, exist_ok=True)
    for var in _HOME_VARS:
        os.environ[var] = str(SANDBOX_HOME)


def restore_real(env: dict[str, str | None]) -> None:
    """在 env dict 里把 home 变量恢复为真实值（供 live 夹具豁免用）。

    用法（live fixture 内）::

        mp = pytest.MonkeyPatch()
        restore_real(mp.__dict__ ...)  # 或手工：
        for k, v in real_home_env().items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)
    """
    for var in _HOME_VARS:
        env[var] = str(REAL_HOME)


def sandboxed() -> bool:
    """当前进程 home 是否已指向沙箱。"""
    return os.environ.get("USERPROFILE") == str(SANDBOX_HOME)
