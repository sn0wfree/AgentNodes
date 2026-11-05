"""B1-B17: source-grep 守门, 确保所有 DELETE-CANDIDATE v0.6 标签都在."""

from __future__ import annotations

import re
from pathlib import Path

# Each entry: (path-relative-to-repo-root, substring-of-existing-context).
# All B1-B17 entries have been executed (deletions) or retracted (stale
# labels with live callers — see docs/archive/undated/final-cleanup-batch-design.md §1).
# The list is intentionally empty; the reverse check below
# (test_no_unlabelled_todo_delete_suggestion) is now the only active rule:
# any NEW 'unused/dormant/0 callers' TODO must still carry a label.
EXPECTED_LABELS: list[tuple[str, str]] = []

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_all_delete_candidate_labels_present():
    missing: list[str] = []
    for rel_path, hint in EXPECTED_LABELS:
        full = REPO_ROOT / rel_path
        assert full.exists(), f"missing file: {rel_path}"
        text = full.read_text(encoding="utf-8")
        # Match: a DELETE-CANDIDATE v0.6 line containing the hint substring
        pattern = rf"DELETE-CANDIDATE v0\.6:[^\n]*{re.escape(hint)}"
        if not re.search(pattern, text):
            missing.append(f"{rel_path}: hint '{hint}' not adjacent to a DELETE-CANDIDATE line")
    assert not missing, "missing DELETE-CANDIDATE v0.6 labels:\n  " + "\n  ".join(missing)


def test_no_unlabelled_todo_delete_suggestion():
    """Files mentioning 'unused/dormant/0 callers' TODOs must also have a
    DELETE-CANDIDATE label somewhere (informational gate)."""
    candidates: list[tuple[str, int, str]] = []
    for ext in ("*.py", "*.ts", "*.tsx"):
        for f in REPO_ROOT.rglob(f"src/**/{ext}"):
            if "/node_modules/" in str(f) or "/__pycache__/" in str(f):
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if re.match(r"\s*#\s*(TODO|FIXME)\(architecture\):", line):
                    lower = line.lower()
                    if any(k in lower for k in (
                        "0 production callers", "unused", "dormant",
                        "no callers", "not wired",
                    )):
                        candidates.append((str(f.relative_to(REPO_ROOT)), i, line.strip()[:80]))
    missing_label: list[str] = []
    for rel, lineno, _ in candidates:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if "DELETE-CANDIDATE v0.6" not in text:
            missing_label.append(f"{rel}:{lineno}")
    assert not missing_label, (
        "files with 'unused/dormant/0 callers' TODOs but no DELETE-CANDIDATE:\n  "
        + "\n  ".join(missing_label)
    )
