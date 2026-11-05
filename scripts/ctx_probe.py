"""Read the actual context-assembly path and memory surface for G1/G2 design."""
from pathlib import Path
import re

root = Path("src/agentnodes")


def dump(rel, head=None, grep=None, limit=60):
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


dump("core/agent/prompt_builder.py", grep=["class ", "def build_system_prompt", "def build_messages",
                                           "def estimate_tokens", "def validate", "return "], limit=70)
print()
dump("core/agent/loop_compaction.py", grep=["def ", "threshold", "estimate_tokens", "tokens"], limit=50)
print()
dump("core/agent/compact.py", grep=["def ", "threshold_tokens", "estimate", "keep_recent"], limit=50)
print()
dump("core/agent/context_injector.py", grep=["class ", "def inject", "order", "memory", "retriev"], limit=60)
