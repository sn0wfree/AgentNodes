"""Extracted from cli.py — server and tool commands.

Contains:
- cmd_webui_serve
- cmd_api_serve
- cmd_mcp_serve
- cmd_mcp_list_tools
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _start_gateway_consumer() -> Any | None:
    """R2.1b：宿主启动常驻消费者（E1=C receipt 契约的执行主体）。

    仅真实服务入口调用——测试经 create_app/TestClient 走同步回落路径。
    惰性构造 Gateway；LLM 未配置等失败只降级（/chat/send 回落同步语义），
    不阻断服务启动。--reload 开发模式不启动（app 在 reloader 子进程重建）。
    """
    try:
        from strategy_research.kernel.gateway import get_gateway
        from strategy_research.kernel.orchestration import WorkKind

        gw = get_gateway()
        # B2：先恢复状态（fold），再启动消费者——「恢复状态，不恢复执行」。
        try:
            stats = gw.recover()
            print(
                f"✓ 事件流恢复：{stats.get('sessions', 0)} 个 session、"
                f"{stats.get('turns', 0)} 个 turn 计数、"
                f"{stats.get('interrupted', 0)} 个中断 run 已标记"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"⚠ 事件流恢复失败（继续启动）: {exc}")
        # E2：配置指纹进事件流（audit 对账；失败只降级）
        fp = gw.emit_config_fingerprint()
        if fp:
            print(f"✓ 配置指纹：{fp}")
        gw.start_consumer(kinds={WorkKind.CHAT})
        print("✓ Gateway 常驻消费者已启动（/chat/send 走 receipt 契约）")
        return gw
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ Gateway 消费者未启动（/chat/send 回落同步语义）: {exc}")
        return None


def cmd_webui_serve(args) -> int:
    """启动 Web UI 服务器。"""
    import os

    import uvicorn

    from strategy_research.interfaces.api.app import configure_from_env, create_app
    from strategy_research.interfaces.cli.llm_config_check import check_llm_config

    # Read env config (CORS_ORIGINS, JWT_SECRET, STATIC_DIR)
    env_config = configure_from_env()

    workspace = Path(args.workspace)
    static_dir = getattr(args, "static_dir", None) or env_config.get("static_dir")

    # ── Startup banner ──────────────────────────────────────────────
    print(f"🌐 Strategy Research Web UI starting at http://{args.host}:{args.port}")
    print(f"   Workspace: {workspace}")
    if static_dir:
        print(f"   Static files: {static_dir}")
    print(f"   Docs:         http://{args.host}:{args.port}/docs")

    # ── LLM 配置状态检测 ──────────────────────────────────────────
    try:
        llm_status = check_llm_config()
        if llm_status["configured"]:
            print(
                f"✓ LLM 配置：{llm_status['provider']} / {llm_status['model']} "
                f"(api_key={llm_status['api_key_source']})"
            )
        else:
            print("⚠ LLM 配置未完成（聊天 API 会返回 503）：")
            print("   - 缺少 ~/.quantnodes/llm.json 或 .env")
            print("   - 运行：quantnodes-research init")
            print("   - 或设置：export OPENAI_API_KEY='sk-...'")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ LLM 配置检测失败: {exc}")

    # ── Server start ──────────────────────────────────────────────
    if getattr(args, "reload", False):
        # Push CLI args into env so create_app() picks them up on each reload
        os.environ["SR_WORKSPACE_PATH"] = str(workspace.resolve())
        if getattr(args, "goal_db", None):
            os.environ["SR_GOAL_DB_PATH"] = args.goal_db
        if getattr(args, "hypotheses_path", None):
            os.environ["SR_HYPOTHESES_PATH"] = args.hypotheses_path
        if static_dir:
            os.environ["STATIC_DIR"] = str(Path(static_dir).resolve())

        uvicorn.run(
            "strategy_research.interfaces.api.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=True,
        )
    else:
        app = create_app(
            workspace_path=workspace if workspace.exists() else None,
            goal_db_path=getattr(args, "goal_db", None),
            hypotheses_path=getattr(args, "hypotheses_path", None),
            static_dir=static_dir,
            cors_origins=env_config.get("cors_origins"),
        )
        gw = _start_gateway_consumer()
        try:
            uvicorn.run(app, host=args.host, port=args.port)
        finally:
            if gw is not None:
                gw.stop_consumer()

    return 0


def cmd_api_serve(args) -> int:
    """启动 HTTP API 服务器。"""
    import os

    import uvicorn

    from strategy_research.interfaces.api.app import create_app

    workspace = Path(args.workspace)

    print(f"🚀 Strategy Research API starting at http://{args.host}:{args.port}")
    print(f"   Workspace: {workspace}")
    print(f"   Docs:      http://{args.host}:{args.port}/docs")

    if getattr(args, "reload", False):
        os.environ["SR_WORKSPACE_PATH"] = str(workspace.resolve())
        if getattr(args, "goal_db", None):
            os.environ["SR_GOAL_DB_PATH"] = args.goal_db
        if getattr(args, "hypotheses_path", None):
            os.environ["SR_HYPOTHESES_PATH"] = args.hypotheses_path

        uvicorn.run(
            "strategy_research.interfaces.api.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=True,
        )
    else:
        app = create_app(
            workspace_path=workspace if workspace.exists() else None,
            goal_db_path=getattr(args, "goal_db", None),
            hypotheses_path=getattr(args, "hypotheses_path", None),
        )
        gw = _start_gateway_consumer()
        try:
            uvicorn.run(app, host=args.host, port=args.port)
        finally:
            if gw is not None:
                gw.stop_consumer()

    return 0


def cmd_mcp_serve(args) -> int:
    """启动 MCP 服务器。"""
    from strategy_research.interfaces.mcp import MCPServer

    server = MCPServer()
    server.register_default_tools()

    if args.transport == "stdio":
        server.serve_stdio()
    else:
        print(f"MCP SSE server not yet implemented (port {args.port})")
        return 1

    return 0


def cmd_mcp_list_tools(args) -> int:
    """列出所有 MCP 工具。"""
    from strategy_research.interfaces.mcp import MCPServer

    server = MCPServer()
    server.register_default_tools()

    tools = server.list_tools()
    print(f"=== MCP Tools (共 {len(tools)} 个) ===")
    for t in tools:
        params = t.get("inputSchema", {}).get("properties", {})
        param_str = ", ".join(params.keys()) if params else "no params"
        print(f"  {t['name']:30s}  {param_str:30s}  {t['description'][:40]}")

    return 0
