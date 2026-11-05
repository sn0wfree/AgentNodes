# -*- coding: utf-8 -*-
"""graph_layer_audit — graphify 符号图的层向审计（第二种量纲交叉验证）。

读取 graphify 抽取的代码图（json），输出：
  1. 跨层 import 矩阵（**符号定义层**口径——边指向符号的定义文件）
  2. core/ 残留节点（应为 0，物理分层后的彻底性证据）
  3. god-nodes in-degree top（结构均衡度旁证）

为什么存在：tests/test_layer_matrix.py 是**语句级**口径（import 语句写的
模块路径），graphify 是**符号级**口径（符号实际定义处）。两把尺子交叉：
2026-11-04 W7，正是「图上有 L4→L2 边、门禁报 0」的矛盾暴露了门禁
_resolve_targets 对绝对导入拼接 importer 前缀的 bug——静态门禁必须有
第二种量纲做交叉验证。

口径说明（读数前必读）：
  - 门禁矩阵（语句级）：违规 = import 语句写的模块路径跨层上行。
  - 本审计（符号级）：数字恒 ≥ 门禁，因为 re-export 门面会把符号级边
    "折回"到定义层。差值 = 经门面间接的耦合，是 G2b 演进的观测数据，
    不是违规。

前置（生成图，exe 被 Device Guard 拦时用 -m）：
    python -m graphify extract . --code-only --no-cluster --out tmp/graphify2

用法：
    python scripts/arch/graph_layer_audit.py tmp/graphify2/graphify-out/graph.json

命名规范见 scripts/README.md（arch/ = check_*，发现即失败；
本工具输出报告不设退出码，供人审 + 文档引用）。

来源：物理分层迁移 W7-3 沉淀（tmp/graph_audit.py + tmp/list_upstream.py
合并固化版）。
"""
from __future__ import annotations

import json
import sys
from collections import Counter

GRAPH_DEFAULT = r"tmp\graphify2\graphify-out\graph.json"
IMPORT_RELS = {"imports", "imports_from", "dynamic_import"}
LAYERS = ("interfaces", "apps", "kernel", "foundation")
LAYER_CODE = {"interfaces": "L1", "apps": "L2", "kernel": "L3", "foundation": "L4"}
ORDER = ["L1", "L2", "L3", "L4"]


def layer_of(path: str) -> str | None:
    p = path.replace("\\", "/")
    for name, code in LAYER_CODE.items():
        if f"src/agentnodes/{name}/" in p:
            return code
    if "src/agentnodes/core/" in p:
        return "CORE!"
    return None


def audit(graph: dict) -> tuple[Counter, Counter, Counter, dict]:
    """对已加载的图做全部统计。返回 (matrix, residual, indeg, nodes)。

    抽成纯函数以便单元测试（tests/test_scripts_arch.py 用合成图验证）。
    """
    nodes = {n["id"]: n for n in graph["nodes"]}
    file_of = {nid: n.get("source_file") or "" for nid, n in nodes.items()}
    matrix: Counter = Counter()
    residual: Counter = Counter()
    for e in graph["edges"]:
        if e["relation"] not in IMPORT_RELS:
            continue
        sf = file_of.get(e["source"], "")
        tf = nodes.get(e["target"], {}).get("source_file", "")  # 符号定义处
        sl, tl = layer_of(sf), layer_of(tf)
        if sl == "CORE!" or tl == "CORE!":
            residual[sf or tf or "?"] += 1
        if sl and tl and sl != tl:
            matrix[(sl, tl)] += 1
    indeg: Counter = Counter()
    for e in graph["edges"]:
        if e["relation"] in ("calls", "imports", "imports_from", "uses", "method"):
            indeg[e["target"]] += 1
    return matrix, residual, indeg, nodes


def main() -> int:
    graph_path = sys.argv[1] if len(sys.argv) > 1 else GRAPH_DEFAULT
    g = json.load(open(graph_path, encoding="utf-8"))
    print(f"graph: {graph_path}  nodes={len(g['nodes'])} edges={len(g['edges'])}")

    matrix, residual, indeg, nodes = audit(g)
    print("\n== 符号级跨层矩阵（语句级门禁对照见 tests/test_layer_matrix.py -s）==")
    print("      ->   L1    L2    L3    L4")
    for sl in ORDER:
        row = f"  {sl}  "
        for tl in ORDER:
            row += f"{matrix.get((sl, tl), 0):>6}"
        print(row)

    print(f"\n== core/ 残留：{sum(residual.values())} 边 / {len(residual)} 文件（应为 0）==")
    for f, c in residual.most_common(5):
        print(f"  {c:>3}  {f}")

    print("\n== god-nodes in-degree top 12 ==")
    for nid, d in indeg.most_common(12):
        n = nodes.get(nid, {})
        sf = (n.get("source_file") or "")[-58:]
        print(f"  {d:>5}  {n.get('label', nid)[:44]:<46} {sf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
