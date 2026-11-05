from pathlib import Path
import re

root = Path("src/agentnodes")

print("════ AutoresearchRunner 到底被谁用")
for p in root.rglob("*.py"):
    t = p.read_text(encoding="utf-8", errors="ignore")
    if "AutoresearchRunner" in t:
        n = t.count("AutoresearchRunner")
        print(f"  {p.name:26s} {n}")

print("\n════ core/study/runner.py 的类与文件名不一致")
print("   文件: core/study/runner.py  类: AutoresearchRunner")
print("   同名文件: core/autoresearch.py （1393 行，阶段函数）")

print("\n════ session(chat) 路径有没有预算门")
p = root / "api/session/service.py"
t = p.read_text(encoding="utf-8", errors="ignore")
for kw in ("budget", "max_iterations", "timeout", "_CHAT_ATTEMPT_TIMEOUT"):
    print(f"   {kw:22s} {t.count(kw)}")
p2 = root / "core/agent/chat_loop.py"
t2 = p2.read_text(encoding="utf-8", errors="ignore")
print("   chat_loop.py budget:", t2.count("budget"), " max_iterations:", t2.count("max_iterations"))

print("\n════ 三个编排的「运行期环境」是否共用同一模块")
cands = ["core/agent/strategy/budget.py", "core/observability", "core/agent/trace.py",
         "core/agent/event_store.py", "core/events/event_v2.py"]
for c in cands:
    q = root / c
    if q.exists():
        if q.is_file():
            print(f"   {c}  ({len(q.read_text(encoding='utf-8').splitlines())} lines)")
        else:
            print(f"   {c}/  -> {sorted(x.name for x in q.iterdir() if x.suffix=='.py')[:6]}")

print("\n════ CancelToken / ControlToken / cancel_event 是否有统一形态")
for sym in ("ControlToken", "CancelToken", "cancel_event", "cancel_scope", "asyncio.CancelledError"):
    users = []
    for p in root.rglob("*.py"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if sym in t:
            users.append(p.name)
    print(f"   {sym:22s} {len(users):3d} files {users[:6]}")
