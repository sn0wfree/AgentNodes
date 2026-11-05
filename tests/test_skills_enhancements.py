"""Tests for Skill enhancements (P6 Phase 2).

Covers:
    - All 24 existing skills have valid YAML frontmatter
    - All 14 new skills load correctly
    - Categories are indexed properly
    - Search by tag/category returns expected results
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentnodes.kernel.skills import SkillRegistry

SKILLS_DIR = Path(__file__).resolve().parent.parent / "src" / "agentnodes" / "templates" / ".skills"

# 10 existing skills (must all have frontmatter)
EXPECTED_EXISTING = {
    "backtest-diagnose": "analysis",
    "correlation-analysis": "analysis",
    "data-routing": "data-source",
    "factor-research": "strategy",
    "ml-strategy": "strategy",
    "performance-attribution": "analysis",
    "quant-statistics": "analysis",
    "research-discipline": "tool",
    "risk-analysis": "analysis",
    "sector-rotation": "strategy",
}

# 17 new skills (Phase 2-B2 — original 14 + 3 bonus)
EXPECTED_NEW = {
    "multi-factor": "strategy",
    "pair-trading": "strategy",
    "event-driven": "strategy",
    "sentiment-analysis": "strategy",
    "bottleneck-hunter": "tool",
    "strategy-generate": "strategy",
    "thesis-tracker": "tool",
    "volatility": "analysis",
    # "alpha-zoo": 已随 alpha_zoo 移除（2026-11-04 做减法批次 1）；
    # 归档见 testcase/golden/apps/alpha_zoo/alpha-zoo-skill.md
    "seasonal": "strategy",
    "asset-allocation": "strategy",
    "macro-analysis": "analysis",
    "options-payoff": "tool",
    "tushare": "data-source",
    # 3 bonus categories (matching vibe-trading distribution)
    "crypto-derivatives": "crypto",
    "micro-cap-flow": "flow",
    "report-generate": "tool",
}


@pytest.fixture
def registry() -> SkillRegistry:
    r = SkillRegistry()
    n = r.load_directory(SKILLS_DIR)
    assert n >= 24, f"Expected ≥24 skills loaded, got {n}"
    return r


# ── Existing skills frontmatter ──────────────────────────────────────


class TestExistingSkillsFrontmatter:
    def test_all_existing_skills_have_frontmatter(self, registry: SkillRegistry):
        for name in EXPECTED_EXISTING:
            skill = registry.get(name)
            assert skill is not None, f"Skill '{name}' not loaded"
            assert skill.category != "general", (
                f"Skill '{name}' still has default 'general' category — "
                "missing frontmatter"
            )

    def test_existing_skills_have_correct_category(self, registry: SkillRegistry):
        for name, expected_cat in EXPECTED_EXISTING.items():
            skill = registry.get(name)
            assert skill.category == expected_cat, (
                f"Skill '{name}': expected category '{expected_cat}', "
                f"got '{skill.category}'"
            )

    def test_existing_skills_have_tags(self, registry: SkillRegistry):
        for name in EXPECTED_EXISTING:
            skill = registry.get(name)
            assert len(skill.tags) > 0, (
                f"Skill '{name}' has no tags — frontmatter incomplete"
            )

    def test_existing_skills_have_description(self, registry: SkillRegistry):
        for name in EXPECTED_EXISTING:
            skill = registry.get(name)
            assert len(skill.description) >= 20, (
                f"Skill '{name}' description too short: {skill.description!r}"
            )

    def test_existing_skills_content_nonempty(self, registry: SkillRegistry):
        for name in EXPECTED_EXISTING:
            skill = registry.get(name)
            assert len(skill.content) > 200, (
                f"Skill '{name}' content too short: {len(skill.content)} chars"
            )


# ── New skills load correctly ───────────────────────────────────────


class TestNewSkills:
    def test_all_new_skills_loaded(self, registry: SkillRegistry):
        for name in EXPECTED_NEW:
            skill = registry.get(name)
            assert skill is not None, f"New skill '{name}' not loaded"

    def test_new_skills_correct_category(self, registry: SkillRegistry):
        for name, expected_cat in EXPECTED_NEW.items():
            skill = registry.get(name)
            assert skill.category == expected_cat, (
                f"New skill '{name}': expected '{expected_cat}', got '{skill.category}'"
            )

    def test_new_skills_have_frontmatter(self, registry: SkillRegistry):
        for name in EXPECTED_NEW:
            skill = registry.get(name)
            assert skill.category != "general"
            assert len(skill.tags) > 0
            assert len(skill.description) >= 20

    def test_new_skills_content_size(self, registry: SkillRegistry):
        # All new skills should be substantive (> 500 chars)
        for name in EXPECTED_NEW:
            skill = registry.get(name)
            assert len(skill.content) > 500, (
                f"New skill '{name}' too small: {len(skill.content)} chars"
            )


# ── Category indexing ───────────────────────────────────────────────


class TestCategoryIndexing:
    def test_strategy_category_has_most_skills(self, registry: SkillRegistry):
        by_cat = {c: len(registry.by_category(c)) for c in registry.categories()}
        # Strategy should have the most (11 = 3 existing + 8 new)
        assert by_cat["strategy"] >= 10

    def test_analysis_category_robust(self, registry: SkillRegistry):
        by_cat = {c: len(registry.by_category(c)) for c in registry.categories()}
        assert by_cat["analysis"] >= 6  # 5 existing + volatility + macro-analysis

    def test_all_categories_have_at_least_one_skill(self, registry: SkillRegistry):
        # 6 categories: analysis, strategy, tool, data-source, crypto, flow
        assert len(registry.categories()) >= 6
        for cat in registry.categories():
            assert len(registry.by_category(cat)) >= 1

    def test_crypto_and_flow_categories_exist(self, registry: SkillRegistry):
        """New categories from Phase 2-B2 additions."""
        assert "crypto" in registry.categories()
        assert "flow" in registry.categories()


# ── Search functionality ────────────────────────────────────────────


class TestSkillSearch:
    def test_search_by_name(self, registry: SkillRegistry):
        results = registry.search("multi-factor")
        names = [s.name for s in results]
        assert "multi-factor" in names

    def test_search_by_tag(self, registry: SkillRegistry):
        # "cointegration" is in correlation-analysis tags
        results = registry.search("cointegration")
        assert any(s.name == "correlation-analysis" for s in results)

    def test_search_by_description_keyword(self, registry: SkillRegistry):
        # "volatility" should match the volatility skill (description + tags + name)
        results = registry.search("volatility")
        assert any(s.name == "volatility" for s in results)

    def test_search_returns_relevant_results(self, registry: SkillRegistry):
        # "策略" in description should return Chinese-strategy skills
        results = registry.search("策略")
        names = [s.name for s in results]
        # Should match at least some strategy-category skills
        assert any("strategy" in n or "factor" in n or "momentum" in n for n in names)

    def test_search_no_match_returns_empty(self, registry: SkillRegistry):
        results = registry.search("xyzzzz_nonexistent_skill_zzz")
        assert results == []


# ── Bulk integrity ──────────────────────────────────────────────────


class TestSkillIntegrity:
    def test_no_duplicate_names(self, registry: SkillRegistry):
        names = [s.name for s in registry.list_all()]
        assert len(names) == len(set(names)), (
            f"Duplicate skill names found: "
            f"{[n for n in names if names.count(n) > 1]}"
        )

    def test_all_paths_resolve(self, registry: SkillRegistry):
        for s in registry.list_all():
            assert s.path.exists(), f"Skill '{s.name}' path doesn't exist: {s.path}"

    def test_total_skill_count(self, registry: SkillRegistry):
        """Total should be 26 (10 existing + 16 new).

        原为 27；``alpha-zoo`` 已随 alpha_zoo 移除（2026-11-04 做减法批次 1）。
        """
        total = len(registry)
        assert total == 26, f"Expected 26 total, got {total}"

    def test_all_skills_have_nontrivial_content(self, registry: SkillRegistry):
        # No empty/placeholder skills
        for s in registry.list_all():
            assert len(s.content.strip()) > 100, (
                f"Skill '{s.name}' has trivial content"
            )
