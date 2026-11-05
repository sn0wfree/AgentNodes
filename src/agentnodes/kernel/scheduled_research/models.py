"""Scheduled Research data models."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    """Scheduled job status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ScheduledResearchJob:
    """A scheduled research job.

    Either `cron` or `interval_ms` must be set (not both).
    - cron: 5-field cron expression (min hour dom month dow)
    - interval_ms: milliseconds between runs
    """

    id: str = ""
    workspace: str = ""
    strategy_name: str = ""
    prompt: str = ""
    cron: str = ""
    interval_ms: int = 0
    next_run_at: float = 0.0
    created_at: float = 0.0
    last_run_at: float | None = None
    last_run_id: str | None = None
    status: JobStatus = JobStatus.PENDING
    config: dict = field(default_factory=dict)
    max_rounds: int = 1
    target: str = "study"  # 'study' (default) | 'autoresearch' (legacy subprocess)
    owner_session_id: str | None = None  # API 创建者 chat 会话（IDOR）

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"job_{uuid.uuid4().hex[:8]}"
        if not self.created_at:
            self.created_at = time.time()

    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage."""
        return {
            "id": self.id,
            "workspace": self.workspace,
            "strategy_name": self.strategy_name,
            "prompt": self.prompt,
            "cron": self.cron,
            "interval_ms": self.interval_ms,
            "next_run_at": self.next_run_at,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "last_run_id": self.last_run_id,
            "status": self.status.value,
            "config": self.config,
            "max_rounds": self.max_rounds,
            "target": self.target,
            "owner_session_id": self.owner_session_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ScheduledResearchJob:
        """Deserialize from dict."""
        return cls(
            id=data.get("id", ""),
            workspace=data.get("workspace", ""),
            strategy_name=data.get("strategy_name", ""),
            prompt=data.get("prompt", ""),
            cron=data.get("cron", ""),
            interval_ms=data.get("interval_ms", 0),
            next_run_at=data.get("next_run_at", 0.0),
            created_at=data.get("created_at", 0.0),
            last_run_at=data.get("last_run_at"),
            last_run_id=data.get("last_run_id"),
            status=JobStatus(data.get("status", "pending")),
            config=data.get("config", {}),
            max_rounds=data.get("max_rounds", 1),
            target=data.get("target", "study"),
            owner_session_id=data.get("owner_session_id"),
        )

    def study_params(self) -> dict:
        """config → study 创建参数字典（dispatch 桥透传用）。

        Keys mirror ``StudyStartRequest`` (minus identity/workspace/objective
        which come from the job's own fields).
        """
        return {
            "metric_targets": self.config.get("metric_targets"),
            "budget_token": self.config.get("budget_token"),
            "budget_turn": self.config.get("budget_turn"),
            "budget_time_seconds": self.config.get("budget_time_seconds"),
            "guidance_md": self.config.get("guidance_md"),
            "monitor_interval_seconds": self.config.get("monitor_interval_seconds"),
            "cooldown_base": self.config.get("cooldown_base", 30.0),
            "cooldown_jitter": self.config.get("cooldown_jitter", 10.0),
            "min_cooldown": self.config.get("min_cooldown", 1.0),
            "max_rounds": self.config.get("max_rounds", self.max_rounds),
            "behavior": self.config.get("behavior"),
            "keep_recent": self.config.get("keep_recent", 10),
            "lazy_detection_interval": self.config.get("lazy_detection_interval", 10),
        }

    def is_due(self, now: float | None = None) -> bool:
        """Check if job is due to run."""
        if self.status not in (JobStatus.PENDING, JobStatus.COMPLETED):
            return False
        t = now if now is not None else time.time()
        return self.next_run_at > 0 and self.next_run_at <= t

    def is_recurring(self) -> bool:
        """Check if job should repeat."""
        return bool(self.cron) or self.interval_ms > 0
