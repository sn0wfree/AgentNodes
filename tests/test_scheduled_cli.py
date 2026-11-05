"""H 档补测 —— `core/scheduled_research` 的 CLI 命令与参数解析。

此前 15%（全文件最低）：``cli.py`` 的命令函数全部零覆盖。它们是「定时
研究」产品能力的入口（`api/routers/schedule.py`、`api/app.py`、
`cli/__init__.py` 共 13 处调用）。

通过 argparse + monkeypatch env（store 落到临时 goals.db）驱动真实命令
函数；`_parse_metric_targets` 的表达式语法（含报错分支）单独锁。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agentnodes.kernel.scheduled_research import cli
from agentnodes.kernel.scheduled_research.cli import (
    _parse_metric_targets,
    cmd_schedule_cancel,
    cmd_schedule_create,
    cmd_schedule_delete,
    cmd_schedule_list,
    cmd_schedule_show,
)
from agentnodes.kernel.scheduled_research.store import ScheduledResearchStore


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    """store 落到临时 goals.db；workspace 用真实临时目录。"""
    monkeypatch.setenv("AGENTNODES_GOAL_DB_PATH",
                       str(tmp_path / "goals.db"))
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ws


def _args(**kw) -> argparse.Namespace:
    base = dict(workspace=".", strategy="s", prompt="", cron="", interval=0,
                max_rounds=1, metric=None, budget_turn=None, budget_token=None,
                budget_time=None, monitor_interval=None, guidance_file=None,
                job_id="")
    base.update(kw)
    return argparse.Namespace(**base)


# ── _parse_metric_targets ───────────────────────────────────


def test_metric_targets_none_for_empty():
    assert _parse_metric_targets(None) is None
    assert _parse_metric_targets("") is None


def test_metric_targets_single():
    assert _parse_metric_targets("calmar>=0.5") == [
        {"name": "calmar", "op": ">=", "value": 0.5}]


def test_metric_targets_multi_with_spaces():
    out = _parse_metric_targets("calmar>=0.5, sharpe<=1.0 , max_dd<-0.1")
    assert out == [
        {"name": "calmar", "op": ">=", "value": 0.5},
        {"name": "sharpe", "op": "<=", "value": 1.0},
        {"name": "max_dd", "op": "<", "value": -0.1},
    ]


def test_metric_targets_equality_op():
    assert _parse_metric_targets("win_rate==0.6") == [
        {"name": "win_rate", "op": "==", "value": 0.6}]


def test_metric_targets_missing_op_raises():
    with pytest.raises(ValueError, match="无效的指标表达式"):
        _parse_metric_targets("calmar 0.5")


def test_metric_targets_bad_value_raises():
    with pytest.raises(ValueError, match="无效的指标值"):
        _parse_metric_targets("calmar>=abc")


# ── cmd_schedule_create ─────────────────────────────────────


def test_create_requires_cron_or_interval(store_env, capsys):
    assert cmd_schedule_create(_args(workspace=str(store_env))) == 1
    assert "必须指定" in capsys.readouterr().out


def test_create_rejects_bad_cron(store_env, capsys):
    rc = cmd_schedule_create(_args(workspace=str(store_env), cron="not a cron"))
    assert rc == 1
    assert "无效的 cron" in capsys.readouterr().out


def test_create_with_interval_succeeds(store_env, capsys):
    rc = cmd_schedule_create(_args(workspace=str(store_env), interval=3600))
    assert rc == 0
    assert "已创建定时任务" in capsys.readouterr().out
    store = ScheduledResearchStore()
    jobs = store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].interval_ms == 3600 * 1000


def test_create_with_cron_succeeds(store_env):
    assert cmd_schedule_create(
        _args(workspace=str(store_env), cron="0 8 * * 1")) == 0
    job = ScheduledResearchStore().list_jobs()[0]
    assert job.cron == "0 8 * * 1"


def test_create_rejects_bad_metric_spec(store_env, capsys):
    rc = cmd_schedule_create(_args(workspace=str(store_env), interval=60,
                                   metric="calmar 0.5"))
    assert rc == 1
    assert "无效的指标" in capsys.readouterr().out


def test_create_with_metric_targets_persisted(store_env):
    cmd_schedule_create(_args(workspace=str(store_env), interval=60,
                              metric="calmar>=0.5"))
    job = ScheduledResearchStore().list_jobs()[0]
    assert job.config["metric_targets"] == [
        {"name": "calmar", "op": ">=", "value": 0.5}]


def test_create_rejects_missing_guidance_file(store_env, capsys):
    rc = cmd_schedule_create(_args(workspace=str(store_env), interval=60,
                                   guidance_file=str(store_env / "nope.md")))
    assert rc == 1
    assert "guidance 文件不存在" in capsys.readouterr().out


def test_create_reads_guidance_file(store_env):
    g = store_env / "guidance.md"
    g.write_text("# 指导\n只用日线数据。", encoding="utf-8")
    assert cmd_schedule_create(_args(workspace=str(store_env), interval=60,
                                     guidance_file=str(g))) == 0
    job = ScheduledResearchStore().list_jobs()[0]
    assert "日线数据" in job.config["guidance_md"]


def test_create_budget_and_monitor_config(store_env):
    cmd_schedule_create(_args(workspace=str(store_env), interval=60,
                              budget_turn=10, budget_token=5000,
                              budget_time=600.0, monitor_interval=120))
    cfg = ScheduledResearchStore().list_jobs()[0].config
    assert cfg["budget_turn"] == 10
    assert cfg["budget_token"] == 5000
    assert cfg["budget_time_seconds"] == 600.0
    assert cfg["monitor_interval_seconds"] == 120


# ── list / show / cancel / delete ───────────────────────────


def _make_job(ws: Path) -> str:
    assert cmd_schedule_create(_args(workspace=str(ws), interval=3600)) == 0
    return ScheduledResearchStore().list_jobs()[0].id


def test_list_empty_ok(store_env, capsys):
    assert cmd_schedule_list(_args()) == 0
    assert "暂无定时任务" in capsys.readouterr().out


def test_list_shows_job(store_env, capsys):
    """workspace 过滤值必须 resolve——job 里存的是绝对路径。

    回归：cmd_schedule_list 曾把 args.workspace 原样传下去，默认值 "."
    与库里的绝对路径比较永不相等，导致 `schedule list` 恒显示「暂无」。
    """
    jid = _make_job(store_env)
    assert cmd_schedule_list(_args(workspace=str(store_env))) == 0
    out = capsys.readouterr().out
    assert jid[:16] in out
    assert "every 3600s" in out


def test_list_resolves_relative_workspace(store_env, capsys, monkeypatch):
    """相对路径（如 "."）也要能匹配到绝对路径存的任务。"""
    jid = _make_job(store_env)
    monkeypatch.chdir(store_env)
    assert cmd_schedule_list(_args(workspace=".")) == 0
    assert jid[:16] in capsys.readouterr().out


def test_list_filters_by_workspace(store_env):
    jid = _make_job(store_env)
    other = store_env.parent / "other-ws"
    other.mkdir(exist_ok=True)
    # 不同 workspace 的任务不应出现在过滤结果里
    rc = cmd_schedule_list(_args(workspace=str(other)))
    assert rc == 0


def test_show_missing_returns_1(store_env, capsys):
    assert cmd_schedule_show(_args(job_id="ghost")) == 1
    assert "不存在" in capsys.readouterr().out


def test_show_existing(store_env, capsys):
    jid = _make_job(store_env)
    assert cmd_schedule_show(_args(job_id=jid)) == 0
    out = capsys.readouterr().out
    assert "定时任务详情" in out
    assert str(store_env) in out


def test_cancel_missing_returns_1(store_env, capsys):
    assert cmd_schedule_cancel(_args(job_id="ghost")) == 1


def test_cancel_marks_cancelled(store_env, capsys):
    jid = _make_job(store_env)
    assert cmd_schedule_cancel(_args(job_id=jid)) == 0
    job = ScheduledResearchStore().get(jid)
    assert job.status.value == "cancelled"


def test_delete_missing_returns_1(store_env, capsys):
    assert cmd_schedule_delete(_args(job_id="ghost")) == 1


def test_delete_removes_job(store_env, capsys):
    jid = _make_job(store_env)
    assert cmd_schedule_delete(_args(job_id=jid)) == 0
    assert ScheduledResearchStore().get(jid) is None


# ── add_schedule_subparsers 完整性 ──────────────────────────


def test_add_schedule_subparsers_registers_all_commands():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    cli.add_schedule_subparsers(sub)

    known = {"create", "list", "show", "cancel", "delete", "run", "start"}
    # 每个 known 命令都能 parse（不抛 SystemExit）
    for cmd in known:
        try:
            ns = parser.parse_args([cmd, "--help"])
        except SystemExit:
            pass   # --help 以 0 退出，能到这说明子命令存在
