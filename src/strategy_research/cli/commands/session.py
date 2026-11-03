"""Extracted from cli.py — session management commands.

Contains:
- cmd_session_list
- cmd_session_show
- cmd_session_search
- cmd_session_delete

B4 D3: ``cmd_session_stats`` was retired with the legacy
``core.session.db`` metrics logger. ``EventStore.health_report()`` is the
current source of write-path/backend health; run
``pytest tests/test_event_store_comprehensive.py -k health`` or call it from
Python.
"""

from __future__ import annotations


def cmd_session_list(args) -> int:
    """列出会话（统一库，B4 D5）。

    原先走 ``core.session.db.SessionDB``（旧栈，schema 不同）。改用
    ``core.session.unified.list_sessions``——与 CLI/TUI/MCP 共用同一查询。
    """
    from strategy_research.kernel.session.unified import list_sessions

    sessions = list_sessions(limit=args.limit)

    if not sessions:
        print("暂无会话")
        return 0

    from datetime import datetime

    print(f"=== 会话列表 (共 {len(sessions)} 个) ===")
    for s in sessions:
        created = datetime.fromtimestamp(s["updated_at"]).strftime("%Y-%m-%d %H:%M")
        title = s.get("title") or "(untitled)"
        print(f"  {s['id'][:16]:16s}  {created}  {title}")

    return 0


def cmd_session_show(args) -> int:
    """显示会话详情（统一库，B4 Step 3）。

    原实现读 ``core.session.db.SessionDB`` 的 ``session.metadata_json`` /
    ``msg.timestamp``——旧 dataclass 根本没有这两个字段，所以它一直是坏的
    （AttributeError）。统一库的列名是 ``created_at`` / ``metadata_json``。
    """
    from strategy_research.kernel.session.unified import get_messages, get_session

    session = get_session(args.session_id)
    if not session:
        print(f"会话 {args.session_id} 不存在")
        return 1

    from datetime import datetime
    created = datetime.fromtimestamp(session["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
    updated = datetime.fromtimestamp(session["updated_at"]).strftime("%Y-%m-%d %H:%M:%S")

    print("=== 会话详情 ===")
    print(f"  ID:        {session['id']}")
    print(f"  标题:      {session.get('title') or '(untitled)'}")
    print(f"  创建时间:  {created}")
    print(f"  更新时间:  {updated}")

    messages = get_messages(args.session_id, limit=50)
    if messages:
        print(f"\n=== 消息 (共 {len(messages)} 条) ===")
        for msg in messages:
            ts = datetime.fromtimestamp(msg["created_at"]).strftime("%H:%M:%S")
            content = (msg["content"] or "")[:120]
            print(f"  [{ts}] {msg['role']}: {content}")
    else:
        print("\n(无消息)")

    return 0


def cmd_session_search(args) -> int:
    """搜索消息（统一库 LIKE，B4 Step 3）。

    旧实现走 ``SessionDB`` 的 FTS5 索引；当前 Web 搜索端点已经改用 LIKE
    （trigram 分词器行为问题），这里跟随线上语义，不重新引入第二套索引。
    """
    from strategy_research.kernel.session.unified import search_messages

    results = search_messages(args.query, limit=args.limit)

    if not results:
        print(f"未找到匹配 '{args.query}' 的消息")
        return 0

    print(f"=== 搜索结果 (共 {len(results)} 条, 关键词: {args.query}) ===")
    for r in results:
        session_id = (r.get("session_id") or "?")[:12]
        role = r.get("role", "?")
        content = (r.get("content", "") or "")[:120]
        print(f"  [{session_id}] {role}: {content}")

    return 0


def cmd_session_delete(args) -> int:
    """删除会话（统一库，B4 Step 3）。"""
    from strategy_research.kernel.session.unified import delete_session, get_session

    if not get_session(args.session_id):
        print(f"会话 {args.session_id} 不存在")
        return 1

    ok = delete_session(args.session_id)
    if ok:
        print(f"已删除会话 {args.session_id}")
    else:
        print(f"删除会话 {args.session_id} 失败")
        return 1

    return 0
