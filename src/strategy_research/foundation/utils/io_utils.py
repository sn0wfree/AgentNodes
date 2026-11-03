"""Shared filesystem helpers: atomic writes + dotenv parsing.

Single implementation of the mkstemp → write → os.replace → chmod 0600
pattern that was previously duplicated across cli/commands/llm.py,
core/llm/config_audit.py, core/goal/workflow_config.py and
core/scheduled_research/store.py (and the .env line parser in
config_audit.py / cli/commands/llm.py).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(
    path: Path,
    content: str,
    *,
    mode: int = 0o600,
    fsync: bool = False,
) -> Path:
    """Atomically write text to ``path`` (temp file + os.replace).

    Args:
        path: Destination path (parent dir is created).
        content: Text payload.
        mode: Permissions applied after replace (best-effort).
        fsync: fsync before replace (durability; used for job stores).

    Returns:
        The destination path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp_name, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def atomic_write_json(path: Path, data: dict[str, Any], *, mode: int = 0o600) -> Path:
    """Atomically write ``data`` as pretty JSON (+ newline)."""
    return atomic_write_text(
        path,
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        mode=mode,
    )


def atomic_write_env(path: Path, lines: dict[str, str], *, mode: int = 0o600) -> Path:
    """Atomically write ``KEY=value`` lines (empty values kept as ``KEY=``)."""
    content = "\n".join(f"{k}={v}" for k, v in lines.items()) + "\n"
    return atomic_write_text(path, content, mode=mode)


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file into ``{KEY: value}`` (empty values kept).

    Skips blank lines and comments; values are NOT quote-stripped
    (callers that need shell-style quoting do their own handling).
    Returns {} on missing/unreadable file — never raises.
    """
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            result[k.strip()] = v
    except OSError:
        pass
    return result
