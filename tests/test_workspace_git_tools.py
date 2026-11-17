"""foundation/git.py + workspace git/skills 工具的单元测试。

覆盖此前 19.4%（git.py）与 26.5%（workspace_tools.py）的两个薄弱模块：
用 ``git init`` 的 tmp 仓库驱动真实 git 行为（本机 git 可用），
skills 工具覆盖错误路径与空列表（深格式由 skills 域测试负责）。
"""
from __future__ import annotations

import json
import subprocess

import pytest

from agentnodes.foundation import git as gitmod
from agentnodes.kernel.agent.tools import ToolContext


def _git(*args, cwd):
    subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True,
    )


@pytest.fixture
def repo(tmp_path):
    """带一次提交的 tmp git 仓库（user 配置就地设置，不碰全局）。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    _git("init", cwd=ws)
    _git("config", "user.email", "t@t", cwd=ws)
    _git("config", "user.name", "t", cwd=ws)
    (ws / "a.txt").write_text("v1")
    _git("add", ".", cwd=ws)
    _git("commit", "-m", "init", cwd=ws)
    return ws


class TestFoundationGit:
    def test_git_commit_and_hash(self, repo):
        (repo / "a.txt").write_text("v2")
        assert gitmod.git_commit(repo, "second") is True
        h = gitmod.git_get_hash(repo, short=True)
        assert h and len(h) == 7
        full = gitmod.git_get_hash(repo, short=False)
        assert len(full) == 40 and full.startswith(h)

    def test_git_commit_fails_without_repo(self, tmp_path):
        assert gitmod.git_commit(tmp_path, "x") is False

    def test_git_reset_rolls_back(self, repo):
        (repo / "a.txt").write_text("v2")
        gitmod.git_commit(repo, "second")
        assert gitmod.git_reset(repo) is True
        assert (repo / "a.txt").read_text() == "v1"

    def test_git_reset_fails_without_repo(self, tmp_path):
        assert gitmod.git_reset(tmp_path) is False

    def test_git_commit_rich_message_format(self, repo):
        (repo / "a.txt").write_text("rich-v2")  # 无改动时 commit 会失败
        assert gitmod.git_commit_rich(
            repo, "momentum", "run3", "accepted",
            {"calmar": 1.5, "sharpe": 2.0, "max_dd": 0.12},
            action="integrate", hypothesis="缩短窗口",
        ) is True
        msg = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"], cwd=str(repo),
            capture_output=True, text=True,
        ).stdout
        assert "accepted: momentum/run3" in msg
        assert "Calmar=1.50 Sharpe=2.00 MaxDD=12.0%" in msg
        assert "integrate: 缩短窗口" in msg

    def test_git_get_hash_empty_without_repo(self, tmp_path):
        assert gitmod.git_get_hash(tmp_path) == ""


# ── workspace git 工具 ───────────────────────────────────────────────


class TestGitDiffTool:
    def test_diff_shows_modification(self, repo):
        from agentnodes.kernel.agent.builtin_tools.workspace_tools import GitDiffTool

        (repo / "a.txt").write_text("v2-changed\n")
        out = json.loads(GitDiffTool().execute(ctx=ToolContext(workspace=repo)))
        assert out["status"] == "ok"
        assert "a.txt" in out["diff"]
        assert "-v1" in out["diff"] and "+v2-changed" in out["diff"]

    def test_diff_no_changes_empty(self, repo):
        from agentnodes.kernel.agent.builtin_tools.workspace_tools import GitDiffTool

        out = json.loads(GitDiffTool().execute(ctx=ToolContext(workspace=repo)))
        assert out["status"] == "ok"
        assert out["diff"].strip() == ""

    def test_diff_requires_workspace(self):
        from agentnodes.kernel.agent.builtin_tools.workspace_tools import GitDiffTool

        out = json.loads(GitDiffTool().execute(ctx=ToolContext()))
        assert out["status"] == "error" and "workspace" in out["error"].lower()

    def test_diff_error_on_non_repo(self, tmp_path):
        from agentnodes.kernel.agent.builtin_tools.workspace_tools import GitDiffTool

        out = json.loads(GitDiffTool().execute(
            ctx=ToolContext(workspace=str(tmp_path))))
        assert out["status"] == "error"
        assert "git init" in out.get("fix", "").lower() or "git" in out["error"].lower()


class TestListHistoryTool:
    def test_reads_results_tsv(self, repo):
        from agentnodes.kernel.agent.builtin_tools.workspace_tools import ListHistoryTool

        run_dir = repo / "strategies" / "mom" / "runs"
        run_dir.mkdir(parents=True)
        (run_dir / "results.tsv").write_text(
            "run\tstatus\tcalmar\nr1\taccepted\t1.5\nr2\trejected\t0.2\n"
        )
        out = json.loads(ListHistoryTool().execute(
            ctx=ToolContext(workspace=repo), strategy_name="mom"))
        assert out["status"] == "ok"
        assert len(out["runs"]) == 2
        assert out["runs"][0]["run"] == "r2", "最新在前"

    def test_missing_tsv_is_empty_not_error(self, repo):
        from agentnodes.kernel.agent.builtin_tools.workspace_tools import ListHistoryTool

        out = json.loads(ListHistoryTool().execute(
            ctx=ToolContext(workspace=repo)))
        assert out["status"] == "ok"
        assert out.get("runs") == []

    def test_requires_workspace(self):
        from agentnodes.kernel.agent.builtin_tools.workspace_tools import ListHistoryTool

        out = json.loads(ListHistoryTool().execute(ctx=ToolContext()))
        assert out["status"] == "error"


class TestSkillsTools:
    def test_list_skills_empty_workspace(self, repo):
        from agentnodes.kernel.agent.builtin_tools.workspace_tools import ListSkillsTool

        out = json.loads(ListSkillsTool().execute(ctx=ToolContext(workspace=repo)))
        assert out["status"] == "ok"
        assert out.get("skills") == []

    def test_list_skills_requires_workspace(self):
        from agentnodes.kernel.agent.builtin_tools.workspace_tools import ListSkillsTool

        out = json.loads(ListSkillsTool().execute(ctx=ToolContext()))
        assert out["status"] == "error"

    def test_load_skill_missing_name(self, repo):
        from agentnodes.kernel.agent.builtin_tools.workspace_tools import LoadSkillTool

        out = json.loads(LoadSkillTool().execute(
            ctx=ToolContext(workspace=repo), name=""))
        assert out["status"] == "error"

    def test_load_skill_nonexistent_lists_available(self, repo):
        from agentnodes.kernel.agent.builtin_tools.workspace_tools import LoadSkillTool

        out = json.loads(LoadSkillTool().execute(
            ctx=ToolContext(workspace=repo), name="no-such-skill"))
        assert out["status"] == "error"
        assert "available" in json.dumps(out, ensure_ascii=False).lower() or \
            "no-such-skill" in out.get("error", "")

    def test_load_skill_requires_workspace(self):
        from agentnodes.kernel.agent.builtin_tools.workspace_tools import LoadSkillTool

        out = json.loads(LoadSkillTool().execute(ctx=ToolContext(), name="x"))
        assert out["status"] == "error"
