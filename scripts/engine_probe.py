from pathlib import Path
import re

root = Path("src/agentnodes")

print("════ RoundOutcome / BranchContext 的定义处与引用处")
for p in root.rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="ignore")
    for sym in ("RoundOutcome", "BranchContext"):
        if sym in t:
            imp = re.findall(rf"from [\w.]*\.?([\w.]+) import[^(\n]*{sym}", t)
            frm = re.search(rf"class {sym}\b", t)
            print(f"  {sym:14s} {p.name:24s} 定义={bool(frm)} import自={imp[:2]}")

print("\n════ phase_engine 的入口")
p = root / "core/study/phase_engine.py"
t = p.read_text(encoding="utf-8", errors="ignore")
for name in ("_run_phases_branch", "_prepare_round_preamble", "_resolve_engine"):
    i = t.find(f"def {name}")
    if i > 0:
        seg = t[i : i + 420]
        doc = re.search(r'"""(.*?)"""', seg, re.S)
        print(f"\n  {name}:")
        if doc:
            print("    ", " / ".join(x.strip() for x in doc.group(1).splitlines()[:3] if x.strip()))

print("\n════ 引擎层与编排层的职责边界：谁决定停止")
for rel in ["core/study/runner.py", "core/study/phase_engine.py"]:
    q = root / rel
    t = q.read_text(encoding="utf-8", errors="ignore")
    print(f"\n  {rel}:")
    for kw in ("_check_stop_conditions", "ShutdownReason", "max_rounds", "early_stop", "stagnation",
               "discard", "novelty", "review_cycle", "claims", "journal"):
        c = t.count(kw)
        if c:
            print(f"    {kw:22s} {c}")
