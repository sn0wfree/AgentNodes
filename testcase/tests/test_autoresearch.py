"""Tests for autoresearch.py — orchestrator helper functions."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.autoresearch import (
    build_agent_prompt,
    detect_lazy_behavior,
    generate_run_summary,
    get_cooldown_seconds,
    get_run_data,
    load_run_summary,
    parse_agent_output,
    read_current_state,
    retry_agent_spawn,
    save_agent_record,
    save_laziness_report,
    save_run_summary,
    should_read_detailed,
    should_run_lazy_detection,
)


class TestBuildAgentPrompt(unittest.TestCase):

    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.prompts_dir = Path(self.tmpdir.name) / ".prompts"
        self.prompts_dir.mkdir()
        (self.prompts_dir / "researcher.md").write_text("# Researcher\nYou are a researcher.")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_includes_role_definition(self) -> None:
        prompt = build_agent_prompt("researcher", self.prompts_dir, {})
        self.assertIn("# Researcher", prompt)

    def test_includes_state(self) -> None:
        prompt = build_agent_prompt(
            "researcher", self.prompts_dir,
            {"strategy_py": "code", "best_calmar": 1.5, "current_calmar": 1.0, "total_runs": 10}
        )
        self.assertIn("code", prompt)
        self.assertIn("1.5", prompt)
        self.assertIn("1.0", prompt)
        self.assertIn("10", prompt)

    def test_includes_previous_output(self) -> None:
        prompt = build_agent_prompt(
            "researcher", self.prompts_dir,
            {}, previous_outputs=[{"action": "search"}]
        )
        self.assertIn("search", prompt)

    def test_missing_prompt_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            build_agent_prompt("nonexistent", self.prompts_dir, {})

    def test_no_previous_outputs(self) -> None:
        prompt = build_agent_prompt("researcher", self.prompts_dir, {})
        self.assertNotIn("上一个 Agent", prompt)


class TestSaveAgentRecord(unittest.TestCase):

    def test_save_creates_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run_001"
            run_dir.mkdir()
            fp = save_agent_record(
                run_dir, "researcher", 1,
                {"input": "data"}, {"output": "result"}, 100,
            )
            self.assertTrue(fp.exists())
            record = json.loads(fp.read_text(encoding="utf-8"))
            self.assertEqual(record["agent"], "researcher")
            self.assertEqual(record["step"], 1)
            self.assertEqual(record["duration_ms"], 100)


class TestReadCurrentState(unittest.TestCase):

    def test_no_strategy_dir(self) -> None:
        with TemporaryDirectory() as tmpdir:
            state = read_current_state(Path(tmpdir), "s1")
            self.assertEqual(state["strategy_py"], "")
            self.assertEqual(state["best_calmar"], 0)
            self.assertEqual(state["total_runs"], 0)

    def test_with_strategy_py(self) -> None:
        with TemporaryDirectory() as tmpdir:
            strat_dir = Path(tmpdir) / "strategies" / "s1"
            strat_dir.mkdir(parents=True)
            (strat_dir / "strategy.py").write_text("x = 1\n")
            state = read_current_state(Path(tmpdir), "s1")
            self.assertEqual(state["strategy_py"], "x = 1\n")

    def test_with_results_tsv(self) -> None:
        with TemporaryDirectory() as tmpdir:
            strat_dir = Path(tmpdir) / "strategies" / "s1"
            runs_dir = strat_dir / "runs"
            runs_dir.mkdir(parents=True)
            tsv_content = "header1\theader2\theader3\theader4\n1\t2\t3\t1.5\n4\t5\t6\t2.0\n"
            (runs_dir / "results.tsv").write_text(tsv_content)
            state = read_current_state(Path(tmpdir), "s1")
            self.assertGreater(state["best_calmar"], 1.5)
            self.assertEqual(state["total_runs"], 2)

    def test_with_invalid_calmar_line(self) -> None:
        with TemporaryDirectory() as tmpdir:
            strat_dir = Path(tmpdir) / "strategies" / "s1"
            runs_dir = strat_dir / "runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "results.tsv").write_text("a\tb\tc\td\n1\t2\t3\tnotanumber\n")
            state = read_current_state(Path(tmpdir), "s1")
            self.assertEqual(state["best_calmar"], 0.0)


class TestParseAgentOutput(unittest.TestCase):

    def test_valid_json(self) -> None:
        result = parse_agent_output('{"action": "search", "score": 0.8}')
        self.assertEqual(result["action"], "search")

    def test_empty_input(self) -> None:
        result = parse_agent_output("")
        self.assertEqual(result["error"], "empty_output")

    def test_whitespace_only(self) -> None:
        result = parse_agent_output("   \n  \t  ")
        self.assertEqual(result["error"], "empty_output")

    def test_json_in_markdown_block(self) -> None:
        raw = '```json\n{"action": "search"}\n```'
        result = parse_agent_output(raw)
        self.assertEqual(result["action"], "search")

    def test_json_in_generic_code_block(self) -> None:
        raw = '```\n{"action": "test"}\n```'
        result = parse_agent_output(raw)
        self.assertEqual(result["action"], "test")

    def test_braces_in_text(self) -> None:
        raw = 'Some text {"key": "value"} more text'
        result = parse_agent_output(raw)
        self.assertEqual(result["key"], "value")

    def test_brackets_in_text(self) -> None:
        raw = 'List: [{"a": 1}, {"b": 2}]'
        result = parse_agent_output(raw)
        self.assertEqual(len(result), 2)

    def test_unparseable(self) -> None:
        result = parse_agent_output("totally gibberish no json at all")
        self.assertEqual(result["error"], "parse_failed")


class TestRetryAgentSpawn(unittest.TestCase):

    def test_success_first_try(self) -> None:
        def spawn():
            return '{"action": "search"}'
        result = retry_agent_spawn(spawn, "researcher", max_retries=3, retry_delay=0)
        self.assertEqual(result["action"], "search")

    def test_success_after_retries(self) -> None:
        attempts = [0]
        def spawn():
            attempts[0] += 1
            if attempts[0] < 3:
                return "invalid"
            return '{"action": "ok"}'
        result = retry_agent_spawn(spawn, "researcher", max_retries=3, retry_delay=0)
        self.assertEqual(result["action"], "ok")
        self.assertEqual(attempts[0], 3)

    def test_max_retries_exceeded(self) -> None:
        def spawn():
            return "invalid"
        result = retry_agent_spawn(spawn, "researcher", max_retries=2, retry_delay=0)
        self.assertEqual(result["error"], "parse_failed")

    def test_exception_handled(self) -> None:
        def spawn():
            raise RuntimeError("boom")
        result = retry_agent_spawn(spawn, "researcher", max_retries=2, retry_delay=0)
        self.assertEqual(result["error"], "execution_failed")


class TestGetCooldownSeconds(unittest.TestCase):

    def test_in_range(self) -> None:
        for _ in range(50):
            cd = get_cooldown_seconds(base_cooldown=10, jitter=2, min_cooldown=1)
            self.assertGreaterEqual(cd, 1)
            self.assertLessEqual(cd, 12)

    def test_respects_min(self) -> None:
        for _ in range(50):
            cd = get_cooldown_seconds(base_cooldown=10, jitter=20, min_cooldown=5)
            self.assertGreaterEqual(cd, 5)


class TestShouldRunLazyDetection(unittest.TestCase):

    def test_round_zero(self) -> None:
        self.assertFalse(should_run_lazy_detection(0, interval=10))

    def test_round_ten(self) -> None:
        self.assertTrue(should_run_lazy_detection(10, interval=10))

    def test_round_twenty(self) -> None:
        self.assertTrue(should_run_lazy_detection(20, interval=10))

    def test_round_five(self) -> None:
        self.assertFalse(should_run_lazy_detection(5, interval=10))

    def test_custom_interval(self) -> None:
        self.assertTrue(should_run_lazy_detection(5, interval=5))
        self.assertFalse(should_run_lazy_detection(3, interval=5))


class TestDetectLazyBehavior(unittest.TestCase):

    def test_empty_history(self) -> None:
        result = detect_lazy_behavior("researcher", {"hypothesis": "x"}, [])
        self.assertFalse(result["is_lazy"])
        self.assertEqual(result["lazy_score"], 0.0)

    def test_researcher_duplicate_hypothesis(self) -> None:
        history = [{"output": {"hypothesis": "x"}}, {"output": {"hypothesis": "y"}}]
        result = detect_lazy_behavior(
            "researcher", {"hypothesis": "x"}, history, threshold=2
        )
        self.assertGreater(result["lazy_score"], 0)
        self.assertIn("hypothesis", " ".join(result["issues"]))

    def test_researcher_duplicate_action(self) -> None:
        history = [{"output": {"action": "search"}}, {"output": {"action": "search"}}]
        result = detect_lazy_behavior(
            "researcher", {"action": "search"}, history, threshold=2
        )
        self.assertGreater(result["lazy_score"], 0)

    def test_factor_analyst_empty_candidates(self) -> None:
        history = [
            {"output": {"candidates": []}},
            {"output": {"candidates": []}},
            {"output": {"candidates": []}},
        ]
        result = detect_lazy_behavior(
            "factor_analyst", {"candidates": []}, history, threshold=3
        )
        self.assertGreater(result["lazy_score"], 0)

    def test_strategist_empty_changes(self) -> None:
        history = [
            {"output": {"changes": [], "action": "noop"}},
            {"output": {"changes": [], "action": "noop"}},
            {"output": {"changes": [], "action": "noop"}},
        ]
        result = detect_lazy_behavior(
            "strategist", {"changes": [], "action": "noop"}, history, threshold=3
        )
        self.assertGreater(result["lazy_score"], 0)

    def test_anti_overfit_discards(self) -> None:
        history = [
            {"output": {"verdict": "discard"}},
            {"output": {"verdict": "discard"}},
            {"output": {"verdict": "discard"}},
        ]
        result = detect_lazy_behavior(
            "anti_overfit_analyst",
            {"verdict": "discard"},
            history,
            threshold=3,
        )
        self.assertGreater(result["lazy_score"], 0)

    def test_lazy_score_capped(self) -> None:
        history = [
            {"output": {"hypothesis": "x", "action": "y"}},
            {"output": {"hypothesis": "x", "action": "y"}},
            {"output": {"hypothesis": "x", "action": "y"}},
        ]
        result = detect_lazy_behavior(
            "researcher",
            {"hypothesis": "x", "action": "y"},
            history,
            threshold=3,
        )
        self.assertLessEqual(result["lazy_score"], 1.0)


class TestSaveLazinessReport(unittest.TestCase):

    def test_save_creates_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run_001"
            run_dir.mkdir()
            fp = save_laziness_report(
                run_dir, 1,
                [{"agent": "r1", "issues": ["issue1"]}],
                0.5,
            )
            self.assertTrue(fp.exists())
            data = json.loads(fp.read_text(encoding="utf-8"))
            self.assertEqual(data["round"], 1)
            self.assertEqual(data["overall_lazy_score"], 0.5)

    def test_save_no_lazy_agents(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run_001"
            run_dir.mkdir()
            fp = save_laziness_report(run_dir, 1, [], 0.0)
            data = json.loads(fp.read_text(encoding="utf-8"))
            self.assertIn("正常", data["summary"])


class TestGenerateRunSummary(unittest.TestCase):

    def test_basic(self) -> None:
        agent_outputs = {
            "researcher": {"action": "search", "hypothesis": "h1"},
            "strategist": {"action": "change", "changes": [{"param": "p", "new": "v"}]},
        }
        metrics = {"calmar": 1.5, "sharpe": 0.8, "max_dd": -0.1, "ann_return": 0.1, "ann_vol": 0.2, "turnover": 0.5}
        summary = generate_run_summary(agent_outputs, metrics, "keep", 1)
        self.assertEqual(summary["round"], 1)
        self.assertEqual(summary["verdict"], "keep")
        self.assertEqual(summary["hypothesis"], "h1")

    def test_with_previous_summary(self) -> None:
        agent_outputs = {"researcher": {"action": "search"}}
        metrics = {"calmar": 1.5, "sharpe": 0.8, "max_dd": -0.1, "verdict": "keep"}
        previous = {
            "verdict": "discard",
            "metrics": {"calmar": 1.0, "sharpe": 0.5, "max_dd": -0.2},
        }
        summary = generate_run_summary(agent_outputs, metrics, "keep", 2, previous)
        self.assertEqual(summary["performance_change"]["calmar_delta"], 0.5)
        self.assertTrue(summary["performance_change"]["verdict_changed"])

    def test_empty_agent_outputs(self) -> None:
        metrics = {"calmar": 0, "sharpe": 0, "max_dd": 0}
        summary = generate_run_summary({}, metrics, "discard", 1)
        self.assertEqual(summary["verdict"], "discard")


class TestSaveLoadRunSummary(unittest.TestCase):

    def test_round_trip(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run_001"
            run_dir.mkdir()
            summary = {"round": 1, "verdict": "keep", "metrics": {"calmar": 1.5}}
            fp = save_run_summary(run_dir, summary)
            self.assertTrue(fp.exists())
            loaded = load_run_summary(run_dir)
            self.assertEqual(loaded["verdict"], "keep")

    def test_load_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "nonexistent"
            result = load_run_summary(run_dir)
            self.assertIsNone(result)


class TestShouldReadDetailed(unittest.TestCase):

    def test_recent(self) -> None:
        self.assertTrue(should_read_detailed(95, 100, keep_recent=10))

    def test_old(self) -> None:
        self.assertFalse(should_read_detailed(80, 100, keep_recent=10))

    def test_equal_to_keep_recent(self) -> None:
        self.assertFalse(should_read_detailed(90, 100, keep_recent=10))


class TestGetRunData(unittest.TestCase):

    def test_detailed_source(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run_0100"
            agents_dir = run_dir / "agents"
            agents_dir.mkdir(parents=True)
            (agents_dir / "researcher.json").write_text(
                json.dumps({"output": {"action": "search"}}), encoding="utf-8"
            )
            result = get_run_data(run_dir, current_round=100)
            self.assertEqual(result["source"], "agents")
            self.assertTrue(result["detailed"])
            self.assertIn("researcher", result["data"])

    def test_summary_source(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run_0050"
            run_dir.mkdir()
            (run_dir / "summary.json").write_text(
                json.dumps({"verdict": "keep"}), encoding="utf-8"
            )
            result = get_run_data(run_dir, current_round=100)
            self.assertEqual(result["source"], "summary")
            self.assertFalse(result["detailed"])

    def test_no_data(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run_0050"
            run_dir.mkdir()
            result = get_run_data(run_dir, current_round=100)
            self.assertEqual(result["source"], "none")

    def test_invalid_dir_name(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "invalid_name"
            run_dir.mkdir()
            result = get_run_data(run_dir, current_round=100)
            self.assertEqual(result["source"], "none")


if __name__ == "__main__":
    unittest.main()
