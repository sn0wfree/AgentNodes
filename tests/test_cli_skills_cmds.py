"""CLI skills 命令测试（此前覆盖率 8.3%）——走真实的内置技能资产。"""
from __future__ import annotations

import argparse

import pytest

from agentnodes.interfaces.cli.commands.skills import (
    cmd_skills_list,
    cmd_skills_search,
    cmd_skills_show,
)


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


@pytest.fixture(scope="module")
def real_skills():
    from agentnodes.kernel.skills import SkillRegistry
    from pathlib import Path

    import agentnodes

    skills_dir = Path(agentnodes.__file__).parent / "templates" / ".skills"
    registry = SkillRegistry()
    registry.load_directory(skills_dir)
    return registry.list_all()


class TestSkillsList:
    def test_lists_bundled_skills(self, real_skills, capsys):
        if not real_skills:
            pytest.skip("无内置技能资产")
        assert cmd_skills_list(_args()) == 0
        out = capsys.readouterr().out
        assert "=== 技能列表" in out
        assert real_skills[0].name in out

    def test_category_filter_path(self, real_skills, capsys):
        """有分类 → 走 by_category 过滤分支。"""
        cats = {s.category for s in real_skills if s.category}
        if not cats:
            pytest.skip("内置技能无分类字段")
        cat = sorted(cats)[0]
        assert cmd_skills_list(_args(category=cat)) == 0
        capsys.readouterr()

    def test_missing_category_attribute_defaults_none(self, real_skills, capsys):
        """args 无 category 属性 → getattr 缺省 None → 全量分支。"""
        assert cmd_skills_list(_args()) == 0
        capsys.readouterr()


class TestSkillsShow:
    def test_show_real_skill(self, real_skills, capsys):
        if not real_skills:
            pytest.skip("无内置技能资产")
        name = real_skills[0].name
        assert cmd_skills_show(_args(name=name)) == 0
        out = capsys.readouterr().out
        assert f"=== {name} ===" in out
        assert real_skills[0].content in out

    def test_show_missing_returns_1(self, capsys):
        assert cmd_skills_show(_args(name="no-such-skill")) == 1
        assert "不存在" in capsys.readouterr().out


class TestSkillsSearch:
    def test_search_hit(self, real_skills, capsys):
        if not real_skills:
            pytest.skip("无内置技能资产")
        token = real_skills[0].name[:4]
        assert cmd_skills_search(_args(query=token)) == 0
        out = capsys.readouterr().out
        assert "=== 搜索结果" in out

    def test_search_no_hits(self, capsys):
        assert cmd_skills_search(_args(query="zzz-no-such-token-zzz")) == 0
        assert "未找到匹配" in capsys.readouterr().out
