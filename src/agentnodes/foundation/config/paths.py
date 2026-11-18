"""默认数据路径的唯一来源（L4），支持品牌 profile 切换。

**为什么需要这个模块**（2026-11-09 路径统一）：

批次 9（2026-11-05）做过一次 ``~/.quantnodes`` → ``~/.agentnodes`` 的迁移，
但只覆盖了**主数据目录**。实测仍有 8 处在用旧品牌，而且分散在
``apps/goal`` / ``interfaces/cli`` / ``foundation/llm`` / ``kernel/gateway``
各处——其中 ``slash_goal.py`` 还把 ``workflow_config.py`` 的路径
**复制了一份**，典型的「改一处漏一处」结构。

**profile 而不是硬编码 brand**：用户明确指出「未来这个项目将会被
quantnodes 使用」。所以 brand 必须是可切换的：

- ``agentnodes``（默认）→ ``~/.agentnodes`` + ``.agentnodes_session.db``
- ``quantnodes``（显式指定）→ ``~/.quantnodes`` + 历史文件名

切换：``AN_DATA_PROFILE=quantnodes``（历史名，仍生效——见
``settings.LEGACY_LIVE_ENVS``）。**默认永远是 agentnodes**——满足
「默认使用 .agentnodes」。

**批 C（2026-11-11）勘误**：本 docstring 曾写"或 CLI ``--data-profile``"，
但 CLI 里从来没有这个 flag（实测 0 处）——删掉不存在的提法。
真要加 profile flag 是新功能，另立批。

**HTTP 无关、零外部依赖**：只 import stdlib，任何层都能用。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_PROFILE",
    "LEGACY_DIRS",
    "PROFILES",
    "DataProfile",
    "active_profile",
    "data_dir",
    "data_subdir",
    "env_override",
    "session_db_filename",
]


@dataclass(frozen=True)
class DataProfile:
    """一套命名约定（home 目录名 + 会话库文件名）。

    Attributes:
        name: profile 标识（``agentnodes`` / ``quantnodes``）。
        home_dir_name: home 下的目录名（含前导点）。
        session_db_name: 会话库文件名（含前导点）。
    """

    name: str
    home_dir_name: str
    session_db_name: str


PROFILES: dict[str, DataProfile] = {
    # 默认：本项目自己的品牌
    "agentnodes": DataProfile(
        name="agentnodes",
        home_dir_name=".agentnodes",
        session_db_name=".agentnodes_session.db",
    ),
    # 未来 quantnodes 集成用：完整保留历史命名，零行为变化
    "quantnodes": DataProfile(
        name="quantnodes",
        home_dir_name=".quantnodes",
        session_db_name=".quantnodes_agentnodes_session.db",
    ),
}

DEFAULT_PROFILE = "agentnodes"

#: 迁移来源（**只读**；`ensure_data_dir` 会在首次使用时做一次性 copytree）。
#: 硬切换后这些目录不再被读取，仅作为历史数据的备份保留。
LEGACY_DIRS: tuple[Path, ...] = (
    Path.home() / ".quantnodes",
    Path.home() / ".quantnodes-research",
)

#: 选择 profile 的环境变量名（规范名）。
PROFILE_ENV = "AGENTNODES_PROFILE"
#: 旧名（批次 B' 前），**仍然生效**——它决定的是"用户数据落在哪"，
#: 不是"配置改名兼容"。见 ``settings.LEGACY_LIVE_ENVS``。
PROFILE_ENV_LEGACY = "AN_DATA_PROFILE"


def active_profile(name: str | None = None) -> DataProfile:
    """返回当前生效的 profile。

    优先级：显式 ``name`` > ``AGENTNODES_PROFILE``（旧名
    ``AN_DATA_PROFILE`` 仍生效）> :data:`DEFAULT_PROFILE`。

    **未知值抛错而不是降级**：用户拼错 ``AGENTNODES_PROFILE=quantnode``
    时必须立刻知道——静默用默认会让「我以为切到 quantnodes 了」的误解
    一直藏着，直到数据写到错误位置。
    """
    if name is not None:
        raw = name
    else:
        # 惰性 import：paths.py 承诺"零外部依赖"（只 import stdlib），
        # settings 是同包（config/）的 stdlib-only 模块，仍走惰性以免导入期成环。
        from agentnodes.foundation.config.settings import get_legacy as _get_legacy

        raw = (os.environ.get(PROFILE_ENV)
               or _get_legacy(PROFILE_ENV_LEGACY, "")
               or "")
    raw = raw.strip()
    if not raw:
        return PROFILES[DEFAULT_PROFILE]
    profile = PROFILES.get(raw)
    if profile is None:
        raise ValueError(
            f"unknown data profile: {raw!r} "
            f"(expected one of {sorted(PROFILES)}; "
            f"set via {PROFILE_ENV} or --data-profile)"
        )
    return profile


def data_dir(profile: DataProfile | str | None = None) -> Path:
    """数据根目录。

    优先级：``AGENTNODES_DATA_DIR`` > profile 的 home 目录名。

    注意它是「整个数据根」的覆盖，所以设了它就与 profile 无关——
    profile 只决定默认值。

    **批 E-2**：旧名 ``AGENTNODES_DATA_DIR`` **不再被读取**（兼容层已删）。
    设了它会被忽略——用 ``agentnodes config audit-legacy`` 可查。
    """
    from agentnodes.foundation.config.settings import get_str as _get_str

    override = (_get_str("AGENTNODES_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    prof = profile if isinstance(profile, DataProfile) else active_profile(profile)
    return Path.home() / prof.home_dir_name


def data_subdir(
    name: str,
    profile: DataProfile | str | None = None,
    *,
    env_var: str | None = None,
) -> Path:
    """数据根下的子目录，可被指定环境变量单独覆盖。

    :param name: 子目录名（如 ``"checkpoints"``）。
    :param profile: 可选 profile 覆盖（默认取 :func:`active_profile`）。
    :param env_var: 该子目录专属的环境变量名（如 ``AGENTNODES_CHECKPOINTS_DIR``）。
        给了且非空时**直接返回它**（绝对路径覆盖，忽略 data_dir）。
    """
    if env_var:
        override = os.environ.get(env_var, "").strip()
        if override:
            return Path(override).expanduser()
    return data_dir(profile) / name


def env_override(var: str, default: Path) -> Path:
    """通用「环境变量覆盖路径」助手。"""
    raw = os.environ.get(var, "").strip()
    return Path(raw).expanduser() if raw else default


def session_db_filename(profile: DataProfile | str | None = None) -> str:
    """会话库文件名（随 profile 变化）。

    默认 ``.agentnodes_session.db``；``AN_DATA_PROFILE=quantnodes`` 时
    回到历史名 ``.quantnodes_agentnodes_session.db``（供 quantnodes 集成）。
    """
    prof = profile if isinstance(profile, DataProfile) else active_profile(profile)
    return prof.session_db_name
