"""Tests for strategy_acceptance/llm_eval.py — LLMEvaluator."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.strategy_acceptance.llm_eval import (
    LLMEvaluator,
    _fail_verdict,
    evaluate_or_skip,
)


class MockClient:
    def __init__(self, response: str | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error

    def chat(self, **kwargs) -> MagicMock:
        if self._error:
            raise self._error
        resp = MagicMock()
        resp.content = self._response
        return resp


class TestLLMEvaluator(unittest.TestCase):

    def test_evaluate_success(self) -> None:
        client = MockClient('{"passed": true, "score": 0.85, "reason": "good", "concerns": []}')
        evaluator = LLMEvaluator(client=client)
        metrics = {"calmar": 1.5, "sharpe": 0.8}
        result = evaluator.evaluate(metrics)
        self.assertTrue(result["passed"])
        self.assertAlmostEqual(result["score"], 0.85)
        self.assertEqual(result["reason"], "good")

    def test_evaluate_with_threshold(self) -> None:
        client = MockClient('{"passed": false, "score": 0.6, "reason": "ok"}')
        evaluator = LLMEvaluator(client=client)
        metrics = {"calmar": 1.0}
        cfg = MagicMock()
        cfg.llm_score_threshold = 0.5
        result = evaluator.evaluate(metrics, cfg=cfg)
        self.assertTrue(result["passed"])

    def test_evaluate_below_threshold(self) -> None:
        client = MockClient('{"passed": true, "score": 0.4, "reason": "weak"}')
        evaluator = LLMEvaluator(client=client)
        metrics = {"calmar": 1.0}
        cfg = MagicMock()
        cfg.llm_score_threshold = 0.5
        result = evaluator.evaluate(metrics, cfg=cfg)
        self.assertFalse(result["passed"])

    def test_chat_failure_returns_fail_verdict(self) -> None:
        client = MockClient(error=RuntimeError("network error"))
        evaluator = LLMEvaluator(client=client)
        metrics = {"calmar": 1.0}
        result = evaluator.evaluate(metrics)
        self.assertFalse(result["passed"])
        self.assertEqual(result["score"], 0.0)
        self.assertIn("network error", result["reason"])

    def test_malformed_json_returns_fail(self) -> None:
        client = MockClient("not json at all")
        evaluator = LLMEvaluator(client=client)
        metrics = {"calmar": 1.0}
        result = evaluator.evaluate(metrics)
        self.assertFalse(result["passed"])

    def test_empty_response_returns_fail(self) -> None:
        client = MockClient("")
        evaluator = LLMEvaluator(client=client)
        metrics = {"calmar": 1.0}
        result = evaluator.evaluate(metrics)
        self.assertFalse(result["passed"])

    def test_json_not_object_returns_fail(self) -> None:
        client = MockClient("[1, 2, 3]")
        evaluator = LLMEvaluator(client=client)
        metrics = {"calmar": 1.0}
        result = evaluator.evaluate(metrics)
        self.assertFalse(result["passed"])

    def test_build_prompt_includes_metrics(self) -> None:
        client = MockClient('{"passed": true}')
        evaluator = LLMEvaluator(client=client)
        prompt = evaluator._build_prompt({"calmar": 1.5, "sharpe": 0.8}, None)
        self.assertIn("| calmar | 1.5 |", prompt)
        self.assertIn("| sharpe | 0.8 |", prompt)

    def test_build_prompt_includes_summary(self) -> None:
        client = MockClient('{"passed": true}')
        evaluator = LLMEvaluator(client=client)
        prompt = evaluator._build_prompt(
            {"calmar": 1.5},
            {"hypothesis": "test hypothesis", "key_insight": "test insight"},
        )
        self.assertIn("test hypothesis", prompt)
        self.assertIn("test insight", prompt)

    def test_extract_json_block_simple(self) -> None:
        result = LLMEvaluator._extract_json_block('{"a": 1}')
        self.assertEqual(result, '{"a": 1}')

    def test_extract_json_block_nested(self) -> None:
        result = LLMEvaluator._extract_json_block('{"a": {"b": 2}}')
        self.assertEqual(result, '{"a": {"b": 2}}')

    def test_extract_json_block_with_surrounding_text(self) -> None:
        result = LLMEvaluator._extract_json_block('ok {"a": 1} end')
        self.assertEqual(result, '{"a": 1}')

    def test_extract_json_block_no_brace(self) -> None:
        result = LLMEvaluator._extract_json_block("no braces")
        self.assertIsNone(result)

    def test_extract_json_block_unmatched_brace(self) -> None:
        result = LLMEvaluator._extract_json_block('{"a": 1')
        self.assertIsNone(result)

    def test_parse_verdict_concerns_valid(self) -> None:
        client = MockClient('{"passed": true, "concerns": ["a", "b"]}')
        evaluator = LLMEvaluator(client=client)
        result = evaluator.evaluate({"calmar": 1.0})
        self.assertEqual(result["concerns"], ["a", "b"])

    def test_parse_verdict_concerns_not_list(self) -> None:
        text = '{"passed": true, "concerns": "not a list"}'
        result = LLMEvaluator(client=MagicMock())._parse_verdict(text)
        self.assertEqual(result["concerns"], [])

    def test_parse_verdict_score_clamped_high(self) -> None:
        text = '{"passed": true, "score": 2.5}'
        result = LLMEvaluator(client=MagicMock())._parse_verdict(text)
        self.assertEqual(result["score"], 1.0)

    def test_parse_verdict_score_clamped_low(self) -> None:
        text = '{"passed": true, "score": -1.0}'
        result = LLMEvaluator(client=MagicMock())._parse_verdict(text)
        self.assertEqual(result["score"], 0.0)

    def test_parse_verdict_reason_empty(self) -> None:
        text = '{"passed": true, "reason": ""}'
        result = LLMEvaluator(client=MagicMock())._parse_verdict(text)
        self.assertEqual(result["reason"], "<no reason>")


class TestEvaluateOrSkip(unittest.TestCase):

    def test_llm_disabled_returns_none(self) -> None:
        cfg = MagicMock()
        cfg.llm_enabled = False
        result = evaluate_or_skip({"calmar": 1.0}, None, cfg)
        self.assertIsNone(result)

    def test_llm_enabled_with_client(self) -> None:
        client = MockClient('{"passed": true, "score": 0.9, "reason": "good"}')
        cfg = MagicMock()
        cfg.llm_enabled = True
        cfg.llm_score_threshold = 0.5
        result = evaluate_or_skip({"calmar": 1.0}, None, cfg, client=client)
        self.assertTrue(result["passed"])

    def test_llm_enabled_no_client_fallback(self) -> None:
        cfg = MagicMock()
        cfg.llm_enabled = True
        result = evaluate_or_skip({"calmar": 1.0}, None, cfg)
        self.assertFalse(result["passed"])


class TestFailVerdict(unittest.TestCase):

    def test_fail_verdict(self) -> None:
        result = _fail_verdict("something went wrong")
        self.assertFalse(result["passed"])
        self.assertEqual(result["score"], 0.0)
        self.assertIn("something went wrong", result["concerns"])


if __name__ == "__main__":
    unittest.main()
