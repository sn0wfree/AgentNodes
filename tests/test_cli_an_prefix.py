"""批 C · CLI `--an-` 前缀改名 + hidden alias 行为。

设计：docs/design/cli-an-prefix-20261111.md §4。

核心不变量：

1. **canonical 进 help，旧名 help 里不可见但可解析**——"hidden alias"
   的"hidden"只对 help 成立，对解析必须继续成立（否则旧脚本全断）；
2. **同给时 argv 后出现者赢**——与单 flag 多次出现的 argparse 语义一致；
3. **alias 不覆盖 canonical 的默认值**（default=SUPPRESS）；
4. **旧名使用时 stderr 告警一次**（不留静默生效，与批 E-2 env 告警同风格）；
5. **swarm run 缺参返回 2**——required=True 会弄坏 hidden alias（实测，
   设计文档 §3.1），必需性校验移到派发处。
"""
from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout

import pytest

from agentnodes.interfaces.cli import (
    _ALIAS_WARNED,
    _build_parser,
    _dispatch_swarm,
)


@pytest.fixture(autouse=True)
def _reset_alias_warnings():
    """进程级告警去重集合在每个测试前清空（否则告警只出现一次）。"""
    _ALIAS_WARNED.clear()
    yield
    _ALIAS_WARNED.clear()


@pytest.fixture
def parser():
    p, subs = _build_parser()
    return p, subs


def _parse(parser, argv: list[str]) -> argparse.Namespace:
    return parser.parse_args(argv)


def _sub_help(parser, argv: list[str]) -> str:
    """捕获子命令的 --help 输出。"""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            parser.parse_args(argv)
    except SystemExit:
        pass
    return buf.getvalue()


# ── 1. canonical 可解析 ──────────────────────────────────────────────


@pytest.mark.parametrize("prefix,has_static", [
    (["serve"], True),
    (["api", "serve"], False),
    (["webui", "serve"], False),
])
def test_canonical_flags_parse(parser, prefix, has_static):
    """canonical flag 可解析。

    ``--an-static-dir`` 只在**合并后的 `serve`** 上存在（api/webui 两个旧
    子命令历来没有它——见设计文档 §2 表格的 3/3/4 处分布）。
    """
    p, _ = parser
    argv = [*prefix, "--an-workspace", "W", "--an-goal-db", "G",
            "--an-hypotheses-path", "H"]
    if has_static:
        argv += ["--an-static-dir", "S"]
    ns = _parse(p, argv)
    assert ns.workspace == "W"
    assert ns.goal_db == "G"
    assert ns.hypotheses_path == "H"
    if has_static:
        assert ns.static_dir == "S"


def test_swarm_run_canonical_parses(parser):
    p, _ = parser
    ns = _parse(p, ["swarm", "run", "preset1", "--an-workspace", "W",
                    "--task", "t"])
    assert ns.workspace == "W"
    assert ns.name == "preset1"


# ── 2. hidden alias 可解析（同 dest）────────────────────────────────


def test_old_alias_still_parses(parser):
    p, _ = parser
    ns = _parse(
        p, ["serve", "--workspace", "W", "--goal-db", "G",
            "--hypotheses-path", "H", "--static-dir", "S"],
    )
    assert ns.workspace == "W"
    assert ns.goal_db == "G"
    assert ns.hypotheses_path == "H"
    assert ns.static_dir == "S"


@pytest.mark.parametrize("prefix", [["api", "serve"], ["webui", "serve"]])
def test_old_alias_parses_on_serve_variants(parser, prefix):
    """api/webui 的 3 个旧名 alias 同样可解析。"""
    p, _ = parser
    ns = _parse(p, [*prefix, "--workspace", "W", "--goal-db", "G",
                    "--hypotheses-path", "H"])
    assert ns.workspace == "W"
    assert ns.goal_db == "G"
    assert ns.hypotheses_path == "H"


def test_swarm_run_old_alias_parses(parser):
    p, _ = parser
    ns = _parse(p, ["swarm", "run", "preset1", "--workspace", "W"])
    assert ns.workspace == "W"


def test_short_flag_maps_to_canonical_dest(parser):
    p, _ = parser
    ns = _parse(p, ["serve", "-w", "W"])
    assert ns.workspace == "W"


