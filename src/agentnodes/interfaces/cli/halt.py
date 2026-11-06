"""Kill-switch / halt sentinel for running operations.

Mirrors ``vibe-trading/src/live/halt.py``. Strategy-research has no live
trading, but the autoresearch / backtest / RSI loops are long-running and
may need to be interrupted cleanly. The HALT sentinel is checked by
long-running agent loops.

**2026-11-06（W4 批次 2）为什么改成落盘**：原实现是纯进程内布尔。
``/halt`` 只能拦住"当前这个进程里正在跑的那个循环"——另起一个 CLI
进程（``agentnodes rsi run``）、或进程重启后，护栏就完全失效。评审把
它列为「D4 护栏失效」：用户以为已经全局停机，实际只是这一个进程停了。

现在的语义：
- 状态落 ``~/.agentnodes/halt.json``（``AGENTNODES_HALT_PATH`` 可覆盖，
  目录用 ``foundation.storage.sqlite.ensure_data_dir`` 解析，不硬编码）
- 进程启动（import）时读取一次
- ``is_halted()`` 顺带检查文件 mtime，**外部 trip 能被看见**（跨进程）；
  检查被节流（``_STAT_INTERVAL_S``），长循环里不产生高频 syscall
- 落盘失败**不阻断**内存态：护栏宁可"只在本进程生效"，也不能因为磁盘
  不可写就完全失效

Public API（签名不变）:

* :data:`HALT` — module-level boolean sentinel（import 时的快照值）。
* :func:`trip_halt` — 置位并落盘。
* :func:`clear_halt` — 清除并删除状态文件。
* :func:`is_halted` — 读当前状态（含跨进程刷新）。
* :func:`require_not_halted` — raise :class:`HaltError` if halted.
* :class:`HaltError` — raised when a protected operation sees a halt.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_HALT_LOCK = threading.Lock()

#: 状态文件的环境变量覆盖（测试 / 多实例隔离用）。
_HALT_PATH_ENV = "AGENTNODES_HALT_PATH"

#: 默认文件名（落在 ``ensure_data_dir()`` 下，即 ``~/.agentnodes/``）。
_HALT_FILENAME = "halt.json"

#: ``is_halted()`` 检查外部状态的节流间隔（秒）。0.5s 足够让"另一个进程
#: 刚按下停止"在人类感知的延迟内生效，又不会让长循环每轮都 stat。
_STAT_INTERVAL_S = 0.5

HALT: bool = False

#: HALT 的原因（trip 时传入；仅用于诊断和 ``/resume`` 提示）。
HALT_REASON: str = ""

#: HALT 置位时间（ISO8601 / epoch 秒，取落盘时的 time.time()）。
HALT_TRIPPED_AT: float = 0.0

#: 上次从磁盘读到的 ``st_mtime_ns``（0 = 还没有文件）。
_last_mtime_ns: int = 0
_last_check_at: float = 0.0


class HaltError(RuntimeError):
    """Raised when a protected operation encounters the HALT sentinel."""


# ── 路径解析 ────────────────────────────────────────────────────────


def halt_path() -> Path:
    """状态文件路径。

    解析顺序：``AGENTNODES_HALT_PATH`` → ``ensure_data_dir()/halt.json``。
    **不硬编码 home 路径**：数据目录的迁移/覆盖逻辑（env、旧目录搬迁）
    全部复用 foundation 的唯一实现。
    """
    override = os.environ.get(_HALT_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from agentnodes.foundation.storage.sqlite import ensure_data_dir

        return ensure_data_dir() / _HALT_FILENAME
    except Exception:  # noqa: BLE001 —— 数据目录不可解析时退回 home
        return Path.home() / ".agentnodes" / _HALT_FILENAME


def _peek_path() -> Path:
    """只读侧的路径解析：**不创建目录、不触发迁移**。

    import 期要读一次磁盘态，而 ``ensure_data_dir()`` 会建目录（首次还会
    搬旧目录）。"import 本模块"不该有这种副作用——测试与工具链都可能
    只是 import 一下却不希望任何写盘。所以读侧直接复刻它的**解析**规则
    （``AN_DATA_DIR`` → ``~/.agentnodes``），不做任何 IO；实际写入时
    （``trip_halt``）才走 ``halt_path()`` 的全量解析。
    """
    override = os.environ.get(_HALT_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from agentnodes.foundation.storage.sqlite import DEFAULT_DATA_DIR

        base = (
            os.environ.get("AN_DATA_DIR", "").strip() or str(DEFAULT_DATA_DIR)
        )
        return Path(base).expanduser() / _HALT_FILENAME
    except Exception:  # noqa: BLE001
        return Path.home() / ".agentnodes" / _HALT_FILENAME


# ── 落盘 ────────────────────────────────────────────────────────────


def _read_state() -> dict | None:
    """读状态文件；不存在/损坏返回 None（损坏按"未置位"处理并记日志）。

    读侧用 ``_peek_path()``：**不建目录、不迁移**。``is_halted()`` 会被
    长循环高频调用，读路径上任何写盘副作用都是不可接受的。

    损坏→未置位是有意的：一个半截写入的 halt.json 如果被当成"已停机"，
    系统会永久卡在停机态且没人知道为什么。反过来（误判为未停机）由
    用户重新按一次 /halt 即可——两害相权。
    """
    path = _peek_path()
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("halt state unreadable (%s): %s", path, exc)
        return None


def _write_state() -> None:
    """把当前内存态写入磁盘（尽力而为）。"""
    path = halt_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "halted": True,
                    "reason": HALT_REASON,
                    "tripped_at": HALT_TRIPPED_AT,
                    "pid": os.getpid(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 —— 落盘失败不阻断内存护栏
        logger.warning("halt state persist failed (%s): %s", path, exc)


def _remove_state() -> None:
    """删除状态文件（不存在时无操作）。"""
    path = halt_path()
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("halt state removal failed (%s): %s", path, exc)


def _apply_disk_state() -> None:
    """把磁盘态同步到内存（调用方需持有 ``_HALT_LOCK``）。

    **只在"观察到变化"时改内存**：``trip_halt`` 落盘失败时内存是 True 而
    磁盘没有文件；若无条件"没文件 → 未停机"，0.5s 后这次 trip 就被自己
    抹掉了（护栏在磁盘不可写的环境下彻底失效）。因此规则是：

    - 文件 mtime 变了（含 有→无） → 按文件内容同步
    - 一直是"没有文件" → 不动内存态（可能是一次没落盘的 trip）
    """
    global HALT, HALT_REASON, HALT_TRIPPED_AT, _last_mtime_ns

    path = _peek_path()
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0

    if mtime_ns == _last_mtime_ns:
        return

    previous = _last_mtime_ns
    _last_mtime_ns = mtime_ns

    if mtime_ns == 0:
        # 文件被删掉了（外部 /resume）→ 同步为未停机。若从来就没有文件
        # （previous == 0），保留内存态——见上方规则。
        if previous != 0:
            HALT = False
            HALT_REASON = ""
            HALT_TRIPPED_AT = 0.0
        return

    raw = _read_state()
    if raw is None:
        # 文件存在但读不出/损坏：保持内存态不变（不擅自解除护栏）。
        return
    HALT = bool(raw.get("halted", True))
    HALT_REASON = str(raw.get("reason") or "")
    try:
        HALT_TRIPPED_AT = float(raw.get("tripped_at") or 0.0)
    except (TypeError, ValueError):
        HALT_TRIPPED_AT = 0.0


# ── 启动时加载 ──────────────────────────────────────────────────────
#
# import 期读取一次：进程重启后仍能看到上一进程留下的停机状态。这是
# "护栏必须活过进程"的最小实现——不需要后台线程，也不需要每次调用
# 都读盘（之后靠 is_halted 的 mtime 节流刷新）。

with _HALT_LOCK:
    _apply_disk_state()
    _last_check_at = time.monotonic()


# ── Public API ──────────────────────────────────────────────────────


def trip_halt(*, reason: str | None = None) -> None:
    """Trip the HALT sentinel and persist it. Idempotent."""
    global HALT, HALT_REASON, HALT_TRIPPED_AT

    with _HALT_LOCK:
        HALT = True
        if reason:
            HALT_REASON = reason
        HALT_TRIPPED_AT = time.time()
        _write_state()
        globals()["_last_mtime_ns"] = _current_mtime_ns()


def clear_halt() -> None:
    """Clear the HALT sentinel and remove the state file. Idempotent.

    **未置位时零副作用**：不建目录、不写文件。测试的 autouse fixture
    每个用例都会调本函数，无条件写盘会让测试污染真实数据目录。
    """
    global HALT, HALT_REASON, HALT_TRIPPED_AT

    with _HALT_LOCK:
        was_clear = not HALT and not HALT_REASON and HALT_TRIPPED_AT == 0.0
        HALT = False
        HALT_REASON = ""
        HALT_TRIPPED_AT = 0.0
        if was_clear and not _peek_path().exists():
            return
        _remove_state()
        globals()["_last_mtime_ns"] = 0


def is_halted() -> bool:
    """True iff HALT has been tripped (by this process or another one).

    跨进程可见性靠 mtime 检查，节流到 ``_STAT_INTERVAL_S`` 一次。
    """
    global _last_check_at

    with _HALT_LOCK:
        now = time.monotonic()
        if now - _last_check_at >= _STAT_INTERVAL_S:
            _last_check_at = now
            _apply_disk_state()
        return HALT


def require_not_halted(*, operation: str = "operation") -> None:
    """Raise :class:`HaltError` if HALT has been tripped."""
    if is_halted():
        detail = f" (reason: {HALT_REASON})" if HALT_REASON else ""
        raise HaltError(f"{operation} aborted: kill switch is active{detail}")


def halt_status() -> dict:
    """诊断快照（``agentnodes rsi`` / 状态命令用）。"""
    halted = is_halted()
    return {
        "halted": halted,
        "reason": HALT_REASON,
        "tripped_at": HALT_TRIPPED_AT,
        "path": str(halt_path()),
    }


def _current_mtime_ns() -> int:
    try:
        return _peek_path().stat().st_mtime_ns
    except OSError:
        return 0


__all__ = [
    "HALT",
    "HaltError",
    "clear_halt",
    "halt_path",
    "halt_status",
    "is_halted",
    "require_not_halted",
    "trip_halt",
]
