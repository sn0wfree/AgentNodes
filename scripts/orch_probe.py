"""Are SessionService / StudyRunner / AgentLoop really 3 orchestrators? Compare their control surfaces."""
from pathlib import Path
import re

root = Path("src/agentnodes")

PARTS = {
    "SessionService": "api/session/service.py",
    "StudyRunner": "core/study/runner.py",
    "AgentLoop": "core/agent/loop.py",
}

print("════ 1. 各自的「控制面」：入口 / 取消 / 暂停 / 状态 / 结果")
for name, rel in PARTS.items():
    p = root / rel
    t = p.read_text(encoding="utf-8", errors="ignore")
    print(f"\n-- {name} ({rel}, {len(t.splitlines())} lines)")
    for kw in ("def run", "def arun", "def start", "def cancel", "def pause", "def resume",
               "def status", "def stop", "CancelledError", "cancel_scope", "_cancel",
               "budget", "event_store", "emit(", "trace"):
        c = t.count(kw)
        if c:
            print(f"   {kw:16s} {c}")

print("\n════ 2. 嵌套关系：谁调用谁")
def callers_of(symbol, exclude):
    out = []
    for p in root.rglob("*.py"):
        if p.name in exclude:
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        if symbol in t:
            out.append(p.name)
    return out

print("  AgentLoop 被引用:", callers_of("AgentLoop", {"loop.py"}))
print("  StudyRunner 被引用:", callers_of("StudyRunner", {"runner.py"}))
print("  SessionService 被引用:", callers_of("SessionService", {"service.py"}))

print("\n════ 3. 共享机制：预算 / 事件 / 取消 / 追踪 是否共用同一实现")
for mech, syms in [
    ("预算", ["evaluate_budget", "BudgetLimits", "record_usage", "budget.py"]),
    ("事件", ["event_bus.emit", "EventStore", "_emit(", "emit_tool_progress"]),
    ("取消", ["CancelledError", "cancel_event", "control.cancelled", "ControlToken"]),
    ("追踪", ["trace", "TraceWriter", "new_trace_id"]),
]:
    hits = []
    for name, rel in PARTS.items():
        t = (root / rel).read_text(encoding="utf-8", errors="ignore")
        n = sum(t.count(s) for s in syms)
        hits.append(f"{name}={n}")
    print(f"  {mech:4s} " + "  ".join(hits))

print("\n════ 4. 结果类型（能否互换）")
for name, rel in PARTS.items():
    t = (root / rel).read_text(encoding="utf-8", errors="ignore")
    rets = re.findall(r"def (?:run|arun|start|send_message)\([^)]*\)[^:]*:\s*->\s*([^:\n]+)", t, re.S)
    print(f"  {name:16s} 返回类型: {rets[:4]}")
