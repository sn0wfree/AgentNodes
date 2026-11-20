"""SkillRegistry — index, search, and load skills."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from agentnodes.kernel.skills.loader import parse_skill_file
from agentnodes.kernel.skills.models import Skill

logger = logging.getLogger(__name__)

#: bundled 技能目录（随包安装的 ``agentnodes/templates/.skills``）。
#: M8（批 3c）统一单点——此前 5 个装载点各自拼路径（其中
#: ``workspace_tools`` 的三级上溯从 ``kernel/agent/builtin_tools`` 落到
#: ``kernel/``，目录不存在，bundled 技能**从未装载**）。
BUNDLED_SKILLS_DIR = Path(__file__).resolve().parents[2] / "templates" / ".skills"


def default_skills_dirs(workspace: Path | None = None) -> list[Path]:
    """技能装载目录序：**bundled 打底 + workspace/.skills 覆盖**。

    统一语义（M8 裁决）：``SkillRegistry._register`` 是同名 last-write-wins，
    所以顺序必须 bundled 先、workspace 后——workspace 同名技能覆盖内置
    （对齐 ``LoadSkillTool`` 工具说明书的既有宣称）。

    ``workspace`` 为 ``None`` 或 ``.skills`` 目录不存在时仅 bundled。
    """
    dirs = [BUNDLED_SKILLS_DIR]
    if workspace is not None:
        dirs.append(Path(workspace) / ".skills")
    return dirs


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

    def load_default(self, workspace: Path | None = None) -> int:
        """统一装载入口（M8）：bundled 打底 + workspace 覆盖。

        目录不存在时静默跳过（workspace 侧没有 ``.skills`` 是常态，不是
        警告）。返回成功装载的技能文件数。既有 :meth:`load_directory`
        公共面不变——调用方可继续精确控制装载目录。
        """
        count = 0
        for d in default_skills_dirs(workspace):
            if d.is_dir():
                count += self.load_directory(d)
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
