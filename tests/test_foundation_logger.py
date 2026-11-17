"""``foundation/logger.py``（统一日志获取入口）的契约网。

设计：docs/design/logging-unified-hub-design-20261117.md §3.4 / §7 批 1。
钉住的语义：
- 同名严格同实例（门面缓存）+ 底层 stdlib logger 同名同对象；
- 模块名保真（caplog / JSON 的 ``logger`` 字段依赖）；
- 门面只转发、不吞 kwargs（``exc_info`` 等 61 处调用点依赖）；
- ``deco`` 成功/异常双路径都有日志与耗时，异常原样 re-raise；
- 内核零项目 import（``settings`` / ``storage.sqlite`` 收编不成环的前提）。
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from agentnodes.foundation.logger import APP_LOG, Logger, get_logger

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "src" / "agentnodes" / "foundation" / "logger.py"


class TestIdentity:
    """同名同实例 —— 「按名获取」的单一性语义（§3.4）。"""

    def test_same_name_strictly_same_facade(self):
        assert get_logger("agentnodes.test.a") is get_logger("agentnodes.test.a")

    def test_different_names_different_facades(self):
        assert get_logger("agentnodes.test.a") is not get_logger("agentnodes.test.b")

    def test_underlying_stdlib_logger_is_shared(self):
        """门面只是壳：底层与 logging.getLogger(name) 是同一个对象。"""
        facade = get_logger("agentnodes.test.underlying")
        assert facade._logger is logging.getLogger("agentnodes.test.underlying")

    def test_default_name_is_caller_module(self):
        facade = get_logger()
        assert facade.name == __name__

    def test_process_facade_is_singleton(self):
        assert APP_LOG is get_logger("agentnodes")
        assert isinstance(APP_LOG, Logger)

    def test_third_party_name_passthrough(self):
        """agentnodes. 之外的名字原样放行，不改名（§4.1）。"""
        assert get_logger("urllib3.poolmanager").name == "urllib3.poolmanager"


class TestForwarding:
    """门面只转发 —— record.name 保真 + kwargs 不吞。"""

    def test_record_name_keeps_module(self, caplog):
        logger = get_logger("agentnodes.test.forwarding")
        with caplog.at_level(logging.INFO, logger="agentnodes.test.forwarding"):
            logger.info("hello %s", "world")
        assert [r.name for r in caplog.records] == ["agentnodes.test.forwarding"]
        assert caplog.records[0].message == "hello world"

    def test_exc_info_forwarded(self, caplog):
        logger = get_logger("agentnodes.test.exc")
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("caught", exc_info=True)
        rec = caplog.records[0]
        assert rec.exc_info is not None
        assert rec.exc_info[0] is ValueError

    def test_getattr_delegates_to_stdlib(self):
        """未显式定义的属性（level / setLevel / isEnabledFor）委托底层。"""
        facade = get_logger("agentnodes.test.delegate")
        assert facade.level == logging.getLogger("agentnodes.test.delegate").level
        assert facade.isEnabledFor(logging.WARNING)

    def test_dunder_access_raises_attributeerror(self):
        with pytest.raises(AttributeError):
            _ = get_logger("agentnodes.test.dunder").__deepcopy__


class TestDeco:
    """计时装饰器：成功 info / 异常 warning+栈，双路径都有耗时（M4 升级）。"""

    def _records(self, caplog, name="agentnodes.test.deco"):
        return [r for r in caplog.records if r.name == name]

    def test_success_path_logs_start_and_spend(self, caplog):
        logger = get_logger("agentnodes.test.deco")

        @logger.deco()
        def work():
            return 42

        with caplog.at_level(logging.INFO, logger="agentnodes.test.deco"):
            assert work() == 42
        msgs = [r.message for r in self._records(caplog)]
        assert any("start to execute work!" in m for m in msgs)
        assert any("work executed! spend:" in m for m in msgs)

    def test_exception_path_logs_warning_with_trace_and_reraises(self, caplog):
        logger = get_logger("agentnodes.test.deco")

        @logger.deco()
        def boom():
            raise RuntimeError("kaput")

        with caplog.at_level(logging.INFO, logger="agentnodes.test.deco"):
            with pytest.raises(RuntimeError, match="kaput"):
                boom()
        recs = self._records(caplog)
        failed = [r for r in recs if "failed! spend:" in r.message]
        assert len(failed) == 1, [r.message for r in recs]
        assert failed[0].levelno == logging.WARNING
        assert failed[0].exc_info is not None
        assert failed[0].exc_info[0] is RuntimeError

    def test_extra_name_gets_dot(self, caplog):
        logger = get_logger("agentnodes.test.deco")

        @logger.deco(extra_name="runner")
        def work():
            return None

        with caplog.at_level(logging.INFO, logger="agentnodes.test.deco"):
            work()
        assert any("runner.work executed!" in r.message for r in self._records(caplog))

    def test_timer_false_skips_start_line(self, caplog):
        logger = get_logger("agentnodes.test.deco")

        @logger.deco(timer=False)
        def work():
            return None

        with caplog.at_level(logging.INFO, logger="agentnodes.test.deco"):
            work()
        msgs = [r.message for r in self._records(caplog)]
        assert not any("start to execute" in m for m in msgs)
        assert any("work executed!" in m for m in msgs)

    def test_invalid_level_rejected_eagerly(self):
        logger = get_logger("agentnodes.test.deco")
        with pytest.raises(ValueError, match="白名单"):
            # level="deco" 这类误用会在装饰时就报错，而不是 getattr 到诡异对象
            logger.deco(level="deco")

    def test_custom_level(self, caplog):
        logger = get_logger("agentnodes.test.deco")

        @logger.deco(level="debug")
        def work():
            return None

        with caplog.at_level(logging.DEBUG, logger="agentnodes.test.deco"):
            work()
        assert any("work executed!" in r.message for r in self._records(caplog))


class TestKernelPurity:
    """零项目依赖 —— settings / storage.sqlite 收编不成环的前提（§3.3）。"""

    def test_zero_project_imports(self):
        tree = ast.parse(KERNEL.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.startswith("agentnodes"):
                    offenders.append(f"L{node.lineno}: from {mod}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("agentnodes"):
                        offenders.append(f"L{node.lineno}: import {alias.name}")
        assert not offenders, f"内核必须零项目 import：{offenders}"

    def test_importable_in_fresh_interpreter_without_settings(self):
        """内核单独可 import —— 不拖起 settings / observability。"""
        import subprocess
        import sys

        code = (
            "import sys;"
            "import agentnodes.foundation.logger as m;"
            "mods = sys.modules;"
            "assert 'agentnodes.foundation.settings' not in mods, 'kernel pulls settings';"
            "assert 'agentnodes.foundation.observability' not in mods, 'kernel pulls observability';"
            "print('OK', m.get_logger('x').name)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "OK x" in proc.stdout
