"""CheckpointStore — persist workflow state for crash recovery (P3.6).

Saves workflow state (layer results, agent statuses, progress) to disk
so execution can resume after a crash or restart.

Storage layout:
    ~/.agentnodes/checkpoints/<session_id>/<goal_id>/
        state.json       — GoalWorkflowState snapshot
        layer_results.json — accumulated agent outputs
        evidence.json    — evidence collected so far
        meta.json        — workflow name, created_at, checkpoint version
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from agentnodes.foundation import paths as _paths
from agentnodes.foundation import settings as _settings

logger = logging.getLogger(__name__)

#: **2026-11-09 路径统一**：默认落 ``~/.agentnodes/checkpoints``（随
#: ``AN_DATA_PROFILE`` 变化）。旧的 ``~/.quantnodes-research`` 已硬切换，
#: 不再读取——需要旧数据请设 ``AN_DATA_PROFILE=quantnodes``。
_DEFAULT_CHECKPOINT_DIR = _paths.data_subdir("checkpoints")
#: 目录覆盖（规范名）。
_CHECKPOINT_DIR_ENV_NEW = "AGENTNODES_CHECKPOINTS_DIR"
#: 目录覆盖（历史名，**仍然生效**——它指向用户数据在哪，见
#: ``settings.LEGACY_LIVE_ENVS``）。
_CHECKPOINT_DIR_ENV = "STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR"
_VERSION = "1.0"


class CheckpointStore:
    """Persists workflow state to disk for crash recovery.

    Args:
        base_dir: Root directory for checkpoints.
                  Default: ``$AGENTNODES_CHECKPOINTS_DIR`` >
                  ``$STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR`` >
                  ``~/.agentnodes/checkpoints/``（随 ``AN_DATA_PROFILE``）。
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        # 优先级：显式 arg > 规范名 env > 历史名 env > profile 默认。
        #
        # **批 E-2 修正**：此前两个分支都读 ``AGENTNODES_CHECKPOINTS_DIR``
        # （改名时把历史名分支也一起改了），历史名分支形同虚设——
        # 老部署的 checkpoint 目录会被静默忽略。
        new_env = _settings.get_str(_CHECKPOINT_DIR_ENV_NEW).strip()
        old_env = _settings.get_legacy(_CHECKPOINT_DIR_ENV, "") or ""
        if base_dir is not None:
            self._base_dir = base_dir
        elif new_env:
            self._base_dir = Path(new_env)
        elif old_env:
            self._base_dir = Path(old_env)
        else:
            self._base_dir = _DEFAULT_CHECKPOINT_DIR

    def _checkpoint_dir(self, session_id: str, goal_id: str) -> Path:
        return self._base_dir / session_id / goal_id

    def save(
        self,
        session_id: str,
        goal_id: str,
        state: dict[str, Any],
        layer_results: dict[str, Any],
        workflow_name: str = "",
    ) -> Path:
        """Save a checkpoint.

        Args:
            session_id: Current session id.
            goal_id: Active goal id.
            state: GoalWorkflowState as dict (from get_summary()).
            layer_results: Accumulated agent outputs.
            workflow_name: Workflow config name.

        Returns:
            Path to the checkpoint directory.
        """
        cp_dir = self._checkpoint_dir(session_id, goal_id)
        cp_dir.mkdir(parents=True, exist_ok=True)

        # State
        (cp_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Layer results
        (cp_dir / "layer_results.json").write_text(
            json.dumps(layer_results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Meta
        meta = {
            "workflow_name": workflow_name,
            "session_id": session_id,
            "goal_id": goal_id,
            "checkpoint_version": _VERSION,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (cp_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        logger.info("Checkpoint saved: %s", cp_dir)
        return cp_dir

    def load(
        self,
        session_id: str,
        goal_id: str,
    ) -> dict[str, Any] | None:
        """Load a checkpoint. Returns None if not found.

        Returns:
            Dict with keys: state, layer_results, meta.
        """
        cp_dir = self._checkpoint_dir(session_id, goal_id)
        if not cp_dir.exists():
            return None

        try:
            state_file = cp_dir / "state.json"
            results_file = cp_dir / "layer_results.json"
            meta_file = cp_dir / "meta.json"

            if not all(f.exists() for f in [state_file, results_file, meta_file]):
                logger.warning("Incomplete checkpoint at %s", cp_dir)
                return None

            return {
                "state": json.loads(state_file.read_text(encoding="utf-8")),
                "layer_results": json.loads(results_file.read_text(encoding="utf-8")),
                "meta": json.loads(meta_file.read_text(encoding="utf-8")),
            }
        except Exception as exc:
            logger.warning("Failed to load checkpoint %s: %s", cp_dir, exc)
            return None

    def delete(self, session_id: str, goal_id: str) -> bool:
        """Delete a checkpoint. Returns True if deleted."""
        import shutil
        cp_dir = self._checkpoint_dir(session_id, goal_id)
        if cp_dir.exists():
            shutil.rmtree(cp_dir)
            logger.info("Checkpoint deleted: %s", cp_dir)
            return True
        return False

    def list_checkpoints(
        self,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all checkpoints, optionally filtered by session_id.

        Returns:
            List of meta dicts.
        """
        if not self._base_dir.exists():
            return []

        results = []
        search_dir = self._base_dir / session_id if session_id else self._base_dir

        if not search_dir.exists():
            return []

        for goal_dir in sorted(search_dir.iterdir()):
            if not goal_dir.is_dir():
                continue
            meta_file = goal_dir / "meta.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    results.append(meta)
                except Exception:
                    pass
        return results


__all__ = ["CheckpointStore"]
