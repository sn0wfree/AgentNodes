"""Skill data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    """A skill document with YAML frontmatter + Markdown body.

    Format:
        ---
        name: factor-research
        category: strategy
        description: 因子研究方法论
        tags: [factor, alpha, research]
        ---
        # 因子研究方法论
        ...
    """

    name: str
    category: str = "general"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    path: Path | None = None
    content: str = ""
