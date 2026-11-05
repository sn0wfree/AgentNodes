"""Tests for builtin workflow templates and loading precedence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentnodes.kernel.workflow.builtin import (
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
    def test_four_builtin_templates(self):
        names = list_builtin_names()
        assert "plan_execute_auto" in names
        assert "plan_execute_approval" in names
        assert "alpha_research" in names
        assert "data_quality_audit" in names

    def test_each_builtin_validates(self, tmp_path: Path):
        for name in list_builtin_names():
            definition = load_definition(name, tmp_path)
            assert definition is not None
            assert definition.validate() == []
            assert definition.source == "builtin"

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
        builtin = load_definition("alpha_research", tmp_path)
        assert builtin.source == "builtin"
        # Save a user version with the same name (drop evaluator + its edges)
        user_def = WorkflowDefinition.from_dict(
            json.loads(builtin.to_json()), source="user",
        )
        user_def.nodes = [n for n in user_def.nodes if n.id != "evaluator"]
        user_def.edges = [e for e in user_def.edges if "evaluator" not in (e.source, e.target)]
        save_user_definition(user_def, tmp_path)
        loaded = load_definition("alpha_research", tmp_path)
        assert loaded.source == "user"
        assert len(loaded.nodes) == 3  # user version wins

    def test_list_marked_with_source(self, tmp_path: Path):
        items = list_definitions(tmp_path)
        by_name = {i["name"]: i for i in items}
        assert by_name["alpha_research"]["source"] == "builtin"
        assert all("node_count" in i for i in items)

    def test_save_and_delete_user(self, tmp_path: Path):
        definition = load_definition("data_quality_audit", tmp_path)
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
