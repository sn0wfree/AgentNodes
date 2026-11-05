"""quantnodes-strategy-research — 通用策略自动研究框架。"""

from __future__ import annotations

from pathlib import Path

__version__ = "0.6.0"

# Shipped templates root (strategy.py / prepare.py / .prompts / .skills / …)
_TEMPLATES_DIR = Path(__file__).parent / "templates"
