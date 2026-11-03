r"""统一日志入口 — configure_logging(surface) + dual_log。

设计契约：docs/logging-unified-entry-design-20261102.md §4。

所有进程入口（web/cli/mcp/script）经由 :func:`configure_logging` 装配
日志：策略表决定 console 流定向，级别与 JSON 统一读环境变量，
文件日志由 ``SR_LOG_FILE`` 显式开启（默认关闭，不创建任何文件）。

"双写"在 handler 层完成（借鉴 CodersWheel ``QuickTool/logger.py`` 的
LoggerHelper：FileHandler + StreamHandler 同挂一个 logger，调用点只调
``logger.info`` 一次）；:func:`dual_log` 是 call-site 双写的收敛形态，
仅在「进程从未装配 handler」时以 print 兜底（CLI 默认模式的可见性）。

=========  ==========  =============================================
surface    console 流  理由
=========  ==========  =============================================
web        stderr      uvicorn 常规；stdout 不承诺
cli        stderr      TUI/界面输出占 stdout；默认不装配（严格开关）
mcp        stderr      stdout = JSON-RPC 协议帧，绝不能混日志
script     stdout      子进程输出被 run_bg 重定向进 run.log（喂 log_stall）
=========  ==========  =============================================
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .trace import JsonFormatter, TraceFilter, ensure_trace_filter

# 文件日志默认目录：项目数据根下 logs/（与各 store 的 ~/.quantnodes-research
# 同根；测试经 conftest home 沙箱自动重定向，不触真实目录）。
from strategy_research.core.storage.sqlite import DEFAULT_DATA_DIR

DEFAULT_TEXT_FORMAT = (
    "%(asctime)s %(levelname)-8s %(name)s "
    "[t=%(trace_id)s sid=%(session_id)s st=%(study_id)s r=%(round_num)s] %(message)s"
)
DEFAULT_DATEFMT = "%H:%M:%S"

LOG_DIR_ENV = "SR_LOG_FILE"
LOG_DIR = DEFAULT_DATA_DIR / "logs"
_ROTATE_MAX_BYTES = 10 * 1024 * 1024
_ROTATE_BACKUP_COUNT = 5

# console 流定向策略（值在调用时解析，capsys/uvicorn 可能在 import 后换流）
_STREAMS: dict[str, str] = {
    "web": "stderr",
    "cli": "stderr",
    "mcp": "stderr",
    "script": "stdout",
}

# 本模块挂的 handler 标记：幂等重挂时只移除自己的，不碰外来 handler
# （uvicorn / pytest caplog 的 handler 原样保留）。
_HANDLER_MARK = "_sr_logging_owner"

_dual_logger = logging.getLogger(__name__)


# ── env / param 解析 ─────────────────────────────────────────────────


def resolve_level(level: int | str | None = None) -> int:
    """级别解析：显式参数 > ``SR_LOG_LEVEL`` > INFO（非法名回落 INFO）。"""
    if level is None:
        level = os.environ.get("SR_LOG_LEVEL", "INFO")
    if isinstance(level, str):
        return getattr(logging, level.strip().upper(), logging.INFO)
    return int(level)


def _json_requested(json_output: bool | None) -> bool:
    if json_output is not None:
        return json_output
    return os.environ.get("SR_LOG_JSON", "").strip().lower() in ("1", "true")


def _resolve_log_file(log_file: str | Path | None) -> Path | None:
    """文件日志路径：显式参数 > ``SR_LOG_FILE`` > 关闭。

    绝对路径原样使用；相对路径/裸文件名落到 ``<数据根>/logs/`` 下。
    返回 ``None`` 表示不启用文件日志（默认）。
    """
    raw = log_file if log_file is not None else os.environ.get(LOG_DIR_ENV, "")
    if not raw or not str(raw).strip():
        return None
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = LOG_DIR / path
    return path


class _TraceTextFormatter(logging.Formatter):
    """Text formatter tolerant of records that skipped ``TraceFilter``.

    防御层：filter 缺席的 handler（configure 之后才挂上的，如测试基建）
    产生的记录没有 trace 属性，裸 ``%(trace_id)s`` 会 KeyError 丢整条
    日志——这里补默认值（与 TraceFilter 的「缺失显示 -」一致）。
    """

    _FIELDS = ("trace_id", "session_id", "study_id", "round_num")

    def format(self, record: logging.LogRecord) -> str:
        for name in self._FIELDS:
            if not hasattr(record, name):
                setattr(record, name, "-")
        return super().format(record)


def _make_formatter(json_output: bool) -> logging.Formatter:
    if json_output:
        return JsonFormatter(datefmt=DEFAULT_DATEFMT)
    return _TraceTextFormatter(DEFAULT_TEXT_FORMAT, datefmt=DEFAULT_DATEFMT)


def _drop_owned_handlers(root: logging.Logger) -> None:
    for h in list(root.handlers):
        if getattr(h, _HANDLER_MARK, False):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:  # noqa: BLE001 — console/file handler close 不阻断
                pass


# ── 统一入口 ─────────────────────────────────────────────────────────


def configure_logging(
    surface: str,
    *,
    level: int | str | None = None,
    json_output: bool | None = None,
    log_file: str | Path | None = None,
    force: bool = False,
    stream: Any | None = None,
) -> None:
    """装配一个进程面的日志（console + 可选文件），幂等可重入。

    Args:
        surface: ``web`` / ``cli`` / ``mcp`` / ``script``（策略表见模块头）。
        level: 显式级别（int 或名字）；缺省读 ``SR_LOG_LEVEL``，再缺 INFO。
        json_output: 显式 JSON 开关；缺省读 ``SR_LOG_JSON``。
        log_file: 显式文件路径；缺省读 ``SR_LOG_FILE``；均未设 = 不落文件。
        force: cli 严格模式下强制装配（默认：未设任何开关则不动，
            保持 TUI 输出与现状逐字节一致）。
        stream: 覆写 console 流（测试用）；缺省按策略表取 sys.stdout/stderr。
    """
    if surface not in _STREAMS:
        raise ValueError(
            f"unknown logging surface {surface!r}; expected one of {sorted(_STREAMS)}"
        )

    want_json = _json_requested(json_output)
    file_path = _resolve_log_file(log_file)

    # cli 严格开关语义：未设 SR_LOG_JSON / SR_LOG_FILE 且未显式 force ——
    # 完全不装配（523 处 logger.info 不得刷穿 TUI）。
    if surface == "cli" and not force and not want_json and file_path is None:
        return

    resolved_level = resolve_level(level)
    formatter = _make_formatter(want_json)

    root = logging.getLogger()
    root.setLevel(resolved_level)
    logging.getLogger("strategy_research").setLevel(resolved_level)
    _drop_owned_handlers(root)

    if stream is None:
        stream = getattr(sys, _STREAMS[surface])
    console = logging.StreamHandler(stream)
    console.setFormatter(formatter)
    setattr(console, _HANDLER_MARK, True)
    root.addHandler(console)

    if file_path is not None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler: logging.Handler = RotatingFileHandler(
            file_path,
            maxBytes=_ROTATE_MAX_BYTES,
            backupCount=_ROTATE_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        setattr(file_handler, _HANDLER_MARK, True)
        root.addHandler(file_handler)

    # TraceFilter 挂 handler（defect #17）：覆盖全部 root handler——含
    # 外来 handler（uvicorn 等），让它们的输出也带 trace 字段。
    ensure_trace_filter(root)


# ── call-site 双写收敛（原 study 包 4 处 _dlog） ─────────────────────


def dual_log(module: str, msg: str, *args) -> None:
    """logger 必写；仅当进程没有任何 handler 时 print 兜底。

    与旧 ``_dlog`` 的语义差异：装配过 handler 的进程（web/mcp/cli 开关
    模式）从「logger + print 双份」变为 logger 单份（console handler
    已按策略表定向，可见性不变）；未装配的进程（CLI 默认模式）print
    兜底保持可见。前缀 ``[<MODULE>]`` 收敛自旧的 ``[RUNNER:x]`` /
    ``[STUDY:x]`` 两种写法。
    """
    text = msg % args if args else msg
    tag = module.upper()
    _dual_logger.info("[%s] %s", tag, text)
    if not logging.getLogger().handlers:
        print(f"[{tag}] {text}", flush=True)  # noqa: T201
