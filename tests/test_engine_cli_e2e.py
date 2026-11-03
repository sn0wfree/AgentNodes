"""Engine CLI e2e 测试 (argparse-based)。

覆盖:
- list-engines 子命令
- validate-signal 子命令
- run-backtest --help / 参数验证
"""
from __future__ import annotations

import argparse
import textwrap

from strategy_research.apps.engine.cli import (
    add_engine_subparsers,
    cmd_engine_list_engines,
    cmd_engine_validate_signal,
)


# ─────────────────────────────────────────────
# list-engines
# ─────────────────────────────────────────────
class TestListEngines:
    def test_list_output(self):
        args = argparse.Namespace()
        # Capture stdout
        import io
        import sys
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        try:
            rc = cmd_engine_list_engines(args)
        finally:
            sys.stdout = old_stdout
        output = buffer.getvalue()
        assert rc == 0
        assert "ChinaA" in output or "china_a" in output.lower() or "GlobalEquity" in output

    def test_list_engines_returns_0(self):
        args = argparse.Namespace()
        import io
        import sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = cmd_engine_list_engines(args)
        finally:
            sys.stdout = old_stdout
        assert rc == 0


# ─────────────────────────────────────────────
# validate-signal
# ─────────────────────────────────────────────
class TestValidateSignal:
    def test_valid_signal(self, tmp_path):
        f = tmp_path / "signal_engine.py"
        f.write_text(textwrap.dedent("""\
            import pandas as pd
            from strategy_research.apps.engine.signals import SignalEngine

            class SignalEngine(SignalEngine):
                def generate(self, data_map):
                    return {code: pd.Series(1.0, index=df.index) for code, df in data_map.items()}
        """))
        args = argparse.Namespace(file=str(f))
        import io
        import sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = cmd_engine_validate_signal(args)
        finally:
            sys.stdout = old_stdout
        assert rc == 0

    def test_invalid_signal(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("import os\n")
        args = argparse.Namespace(file=str(f))
        import io
        import sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = cmd_engine_validate_signal(args)
        finally:
            sys.stdout = old_stdout
        assert rc != 0

    def test_nonexistent_file(self, tmp_path):
        args = argparse.Namespace(file=str(tmp_path / "nope.py"))
        import io
        import sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = cmd_engine_validate_signal(args)
        finally:
            sys.stdout = old_stdout
        assert rc != 0


# ─────────────────────────────────────────────
# add_engine_subparsers: parser creation
# ─────────────────────────────────────────────
class TestAddEngineSubparsers:
    def test_creates_engine_parser(self):
        top = argparse.ArgumentParser()
        subs = top.add_subparsers(dest="command")
        add_engine_subparsers(subs)
        # Parsing "engine list-engines" should not error
        args = top.parse_args(["engine", "list-engines"])
        assert args.engine_command == "list-engines"

    def test_run_backtest_args(self):
        top = argparse.ArgumentParser()
        subs = top.add_subparsers(dest="command")
        add_engine_subparsers(subs)
        args = top.parse_args([
            "engine", "run-backtest",
            "--workspace", "/tmp/ws",
            "--strategy", "test",
            "--signal-engine", "/tmp/signal.py",
            "--optimizer", "risk_parity",
        ])
        assert args.optimizer == "risk_parity"
        assert args.workspace == "/tmp/ws"

    def test_validate_signal_args(self):
        top = argparse.ArgumentParser()
        subs = top.add_subparsers(dest="command")
        add_engine_subparsers(subs)
        args = top.parse_args(["engine", "validate-signal", "/tmp/signal.py"])
        assert args.file == "/tmp/signal.py"
