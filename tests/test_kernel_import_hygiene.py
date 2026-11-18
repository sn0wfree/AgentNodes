# -*- coding: utf-8 -*-
"""K3 隔离进程导入测试（顺序④）：swarm 轻量契约导入不拉重依赖。

背景（设计 §11.2-K3）：``swarm/__init__.py`` 曾顶层 eager import
``SwarmRuntime``（→ LangGraph/GraphEngine），导致
``from agentnodes.kernel.swarm.types import AgentStatus`` 这类轻量契约
导入也付出 LangGraph 安装/加载成本。懒导出（PEP 562 __getattr__）后，
只有真正访问 SwarmRuntime 属性才加载 runtime 模块。

每个用例都在**隔离子进程**里跑（sys.modules 与本进程隔离）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")

_PY = sys.executable


def _run(code: str) -> subprocess.CompletedProcess:
    # 继承宿主环境（HOME/USERPROFILE 等被 settings/paths 依赖），
    # 仅覆盖 PYTHONPATH（指向 src）与 UTF-8 模式。
    env = {**os.environ, "PYTHONPATH": SRC, "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        capture_output=True, text=True, encoding="utf-8", timeout=60, env=env,
    )


class TestLightweightSwarmImports:
    def test_types_import_does_not_load_langgraph(self):
        """轻量契约导入不得拉起 LangGraph（K3 主断言）。"""
        r = _run(
            "import sys; from agentnodes.kernel.swarm.types import AgentStatus; "
            "print('LANGGRAPH' if 'langgraph' in sys.modules else 'CLEAN')"
        )
        assert r.returncode == 0, r.stderr
        assert "CLEAN" in r.stdout

    def test_types_import_does_not_load_graph_engine(self):
        r = _run(
            "import sys; from agentnodes.kernel.swarm.types import AgentStatus; "
            "print('ENGINE' if 'agentnodes.kernel.runtimes.graph_engine' in sys.modules else 'CLEAN')"
        )
        assert r.returncode == 0, r.stderr
        assert "CLEAN" in r.stdout

    def test_contract_import_does_not_load_runtime_module(self):
        """contracts 侧（preset_loader 依赖 types，不依赖 runtime）。"""
        r = _run(
            "import sys; from agentnodes.kernel.swarm.contracts_stub import _; "
            "print('NEVER')"
        )
        # 该 stub 模块不存在——此用例改为正向：types 模块路径不含 runtime
        r = _run(
            "import sys; import agentnodes.kernel.swarm.types as m; "
            "print('RUNTIME' if 'agentnodes.kernel.swarm.runtime' in sys.modules else 'CLEAN')"
        )
        assert r.returncode == 0, r.stderr
        assert "CLEAN" in r.stdout

    def test_runtime_lazy_via_getattr_still_works(self):
        """兼容面：属性访问触发懒加载，SwarmRuntime 仍可用。"""
        r = _run(
            "from agentnodes.kernel.swarm import SwarmRuntime; "
            "print('OK' if SwarmRuntime is not None else 'FAIL')"
        )
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout

    def test_load_preset_lazy_via_getattr(self):
        r = _run(
            "from agentnodes.kernel.swarm import load_preset, list_presets; "
            "print('OK' if callable(load_preset) and callable(list_presets) else 'FAIL')"
        )
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout

    def test_dir_exposes_lazy_names(self):
        r = _run(
            "import agentnodes.kernel.swarm as m; "
            "print('OK' if {'SwarmRuntime','load_preset','list_presets'} <= set(dir(m)) else 'FAIL')"
        )
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout

    def test_unknown_attribute_raises_attribute_error(self):
        r = _run(
            "import agentnodes.kernel.swarm as m\n"
            "try:\n"
            "    m.nonexistent\n"
            "    print('FAIL')\n"
            "except AttributeError:\n"
            "    print('OK')\n"
        )
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout


class TestAgentMemoryFrontmatterMigration:
    """顺序④：frontmatter 迁 kernel/memory——agent↔memory 反向边拆除的回归。"""

    def test_frontmatter_lives_in_memory(self):
        from agentnodes.kernel.memory import frontmatter

        assert frontmatter.parse_frontmatter("---\ntitle: x\n---\nbody")[0] == {
            "title": "x",
        }

    def test_memory_persistent_uses_local_frontmatter(self):
        import inspect

        from agentnodes.kernel.memory import persistent

        src = inspect.getsource(persistent)
        assert "kernel.agent.factory.frontmatter" not in src, (
            "memory→agent 反向边已拆除，persistent 不得再引用 agent.factory"
        )
        assert "_parse_frontmatter = " in src or "parse_frontmatter as _parse_frontmatter" in src

    def test_agent_factory_no_longer_imports_memory_frontmatter(self):
        """factory 簇不得再 **import** 已迁出的 frontmatter 模块。

        docstring/注释里的文字提及不算（迁移说明允许）；只钉 import 语句
        与模块属性引用。
        """
        import ast

        init_src = (ROOT / "src/agentnodes/kernel/agent/factory/__init__.py").read_text(
            encoding="utf-8",
        )
        tree = ast.parse(init_src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + (
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                )
                for name in names:
                    assert "frontmatter" not in name, (
                        f"factory 不得 import frontmatter：{node.lineno}: {name}"
                    )


class TestDagMovedToKernelRoot:
    """顺序④：workflow/dag.py → kernel/dag.py（中立 DAG 算法，双向依赖拆除）。"""

    def test_dag_importable_from_kernel_root(self):
        from agentnodes.kernel.dag import topological_layers, validate_dag

        assert callable(topological_layers)
        assert callable(validate_dag)

    def test_workflow_package_reexports_dag(self):
        """workflow/__init__ 兼容 re-export 保持（既有消费方零改动）。"""
        from agentnodes.kernel.workflow import topological_layers

        assert callable(topological_layers)

    def test_swarm_no_longer_imports_workflow(self):
        import inspect

        from agentnodes.kernel.swarm import runtime

        src = inspect.getsource(runtime)
        assert "kernel.workflow" not in src, (
            "swarm→workflow 边已拆除（dag 下沉 kernel 根级）"
        )
