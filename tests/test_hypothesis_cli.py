"""Tests for core.hypothesis.cli — 6 subcommands + argparse wiring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from strategy_research.apps.hypothesis import HypothesisRegistry
from strategy_research.apps.hypothesis.cli import (
    add_hypothesis_subparsers,
    cmd_hypothesis_create,
    cmd_hypothesis_link,
    cmd_hypothesis_list,
    cmd_hypothesis_search,
    cmd_hypothesis_show,
    cmd_hypothesis_update,
)


def _make_args(**kwargs) -> argparse.Namespace:
    base = dict(
        path=None,
        title=None,
        thesis=None,
        status=None,
        universe="",
        signal="",
        data_source=None,
        skill=None,
        invalidation_notes="",
        hypothesis_id=None,
        query="",
        limit=10,
        run_card=None,
        run_dir=None,
        metric=None,
        notes="",
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_hypothesis_subparsers(sub)
    return parser


def _parse(parser, *args):
    return parser.parse_args(["hypothesis", *args])


@pytest.fixture
def custom_registry(tmp_path: Path, monkeypatch):
    """Use a per-test hypotheses.json via --path."""
    return tmp_path / "hypotheses.json"


# ─── create ──────────────────────────────────────────────────────────────


class TestCreate:
    def test_basic(self, custom_registry, capsys):
        args = _make_args(
            path=str(custom_registry),
            title="Momentum A-shares",
            thesis="20-day momentum predicts 60-day returns",
            status="exploring",
            universe="a_share",
            signal="momentum_20_60",
        )
        rc = cmd_hypothesis_create(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Hypothesis created" in out
        assert "Momentum A-shares" in out

    def test_rejects_empty_title(self, custom_registry, capsys):
        args = _make_args(path=str(custom_registry), title="", thesis="x")
        rc = cmd_hypothesis_create(args)
        assert rc == 1

    def test_rejects_invalid_status(self, custom_registry):
        args = _make_args(
            path=str(custom_registry), title="t", thesis="x", status="bogus",
        )
        rc = cmd_hypothesis_create(args)
        assert rc == 1


# ─── list ────────────────────────────────────────────────────────────────


class TestList:
    def test_empty(self, custom_registry, capsys):
        args = _make_args(path=str(custom_registry))
        rc = cmd_hypothesis_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "no hypotheses" in out

    def test_lists(self, custom_registry, capsys):
        cmd_hypothesis_create(_make_args(
            path=str(custom_registry), title="a", thesis="x",
        ))
        cmd_hypothesis_create(_make_args(
            path=str(custom_registry), title="b", thesis="y", status="testing",
        ))
        capsys.readouterr()
        rc = cmd_hypothesis_list(_make_args(path=str(custom_registry)))
        assert rc == 0
        out = capsys.readouterr().out
        assert "a" in out
        assert "b" in out


# ─── show ────────────────────────────────────────────────────────────────


class TestShow:
    def test_found(self, custom_registry, capsys):
        h = HypothesisRegistry(path=custom_registry).create(
            title="x", thesis="y",
        )
        args = _make_args(path=str(custom_registry), hypothesis_id=h.hypothesis_id)
        rc = cmd_hypothesis_show(args)
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["title"] == "x"

    def test_not_found(self, custom_registry, capsys):
        args = _make_args(path=str(custom_registry), hypothesis_id="hyp_nope")
        rc = cmd_hypothesis_show(args)
        assert rc == 1


# ─── update ──────────────────────────────────────────────────────────────


class TestUpdate:
    def test_update_status(self, custom_registry, capsys):
        h = HypothesisRegistry(path=custom_registry).create(title="x", thesis="y")
        args = _make_args(
            path=str(custom_registry),
            hypothesis_id=h.hypothesis_id,
            status="testing",
        )
        rc = cmd_hypothesis_update(args)
        assert rc == 0
        assert HypothesisRegistry(path=custom_registry).get(h.hypothesis_id).status == "testing"

    def test_invalid_status(self, custom_registry):
        h = HypothesisRegistry(path=custom_registry).create(title="x", thesis="y")
        args = _make_args(
            path=str(custom_registry),
            hypothesis_id=h.hypothesis_id,
            status="bogus",
        )
        rc = cmd_hypothesis_update(args)
        assert rc == 1

    def test_unknown_id(self, custom_registry):
        args = _make_args(
            path=str(custom_registry),
            hypothesis_id="hyp_nope",
            status="testing",
        )
        rc = cmd_hypothesis_update(args)
        assert rc == 1


# ─── search ──────────────────────────────────────────────────────────────


class TestSearch:
    def test_no_match(self, custom_registry, capsys):
        HypothesisRegistry(path=custom_registry).create(title="x", thesis="y")
        args = _make_args(path=str(custom_registry), query="nonexistent_token_xyz")
        rc = cmd_hypothesis_search(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "no matches" in out

    def test_match(self, custom_registry, capsys):
        HypothesisRegistry(path=custom_registry).create(
            title="Momentum in large caps", thesis="20-day winners",
        )
        HypothesisRegistry(path=custom_registry).create(
            title="Value investing", thesis="low P/E",
        )
        capsys.readouterr()
        args = _make_args(path=str(custom_registry), query="momentum")
        rc = cmd_hypothesis_search(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Momentum in large caps" in out

    def test_invalid_status(self, custom_registry):
        args = _make_args(
            path=str(custom_registry), query="x", status="bogus",
        )
        rc = cmd_hypothesis_search(args)
        assert rc == 1


# ─── link ────────────────────────────────────────────────────────────────


class TestLink:
    def test_link_run_card(self, custom_registry, capsys):
        h = HypothesisRegistry(path=custom_registry).create(title="x", thesis="y")
        args = _make_args(
            path=str(custom_registry),
            hypothesis_id=h.hypothesis_id,
            run_card="/path/run_card.json",
            notes="validated",
        )
        rc = cmd_hypothesis_link(args)
        assert rc == 0
        assert len(HypothesisRegistry(path=custom_registry).get(h.hypothesis_id).run_cards) == 1

    def test_link_metrics_parsed(self, custom_registry, capsys):
        h = HypothesisRegistry(path=custom_registry).create(title="x", thesis="y")
        args = _make_args(
            path=str(custom_registry),
            hypothesis_id=h.hypothesis_id,
            run_card="/p/c.json",
            metric=["sharpe=0.85", "max_dd=-0.12", "label=momentum_20"],
        )
        rc = cmd_hypothesis_link(args)
        assert rc == 0
        rc_link = HypothesisRegistry(path=custom_registry).get(h.hypothesis_id).run_cards[0]
        assert rc_link["metrics"]["sharpe"] == 0.85
        assert rc_link["metrics"]["max_dd"] == -0.12
        assert rc_link["metrics"]["label"] == "momentum_20"

    def test_link_invalid_metric_format(self, custom_registry, capsys):
        h = HypothesisRegistry(path=custom_registry).create(title="x", thesis="y")
        args = _make_args(
            path=str(custom_registry),
            hypothesis_id=h.hypothesis_id,
            run_card="/p/c.json",
            metric=["no_equals_sign"],
        )
        rc = cmd_hypothesis_link(args)
        assert rc == 1

    def test_link_requires_run_card_or_dir(self, custom_registry):
        h = HypothesisRegistry(path=custom_registry).create(title="x", thesis="y")
        args = _make_args(
            path=str(custom_registry),
            hypothesis_id=h.hypothesis_id,
        )
        rc = cmd_hypothesis_link(args)
        assert rc == 1


# ─── argparse wiring ─────────────────────────────────────────────────────


class TestArgparseWiring:
    def test_six_subcommands_present(self):
        parser = _make_parser()
        args = parser.parse_args(["hypothesis"])
        assert args.command == "hypothesis"
        assert args.hypothesis_command is None

    def test_create_args(self):
        parser = _make_parser()
        args = _parse(parser, "create",
                      "--title", "x",
                      "--thesis", "y",
                      "--status", "testing",
                      "--universe", "a_share",
                      "--data-source", "tushare",
                      "--data-source", "akshare",
                      "--skill", "momentum",
                      "--invalidation-notes", "drawdown > 30%",
                      )
        assert args.title == "x"
        assert args.thesis == "y"
        assert args.status == "testing"
        assert args.universe == "a_share"
        assert args.data_source == ["tushare", "akshare"]
        assert args.skill == ["momentum"]
        assert args.invalidation_notes == "drawdown > 30%"

    def test_search_args(self):
        parser = _make_parser()
        args = _parse(parser, "search", "--query", "momentum", "--limit", "5")
        assert args.query == "momentum"
        assert args.limit == 5
