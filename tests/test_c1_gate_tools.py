"""C1 team-gate tooling: baseline extraction + gate comparison.

Why these tests exist
---------------------
The gate is the only thing standing between a migration worker and a
false "all green" report. If the parser silently misses a summary line,
or the comparison falls back to counting, every worker's verdict becomes
worthless. So the tooling gets its own regression tests.

Contract: docs/archive/2026-10/stage-c1-team-contract-20261028.md §6
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"


def _load(name: str):
    """Import scripts/<name>.py as a module (scripts/ is not a package)."""
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_c1_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


baseline_mod = _load("c1_baseline")
gate_mod = _load("c1_gate")


SAMPLE = """\
..F
FAILED tests/test_a.py::test_one - AssertionError: boom
ERROR tests/test_b.py::test_two - fixture 'db' not found
=========================== short test summary info ===========================
FAILED tests/test_a.py::test_one - AssertionError: boom
ERROR tests/test_b.py::test_two - fixture 'db' not found
FAILED tests/test_c.py::test_three
2 failed, 1 error, 10 passed in 1.23s
"""


class TestBaselineParser:
    def test_parses_failed_and_error(self):
        got = baseline_mod.parse_log(SAMPLE)
        assert got == {
            "tests/test_a.py::test_one": "FAILED",
            "tests/test_b.py::test_two": "ERROR",
            "tests/test_c.py::test_three": "FAILED",
        }

    def test_duplicate_lines_do_not_double_count(self):
        # A node can appear in the progress line and the summary block.
        text = "FAILED tests/x.py::t - e\nFAILED tests/x.py::t - e\n"
        assert len(baseline_mod.parse_log(text)) == 1

    def test_failed_wins_over_error_for_same_node(self):
        text = "ERROR tests/x.py::t - e\nFAILED tests/x.py::t - e\n"
        assert baseline_mod.parse_log(text)["tests/x.py::t"] == "FAILED"

    def test_parametrised_ids_are_distinct(self):
        text = (
            "FAILED tests/x.py::t[a] - e\n"
            "FAILED tests/x.py::t[b] - e\n"
        )
        assert len(baseline_mod.parse_log(text)) == 2

    def test_ignores_noise_lines(self):
        text = "warning: something\nok tests/x.py::t\nFAILED tests/x.py::t - e\n"
        assert list(baseline_mod.parse_log(text)) == ["tests/x.py::t"]

    def test_counts_are_derived_from_nodes_not_pytest_tally(self):
        payload = baseline_mod.build_payload(SAMPLE, command="pytest")
        # pytest's own tally goes to pytest_totals, never overwrites counts.
        assert payload["counts"] == {"failed": 2, "error": 1, "total": 3}
        assert payload["pytest_totals"]["passed"] == 10

    def test_by_file_ordering_is_descending(self):
        payload = baseline_mod.build_payload(SAMPLE, command="pytest")
        counts = list(payload["by_file"].values())
        assert counts == sorted(counts, reverse=True)


class TestGate:
    def _write(self, tmp_path: Path, text: str) -> Path:
        p = tmp_path / "run.log"
        p.write_text(text, encoding="utf-8")
        return p

    def _baseline(self, tmp_path: Path, nodes: list[str]) -> Path:
        p = tmp_path / "baseline.json"
        p.write_text(
            json.dumps({"commit": "deadbeef", "known_failures": nodes}),
            encoding="utf-8",
        )
        return p

    def test_clean_when_subset_of_baseline(self, tmp_path):
        log = self._write(tmp_path, "FAILED tests/a.py::t1 - e\n")
        bl = self._baseline(tmp_path, ["tests/a.py::t1", "tests/a.py::t2"])
        assert gate_mod.main([str(log), "--baseline", str(bl), "--quiet"]) == 0

    def test_regression_detected(self, tmp_path, capsys):
        log = self._write(
            tmp_path, "FAILED tests/a.py::t1 - e\nFAILED tests/a.py::new - e\n",
        )
        bl = self._baseline(tmp_path, ["tests/a.py::t1"])
        assert gate_mod.main([str(log), "--baseline", str(bl), "--quiet"]) == 1
        out = capsys.readouterr().out
        assert "tests/a.py::new" in out

    def test_trade_one_for_one_is_still_a_regression(self, tmp_path):
        """The failure mode a count-based gate cannot see."""
        log = self._write(tmp_path, "FAILED tests/a.py::new - e\n")
        bl = self._baseline(tmp_path, ["tests/a.py::old"])
        # 1 failure before, 1 failure now -- but a different one.
        assert gate_mod.main([str(log), "--baseline", str(bl), "--quiet"]) == 1

    def test_files_scope_filters_both_sides(self, tmp_path, capsys):
        log = self._write(tmp_path, "FAILED tests/a.py::t1 - e\n")
        bl = self._baseline(
            tmp_path, ["tests/a.py::t1", "tests/b.py::other"],
        )
        rc = gate_mod.main([
            str(log), "--baseline", str(bl), "--files", "tests/a.py",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        # b.py was never run -- it must not be reported as "now passes".
        assert "tests/b.py::other" not in out
        assert "entries : 1" in out

    def test_missing_baseline_is_usage_error(self, tmp_path):
        log = self._write(tmp_path, "FAILED tests/a.py::t1 - e\n")
        with pytest.raises(SystemExit):
            gate_mod.main([
                str(log), "--baseline", str(tmp_path / "nope.json"), "--quiet",
            ])

    def test_missing_log_is_usage_error(self, tmp_path):
        bl = self._baseline(tmp_path, [])
        assert gate_mod.main([
            str(tmp_path / "nope.log"), "--baseline", str(bl),
        ]) == 2


class TestFrozenBaseline:
    """The committed baseline must stay machine-readable and non-empty."""

    def test_baseline_file_parses(self):
        path = REPO / "tests" / "baselines" / "pr-lane-failures.json"
        if not path.exists():
            pytest.skip("baseline not generated in this checkout")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["known_failures"], "baseline must not be empty"
        assert data["counts"]["total"] == len(data["known_failures"])
        assert all("::" in n for n in data["known_failures"])
