r"""统一日志获取入口 —— 「统一装配，按名获取」。

**职责边界（docs/design/logging-unified-hub-design-20261117.md §1/§3.3）**：

- **装配**归 ``foundation/observability/logging_setup.py``（``configure_logging``：
  handler / 格式 / 级别 / 文件 / surface 流定向，进程入口一次性装配）—— 本模块**不碰**。
- **获取**归本模块：``get_logger(name)`` 是全仓唯一取 logger 的地方。
  收编前全仓 164 处散落 ``logging.getLogger(__name__)``，绕过了中枢。

**实例语义（§3.4，实测钉死）**：

- **同名严格同实例**：``get_logger("x") is get_logger("x")`` —— stdlib
  ``LoggerManager`` 注册表保证同名同 logger 对象，本模块再对门面缓存一层，
  保证门面壳也不重复创建。
- **模块级**：``get_logger(__name__)``，logger 名 = 模块名。caplog 的定向
  （``caplog.at_level(..., logger="agentnodes.xxx")``）与 JSON 输出的
  ``logger`` 字段都依赖这个名字，**不许改名**。
- **进程级门面**：``APP_LOG = get_logger("agentnodes")``（单例），供库级 /
  进程级消息使用；与模块级共享同一套装配与 handler。
- **不是全进程单实例**：全局单一 logger 对象会破坏 caplog 定向（13 处指名
  调级全部落空）、JSON 来源字段、按包分级（``AGENTNODES_LOG_LEVELS``）
  三项能力 —— 详见设计文档 §3.4 的 P1–P7。

**合并来源（用户提供的 pf_analysis/utils/logger.py，与 CodersWheel
QuickTool/logger.py 同源）**：只合并其「获取 + 门面 + deco 计时」；
其 handler 双挂 / Singleton / sql()/status() 由 ``configure_logging``
的更强实现替代（设计文档 §3.1 的 M1/M2/M5 作废项）。

**零项目依赖**：本模块只 import stdlib —— ``foundation/settings.py`` 与
``foundation/storage/sqlite.py`` 自己也要用 logger，而装配层依赖它们；
本模块若依赖任何项目代码，最底层两个文件收编即成环（§2.4/§3.3）。
"""

from __future__ import annotations

import functools
import logging
import sys
import time

__all__ = ["APP_LOG", "Logger", "get_logger"]

#: 门面缓存：保证同名严格同实例（get_logger("x") is get_logger("x")）。
#: stdlib 注册表只保证底层 logger 同名同对象；门面壳的同一性由这里保证。
_FACADES: dict[str, "Logger"] = {}

#: deco 的 level 实参白名单 —— 防止 getattr 意外取到非日志方法（如 deco 自身）。
_DECO_LEVELS = ("debug", "info", "warning", "warn", "error", "critical", "exception")


class Logger:
    """模块级 logger 门面 —— 转发 stdlib logger，不吞任何参数。

    只做转发：``exc_info`` / ``stack_info`` / ``stacklevel`` / ``extra``
    原样透传（仓内 61 处 ``exc_info=`` 依赖这一点）。未显式定义的属性
    （``setLevel`` / ``addHandler`` / ``level`` / ``isEnabledFor`` ...）
    经 ``__getattr__`` 委托给底层 stdlib logger。
    """

    __slots__ = ("_logger",)

    def __init__(self, stdlib_logger: logging.Logger) -> None:
        self._logger = stdlib_logger

    @property
    def name(self) -> str:
        """logger 名 = 传入 get_logger 的模块名（caplog / JSON 来源字段依赖）。"""
        return self._logger.name

    # ── 常用级别（显式列出以获得 IDE 提示；签名与 stdlib 对齐）──────────

    def debug(self, msg: str, *args, **kwargs) -> None:
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self._logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs) -> None:
        self._logger.critical(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        self._logger.exception(msg, *args, **kwargs)

    def warn(self, msg: str, *args, **kwargs) -> None:
        """参考实现的别名（= warning）；保持调用点零改动。"""
        self._logger.warning(msg, *args, **kwargs)

    # ── 计时装饰器（合并自参考实现的 deco，升级异常路径）───────────────

    def deco(self, level: str = "info", timer: bool = True, extra_name: str = ""):
        """函数计时装饰器：成功 info、异常 warning（含栈），都带耗时。

        与参考实现的差异（设计文档 §3.1 M4 / U5）：原实现异常时 ``start``
        泄漏且无异常日志；这里 ``try/except`` 保证异常路径也记耗时 +
        ``exc_info=True``，然后原样 re-raise（不吞异常）。

        Args:
            level: 成功路径的输出级别（debug/info/warning/...）。
            timer: True 时额外打 start 行与耗时；False 只打 executed 行。
            extra_name: 前缀（自动补 ``.``），如 ``"runner."``。
        """
        if level not in _DECO_LEVELS:
            raise ValueError(
                f"deco(level={level!r}) 不在白名单 {_DECO_LEVELS}；"
                "避免 getattr 取到非日志方法"
            )
        if extra_name and not extra_name.endswith("."):
            extra_name += "."
        log_fn = getattr(self, level)

        def _deco(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                label = f"{extra_name}{func.__name__}"
                start = time.perf_counter()
                if timer:
                    log_fn(f"start to execute {label}!")
                try:
                    result = func(*args, **kwargs)
                except Exception:
                    spend = time.perf_counter() - start
                    self.warning(f"{label} failed! spend: {spend:.3f}s", exc_info=True)
                    raise
                if timer:
                    spend = time.perf_counter() - start
                    log_fn(f"{label} executed! spend: {spend:.3f}s")
                else:
                    log_fn(f"{label} executed!")
                return result

            return wrapper

        return _deco

    # ── 其余属性委托给 stdlib logger（setLevel / addHandler / level ...）─

    def __getattr__(self, attr: str):
        # 只委托非 dunder；_logger 未就绪时直接抛 AttributeError 防递归。
        if attr.startswith("__") or attr == "_logger":
            raise AttributeError(attr)
        return getattr(self._logger, attr)


def get_logger(name: str | None = None) -> Logger:
    """全仓唯一取 logger 的入口。

    Args:
        name: logger 名（惯例传 ``__name__``，caplog 与 JSON 的 ``logger``
            字段依赖它）。缺省取**调用方**的 ``__name__``（``sys._getframe``，
            CPython 专属 —— 本仓固定 CPython，可接受）。``agentnodes.`` 之外
            的第三方名字原样放行，不改名。

    Returns:
        :class:`Logger` 门面；同名严格同实例。
    """
    if name is None:
        frame = sys._getframe(1)
        name = frame.f_globals.get("__name__") or "agentnodes"
    facade = _FACADES.get(name)
    if facade is None:
        facade = Logger(logging.getLogger(name))
        _FACADES[name] = facade
    return facade


#: 进程级门面（单例）：库级 / 进程级消息用它；模块级一律 ``get_logger(__name__)``。
APP_LOG = get_logger("agentnodes")
