"""Read memory surface (FTS5 + injector) and pre_run for G2 design."""
from pathlib import Path
import re

root = Path("src/agentnodes")


def dump(rel, head=None, grep=None):
    p = root / rel
    if not p.exists():
        print("MISSING", rel)
        return
    t = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    print("=" * 8, rel, f"({len(t)} lines)")
    if head:
        print("\n".join(t[:head]))
    if grep:
        for i, line in enumerate(t, 1):
            if any(g in line for g in grep):
                print(f"{i}: {line}")


dump("core/agent/context_injector.py", head=35)
print()
dump("core/agent/context_injector.py", grep=["class MemoryRetrieverInjector", "def inject", "order", "return"])
print()
dump("core/agent/memory_manager.py", grep=["class ", "def search", "def retrieve", "FTS5", "MATCH", "def add"], limit=40)
print()
dump("core/agent/strategy/steps/pre_run.py", head=60)
