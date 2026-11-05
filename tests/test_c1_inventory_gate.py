"""C1 acceptance gates: test-inventory freeze + comparison.

Contract: docs/archive/2026-10/stage-c1-acceptance-20261028.md §2.2

The inventory gate exists because a migration *legitimately* removes ~100
baseline failures. Without a frozen set of node ids, deleting the suite is
indistinguishable from fixing the code -- and the report looks identical.
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
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_c1_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


inv = _load("c1_inventory")


COLLECT = """\
tests/test_a.py::test_one
tests/test_a.py::TestK::test_two
tests/test_b.py::test_param[a-1]
tests/test_b.py::test_param[STOP kill all]
tests/test_b.py::test_param[   ]

9822/12895 tests collected (3073 deselected) in 10.94s
"""


class TestCollectParser:
    def test_parses_plain_ids(self):
        got = inv.parse_collect_log(COLLECT)
        assert "tests/test_a.py::test_one" in got
        assert "tests/test_a.py::TestK::test_two" in got

    def test_parametrised_ids_with_spaces_are_kept(self):
        """The bug this test locks: `\\S+` dropped `[STOP kill all]`.

        A silent parse gap here means the gate cannot see those tests at
        all -- they could be deleted with no report.
        """
        got = inv.parse_collect_log(COLLECT)
        assert "tests/test_b.py::test_param[STOP kill all]" in got
        assert "tests/test_b.py::test_param[   ]" in got

    def test_tally_line_is_not_a_node(self):
        got = inv.parse_collect_log(COLLECT)
        assert not any("collected" in n for n in got)
        assert len(got) == 5

    def test_deduplicates(self):
        text = "tests/a.py::t\ntests/a.py::t\n"
        assert inv.parse_collect_log(text) == ["tests/a.py::t"]

    def test_backslashes_normalised(self):
        text = "tests\\sub\\test_a.py::test_one\n"
        assert inv.parse_collect_log(text) == ["tests/sub/test_a.py::test_one"]

    def test_ignores_prose(self):
        text = "warning tests/foo.py::bar is missing\ntests/a.py::t\n"
        # Prose doesn't start with the literal "tests/" followed by "::".
        assert inv.parse_collect_log(text) == ["tests/a.py::t"]


class TestInventoryGate:
    def _freeze(self, tmp_path: Path, nodes: list[str]) -> Path:
        out = tmp_path / "inv.json"
        out.write_text(
            json.dumps({"commit": "deadbeef", "nodeids": nodes}), encoding="utf-8",
        )
        return out

    def _log(self, tmp_path: Path, nodes: list[str]) -> Path:
        p = tmp_path / "collect.log"
        p.write_text("\n".join(nodes) + "\n", encoding="utf-8")
        return p

    def test_clean_when_identical(self, tmp_path):
        nodes = ["tests/a.py::t1", "tests/a.py::t2"]
        inv_path = self._freeze(tmp_path, nodes)
        log = self._log(tmp_path, nodes)
        assert inv.main(["verify", str(log), "--baseline", str(inv_path)]) == 0

    def test_addition_is_allowed(self, tmp_path):
        inv_path = self._freeze(tmp_path, ["tests/a.py::t1"])
        log = self._log(tmp_path, ["tests/a.py::t1", "tests/a.py::new"])
        assert inv.main(["verify", str(log), "--baseline", str(inv_path)]) == 0

    def test_removal_fails(self, tmp_path, capsys):
        inv_path = self._freeze(tmp_path, ["tests/a.py::t1", "tests/a.py::t2"])
        log = self._log(tmp_path, ["tests/a.py::t1"])
        assert inv.main(["verify", str(log), "--baseline", str(inv_path)]) == 1
        out = capsys.readouterr().out
        assert "REMOVED" in out
        assert "tests/a.py" in out

    def test_allow_shrink_downgrades_to_informational(self, tmp_path):
        inv_path = self._freeze(tmp_path, ["tests/a.py::t1", "tests/a.py::t2"])
        log = self._log(tmp_path, ["tests/a.py::t1"])
        rc = inv.main([
            "verify", str(log), "--baseline", str(inv_path), "--allow-shrink",
        ])
        assert rc == 0

    def test_freeze_writes_count(self, tmp_path):
        log = self._log(tmp_path, ["tests/a.py::t1", "tests/a.py::t2"])
        out = tmp_path / "frozen.json"
        assert inv.main(["freeze", str(log), "--out", str(out)]) == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["count"] == 2
        assert len(data["nodeids"]) == 2


class TestFrozenInventoryFile:
    def test_committed_inventory_is_consistent(self):
        path = REPO / "tests" / "baselines" / "pr-lane-nodeids.json"
        if not path.exists():
            pytest.skip("inventory not frozen in this checkout")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["count"] == len(data["nodeids"])
        assert data["count"] > 9000, "PR lane should collect a large suite"
        assert all("::" in n for n in data["nodeids"])
        assert len(set(data["nodeids"])) == data["count"]
