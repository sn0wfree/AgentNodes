"""U6：legacy 引擎审计命令的网（R-015 条件 ④）。

**验三件事**（按 R-019：门禁的价值 = 注入违规后会不会红）：

1. **只读性**：跑审计前后，工作区文件 mtime/环境变量都不变；
2. **能发现依赖**：当前仓有 legacy 残留 → 必须报出来（不空转）；
3. **注入违规会红**：临时造一份含 legacy 调用的最小仓库 → 审计必须发现。

**为什么第 3 条最重要**：只看「当前仓库有残留」无法区分「审计器能工作」
与「审计器恰好返回了非空」——注入法才能证明检测逻辑真的在跑
（R-019 的判据）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from agentnodes.kernel.agent.legacy_audit import (
    LEGACY_ENGINE_SYMBOL,
    audit_legacy_engine,
    audit_legacy_engine_text,
)

ROOT = Path(__file__).resolve().parents[1]


# ── 1. 只读性 ──


def test_audit_is_read_only(tmp_path: Path):
    """审计前后：仓库文件内容不变、不新建文件。

    只读是硬约束（R-004：审计命令必须零副作用）——否则「审计」会变成
    「迁移」的伪装。
    """
    # 快照：仓库下所有 .py 的 (path, mtime, size)
    before = {}
    for sub in ("src", "tests"):
        for p in (ROOT / sub).rglob("*.py"):
            st = p.stat()
            before[str(p)] = (st.st_mtime_ns, st.st_size)

    result = audit_legacy_engine(ROOT)
    assert result.error is None, result.error

    after = {}
    for sub in ("src", "tests"):
        for p in (ROOT / sub).rglob("*.py"):
            st = p.stat()
            after[str(p)] = (st.st_mtime_ns, st.st_size)

    assert before == after, (
        "审计改动了文件（mtime/size 变化）——违反只读约束"
    )


def test_audit_does_not_import_legacy():
    """审计器不 import 被审计对象（否则删了 legacy 就没法审计）。

    结构验证：源码里不出现 `from ...loop import` 之类的引擎导入。
    """
    src = (ROOT / "src/agentnodes/kernel/agent/legacy_audit.py").read_text(
        encoding="utf-8")
    # 只允许 import 标准库 + 本模块需要的 ast/io/os/re/tokenize
    forbidden = ("from .loop import", "import loop", "from .skeleton import")
    for f in forbidden:
        assert f not in src, f"审计器 import 了被审计对象：{f}"


def test_audit_does_not_read_env():
    """审计器不读环境变量（否则结果依赖运行环境，不可复现）。"""
    src = (ROOT / "src/agentnodes/kernel/agent/legacy_audit.py").read_text(
        encoding="utf-8")
    for pat in ("os.environ", "os.getenv"):
        assert pat not in src, f"审计器读了环境变量：{pat}"


# ── 2. 能发现当前仓库的残留（不空转）──


def test_audit_finds_current_residue():
    """当前仓有 legacy 残留 → 必须报出来，且退出码为 1。"""
    result = audit_legacy_engine(ROOT)
    assert result.error is None

    # 定义 + 真调用（loop.py 里只剩 arun(engine="legacy") 那一条真调用）
    assert result.by_kind("definition"), "未发现 _run_loop_core 定义"
    assert result.by_kind("call"), "未发现 _run_loop_core 调用"

    # 开关读取
    assert result.by_kind("env_read"), "未发现引擎开关读取"

    text, code = audit_legacy_engine_text(result)
    assert code == 1, f"有残留时退出码应为 1，实得 {code}"
    assert "尚不可删除 legacy" in text


# ── 3. 注入违规会红（R-019 判据）──


def _make_min_repo(root: Path, *, with_legacy: bool) -> None:
    """造一个最小仓库：只有一个 src/ 文件，可选含 legacy 调用。"""
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    if with_legacy:
        (src / "engine.py").write_text(textwrap.dedent(f"""
            async def _run_loop_core(task):
                return task

            async def run(a):
                return await a._run_loop_core("x")
        """), encoding="utf-8")
    else:
        (src / "engine.py").write_text(
            "def run():\n    return 1\n", encoding="utf-8")


def test_audit_detects_injected_legacy_call(tmp_path: Path):
    """注入 legacy 调用 → 审计必须发现（证明检测逻辑真的在跑）。"""
    _make_min_repo(tmp_path, with_legacy=True)
    result = audit_legacy_engine(tmp_path)
    assert result.error is None
    assert result.by_kind("definition"), "注入的定义没被发现"
    assert result.by_kind("call"), "注入的调用没被发现"
    _, code = audit_legacy_engine_text(result)
    assert code == 1


def test_audit_clean_repo_returns_zero(tmp_path: Path):
    """干净仓库 → 退出码 0（避免「永远报错」的假安全网）。"""
    _make_min_repo(tmp_path, with_legacy=False)
    result = audit_legacy_engine(tmp_path)
    assert result.error is None
    assert not result.blocking, (
        f"干净仓库不该有阻塞项：{[f.kind for f in result.blocking]}"
    )
    _, code = audit_legacy_engine_text(result)
    assert code == 0


def test_audit_detects_injected_env_read(tmp_path: Path):
    """注入引擎开关读取 → 必须发现。"""
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "cfg.py").write_text(textwrap.dedent("""
        def pick():
            return os.environ.get("AGENTNODES_ENGINE")
    """), encoding="utf-8")
    result = audit_legacy_engine(tmp_path)
    envs = result.by_kind("env_read")
    assert envs, "注入的开关读取没被发现"
    assert any("AGENTNODES_ENGINE" in f.text for f in envs)


def test_audit_detects_test_legacy_dependency(tmp_path: Path):
    """注入测试里的 engine="legacy" → 必须发现。"""
    tests = tmp_path / "tests"
    tests.mkdir(parents=True)
    (tests / "test_x.py").write_text(textwrap.dedent("""
        async def test_it(loop):
            await loop.arun("x", engine="legacy")
    """), encoding="utf-8")
    result = audit_legacy_engine(tmp_path)
    tl = result.by_kind("test_legacy")
    assert tl, "注入的测试依赖没被发现"


def test_audit_detects_template_code(tmp_path: Path):
    """subprocess 模板（多行字符串）里的 engine="legacy" → 必须发现。

    这是审计的最难点：模板内代码是**字符串**，AST 看不到。实测仓内
    `test_skeleton_equivalence.py` 的模板里就有一处真调用。
    """
    tests = tmp_path / "tests"
    tests.mkdir(parents=True)
    tpl_body = (
        "_INNER = r'''\n"
        "import asyncio\n"
        "\n"
        "async def main():\n"
        '    await loop.arun("x", engine="legacy")\n'
        "\n"
        "asyncio.run(main())\n"
        "'''\n"
    )
    (tests / "test_tpl.py").write_text(tpl_body, encoding="utf-8")
    result = audit_legacy_engine(tmp_path)
    tl = result.by_kind("test_legacy")
    assert tl, (
        "模板内的 engine='legacy' 没被发现——"
        "subprocess 模板是审计的最难点"
    )


def test_audit_ignores_docstring_mentions(tmp_path: Path):
    """模块 docstring 里提到 engine="legacy" → **不算**依赖（防假阳性）。"""
    tests = tmp_path / "tests"
    tests.mkdir(parents=True)
    body = (
        '"""说明：本测试演示 arun(engine="legacy") 的用法。"""\n'
        "\n"
        "def test_nothing():\n"
        "    pass\n"
    )
    (tests / "test_doc.py").write_text(body, encoding="utf-8")
    result = audit_legacy_engine(tmp_path)
    tl = result.by_kind("test_legacy")
    assert not tl, f"docstring 提及被误判为依赖：{[f.text for f in tl]}"


def test_audit_flags_unresolvable_engine_variable(tmp_path: Path):
    """engine=<变量> → 列为「需人工确认」（不阻塞，但必须可见）。"""
    tests = tmp_path / "tests"
    tests.mkdir(parents=True)
    (tests / "test_var.py").write_text(textwrap.dedent("""
        async def test_it(loop, engine):
            await loop.arun("x", engine=engine)
    """), encoding="utf-8")
    result = audit_legacy_engine(tmp_path)
    vars_ = result.by_kind("engine_var")
    assert vars_, "变量传参没被列为需确认项"
    # 不阻塞
    assert all(not f.blocking for f in vars_)


# ── 4. CLI 接线 ──


def test_cli_subcommand_registered():
    """`config audit-legacy-engine` 已挂到 CLI（可被 argparse 解析）。"""
    from agentnodes.interfaces.cli.commands.config_cmd import cmd_config

    assert callable(cmd_config)
    src = (ROOT / "src/agentnodes/interfaces/cli/commands/config_cmd.py").read_text(
        encoding="utf-8")
    assert "audit-legacy-engine" in src
    assert "audit_legacy_engine_text" in src


def test_cli_json_output_shape():
    """`--json` 输出必须含机器可读字段（CI 可消费）。"""
    result = audit_legacy_engine(ROOT)
    d = result.to_dict()
    for key in ("scanned_files", "blocking_count", "mention_count",
                "findings", "error"):
        assert key in d, f"JSON 缺字段 {key}"
    assert d["scanned_files"] > 0
    assert isinstance(d["findings"], list)
