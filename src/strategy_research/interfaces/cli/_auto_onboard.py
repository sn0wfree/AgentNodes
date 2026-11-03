"""Auto-trigger onboarding wizard on first-launch (TTY only).

Mirrors ``vibe-trading/cli/main.py:268 _maybe_run_onboarding``. Provides:

* :data:`_QUANTNODES_LLM_JSON_PATH` — canonical LLM config location
  (``~/.quantnodes/llm.json``). The wizard writes here.
* :data:`_QUANTNODES_DOTENV_PATH` — canonical token store
  (``~/.quantnodes/.env``). Tokens (LLM_API_KEY, TUSHARE_TOKEN) live here.
* :data:`_PROJECT_DOTENV_PATH` — package install dir fallback
  (``<pkg_root>/.env``).
* :data:`_CWD_DOTENV_PATH` — current working directory fallback.
* :func:`_first_existing_dotenv_path` — return the first candidate that
  actually exists, or ``None``.
* :func:`_migrate_legacy_env` — one-shot copy of legacy dotenv file
  into the new ``~/.quantnodes/.env`` location; legacy files left
  intact for inspection.
* :func:`_maybe_run_onboarding` — if no candidate ``.env`` exists and the
  session is interactive, run :func:`run_onboarding`. Returns ``True`` if
  startup should continue, ``False`` if the user cancelled.

Public API:

* :func:`_maybe_run_onboarding` — single entry point used by
  ``cli.interactive.main`` and the binary's top-level ``main`` before
  any TTY prompt is shown.
"""

from __future__ import annotations

import strategy_research  # noqa: F401  (路径锚点)

import shutil
import sys
from pathlib import Path

from strategy_research.interfaces.cli.onboard import (
    _QUANTNODES_DOTENV_PATH,
    _QUANTNODES_LLM_JSON_PATH,
    run_onboarding,
)

# Two additional candidates per vibe-trading/cli/main.py:100-102.
_PROJECT_DOTENV_PATH = Path(strategy_research.__file__).parent.parent / ".env"
_CWD_DOTENV_PATH = Path.cwd() / ".env"


def _first_existing_dotenv_path() -> Path | None:
    """Return the first ``.env`` candidate that exists, or ``None``.

    Order matches vibe-trading: ``HOME`` first (privacy-of-credentials
    preference), then project-local, then cwd.
    """
    for path in (_QUANTNODES_DOTENV_PATH, _PROJECT_DOTENV_PATH, _CWD_DOTENV_PATH):
        if path.exists():
            return path
    return None


def _migrate_legacy_env() -> None:
    """Silently copy legacy dotenv files into the new location.

    Idempotent. Skips when the legacy file is missing, when the new file
    already exists, or when the copy fails for any reason. Leaves the
    legacy file intact so the user can diff / mv / diff+rm at leisure.

    Legacy paths handled (older this-project names, ordered newest→oldest):

    * ``~/.strategy-research/.env`` (pre-rebrand)
    * ``~/.quantnodes/strategy_research/.env`` (post-rebrand v0.4.x)
    """
    legacy_candidates = (
        Path.home() / ".strategy-research" / ".env",
        Path.home() / ".quantnodes" / "strategy_research" / ".env",
    )
    if _QUANTNODES_DOTENV_PATH.exists():
        return  # newer config already wins; do not clobber

    for legacy in legacy_candidates:
        if not legacy.exists():
            continue
        try:
            _QUANTNODES_DOTENV_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, _QUANTNODES_DOTENV_PATH)
            try:
                _QUANTNODES_DOTENV_PATH.chmod(0o600)
            except OSError:
                pass
        except OSError:
            pass
        # Only one legacy file is needed.
        return


def _maybe_run_onboarding(console) -> bool:
    """First-launch wizard — return True to continue, False on cancel.

    Triggers only when **all** of the following hold:

    1. stdin+stdout are both TTYs (so prompt_toolkit can actually draw)
    2. No ``.env`` candidate exists in any of three locations

    The migration step runs unconditionally before the probe so legacy
    users get a silent upgrade.
    """
    _migrate_legacy_env()

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return True  # non-TTY → let the user use --init manually
    if _first_existing_dotenv_path() is not None:
        return True

    written = run_onboarding()
    if written is None:
        return False

    try:
        from dotenv import load_dotenv
        load_dotenv(_QUANTNODES_DOTENV_PATH, override=True)
    except Exception:
        pass
    return True


__all__ = [
    "_QUANTNODES_LLM_JSON_PATH",
    "_QUANTNODES_DOTENV_PATH",
    "_PROJECT_DOTENV_PATH",
    "_CWD_DOTENV_PATH",
    "_first_existing_dotenv_path",
    "_migrate_legacy_env",
    "_maybe_run_onboarding",
]
