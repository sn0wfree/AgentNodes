"""Tests for builtin workflow templates and loading precedence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentnodes.kernel.workflow.builtin import (
    builtin_dir,
    delete_user_definition,
    list_builtin_names,
    list_definitions,
    load_definition,
    save_user_definition,
    user_dir,
)
from agentnodes.kernel.workflow.definition import (
    WorkflowDefinition,
    WorkflowDefinitionError,
)


class TestBuiltins:
    def test_builtin_templates_are_generic(self):
        """builtin 只发布通用模板（批次 4c：量化模板迁 apps/agent_plugins/）。

        **契约变更**：``alpha_research`` / ``data_quality_audit`` 引用的
        ``check_data`` / ``run_backtest`` / ``factor_analysis`` 等工具实现
        随批次 1 删除，它们已迁 ``apps/agent_plugins/quant_templates/``
        作为第三步素材。kernel 的 builtin 目录**只发布工具引用为零的
        通用编排模板**——这是「删掉 apps/ 也能跑」的一部分。
        """
        names = list_builtin_names()
        assert "plan_execute_auto" in names
        assert "plan_execute_approval" in names
        # 业务模板不得再出现在 kernel 的 builtin 集里
        assert "alpha_research" not in names
        assert "data_quality_audit" not in names

    def test_each_builtin_validates(self, tmp_path: Path):
        for name in list_builtin_names():
            definition = load_definition(name, tmp_path)
            assert definition is not None
            assert definition.validate() == []
            assert definition.source == "builtin"

    def test_builtin_templates_have_no_dangling_tools(self):
        """内置模板不得引用未实现的工具（check_tool_refs 的同一判据）。"""
        from agentnodes.kernel.agent.builtin_tools import (
            build_default_registry,
        )

        installed = {t.name for t in build_default_registry().all_tools()}
        for name in list_builtin_names():
            path = builtin_dir() / f"{name}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            for node in data.get("nodes") or []:
                cfg = node.get("config") or {}
                refs = list(cfg.get("tools") or [])
                if cfg.get("tool"):
                    refs.append(cfg["tool"])
                missing = [r for r in refs if r not in installed]
                assert not missing, f"{name}/{node.get('id')} 悬空: {missing}"

    def test_builtin_planner_templates_cut_correctly(self, tmp_path: Path):
        auto = load_definition("plan_execute_auto", tmp_path)
        assert len(auto.segment_cut()) == 1
        approval = load_definition("plan_execute_approval", tmp_path)
        segs = approval.segment_cut()
        assert len(segs) == 2
        assert segs[1].approval_after == "approval"

    def test_missing_returns_none(self, tmp_path: Path):
        assert load_definition("no_such_workflow", tmp_path) is None


class TestUserShadowing:
    def test_user_definition_shadows_builtin(self, tmp_path: Path):
        builtin = load_definition("plan_execute_approval", tmp_path)
        assert builtin.source == "builtin"
        # Save a user version with the same name (drop evaluator + its edges)
        user_def = WorkflowDefinition.from_dict(
            json.loads(builtin.to_json()), source="user",
        )
        user_def.nodes = [n for n in user_def.nodes if n.id != "evaluator"]
        user_def.edges = [
            e for e in user_def.edges if "evaluator" not in (e.source, e.target)
        ]
        save_user_definition(user_def, tmp_path)
        loaded = load_definition("plan_execute_approval", tmp_path)
        assert loaded.source == "user"
        assert len(loaded.nodes) == len(builtin.nodes) - 1  # user version wins

    def test_list_marked_with_source(self, tmp_path: Path):
        items = list_definitions(tmp_path)
        by_name = {i["name"]: i for i in items}
        assert by_name["plan_execute_auto"]["source"] == "builtin"
        assert all("node_count" in i for i in items)

    def test_save_and_delete_user(self, tmp_path: Path):
        definition = load_definition("plan_execute_auto", tmp_path)
        user_def = WorkflowDefinition.from_dict(
            json.loads(definition.to_json()), source="user",
        )
        user_def.name = "my_custom"
        path = save_user_definition(user_def, tmp_path)
        assert path.is_file()
        assert user_dir(tmp_path).joinpath("my_custom.json").is_file()
        assert delete_user_definition("my_custom", tmp_path)
        assert load_definition("my_custom", tmp_path) is None

    def test_delete_missing_is_false(self, tmp_path: Path):
        assert delete_user_definition("ghost", tmp_path) is False

    def test_invalid_user_file_skipped_in_list(self, tmp_path: Path):
        bad = user_dir(tmp_path)
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "broken.json").write_text('{"name": "broken", "nodes": [{"id": "x", "type": "nope"}]}',
                                         encoding="utf-8")
        items = list_definitions(tmp_path)
        assert "broken" not in {i["name"] for i in items}

    def test_load_invalid_user_raises(self, tmp_path: Path):
        bad = user_dir(tmp_path)
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "broken.json").write_text('{"name": "broken", "nodes": []}', encoding="utf-8")
        with pytest.raises(WorkflowDefinitionError):
            load_definition("broken", tmp_path)
