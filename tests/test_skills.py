"""Tests for Skills loader, registry, and models."""

from __future__ import annotations

from strategy_research.kernel.skills.loader import parse_skill_file
from strategy_research.kernel.skills.models import Skill
from strategy_research.kernel.skills.registry import SkillRegistry

# ── Skill Model Tests ──────────────────────────────────────


class TestSkillModel:
    def test_default_values(self):
        skill = Skill(name="test")
        assert skill.name == "test"
        assert skill.category == "general"
        assert skill.description == ""
        assert skill.tags == []
        assert skill.path is None
        assert skill.content == ""

    def test_full_values(self):
        skill = Skill(
            name="factor-research",
            category="strategy",
            description="Factor research methodology",
            tags=["factor", "alpha"],
            content="# Factor Research",
        )
        assert skill.name == "factor-research"
        assert skill.category == "strategy"
        assert len(skill.tags) == 2


# ── Loader Tests ───────────────────────────────────────────


class TestSkillLoader:
    def test_parse_with_frontmatter(self, tmp_path):
        skill_file = tmp_path / "test.md"
        skill_file.write_text(
            "---\n"
            "name: my-skill\n"
            "category: analysis\n"
            "description: A test skill\n"
            "tags: [test, demo]\n"
            "---\n"
            "# My Skill\n"
            "Content here."
        )
        skill = parse_skill_file(skill_file)
        assert skill is not None
        assert skill.name == "my-skill"
        assert skill.category == "analysis"
        assert skill.description == "A test skill"
        assert skill.tags == ["test", "demo"]
        assert "# My Skill" in skill.content
        assert "Content here." in skill.content

    def test_parse_without_frontmatter(self, tmp_path):
        skill_file = tmp_path / "plain.md"
        skill_file.write_text("# Plain Skill\nJust content.")
        skill = parse_skill_file(skill_file)
        assert skill is not None
        assert skill.name == "plain"  # from filename
        assert skill.content == "# Plain Skill\nJust content."

    def test_parse_minimal_frontmatter(self, tmp_path):
        skill_file = tmp_path / "minimal.md"
        skill_file.write_text("---\nname: min\n---\nBody.")
        skill = parse_skill_file(skill_file)
        assert skill is not None
        assert skill.name == "min"
        assert skill.category == "general"

    def test_parse_nonexistent_file(self, tmp_path):
        skill = parse_skill_file(tmp_path / "nonexistent.md")
        assert skill is None

    def test_parse_empty_file(self, tmp_path):
        skill_file = tmp_path / "empty.md"
        skill_file.write_text("")
        skill = parse_skill_file(skill_file)
        assert skill is not None
        assert skill.name == "empty"

    def test_frontmatter_name_defaults_to_filename(self, tmp_path):
        skill_file = tmp_path / "auto-name.md"
        skill_file.write_text("---\ncategory: test\n---\nBody.")
        skill = parse_skill_file(skill_file)
        assert skill.name == "auto-name"

    def test_tags_as_string(self, tmp_path):
        skill_file = tmp_path / "tags.md"
        skill_file.write_text("---\nname: t\ntags: single-tag\n---\nBody.")
        skill = parse_skill_file(skill_file)
        assert skill.tags == ["single-tag"]


# ── Registry Tests ─────────────────────────────────────────


