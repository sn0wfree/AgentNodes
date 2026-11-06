"""FastAPI app factory — HTTP API server for strategy research。"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

import agentnodes
from agentnodes import __version__

logger = logging.getLogger(__name__)


def create_app(
    workspace_path: Optional[Path] = None,
    goal_db_path: Optional[str] = None,
    hypotheses_path: Optional[str] = None,
    static_dir: Optional[str] = None,
    cors_origins: Optional[list] = None,
) -> FastAPI:
    """创建 FastAPI app。

    Args:
        workspace_path: Workspace directory path.
        goal_db_path: Goal DB path.
        hypotheses_path: Hypotheses file path.
        static_dir: Static files directory (e.g. `webui/static/`). If exists, will be served at `/`.
        cors_origins: CORS allowed origins (default: `["*"]` for dev).
    """
    # 日志统一入口（docs/guides/logging-unified-entry-design-20261102.md §4.4）：
    # web 面 console → stderr；AN_LOG_LEVEL / AN_LOG_JSON / AN_LOG_FILE
    # 全部由 configure_logging 统一解析。默认文本格式自带 trace 字段
    # （trace_id/session_id/study_id/round_num），AN_LOG_JSON=1 切 JSON。
    # v2 §14.2: hypotheses 默认走 SQLite（旧 JSON 不迁移、不读取）。
    from agentnodes.foundation.observability import configure_logging
    configure_logging("web")
    logger.info("[STARTUP] create_app called")

    # Register the core loop's compaction persister (legacy fallback
    # path) so core/agent/loop.py never imports the api layer.
    from agentnodes.kernel.agent.loop import register_compaction_persister
    from .routers.web_session import persist_message
    register_compaction_persister(persist_message)

    # W10-3 (2026-11-04): fill the L4 DataStore slot. AgentLoop reads the
    # slot instead of importing apps.data_store_registry (that would be a
    # L3 → L2 upward import). Money path: without this, ToolContext.data_store
    # is None and DB-backed tools fail at call time.
    #
    # 2026-11-05 批次 5：DuckDBDataStore 随数据域删除，注册表初始为空。
    # 这里 try/except —— 无 provider 时留空槽（ToolContext.data_store = None），
    # 业务侧若要恢复，在装配根 register_store(name, cls) 即可。
    try:
        from agentnodes.apps.data_store_registry import get_store
        from agentnodes.foundation.storage import register_default_data_store

        register_default_data_store(get_store())
        logger.info("[STARTUP] DataStore provider registered")
    except Exception as exc:  # noqa: BLE001 —— 无 provider 是合法状态
        logger.info("[STARTUP] no DataStore provider (%s); data_store slot stays empty", exc)

    workspace_path, goal_db_path, hypotheses_path, static_dir, cors_origins = (
        _resolve_app_env(
            workspace_path, goal_db_path, hypotheses_path, static_dir, cors_origins
        )
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Background tasks that run during the app's lifetime.

        - Schedule model catalog refresh 5s after startup so the user
          sees fresh metadata without blocking first response.

        The SessionService + EventStore are created eagerly by the DI
        container (``build_container``) at app creation; routers resolve
        them via ``api.dependencies`` (container-backed).
        """
        logger.info("[STARTUP] SessionService ready (DI container)")

        task = asyncio.create_task(_refresh_model_catalog_async())

        # Scheduled research daemon: migrate legacy JSON jobs once, then
        # tick on the main loop dispatching due jobs.
        # 2026-11-05 批次 3：study 目标已移除（apps/study 归档）。
        # 调度器骨架保留（cron 计算 + scheduled_jobs 表是通用能力），
        # target='study' 的 job 会抛明确错误而不是静默 no-op。
        from agentnodes.kernel.scheduled_research.executor import ScheduledResearchExecutor
        from agentnodes.kernel.scheduled_research.store import ScheduledResearchStore

        schedule_store = ScheduledResearchStore()
        migrated = schedule_store.migrate_from_json()
        if migrated:
            logger.info("[STARTUP] migrated %d legacy scheduled jobs → SQLite", migrated)

        # Pre-parse agent schemas from .prompts/*.md (for /api/agents/schemas)
        _agents_warmup()

        schedule_executor = ScheduledResearchExecutor(schedule_store)
        schedule_executor.start(loop=asyncio.get_running_loop())
        logger.info("[STARTUP] scheduled research daemon started")

        try:
            yield
        finally:
            schedule_executor.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    app = FastAPI(
        title="Strategy Research API",
        version=__version__,
        description="HTTP API for quantnodes strategy research framework",
        lifespan=lifespan,
    )

    # Store config in app state
    app.state.workspace_path = workspace_path
    app.state.goal_db_path = goal_db_path
    app.state.hypotheses_path = hypotheses_path

    # ── DI container: single construction point for API services ─────
    # Wire the container eagerly so all routers share one SessionService
    # / SessionStore / EventStore (queue/cancel state, SSE bridges).
    # The legacy routers/chat.py singleton cache is pre-seeded with the
    # container's services so both access paths stay identical.
    from .container import attach_container, build_container, services_from_container
    from .routers.chat import _session_service_cache
    from .routers.web_session import _get_db_path

    container = build_container(workspace_path=workspace_path, db_path=Path(_get_db_path()))
    attach_container(app, container)
    for key, svc in services_from_container(container).items():
        setattr(app.state, key, svc)
    _session_service_cache[str(container.db_path)] = container.session_service
    logger.info("[STARTUP] DI container attached (db=%s)", container.db_path)

    # DuckDB init on startup —— 2026-11-05 批次 5 移除
    # apps/db.py（DuckDB OHLCV 表）随数据域一起删除。工作区的 data.duckdb
    # 若由业务侧维护，请在业务侧的启动钩子里初始化；框架不再建这个文件。
    if workspace_path:
        # Smart scaffold: recursively ensure workspace templates/ mirrors
        # package templates/. Idempotent — user customizations preserved.
        # See apps/workspace_setup.py and docs/archive/undated/scaffold-fix.md.
        try:
            from agentnodes.apps.workspace_setup import smart_init_workspace_templates
            smart_init_workspace_templates(workspace_path)
        except Exception as exc:
            logger.warning("Smart scaffold failed: %s", exc)

    # CORS
    origins = cors_origins if cors_origins is not None else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register auth middleware
    from .middleware import AuthMiddleware
    app.add_middleware(AuthMiddleware)

    # Register routers
    # goal / workflow 两个业务 router 已随 R2（批次 3）恢复：apps/goal 在
    # 批次 2（R1）重建，L1 的 goal.py / workflow.py 随之搬回并重新挂载。
    # hypothesis / validation router 仍保持移除状态（apps/hypothesis、
    # apps/validation 未恢复，无条件不可达）。
    # study / slash_commands 两个 router 已随 apps/study 移除（2026-11-05 批次 3）。
    # run router 已随 apps/backtest 移除（2026-11-05 批次 4）。
    from .routers import (
        admin,
        auth,
        chat,
        goal,
        permission,
        web_session,
        workflow,
    )

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(web_session.router, prefix="/api/chat/session", tags=["chat-session"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
    from .routers import admin_users
    app.include_router(admin_users.router, prefix="/api/admin", tags=["admin-users"])

    # Goal ledger (R2): /api/goal/* — frontend panel + goal_updated SSE.
    app.include_router(goal.router, prefix="/api/goal", tags=["goal"])
    # Goal workflow control + modular DAG workflow runs.
    # Frontend contract is /api/goal/workflow/* (see webui types.gen.ts).
    app.include_router(
        workflow.router, prefix="/api/goal/workflow", tags=["goal-workflow"],
    )

    # /api/study 路由已随 apps/study 移除（2026-11-05 批次 3）
    # /api/run 路由已随 apps/backtest 移除（2026-11-05 批次 4）
    from .routers import schedule
    app.include_router(schedule.router, prefix="/api/schedule", tags=["schedule"])
    # MiniMax / DuckDuckGo web search (front-end direct invocation)
    from .routers import search as search_router
    app.include_router(search_router.router, tags=["search"])

    # Permission gate (Tier 1 A1) — answers to permission_request SSE
    # events. Mounted under /api/chat so the frontend can use the
    # same base path as the rest of the chat surface.
    app.include_router(permission.router, prefix="/api/chat/permission", tags=["permission"])

    # System info (settings modal)
    from .routers import system
    app.include_router(system.router, prefix="/api/system", tags=["system"])

    # Agent schemas (from .prompts/*.md — auto-refresh on file change)
    from .routers import agents
    app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
    from .routers.agents import startup_warmup as _agents_warmup

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # Legacy HTMX /webui routes removed (structure-debt W3) — use the
    # React SPA under webui/static (mounted below) + /api/* REST.

    # Serve static files if available
    static_path = Path(static_dir) if static_dir else Path(agentnodes.__file__).parent.parent.parent / "webui" / "static"
    if static_path.exists():
        _mount_static_files(app, static_path)
    else:
        @app.get("/")
        async def root():
            return {
                "service": "strategy-research-api",
                "version": __version__,
                "docs": "/docs",
            }

    return app


def _resolve_app_env(
    workspace_path: Optional[Path],
    goal_db_path: Optional[str],
    hypotheses_path: Optional[str],
    static_dir: Optional[str],
    cors_origins: Optional[list],
) -> tuple:
    """Resolve config values from environment when not explicitly provided."""
    if workspace_path is None:
        env = os.environ.get("AN_WORKSPACE_PATH")
        workspace_path = Path(env) if env else None
    if goal_db_path is None:
        # A5 (评审 F10): one name for the goal ledger. The helper reads
        # AGENTNODES_GOAL_DB_PATH first, then the legacy aliases
        # (AN_GOAL_DB_PATH / QUANTNODES_* / STRATEGY_RESEARCH_*), so the
        # CLI ``--goal-db`` flag and the agent tools resolve the same file.
        from agentnodes.foundation.storage.sqlite import goal_db_env_value

        goal_db_path = goal_db_env_value() or None
    if hypotheses_path is None:
        hypotheses_path = os.environ.get("AN_HYPOTHESES_PATH")
    if static_dir is None:
        static_dir = os.environ.get("STATIC_DIR")
    if cors_origins is None:
        cors_str = os.environ.get("CORS_ORIGINS")
        cors_origins = cors_str.split(",") if cors_str else None
    return workspace_path, goal_db_path, hypotheses_path, static_dir, cors_origins


def _mount_static_files(app: FastAPI, static_path: Path) -> None:
    """Mount static assets + SPA fallback routes."""
    assets_path = static_path / "assets"
    if assets_path.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_path)), name="assets")

    # Serve index.html for all non-API routes (SPA fallback)
    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(static_path / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        # Don't intercept API routes
        if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not found")

        # Resolve + contain the path inside the static dir (blocks
        # path traversal via ".." / encoded segments).
        try:
            resolved = (static_path / full_path).resolve()
            resolved.relative_to(static_path.resolve())
        except (ValueError, OSError):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not found")

        if resolved.is_file():
            return FileResponse(resolved)
        # Fallback to index.html (SPA routing)
        return FileResponse(static_path / "index.html")

    logger.info("Serving static files from %s", static_path)


def configure_from_env():
    """Read environment variables and return configuration."""
    config = {
        "cors_origins": os.environ.get("CORS_ORIGINS", "").split(",") if os.environ.get("CORS_ORIGINS") else None,
        "static_dir": os.environ.get("STATIC_DIR"),
        "jwt_secret": os.environ.get("JWT_SECRET"),
    }
    return config


async def _refresh_model_catalog_async() -> None:
    """Background: refresh model catalog for the currently configured LLM.

    Runs 5s after startup so the first user request doesn't pay the
    network cost. Failure is silent (log warning; bundled data is used).

    User-config overrides on ``model_context_tokens`` etc. are passed
    through so the refreshed entry reflects user expectations.
    """
    await asyncio.sleep(5)
    try:
        from agentnodes.foundation.llm.config import LLMConfig
        from agentnodes.foundation.llm.model_catalog import refresh_model_info

        llm_config = LLMConfig.load()
        provider = llm_config.provider
        model = llm_config.model
        if not provider or not model or provider == "auto":
            logger.debug("Model catalog refresh skipped: LLM not configured")
            return
        info = await refresh_model_info(
            provider, model, user_config=llm_config
        )
        logger.info(
            "Model catalog refreshed: %s/%s context=%d source=%s",
            info.provider,
            info.model,
            info.context_tokens,
            info.source,
        )
    except Exception as exc:
        logger.warning("Model catalog refresh failed: %s", exc)


__all__ = ["create_app", "configure_from_env", "_refresh_model_catalog_async"]
