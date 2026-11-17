"""``logging_setup.timed_deco``（call-site 计时装饰器）的契约网。

吸收自统一中枢批 1 的 deco（源自参考实现 CodersWheel LoggerHelper.deco /
pf_analysis logger.py）；终局裁决见
docs/design/logging-unified-hub-design-20261117.md §12.3。
钉住的语义：
- 成功路径按 level 输出 start + spend；
- 异常路径 warning + exc_info + 耗时，并原样 re-raise（不吞异常）；
- level 白名单在装饰时就校验（防 getattr 到非日志方法）；
- logger 由调用方显式传入（stdlib logger，无 frame 魔法）。
"""

from __future__ import annotations

import logging

import pytest

from agentnodes.foundation.observability import timed_deco

NAME = "agentnodes.test.timed_deco"


def _records(caplog):
    return [r for r in caplog.records if r.name == NAME]


def test_success_logs_start_and_spend(caplog):
    logger = logging.getLogger(NAME)

    @timed_deco(logger)
    def work():
        return 42

    with caplog.at_level(logging.INFO, logger=NAME):
        assert work() == 42
    msgs = [r.message for r in _records(caplog)]
    assert any("start to execute work!" in m for m in msgs)
    assert any("work executed! spend:" in m for m in msgs)


def test_exception_logs_warning_with_trace_and_reraises(caplog):
    logger = logging.getLogger(NAME)

    @timed_deco(logger)
    def boom():
        raise RuntimeError("kaput")

    with caplog.at_level(logging.INFO, logger=NAME):
        with pytest.raises(RuntimeError, match="kaput"):
            boom()
    recs = _records(caplog)
    failed = [r for r in recs if "failed! spend:" in r.message]
    assert len(failed) == 1, [r.message for r in recs]
    assert failed[0].levelno == logging.WARNING
    assert failed[0].exc_info is not None
    assert failed[0].exc_info[0] is RuntimeError


def test_extra_name_gets_dot(caplog):
    logger = logging.getLogger(NAME)

    @timed_deco(logger, extra_name="runner")
    def work():
        return None

    with caplog.at_level(logging.INFO, logger=NAME):
        work()
    assert any("runner.work executed!" in r.message for r in _records(caplog))


def test_timer_false_skips_start_line(caplog):
    logger = logging.getLogger(NAME)

    @timed_deco(logger, timer=False)
    def work():
        return None

    with caplog.at_level(logging.INFO, logger=NAME):
        work()
    msgs = [r.message for r in _records(caplog)]
    assert not any("start to execute" in m for m in msgs)
    assert any("work executed!" in m for m in msgs)


def test_invalid_level_rejected_eagerly():
    logger = logging.getLogger(NAME)
    with pytest.raises(ValueError, match="白名单"):
        # level="timed_deco" 这类误用会在装饰时就报错，而不是 getattr 到诡异对象
        timed_deco(logger, level="timed_deco")


def test_custom_level(caplog):
    logger = logging.getLogger(NAME)

    @timed_deco(logger, level="debug")
    def work():
        return None

    with caplog.at_level(logging.DEBUG, logger=NAME):
        work()
    assert any("work executed!" in r.message for r in _records(caplog))


def test_takes_plain_stdlib_logger_only():
    """logger 参数就是普通 stdlib logger —— 无门面、无包装（§12.3）。"""
    logger = logging.getLogger(NAME)
    deco = timed_deco(logger)
    assert callable(deco(work := lambda: None)) and work() is None
