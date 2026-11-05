"""Renumber sections in architecture-v2 doc after inserting a new section 5."""
from pathlib import Path
import re

p = Path("docs/archive/2026-10/architecture-v2-and-rsi-20261008.md")
text = p.read_text(encoding="utf-8")

# split at the RSI part marker so we only renumber the RSI half
marker = "# 第二部分 · RSI"
i = text.index(marker)
head, tail = text[:i], text[i:]

# renumber tail: 6..13 -> 7..14 (process descending to avoid collisions)
for old in range(13, 5, -1):
    tail = re.sub(rf"^## {old}\. ", f"## {old+1}. ", tail, flags=re.M)

p.write_text(head + tail, encoding="utf-8")
print("headings now:")
for line in (head + tail).splitlines():
    if line.startswith("## ") or line.startswith("# "):
        print("  ", line[:70])
