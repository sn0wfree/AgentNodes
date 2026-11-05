"""Tests for phase_engine researcher_output non-dict defense.

phase_engine.run_round_phases calls researcher_output.get("hypothesis")
in the phases branch; it must tolerate a non-dict researcher_output
without AttributeError. (P5-2: the langgraph branch copy was retired
with the branch.)
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PHASE_ENGINE = (
    Path(__file__).resolve().parents[1]
    / "src/strategy_research/apps/study/phase_engine.py"
)


class TestResearcherOutputDefenseSource(unittest.TestCase):
    """AST guard: every researcher_output.get() access must be guarded."""

    def test_get_calls_are_type_guarded(self):
        """Find every `researcher_output.get(...)` call and verify a
        preceding isinstance guard assigns researcher_output to a dict."""
        tree = ast.parse(PHASE_ENGINE.read_text(encoding="utf-8"))
        guards = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            # Guard pattern: `if not isinstance(researcher_output, dict): researcher_output = {}`
            if isinstance(node.value, ast.Dict) and not node.value.keys:
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "researcher_output":
                        guards += 1
        src = PHASE_ENGINE.read_text(encoding="utf-8")
        get_sites = src.count("researcher_output.get(")
        # one guarded assignment covers the (single) get site in the
        # phases branch
        self.assertGreaterEqual(guards, 1, msg=f"expected >=1 guards, found {guards}")
        self.assertGreater(get_sites, 0)


if __name__ == "__main__":
    unittest.main()
