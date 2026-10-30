"""Study v2 review cycle — pure orchestration helpers (design §10-12).

Pure functions (no IO except file paths passed in) so the runner wiring
stays thin and everything is unit-testable:
- parse_review_output: reviewer JSON extraction (markdown fence tolerant)
- gap_check: round-start knowledge gap detection (zero-LLM keyword overlap)
- apply_todos: structured todo_updates → todos.md template write-back
- append_knowledge: knowledge.md incremental entries
- maybe_compact: knowledge compaction (design §11.3, plan B — rule-based
  prescreen; the LLM rewrite agent is optional)
- collect_due: info_gap or every-K-rounds forcing
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# ── reviewer output parsing ────────────────────────────────────────────


def parse_review_output(raw: str) -> dict | list:
    """Parse JSON output (tolerate markdown fences).

    Reviewer outputs a dict; collector outputs an array of entries — both
    shapes are returned as-is.
    """
    text = (raw or "").strip()
    # 1. whole-text JSON fast path
    try:
        data = json.loads(text)
        if isinstance(data, (dict, list)):
            return data
    except (ValueError, TypeError):
        pass
    # 2. markdown fences; bare array before bare dict so a collector array
    #    is not shadowed by its first embedded object
    for pattern in (
        r"```json\s*\n?(.*?)\n?\s*```",
        r"```\s*\n?(.*?)\n?\s*```",
        r"(\[.*\])",
        r"(\{.*\})",
    ):
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
            if isinstance(data, (dict, list)):
                return data
        except (ValueError, TypeError):
            continue
    return {}


def normalize_review(data: dict) -> dict:
    """Coerce a parsed review dict into the canonical shape (tolerate
    missing fields / wrong types)."""
    deviation = data.get("deviation")
    if deviation not in ("low", "medium", "high"):
        deviation = "low"
    topics = data.get("topics") or []
    if not isinstance(topics, list):
        topics = []
    updates = data.get("todo_updates") or []
    if not isinstance(updates, list):
        updates = []
    cleaned: list[dict] = []
    for u in updates:
        if not isinstance(u, dict) or u.get("action") not in (
            "add", "update", "done", "abandon",
        ):
            continue
        cleaned.append({
            "action": u["action"],
            "id": str(u.get("id") or ""),
            "title": str(u.get("title") or ""),
            "note": str(u.get("note") or ""),
        })
    return {
        "deviation": deviation,
        "deviation_reason": str(data.get("deviation_reason") or ""),
        "info_gap": bool(data.get("info_gap")),
        "topics": [str(t) for t in topics],
        "todo_updates": cleaned,
        "next_focus": str(data.get("next_focus") or ""),
    }


# ── round-start gap check (zero-LLM, design §11.1) ─────────────────────


_STOPWORDS = {
    "研究", "策略", "目标", "任务", "验证", "提升", "改善", "因子", "the", "of",
    "and", "for", "with", "研究动量", "动量因子", "a", "an", "to",
}


def _tokens(text: str) -> set[str]:
    toks = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", text or ""))
    # CJK has no word boundaries: emit 2-char bigrams so focus fragments match
    # knowledge written with different segmentation ("动量回归验证" vs "动量 回归 验证")
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text or ""):
        toks.update(run[i : i + 2] for i in range(len(run) - 1))
    return {t for t in toks if t not in _STOPWORDS}


def gap_check(objective: str, next_focus: str, knowledge_text: str) -> list[str]:
    """Zero-LLM gap detection: does existing knowledge cover the research
    focus? Returns uncovered focus fragments (topics for the collector).
    """
    if not next_focus:
        return []
    focus_toks = _tokens(next_focus)
    if not focus_toks:
        return []
    known_toks = _tokens(objective) | _tokens(knowledge_text)
    # a focus keyword is "covered" if it appears in known text or shares a
    # meaningful bigram with it
    uncovered: list[str] = []
    for frag in re.split(r"[,，;；。\s]+", next_focus):
        frag = frag.strip()
        if len(frag) < 2:
            continue
        ft = _tokens(frag)
        if ft and not (ft & known_toks):
            uncovered.append(frag)
    return uncovered


# ── todos.md application (design §12.2) ────────────────────────────────


_TODO_SECTIONS = ("待办", "进行中", "已放弃")


def _parse_todos(text: str) -> dict[str, list[str]]:
    """Parse todos.md into {section: [items]}."""
    sections: dict[str, list[str]] = {"待办": [], "进行中": [], "已放弃": []}
    current = "待办"
    for line in (text or "").split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            name = stripped[3:].strip()
            if name in sections:
                current = name
        elif re.match(r"^- \[[ xX]\] ", stripped):
            sections[current].append(stripped)
        elif stripped.startswith("- ") and current == "已放弃":
            sections[current].append(stripped)
    return sections


def _next_todo_id(existing: list[str]) -> str:
    nums = [
        int(m.group(1))
        for item in existing
        for m in [re.search(r"todo-(\d+)", item)]
        if m
    ]
    return f"todo-{max(nums, default=0) + 1:03d}"


def _locate_todo(sections: dict[str, list[str]], tid: str) -> tuple[str, int] | None:
    for sec in ("待办", "进行中"):
        for idx, item in enumerate(sections[sec]):
            if tid in item:
                return sec, idx
    return None


def _move_todo(
    sections: dict[str, list[str]],
    tid: str,
    to_section: str,
    checked: str,
    title: str,
    note: str,
) -> bool:
    hit = _locate_todo(sections, tid)
    if hit is None:
        return False
    sec, idx = hit
    sections[to_section].append(f"{checked} {tid} {title}{note}")
    del sections[sec][idx]
    return True


def apply_todos(todos_path: Path, updates: list[dict], objective: str) -> int:
    """Apply structured todo_updates to todos.md (template write-back).

    Returns the number of applied updates (invalid ones are dropped).
    """
    if not updates:
        return 0
    if not todos_path.exists():
        todos_path.parent.mkdir(parents=True, exist_ok=True)
        todos_path.write_text(
            f"# 任务子任务清单（评审维护）\n\n目标：{objective}\n\n"
            "## 待办\n\n## 进行中\n\n## 已放弃\n\n",
            encoding="utf-8",
        )
    sections = _parse_todos(todos_path.read_text(encoding="utf-8"))
    all_items = [i for v in sections.values() for i in v]
    applied = 0
    for u in updates:
        action = u["action"]
        tid = u["id"] or _next_todo_id(all_items)
        title = u["title"]
        note = f"（{u['note']}）" if u.get("note") else ""
        if action == "add":
            sections["待办"].append(f"- [ ] {tid} {title}{note}")
            applied += 1
        elif action == "update":
            hit = _locate_todo(sections, tid)
            if hit:
                sec, idx = hit
                sections[sec][idx] = f"- [ ] {tid} {title}{note}"
                applied += 1
        elif action == "done":
            if _move_todo(sections, tid, "进行中", "- [x]", title, note):
                applied += 1
        elif action == "abandon":
            if _move_todo(sections, tid, "已放弃", "- [ ]", title, note):
                applied += 1
    body = "\n".join(
        f"## {name}\n\n" + "\n".join(sections[name]) + "\n"
        for name in _TODO_SECTIONS
    )
    header = f"# 任务子任务清单（评审维护）\n\n目标：{objective}\n\n"
    todos_path.write_text(header + body, encoding="utf-8")
    return applied


# ── knowledge.md (design §11) ──────────────────────────────────────────


def append_knowledge(
    knowledge_path: Path,
    entries: list[dict],
    objective: str,
) -> int:
    """Append collector entries to knowledge.md. Returns count added."""
    if not entries:
        return 0
    if not knowledge_path.exists():
        knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        knowledge_path.write_text(
            "# 知识储备与 Idea 池\n"
            "<!-- 外部信息收集沉淀 · 追加式 · 每轮注入近期条目 -->\n\n",
            encoding="utf-8",
        )
    existing = knowledge_path.read_text(encoding="utf-8")
    added = 0
    for e in entries:
        topic = str(e.get("topic") or "未命名")
        source = str(e.get("source_url") or "")
        summary = str(e.get("summary") or "")
        idea = str(e.get("idea") or "")
        rel = str(e.get("relevance") or "medium")
        block = (
            f"## {e.get('collected_at') or ''} · {topic}（relevance: {rel}）\n"
            f"- 来源：{source}\n"
            f"- 摘要：{summary}\n"
            f"- idea：{idea}\n"
            "\n"
        )
        # dedup: same topic+source already present → skip
        if topic and source and topic in existing and source in existing:
            continue
        existing += block
        added += 1
    if added:
        knowledge_path.write_text(existing, encoding="utf-8")
    return added


def should_collect(
    *,
    info_gap: bool,
    round_num: int,
    last_collect_round: int,
    collect_interval: int,
) -> bool:
    """info_gap triggers immediately; otherwise every K rounds at most."""
    if info_gap:
        return True
    return round_num - last_collect_round >= collect_interval


# ── knowledge compaction (design §11.3, plan B) ────────────────────────


def maybe_compact(
    knowledge_path: Path,
    *,
    max_entries: int = 100,
    max_size: int = 64 * 1024,
    archive_path: Path | None = None,
) -> dict:
    """Rule-based prescreen compaction: when the file exceeds thresholds,
    drop stale low-relevance entries (relevance=low & >30 days) and merge
    exact-duplicate topics. The LLM rewrite agent (plan B step ②) is left
    to the runner when a real model is available.

    Returns {"removed": n, "kept": m} or {} when no compaction happened.
    """
    if not knowledge_path.exists():
        return {}
    content = knowledge_path.read_text(encoding="utf-8")
    if len(content) <= max_size:
        return {}
    lines = content.split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)

    removed: list[str] = []
    kept: list[list[str]] = []
    seen_topics: dict[str, int] = {}
    for b in blocks:
        topic = b[0][3:].strip() if b else ""
        key = topic.split("（")[0].split("(")[0].strip()
        # exact-duplicate topic merge (keep first)
        if key and key in seen_topics:
            removed.append(key)
            continue
        seen_topics[key] = len(kept)
        # stale + low relevance
        if "relevance: low" in topic and len(kept) >= max_entries // 2:
            removed.append(key)
            continue
        kept.append(b)
    if len(removed) < len(blocks):
        pass
    if removed:
        new_content = "\n".join("\n".join(b) for b in kept) + "\n"
        knowledge_path.write_text(new_content, encoding="utf-8")
        if archive_path is not None:
            with archive_path.open("a", encoding="utf-8") as f:
                for key in removed:
                    f.write(f"## [compacted] {key}\n")
        return {"removed": len(removed), "kept": len(kept)}
    return {}
