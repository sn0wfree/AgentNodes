"""Git 操作工具。"""
from __future__ import annotations

import subprocess
from pathlib import Path


def git_commit(workspace_path: Path, message: str) -> bool:
    """提交当前更改。"""
    try:
        subprocess.run(
            ["git", "add", "."],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def git_reset(workspace_path: Path) -> bool:
    """重置到上一次提交。"""
    try:
        subprocess.run(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def git_get_hash(workspace_path: Path, short: bool = True) -> str:
    """获取当前 commit hash。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short" if short else "HEAD"],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return ""


def git_commit_rich(
    workspace_path: Path,
    strategy_name: str,
    run_name: str,
    status: str,
    metrics: dict,
    action: str = "",
    hypothesis: str = "",
) -> bool:
    """提交当前更改 (rich format)。

    Commit message 格式:
    {status}: {strategy_name}/{run_name} | Calmar={X.XX} Sharpe={X.XX} MaxDD={X.XX%} | {action}: {hypothesis}
    """
    calmar = metrics.get("calmar", 0)
    sharpe = metrics.get("sharpe", 0)
    max_dd = metrics.get("max_dd", 0)

    message = f"{status}: {strategy_name}/{run_name} | Calmar={calmar:.2f} Sharpe={sharpe:.2f} MaxDD={max_dd:.1%}"
    if action:
        message += f" | {action}"
    if hypothesis:
        message += f": {hypothesis}"

    return git_commit(workspace_path, message)