class TestSkillRegistry:
    def test_load_directory(self, tmp_path):
        (tmp_path / "skill1.md").write_text("---\nname: s1\ncategory: a\n---\nBody 1")
        (tmp_path / "skill2.md").write_text("---\nname: s2\ncategory: b\n---\nBody 2")

        registry = SkillRegistry()
        count = registry.load_directory(tmp_path)
        assert count == 2
        assert len(registry) == 2

    def test_load_directory_nonexistent(self, tmp_path):
        registry = SkillRegistry()
        count = registry.load_directory(tmp_path / "nonexistent")
        assert count == 0
        assert len(registry) == 0

    def test_get_skill(self, tmp_path):
        (tmp_path / "s.md").write_text("---\nname: my-skill\n---\nBody")
        registry = SkillRegistry()
        registry.load_directory(tmp_path)

        skill = registry.get("my-skill")
        assert skill is not None
        assert skill.name == "my-skill"

    def test_get_missing_skill(self):
        registry = SkillRegistry()
        assert registry.get("nonexistent") is None

    def test_list_all(self, tmp_path):
        (tmp_path / "a.md").write_text("---\nname: alpha\n---\nBody")
        (tmp_path / "b.md").write_text("---\nname: beta\n---\nBody")
        registry = SkillRegistry()
        registry.load_directory(tmp_path)

        skills = registry.list_all()
        assert len(skills) == 2
        assert skills[0].name == "alpha"  # sorted
        assert skills[1].name == "beta"

    def test_by_category(self, tmp_path):
        (tmp_path / "a.md").write_text("---\nname: s1\ncategory: strategy\n---\nBody")
        (tmp_path / "b.md").write_text("---\name: s2\ncategory: analysis\n---\nBody")
        (tmp_path / "c.md").write_text("---\nname: s3\ncategory: strategy\n---\nBody")
        registry = SkillRegistry()
        registry.load_directory(tmp_path)

        strategy_skills = registry.by_category("strategy")
        assert len(strategy_skills) == 2

    def test_categories(self, tmp_path):
        (tmp_path / "a.md").write_text("---\nname: s1\ncategory: strategy\n---\nBody")
        (tmp_path / "b.md").write_text("---\nname: s2\ncategory: analysis\n---\nBody")
        registry = SkillRegistry()
        registry.load_directory(tmp_path)

        cats = registry.categories()
        assert "analysis" in cats
        assert "strategy" in cats

    def test_search_by_name(self, tmp_path):
        (tmp_path / "s.md").write_text("---\nname: momentum-factor\n---\nBody")
        registry = SkillRegistry()
        registry.load_directory(tmp_path)

        results = registry.search("momentum")
        assert len(results) == 1
        assert results[0].name == "momentum-factor"

    def test_search_by_tag(self, tmp_path):
        (tmp_path / "s.md").write_text("---\name: s1\ntags: [factor, alpha]\n---\nBody")
        registry = SkillRegistry()
        registry.load_directory(tmp_path)

        results = registry.search("factor")
        assert len(results) == 1

    def test_search_by_description(self, tmp_path):
        (tmp_path / "s.md").write_text("---\nname: s1\ndescription: risk analysis tool\n---\nBody")
        registry = SkillRegistry()
        registry.load_directory(tmp_path)

        results = registry.search("risk")
        assert len(results) == 1

    def test_search_no_match(self, tmp_path):
        (tmp_path / "s.md").write_text("---\nname: s1\n---\nBody")
        registry = SkillRegistry()
        registry.load_directory(tmp_path)

        results = registry.search("nonexistent")
        assert len(results) == 0

    def test_load_subdirectories(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "s.md").write_text("---\nname: nested\n---\nBody")
        (tmp_path / "root.md").write_text("---\nname: root\n---\nBody")

        registry = SkillRegistry()
        count = registry.load_directory(tmp_path)
        assert count == 2

    def test_duplicate_name_warning(self, tmp_path, caplog):
        (tmp_path / "a.md").write_text("---\nname: dup\n---\nBody A")
        (tmp_path / "b.md").write_text("---\nname: dup\n---\nBody B")

        registry = SkillRegistry()
        registry.load_directory(tmp_path)
        assert "Duplicate skill name" in caplog.text

    def test_iteration(self, tmp_path):
        (tmp_path / "a.md").write_text("---\nname: s1\n---\nBody")
        registry = SkillRegistry()
        registry.load_directory(tmp_path)

        names = [s.name for s in registry]
        assert "s1" in names
