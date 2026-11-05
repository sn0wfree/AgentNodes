"""Extracted from cli.py — skills management commands.

Contains:
- cmd_skills_list
- cmd_skills_show
- cmd_skills_search
"""

from __future__ import annotations

import agentnodes  # noqa: F401  (路径锚点)

from pathlib import Path


def cmd_skills_list(args) -> int:
    """列出所有技能。"""
    from agentnodes.kernel.skills import SkillRegistry

    skills_dir = Path(agentnodes.__file__).parent / "templates" / ".skills"
    registry = SkillRegistry()
    registry.load_directory(skills_dir)

    category = getattr(args, "category", None)
    if category:
        skills = registry.by_category(category)
    else:
        skills = registry.list_all()

    if not skills:
        print("暂无技能")
        return 0

    print(f"=== 技能列表 (共 {len(skills)} 个) ===")
    for s in skills:
        desc = s.description[:60] if s.description else "(无描述)"
        tags = f" [{', '.join(s.tags)}]" if s.tags else ""
        print(f"  {s.name:30s}  {desc}{tags}")

    return 0


def cmd_skills_show(args) -> int:
    """显示技能内容。"""
    from agentnodes.kernel.skills import SkillRegistry

    skills_dir = Path(agentnodes.__file__).parent / "templates" / ".skills"
    registry = SkillRegistry()
    registry.load_directory(skills_dir)

    skill = registry.get(args.name)
    if not skill:
        print(f"技能 '{args.name}' 不存在")
        return 1

    print(f"=== {skill.name} ===")
    if skill.description:
        print(f"描述: {skill.description}")
    if skill.category:
        print(f"类别: {skill.category}")
    if skill.tags:
        print(f"标签: {', '.join(skill.tags)}")
    print()
    print(skill.content)

    return 0


def cmd_skills_search(args) -> int:
    """搜索技能。"""
    from agentnodes.kernel.skills import SkillRegistry

    skills_dir = Path(agentnodes.__file__).parent / "templates" / ".skills"
    registry = SkillRegistry()
    registry.load_directory(skills_dir)

    results = registry.search(args.query)
    if not results:
        print(f"未找到匹配 '{args.query}' 的技能")
        return 0

    print(f"=== 搜索结果 (共 {len(results)} 个, 关键词: {args.query}) ===")
    for s in results:
        desc = s.description[:60] if s.description else "(无描述)"
        print(f"  {s.name:30s}  {desc}")

    return 0
