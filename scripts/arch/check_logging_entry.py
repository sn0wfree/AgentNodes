# -*- coding: utf-8 -*-
"""裸 ``logging.getLogger`` 收编棘轮（统一日志获取入口 · R-005' 棘轮）。

**为什么需要它**：统一日志中枢落地后（docs/design/logging-unified-hub-design-20261117.md），
``get_logger(__name__)``（``agentnodes.foundation.logger``）是全仓唯一获取入口；
裸调 ``logging.getLogger`` 会绕过门面缓存与后续按包分级等中枢能力，且**没有门禁就会回流**
—— 本仓的教训（R-019）是「门禁的价值 = 注入违规后会不会红，不是当前 0 违规」。

判定：AST 扫 ``src/agentnodes`` 下的 ``logging.getLogger(...)`` / ``getLogger(...)``
调用点（每处记 1），与棘轮基线 ``tests/baselines/logging_entry.json`` 比较：
**只许减不许增**（current > max_bare → 红）。

豁免（日志体系的 owner，装配层合法用法）：
  - ``foundation/observability/`` 整包 —— ``configure_logging`` / ``trace`` 本身
    要操作 root logger 与命名 logger，是装配层的本职；
  - ``foundation/logger.py`` —— 获取入口自身（``logging.getLogger(name)``）。

用法：
    python check_logging_entry.py             # 棘轮检查（rc 0/1）
    python check_logging_entry.py --update    # 把当前计数写回基线（收编批次落地后用）
    python check_logging_entry.py --selftest  # 注入违规自检（R-019，不动 src/）
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "agentnodes"
BASELINE = ROOT / "tests" / "baselines" / "logging_entry.json"

#: 豁免前缀（相对 src/agentnodes 的 posix 路径前缀）
EXEMPT_PREFIXES = (
    "foundation/observability/",
    "foundation/logger.py",
)


def _iter_py(root: Path):
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        yield p


def count_bare(src_root: Path) -> list[tuple[str, int]]:
    """返回 [(相对路径, 行号), ...]：裸 getLogger 调用点（豁免除外）。"""
    hits: list[tuple[str, int]] = []
    for p in _iter_py(src_root):
        rel = p.relative_to(src_root).as_posix()
        if rel.startswith(EXEMPT_PREFIXES):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getLogger"
            ):
                hits.append((rel, node.lineno))
    return hits


def _load_baseline() -> dict:
    if BASELINE.exists():
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    return {}


def selftest() -> int:
    """R-019：注入一条违规 → 计数器必须抓到（在临时目录里做，不碰 src/）。"""
    # 场景 1：有违规（裸 getLogger）→ 必须报出
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "pkg" / "mod.py"
        bad.parent.mkdir()
        bad.write_text(
            "import logging\nlogger = logging.getLogger(__name__)\n",
            encoding="utf-8",
        )
        clean = Path(td) / "pkg" / "clean.py"
        clean.write_text(
            "from agentnodes.foundation.logger import get_logger\nlogger = get_logger(__name__)\n",
            encoding="utf-8",
        )
        hits = count_bare(Path(td))
        if len(hits) != 1 or hits[0][0] != "pkg/mod.py" or hits[0][1] != 2:
            print(f"[X] selftest 场景 1 失败：期望恰好报出 pkg/mod.py:2，实得 {hits}")
            return 1
    print("[OK] selftest：注入违规被抓到（pkg/mod.py:2），合规文件未误报")
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()

    hits = count_bare(SRC)
    current = len(hits)
    base = _load_baseline()
    max_bare = base.get("max_bare")

    if "--update" in argv:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "max_bare": current,
            "note": base.get(
                "note",
                "统一日志中枢批 1 基线：src/ 内裸 getLogger 总数（豁免 observability 包与"
                " foundation/logger.py 自身）；只许减不许增。收编批次落地后用 --update 下调。",
            ),
        }
        BASELINE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"[OK] 基线已写回：max_bare = {current} → {BASELINE.relative_to(ROOT)}")
        return 0

    if max_bare is None:
        print(f"[X] 基线缺失：{BASELINE.relative_to(ROOT)}")
        print("    首次生成：python scripts/arch/check_logging_entry.py --update")
        return 1

    print(f"裸 getLogger 计数：current = {current} / 基线 max_bare = {max_bare}")
    if current > max_bare:
        print("[X] 棘轮回流：裸 getLogger 数量超过基线 —— 新代码必须用")
        print("    from agentnodes.foundation.logger import get_logger")
        by_file: dict[str, int] = {}
        for rel, _ln in hits:
            by_file[rel] = by_file.get(rel, 0) + 1
        for rel, n in sorted(by_file.items(), key=lambda kv: -kv[1])[:15]:
            print(f"    {n:3d}  {rel}")
        print("    （完整清单见上方计数逻辑；豁免仅 observability 包与 foundation/logger.py）")
        return 1

    if current < max_bare:
        print(f"[OK] 棘轮下降：{max_bare} → {current}（收编生效，可用 --update 下调基线）")
    else:
        print("[OK] 裸 getLogger：与基线持平（只许减不许增）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