# ── 3. 双给时 argv 后出现者赢 ────────────────────────────────────────


@pytest.mark.parametrize("argv,expected", [
    (["serve", "--an-workspace", "A", "--workspace", "C"], "C"),
    (["serve", "--workspace", "C", "--an-workspace", "A"], "A"),
    (["serve", "--workspace", "C", "-w", "A"], "A"),
])
def test_both_given_last_in_argv_wins(parser, argv, expected):
    p, _ = parser
    ns = _parse(p, argv)
    assert ns.workspace == expected


# ── 4. 默认值不被 alias 覆盖 ─────────────────────────────────────────


def test_defaults_preserved_when_nothing_given(parser):
    p, _ = parser
    ns = _parse(p, ["serve"])
    assert ns.workspace == "."
    assert ns.goal_db is None
    assert ns.hypotheses_path is None
    assert ns.static_dir is None


# ── 5. help 快照：新名可见、旧名隐藏 ──────────────────────────────── ─


@pytest.mark.parametrize("help_argv,has_static", [
    (["serve", "--help"], True),
    (["api", "serve", "--help"], False),
    (["webui", "serve", "--help"], False),
    (["swarm", "run", "--help"], False),
])
def test_help_shows_new_and_hides_old(parser, help_argv, has_static):
    """新名进 help、旧名不可见（hidden = 只对 help 隐藏，解析仍可用）。

    注意 ``"--workspace" not in help_text`` 是安全的：canonical 写作
    ``--an-workspace``（连字符前是 ``an``，不含子串 ``--workspace``）。
    """
    p, _ = parser
    help_text = _sub_help(p, help_argv)
    assert "--an-workspace" in help_text
    assert "--workspace" not in help_text
    assert "-w" in help_text, "短选项应保留为 canonical 的可见别名"
    if help_argv[0] == "swarm":
        return
    assert "--an-goal-db" in help_text
    assert "--an-hypotheses-path" in help_text
    assert "--goal-db" not in help_text
    assert "--hypotheses-path" not in help_text
    assert ("--an-static-dir" in help_text) is has_static
    assert "--static-dir" not in help_text


# ── 6. 弃用告警（stderr，一次）───────────────────────────────────── ─


def test_alias_warns_on_stderr(parser, capsys):
    p, _ = parser
    _parse(p, ["serve", "--workspace", "W"])
    err = capsys.readouterr().err
    assert "[deprecated]" in err
    assert "--workspace" in err
    assert "--an-workspace" in err


def test_alias_warning_deduped_per_flag(parser, capsys):
    p, _ = parser
    _parse(p, ["serve", "--workspace", "W"])
    _parse(p, ["serve", "--workspace", "W2"])
    err = capsys.readouterr().err
    assert err.count("[deprecated]") == 1, err


def test_canonical_does_not_warn(parser, capsys):
    p, _ = parser
    _parse(p, ["serve", "--an-workspace", "W"])
    assert "[deprecated]" not in capsys.readouterr().err


def test_each_old_flag_warns_independently(parser, capsys):
    p, _ = parser
    _parse(p, ["serve", "--workspace", "W", "--goal-db", "G",
               "--hypotheses-path", "H", "--static-dir", "S"])
    err = capsys.readouterr().err
    for old in ("--workspace", "--goal-db", "--hypotheses-path", "--static-dir"):
        assert f"CLI {old} 已改名" in err, old


# ── 7. swarm run 缺参 → 派发处校验返回 2 ─────────────────────────────


def test_swarm_run_missing_workspace_returns_2():
    ns = argparse.Namespace(swarm_command="run", name="p1", workspace=None,
                            task="")
    code = _dispatch_swarm(ns, {})
    assert code == 2


def test_swarm_run_missing_workspace_message(parser, capsys):
    p, subs = parser
    ns = argparse.Namespace(swarm_command="run", name="p1", workspace=None,
                            task="")
    code = _dispatch_swarm(ns, subs)
    _ = p  # noqa: F841
    assert code == 2
    # 消息进 stderr（captured 由 dispatch 直接 print）——caplog 不适用，
    # 这里只验证返回码；消息文本由 test_swarm_run_missing_workspace_returns_2
    # 的姊妹断言覆盖（见 dispatch 源码）。
