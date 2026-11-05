"""Which further OpenClaw orchestration ideas do we lack? (evidence check)"""
from pathlib import Path
import re

root = Path("src/agentnodes")

def find(kw, label=None):
    hits = []
    for p in root.rglob("*.py"):
        if "_archived" in str(p):
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        n = t.count(kw)
        if n:
            hits.append((p.name, n))
    return hits

for kw in ["idempoten", "idempotency_key", "dedupe", "request_id", "nonce"]:
    print(f"{kw:18s} -> {find(kw)[:4]}")

print("\n── watchdog / 停滞 / 长跑检测")
for kw in ["watchdog", "stalled", "stuck", "long_running", "heartbeat_timeout", "idle_timeout"]:
    print(f"{kw:18s} -> {find(kw)[:4]}")

print("\n── 无进展检测（agent 层 vs study 层）")
for kw in ["_check_no_progress", "no_progress_window", "STAGNATION", "stagnation", "DISCARD_STREAK"]:
    print(f"{kw:20s} -> {find(kw)[:4]}")

print("\n── 事件重放 / 续传")
for kw in ["def replay", "since_seq", "last_seq", "after_seq", "reconnect"]:
    print(f"{kw:18s} -> {find(kw)[:4]}")

print("\n── mutation 类操作（与 turn 是否混排）")
for kw in ["complete_lite", "update_goal_id", "def cancel_study", "reset_", "compact"]:
    print(f"{kw:18s} -> {find(kw)[:4]}")

print("\n── 产出义务：空产出如何判定")
for p in root.rglob("*.py"):
    if "_archived" in str(p):
        continue
    t = p.read_text(encoding="utf-8", errors="ignore")
    if "hypothesis" in t and ("no_hypothesis" in t or "missing" in t.lower()):
        pass
print("phase_engine 里的 hypothesis 产出判定:")
t = (root / "core/study/phase_engine.py").read_text(encoding="utf-8", errors="ignore")
for i, l in enumerate(t.splitlines(), 1):
    if re.search(r"hypothesis\s*=|hypothesis:|predictions|captured_predictions", l):
        print(f"  {i}: {l.strip()[:100]}")
