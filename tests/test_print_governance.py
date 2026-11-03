"""Architecture gate: print governance (docs/logging-unified-entry-design-20261102.md §5).

Rules:
1. ``core/**`` and ``api/**`` — no runtime ``print()`` calls, except
   frozen-count exempt files (data channels / CLI report renderers).
2. Exempt files keep their frozen counts (grows need an explicit
   update here + a documented reason).
3. ``core/study`` must not re-introduce local ``def _dlog`` (all dual
   writes go through ``observability.logging_setup.dual_log``).
4. Docstring examples don't count (AST scan, not regex).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "strategy_research"

# 数据通道 / CLI 报告渲染器：print 是产品语义不是日志（冻结数量，增长需改此表）
EXEMPT_FILES: dict[str, int] = {
    "core/engine/base.py": 2,        # print(flush=True) → run_bg 重定向 run.log → log_stall 检测
    "interfaces/mcp/server.py": 3,   # stdout JSON-RPC 协议帧    "apps/preflight.py": 10,         # isatty 彩色 CLI 报告
    "apps/alpha_zoo_convert.py": 5,  # CLI 批处理驱动进度
}

# print 兜底机制的实现本体（dual_log 的无 handler 分支）——机制自身不在治理范围
GATE_SELF = "core/observability/logging_setup.py"


def _print_calls(path: Path) -> list[int]:
    """Line numbers of runtime print/pprint calls (AST — docstrings excluded)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in ("print", "pprint"):
            lines.append(node.lineno)
    return lines


def _py_files(rel_dir: str) -> list[Path]:
    root = SRC / rel_dir
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


class TestPrintGovernance:
    def test_core_has_no_runtime_prints(self):
        offenders: list[str] = []
        for path in _py_files("core"):
            rel = path.relative_to(SRC).as_posix()
            if rel.endswith("cli.py") or rel in EXEMPT_FILES or rel == GATE_SELF:
                continue
            calls = _print_calls(path)
            if calls:
                offenders.append(f"{rel}: lines {calls}")
        assert not offenders, (
            "core/ 出现新的运行时 print() —— 库内诊断请用 logger（>=WARNING 未配置时 "
            "lastResort 仍可见）或 observability.logging_setup.dual_log（INFO 进度）。\n"
            + "\n".join(offenders)
        )

    def test_api_has_no_runtime_prints(self):
        offenders: list[str] = []
        for path in _py_files("api"):
            rel = path.relative_to(SRC).as_posix()
            calls = _print_calls(path)
            if calls:
                offenders.append(f"{rel}: lines {calls}")
        assert not offenders, "\n".join(offenders)

    def test_exempt_file_counts_frozen(self):
        for rel, expected in EXEMPT_FILES.items():
            actual = len(_print_calls(SRC / rel))
            assert actual == expected, (
                f"{rel} print 数量 {actual} != 冻结值 {expected}。增长需要在本表"
                "登记并注明数据通道/CLI 语义理由；减少则顺手把冻结值改掉。"
            )

    def test_no_local_dlog_in_study(self):
        offenders: list[str] = []
        for path in _py_files("study"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "_dlog"
                ):
                    offenders.append(
                        f"{path.relative_to(SRC)}:{node.lineno}"
                    )
        assert not offenders, (
            "core/study 不得重新定义 _dlog —— 双写统一走 "
            "observability.logging_setup.dual_log：\n" + "\n".join(offenders)
        )

    def test_docstring_examples_not_counted(self):
        """Sanity: openai_client.py 的 docstring 里有 print 示例文本，
        AST 扫描必须不算运行时调用（否则规则 1 会误伤）。"""
        raw = (SRC / "core" / "llm" / "openai_client.py").read_text(encoding="utf-8")
        assert "print(resp.content)" in raw       # 文本里存在
        assert _print_calls(SRC / "core" / "llm" / "openai_client.py") == []
