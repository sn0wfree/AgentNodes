"""Tests for ``cli.mandate`` — research-proposal intercept."""

from __future__ import annotations

from dataclasses import dataclass, field

from strategy_research.interfaces.cli.interactive.main import process_turn
from strategy_research.interfaces.cli.mandate import (
    Proposal,
    capture_pick,
    has_pending_proposal,
    is_pick,
    make_proposal,
)


@dataclass
class _Ctx:
    pending_proposal: object = None
    history: list = field(default_factory=list)


# ─── Proposal dataclass ────────────────────────────────────────────────


class TestProposal:
    def test_string_choices(self):
        p = Proposal(title="Pick:", choices=["alpha", "beta", "gamma"])
        assert p.title == "Pick:"
        assert p.choice_labels() == ["alpha", "beta", "gamma"]

    def test_dict_choices(self):
        choices = [
            {"label": "Alpha", "payload": {"id": 1}},
            {"label": "Beta", "payload": {"id": 2}},
        ]
        p = Proposal(title="Pick:", choices=choices)
        assert p.choice_labels() == ["Alpha", "Beta"]

    def test_mixed_choices(self):
        choices = [
            "plain",
            {"label": "rich", "payload": {"x": 1}},
        ]
        p = Proposal(title="Pick:", choices=choices)
        assert p.choice_labels() == ["plain", "rich"]

    def test_outer_payload(self):
        p = Proposal(title="t", choices=["a"], payload={"ctx": "research_001"})
        assert p.payload == {"ctx": "research_001"}


# ─── make_proposal ────────────────────────────────────────────────────


class TestMakeProposal:
    def test_basic(self):
        p = make_proposal("Choose:", ["x", "y", "z"])
        assert isinstance(p, Proposal)
        assert p.title == "Choose:"
        assert len(p.choices) == 3

    def test_with_payload(self):
        p = make_proposal("Choose:", ["x"], payload={"run_id": "r1"})
        assert p.payload == {"run_id": "r1"}


# ─── is_pick ───────────────────────────────────────────────────────────


class TestIsPick:
    def test_no_proposal(self):
        assert is_pick("1", None) is False

    def test_empty_string(self):
        p = make_proposal("t", ["a", "b"])
        assert is_pick("", p) is False

    def test_non_integer(self):
        p = make_proposal("t", ["a", "b"])
        assert is_pick("abc", p) is False
        assert is_pick("1.5", p) is False
        assert is_pick("1a", p) is False

    def test_valid_range(self):
        p = make_proposal("t", ["a", "b", "c"])
        assert is_pick("1", p) is True
        assert is_pick("2", p) is True
        assert is_pick("3", p) is True

    def test_out_of_range(self):
        p = make_proposal("t", ["a", "b"])
        assert is_pick("0", p) is False
        assert is_pick("3", p) is False
        assert is_pick("-1", p) is False


# ─── capture_pick ──────────────────────────────────────────────────────


class TestCapturePick:
    def test_returns_dict_for_valid_pick(self):
        p = make_proposal("Choose:", [{"label": "A", "payload": {"x": 1}}, {"label": "B"}])
        result = capture_pick("1", p)
        assert result is not None
        assert result["index"] == 1
        assert result["label"] == "A"
        assert result["payload"] == {"x": 1}

    def test_string_choice(self):
        p = make_proposal("Choose:", ["alpha", "beta"])
        result = capture_pick("2", p)
        assert result is not None
        assert result["index"] == 2
        assert result["label"] == "beta"
        assert result["payload"] is None  # string choice has no payload

    def test_invalid_pick_returns_none(self):
        p = make_proposal("Choose:", ["a"])
        assert capture_pick("99", p) is None
        assert capture_pick("abc", p) is None

    def test_no_proposal_returns_none(self):
        assert capture_pick("1", None) is None

    def test_payload_included(self):
        p = make_proposal(
            "Choose:", ["a"], payload={"ctx": "research_42"}
        )
        result = capture_pick("1", p)
        assert result is not None
        assert result["context"] == {"ctx": "research_42"}

    def test_dict_with_only_label(self):
        p = make_proposal("Choose:", [{"label": "A"}])
        result = capture_pick("1", p)
        assert result is not None
        assert result["label"] == "A"
        assert result["payload"] is None


# ─── has_pending_proposal ──────────────────────────────────────────────


class TestPendingProposal:
    def test_no_proposal(self):
        ctx = _Ctx()
        assert has_pending_proposal(ctx) is False

    def test_with_proposal(self):
        ctx = _Ctx(pending_proposal=make_proposal("t", ["a"]))
        assert has_pending_proposal(ctx) is True

    def test_with_none_value(self):
        ctx = _Ctx(pending_proposal=None)
        assert has_pending_proposal(ctx) is False


# ─── Integration with process_turn ─────────────────────────────────────


class TestProcessTurnIntercept:
    def test_digit_input_with_pending_proposal_consumed(self):
        ctx = _Ctx(pending_proposal=make_proposal("Choose:", ["alpha", "beta"]))
        rc = process_turn("1", ctx)
        assert rc == 0
        # Should NOT be appended to history — consumed by the pick.
        assert ctx.history == []
        # proposal cleared after pick
        assert ctx.pending_proposal is None

    def test_digit_out_of_range_falls_through(self):
        ctx = _Ctx(
            pending_proposal=make_proposal("Choose:", ["alpha"])
        )
        rc = process_turn("99", ctx)
        assert rc == 0
        # Out-of-range → fall through to history
        assert len(ctx.history) == 1
        # proposal NOT cleared (still pending)
        assert ctx.pending_proposal is not None

    def test_text_with_pending_proposal_falls_through(self):
        ctx = _Ctx(pending_proposal=make_proposal("Choose:", ["alpha"]))
        process_turn('Try alpha again', ctx)
        # Not a digit string — falls through to history
        assert len(ctx.history) == 1
        # proposal NOT consumed
        assert ctx.pending_proposal is not None

    def test_digit_with_no_proposal_just_history(self):
        ctx = _Ctx(pending_proposal=None)
        process_turn('3', ctx)
        # No proposal — digit is plain text, goes to history
        assert len(ctx.history) == 1
        assert ctx.history[0]["content"] == "3"

    def test_proposal_cleared_after_successful_pick(self):
        proposal = make_proposal("Choose:", ["x", "y"])
        ctx = _Ctx(pending_proposal=proposal)
        process_turn("2", ctx)
        assert ctx.pending_proposal is None
