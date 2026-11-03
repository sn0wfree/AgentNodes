"""SkillRegistry — index, search, and load skills."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from .loader import parse_skill_file
from .models import Skill

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Registry of loaded skills, indexed by name and category.

    Usage:
        registry = SkillRegistry()
        count = registry.load_directory(Path("skills/"))
        skill = registry.get("factor-research")
        results = registry.search("momentum")
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._by_category: dict[str, list[Skill]] = {}

    def load_directory(self, path: Path) -> int:
        """Load all .md files from a directory recursively.

        Returns the number of skills successfully loaded.
        """
        count = 0
        if not path.is_dir():
            logger.warning("Skill directory does not exist: %s", path)
            return 0

        for md_file in sorted(path.rglob("*.md")):
            skill = parse_skill_file(md_file)
            if skill is not None:
                self._register(skill)
                count += 1

        logger.info("Loaded %d skills from %s", count, path)
        return count

    def load_file(self, path: Path) -> Skill | None:
        """Load a single skill file."""
        skill = parse_skill_file(path)
        if skill is not None:
            self._register(skill)
        return skill

    def get(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        """List all loaded skills, sorted by name."""
        return sorted(self._skills.values(), key=lambda s: s.name)

    def by_category(self, category: str) -> list[Skill]:
        """Get all skills in a category."""
        return list(self._by_category.get(category, []))

    def categories(self) -> list[str]:
        """List all categories."""
        return sorted(self._by_category.keys())

    def search(self, query: str) -> list[Skill]:
        """Search skills by name, description, or tags.

        Simple substring matching — no FTS5 needed for a small skill set.
        """
        query_lower = query.lower()
        results: list[tuple[int, Skill]] = []

        for skill in self._skills.values():
            score = 0
            if query_lower in skill.name.lower():
                score += 3  # name match is strongest
            if query_lower in skill.description.lower():
                score += 2
            if any(query_lower in tag.lower() for tag in skill.tags):
                score += 1
            if score > 0:
                results.append((score, skill))

        results.sort(key=lambda x: (-x[0], x[1].name))
        return [skill for _, skill in results]

    def _register(self, skill: Skill) -> None:
        """Register a skill in the index."""
        if skill.name in self._skills:
            logger.warning("Duplicate skill name: %s (from %s)", skill.name, skill.path)
        self._skills[skill.name] = skill
        self._by_category.setdefault(skill.category, []).append(skill)

    def __len__(self) -> int:
        return len(self._skills)

    def __iter__(self) -> Iterator[Skill]:
        return iter(self.list_all())
