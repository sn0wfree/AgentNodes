# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed — 项目重定位（2026-11-05）

本仓库从 QuantNodes 量化研究项目中剥离出 **agent 框架**部分独立开发。
业务侧（回测 / 因子 / 行情 / 研究流水线）按
[`docs/design/subtraction-roadmap-v2-20261105.md`](docs/design/subtraction-roadmap-v2-20261105.md)
分批移除，最终 `apps/` 只保留 `evolution`（RSI 闭环）与 `capabilities`（注册门面）。

已完成的减法批次（累计删除约 48,600 行，原 L2 的 75%）：

| 批次 | 内容 | 删除行数 |
|---|---|---:|
| 1 | `apps/alpha_zoo`（468 文件） | 27,263 |
| 2 | `goal` / `hypothesis` / `validation` / `portfolio` / `export` | 9,328 |
| 3 | `study` / `autoresearch`（Ticket 提取到 L3） | ~12,000 |
| 4 | 回测与验证域（进行中） | ~5,982 |

**结构性成果**：`L3→L2` 从 46 条清零（CapabilityRegistry 端口化），
`L2→L1` 清零，上行违规从 55 降到 2（均为 L3→L1 展示层引用）。

**文档重组**：`docs/` 分为 `guides/`（现行手册）· `design/`（仍生效的设计）·
`archive/`（历史记录）三层；新增 `docs/REFERENCE.md`（命令/工具/环境变量全表）；
根 `README.md` 从 840 行精简到约 260 行，只描述框架本体。

**包名重命名** `strategy_research` → `agentnodes` 是批次 9，**尚未执行**。

### Added
- Chat 智能体 `run_command`（bash/python）能力：`SR_ALLOW_SHELL_TOOLS=1`
  环境变量开启（支持 cwd `.env` 配置），plan mode 始终禁用；
  抽出 `chat._shell_tools_enabled()` 统一开关决策。
- `tests/test_chat_shell_tools.py` — shell 工具门控决策测试。

### Fixed
- `.env` 加载：`LLMConfig._try_load_dotenv` 显式加载进程 cwd 的 `.env`。
  原实现使用无参 `load_dotenv()`，其 `find_dotenv()` 从库文件位置向上
  搜索而非 cwd，导致 serve 进程永远读不到工作区 `.env`。
- `chat.md` 提示词：移除「不要执行 shell 命令」禁令，改为指引经
  `run_command` 在工作区内执行。
- `workspace_setup.smart_init_workspace_templates`：排除 `__pycache__`，
  不再把编译缓存 `.pyc` 复制进用户工作区。

### Tests
- 工具测试迁移 v2 调用（`execute(ctx=ToolContext(...), ...)`）：
  `test_tools_integration.py`（20）、`test_goal_e2e.py`（15）。
- `test_persona_threading.py` / `test_iteration_budget_wiring.py`：
  attempts 表补齐 `mode/model_override/thinking` 列、`_Body` 补齐
  `mode/model/thinking` 字段（web_session v5 迁移后同步）。
- `test_workspace_setup.py`：文件计数改为动态计算（模板目录实扫）。
- compact 测试同步新默认（`llm_summarize_ratio=0.80`）与 L4 触发条件
  （消息需超出 recent 保留预算、user/assistant 交替）。
- `test_common_prompts.py`：`study_collector`/`study_reviewer`/
  `workflow_orchestrator` 补 common 层引用。
- swarm worker / projector / run_backtest 测试同步 v2 行为
  （ctx 注入、compaction 标记保留原消息、结构化错误 envelope）。
- `auth.py`：注册端点恢复为可配置开关
  （`SR_ALLOW_REGISTRATION=1` 开启，默认仍禁用；E2E 测试启用）。
- `conftest_e2e.py`：前端构建检测 E2E hooks（`VITE_E2E=1`），缺失自动重建。
- `test_webui_e2e_playwright.py`：适配 Chat 路由 `/chat`（`/` 现为 Dashboard）、
  自动创建会话行为、发送按钮新选择器。

### Fixed（测试套件顺序污染——全套 391 失败归零）
- **浏览器测试默认跳过**（`SR_RUN_BROWSER_TESTS=1` 开启，e2e.yml 显式设置）：
  Playwright sync API 会在主线程保留运行中的 asyncio loop，导致后续所有
  async 测试报 ``Runner.run() cannot be called from a running event loop``
  （波及 300+ 测试）。
- **`sse_buffer.push`**：无当前 event loop 时（主线程被
  `asyncio.set_event_loop(None)` 分离）直接 `evt.set()`，不再因
  `get_event_loop()` 抛错而静默吞掉通知。
- **`test_event_log_schema`**：tearDown 还原 `SR_WORKSPACE_PATH`
  （泄漏后整套 session DB 被重定向到共享临时库 → 级联 `database is locked`）。
- **conftest 隔离**：每测试快照/还原 `SR_WORKSPACE_PATH`/`SR_SESSIONS_DB`/
  `HYPOTHESIS_USE_SQLITE`/`STATIC_DIR`；清理 SessionService 缓存 +
  MemoryManager 单例 + 主线程 SQLite 连接 + logging 级别 + asyncio loop 绑定。
- **`test_hypothesis_registry_sqlite` 后的日志污染**：`create_app()` 会把
  `strategy_research` 命名空间日志级别设为 INFO，过滤后续测试的 DEBUG
  断言（`assertLogs`）——conftest 测试后还原日志级别。
- **`test_chat_loop` 后的 AgentLoop mock 失效**：`chat_loop` 模块级绑定
  `AgentLoop`，patch `loop.AgentLoop` 对已 import 的 `chat_loop` 无效——
  `test_cli_llm_streaming` 改为 patch `chat_loop.AgentLoop`。
- **`test_cli_registry` 清空全局命令表**：`reset_registry()` 后未重注册核心
  命令，后续 `init` 等 CLI 分发全部失效——模块级 setup/teardown 重载
  core_commands。
- **`test_init_e2e`**：同时 patch `_auto_onboard.run_onboarding`（模块级
  绑定，patch `onboard.run_onboarding` 对其无效）。
- **`test_webui_visual`**：适配新版前端——聊天/DAG 路由（`/chat`、`/dag`）、
  整页截图替代失效的元素选择器、DAG 可视化类改为 skip（被
  `test_webui_catalog` 的 `catalog_dag-*` 覆盖）；视觉基线刷新。

## [0.6.0] - 2026-07-28

### Added
- **8 prompt templates** for workflow agents:
  `market_scanner`, `regime_classifier`, `report_writer`,
  `position_auditor`, `stress_tester`, `icarus_review`,
  `benchmark_compare`, `concentration_check`.
- `tests/test_goal_workflow_prompts.py` — 27 tests for prompt template
  loading, content validation, and PromptBuilder integration.

## [0.5.5] - 2026-07-28

### Added
- **ASCII DAG visual editor** (`dag_renderer.py`): renders workflow DAGs
  with status icons (✓ ⏳ ✗ ○), selection markers, and progress display.
- **`DAGView` TUI widget** (`dag_view.py`): Textual widget with j/k/h/l
  keyboard navigation, status updates, and close action.
- **`save_goal_workflow()`**: atomic YAML write with optional `.bak`
  backup and DAG validation.
- `tests/test_dag_renderer.py` — 22 tests for render_dag + save_goal_workflow.
- `tests/test_dag_view_widget.py` — 8 tests for DAGView rendering + navigation.

## [0.5.4] - 2026-07-28

### Added
- **4 new workflow presets**: `goal_market_analysis`, `goal_risk_assessment`,
  `goal_strategy_review`, `goal_portfolio_review`.
- **Cookbook** (`docs/archive/undated/goal-workflow-cookbook.md`): 5-minute tutorial for
  custom workflow creation.
- **Demo script** (`examples/goal_workflow_demo.py`): Python API usage example.
- `tests/test_goal_workflow_v054.py` — 43 tests for presets + cookbook + demo.

## [0.5.3] - 2026-07-28

### Fixed (P1 — 让自定义真正生效)
- **P1.1**: `_build_controller` now populates `AgentRegistry` from YAML agents.
- **P1.2**: `should_stop()` checks `runner._state.cancelled` so
  `pause(immediate=True)` actually interrupts DAG execution.
- **P1.3**: Checkpoint saves/restores real `layer_results` (not empty dict).
- **P1.4**: Expression DSL supports `and`/`or`/`not` boolean logic
  with correct precedence.
- **P1.5**: SwarmRuntime uses `PromptBuilder` for structured prompts.
- **P1.6**: Dead params (`agent_runner`, `runner_kwargs`) emit
  `DeprecationWarning` (to be removed in v0.6.0).
- **P1.7**: `workflow_id` persisted in goal row on `start()`.
- `tests/test_goal_workflow_v053.py` — 17 tests for all P1 fixes.

## [0.5.2] - 2026-07-28

### Added
- **`WorkflowWorker`**: async wrapper for `GoalWorkflowRunner.start()`
  with state transitions (idle → running → completed/failed).
- **Ctrl+G pause**: pressing Ctrl+G pauses active workflow before
  falling back to continuation toggle.
- **GoalPanel subscription**: `GoalPanelObserver` receives workflow events
  and updates the TUI goal panel in real-time.
- `tests/test_goal_workflow_v052.py` — 16 tests for worker + TUI integration.

## [0.5.1] - 2026-07-28

### Added
- **`/goal start <objective> --workflow <name>`**: loads YAML workflow preset.
- **`/goal workflows [list|show|path]`**: enumerate/display workflow presets.
- **`/goal checkpoint [save|list|resume|delete]`**: crash recovery for workflows.
- `tests/test_goal_workflow_v051.py` — 22 tests for CLI subcommands.

### Fixed (DAG framework)
- **Unified DAG convention** to `{node: [upstream_deps]}` across all modules.
- **`dag.py`**: `topological_layers` + `validate_dag` now use deps convention.
- **`dag_renderer.py`**: edge-drawing logic corrected for deps convention.
- **`SwarmRuntime._topological_layers` removed**: unified with `dag.py` version.
- **`find_downstream(adj, node)`**: BFS to find all nodes depending on a node.
- **`find_upstream(adj, node)`**: BFS to find all nodes a node depends on.
- **`ExpressionEvaluator`**: `not` operator now has correct precedence
  (higher than `and`/`or`).
- **`AgentLoop` tests**: fixed hang caused by `stream_mode=True` default
  without mocking `client.stream`.

## [0.5.0] - 2026-07-24

### Changed
- **`quantnodes-research init`** rewritten as a 5-step TTY credentials wizard
  (mirrors vibe-trading). Two init paths now share a single backend:
  - A. Explicit: `quantnodes-research init` → `cli.cmd_run_onboarding`
    (Rich prompt_toolkit wizard, `--force` flag)
  - B. Implicit: `quantnodes-research` bare TTY → `cli._auto_onboard.
    _maybe_run_onboarding` (auto-trigger when no `.env` candidate exists)
- Both paths write `~/.quantnodes/strategy_research/.env` with chmod 0600,
  atomic `.env.partial → os.replace`.

### Removed
- Workspace scaffold helpers from `cli/__init__.py`:
  `_load_template`, `_render_template`, `_create_strategy`,
  `_init_duckdb`, `_init_git`, `_run_baseline_backtest`.
  These are dead code as of v0.5.0 — `init` no longer creates config.yaml,
  .prompts/, .skills/, strategies/, DuckDB, or git repos.
- Test classes that tested the removed scaffold:
  `TestRenderTemplate`, `TestCmdInit`, `TestCmdInitNoBaseline`,
  `TestCmdInitConfigYAML`.

### Added
- `cli/_auto_onboard.py` — auto-trigger wizard on bare TTY launch.
- `tests/test_init_wizard.py` — 25 test cases covering wizard flow,
  migration, overwrite, auto-trigger, and cancel paths.
- `docs/archive/2026-11/research/v0.5.0-init-literature.md` — literature survey
  (Red Hat CLI UX guide, python-prompt_toolkit docs, dotenv README).
- `docs/archive/undated/PLAN-phase1-4.md §5` — init rewrite design doc.

### Fixed
- Legacy users with `~/.strategy-research/.env` are silently migrated to
  `~/.quantnodes/strategy_research/.env` on first wizard run (old file
  left intact).

## [0.4.0] - 2026-07-24

### Added (Textual TUI — full-screen multi-pane interface)
The ``quantnodes-research`` binary now launches a real Textual-based
full-screen terminal UI by default (TTY only). All 16 slash commands
plus the streaming LLM bridge live inside the same Textual app.

### Added (TUI startup capture tests — CI artifact upload)
``tests/test_cli_tui_startup_capture.py`` captures the TUI at 7 lifecycle
states (mount, tool event, LLM streaming, halt, full lifecycle, ASCII
fallback, responsive sizes) and saves SVG screenshots + transcript text
to ``tui-captures/``.  The GitHub Actions workflow uploads these as
build artifacts (30-day retention) for visual regression review.

### Added (Unicode ↔ ASCII fallback)
Components that emit a small set of Unicode glyphs (``●``, ``×``,
``…``, ``·``, ``→``) now auto-detect ASCII-only terminals and substitute
one-character ASCII lookalikes (``*``, ``x``, ``...``, ``-``, ``->``)
so non-UTF-8 environments (legacy ``vt100``, plain serial consoles,
``LANG=C`` locales) render readable output.

- **`cli.utils.ascii_compat`** — new module. ``is_ascii_mode()`` probes
  three signals in order: per-thread ``register_ascii_mode``
  override → ``STRATEGY_ASCII_MODE=1`` env → ``LANG/LC_ALL/LANGUAGE``
  starting with ``C`` or ``POSIX`` → ``sys.stdout.encoding``.
- `ELLIPSIS_*`, `MIDDOT_*`, `ARROW_*` symbol pairs exposed for direct
  lookup. ``status_marker(status)`` returns the matching glyph pair.
- ``ascii_fallback(text)`` substitutes Unicode glyphs in arbitrary
  strings (e.g. user-supplied log content).
- Consumers wired up:
  - ``cli.components.tool_event.render_tool_event`` picks the live
    marker glyph at call time so post-import mode changes are picked
    up; ``summarize_args`` uses the live ellipsis.
  - ``cli.components.hint_bar.render_hint_bar`` uses the live ellipsis
    when truncating the left side to fit width.

##- **`cli.tui.app.ResearchApp`** — top-level ``textual.App`` subclass
  composing Header + Horizontal(Sidebar / Transcript / Rail) +
  ChatInput + HintFooter. CSS at ``cli/tui/styles.tcss``.
- **`cli.tui.widgets`** — six thin Textual-native wrappers:
  - `Banner(Static)` — gradient logo Renderable at transcript top.
  - `TranscriptView(RichLog)` — scrolling chat log (write messages).
  - `ActivityRail(Log)` — right-panel ticker; ``write_event()`` formats
    via the existing `tool_event.beautify_tool_name` /
    `summarize_args` / `render_tool_event` helpers.
  - `CommandSidebar(ListView)` — clickable list of slash commands.
  - `ChatInput(Input)` — bottom prompt; submits via ``SynthesizeInput``.
  - `HintFooter(Footer)` — Textual standard footer with our brand.
- **`cli.tui.session.ChatSession`** — async turn dispatcher. Wraps
  ``cli.interactive.main.process_turn``; on ``rc == 2`` invokes
  ``app.exit()``; on ``ctx.pending_prompt`` (queued by ``/journal`` /
  ``/shadow``) re-dispatches automatically.
- **`cli.theme.captured_console(width=120)`** — contextvars-based
  context manager that installs a recording Console as the singleton
  override for one turn. ``ChatSession.dispatch`` uses it to capture
  handler output without disturbing the legacy REPL path.
- **`cli.llm_streaming.stream_chat_to_tui`** — async bridge that runs
  the configured ``OpenAICompatClient.stream`` from a worker thread
  (via ``asyncio.to_thread`` so the Textual event loop never blocks
  on the network), posts a "thinking" line + the final assistant
  reply to the TranscriptView, and appends the reply to ``ctx.history``
  for future-turn context.
- **`cli.tui.widgets.ResumeOrNewModal`** — Textual ``ModalScreen``
  mirroring the legacy ``(r)esume / (n)ew`` prompt. Pushes the most
  recent session title via the latest ``core/session/db.py``
  ``list_sessions(limit=1)``.
- **`cli/__main__.py`** — TTY-aware dispatcher:
  - TTY + bare argv → ``ResearchApp().run()`` (the TUI).
  - TTY + ``--repl`` / ``--banner`` → legacy prompt_toolkit REPL
    (escape hatch for terminals that don't support mouse / truecolor).
  - TTY + subcommand/``--help``/``--llm-list-profiles`` → argparse CLI.
  - Non-TTY (piped / CI) → argparse help (no hang).
  - All branches exit cleanly via ``SystemExit`` propagation; the
    ``sys.argv`` stub is restored in a ``finally``.
- New dependency in base ``dependencies``: ``textual>=0.50``.
- Test suite: 5 new files, +61 tests (``test_cli_tui_app``,
  ``test_cli_tui_session``, ``test_cli_tui_session_capture``,
  ``test_cli_captured_console``, ``test_cli_llm_streaming``) +
  rewrite of ``test_cli_entry_dispatch`` (29 tests, TTY-injection).
  All 25+ legacy handler tests in ``test_cli_chat_cmd``,
  ``test_cli_show_cmd``, ``test_cli_help``, ``test_cli_goal_cmd``,
  ``test_cli_memory_cmd``, ``test_cli_session_cmd`` continue to pass
  because the capture context is opt-in via the session dispatcher
  rather than baked into the handlers themselves.

### Added (Rich CLI / vibe-trading parity)
- **`cli.theme`** — Rich stylesheet + dark-mode detection (`is_dark()`, `force_dark()`).
- **`cli.utils.format`** — `format_duration(ms/s)`, `format_tokens(n)`, `abbreviate_num(n, currency=)`
  with banker's rounding + 3-decimal precision.
- **`cli.utils.thinking_verbs`** — verb pool + `pick_thinking_verb(seed=)` for seeded determinism.
- **`cli.components`** —
  - `WorkingIndicator` (`ThinkingSpinner`).
  - `tool_event.py` — `beautify_tool_name`, `summarize_args`, `render_tool_event(s)`.
  - `hint_bar.py` — left+right hint bar with overflow truncation.
  - `chat_log.py` — turn replay rendering.
- **`cli.ui`** —
  - `banner.py` — 8-line gradient logo + version line, `#258BFF→#A5CFFF` lerp.
  - `transcript.py` — markdown answer renderer (pipe-table upgrade, **bold**/*italic*/`code`/--- strip).
  - `rail.py` — `RailRunDashboard` event dispatcher (tool_call/text_delta/tool_progress/
    tool_heartbeat/tool_result/thinking_done/llm_usage/compact).
- **`cli.commands`** —
  - `slash_router.py` — 16-entry `SLASH_COMMANDS` registry, fuzzy matcher
    (prefix > substring > subsequence), `_ALIASES` (q/exit/:q → quit, ? → help),
    `match_commands` (bare `/` returns full list).
  - `help.py` — `render_help_table`: commands grid + shortcuts grid.
  - `show.py` — `/show`, `/pine`, `/skill` with `_locate_run`.
  - `slash_session.py` — `/history`, `/search`, `/export`.
  - `slash_memory.py` — `/memory list/show/search/forget` (refuses when `yes=False`).
  - `slash_goal.py` — `/goal status/start/evidence/complete/cancel/help`.
  - `slash_chat.py` — `/model`, `/clear(ctx)`, `/quit → 2`, `/debug(ctx)` toggle,
    `/journal`, `/shadow` (queues prompt on `ctx.pending_prompt`).
  - `slash_halt.py` — bare-word kill switch (`停/停手/stop/kill/halt`) + `/resume/continue/go`.
- **`cli.interactive`** —
  - `completer.py` — `SlashCompleter(max_suggestions=8)` for prompt_toolkit.
  - `main.py` — `InteractiveContext` dataclass, `_DISPATCH` table (16 handlers),
    `dispatch_slash()`, `process_turn()` (single-turn driver, halt/resume intercept,
    proposal pick intercept, plain text → `ctx.history`), `main(argv)` (`--banner` flag).
- **`cli.onboard`** — 5-step onboarding wizard (provider → model → key → timeout →
  optional tushare). `Provider` × 5, `BACK`/`CANCEL` sentinels, `run_onboarding(env_dir,
  inputs, skip_tushare)`, `is_onboarded(env_dir)`. Skips step 3 if `key_env is None`.
- **`cli.halt`** — thread-safe `HALT` sentinel + `trip_halt(reason=)`/`clear_halt()`/
  `is_halted()`/`require_not_halted(operation=)` + `HaltError`.
- **`cli.mandate`** — research-proposal intercept: `Proposal`, `make_proposal`,
  `is_pick(input, proposal)`, `capture_pick(input, proposal)` returning
  `{index, label, payload, context}` or `None`, `has_pending_proposal(ctx)`.
- **Entry dispatcher** — `cli/__main__.main()` picks Rich REPL when argv is empty or
  contains only `--banner`; otherwise delegates to argparse `cli.main()`. New
  console_script: `quantnodes-research` (v0.4.0).

### Fixed
- ``data_source_registry.get_loader_or_fallback``: unknown source names
  (e.g. ``data_fusion``) now raise ``DataSourceNotFoundError`` immediately
  instead of silently falling back to tushare.
- `cli.onboard.run_onboarding`: step 1 now consumes one input (not N), fixing the
  "provider loop reads too many lines" bug.
- `cli.slash_goal`: `append_evidence` switched from positional to keyword-only
  `session_id/goal_id/expected_goal_id/evidence` to match store API.
- TUI dispatch: ``app.post_message(WriteTranscript)`` does not reach
  nested widget children; the streaming bridge resolves the
  ``TranscriptView`` via ``app.query_one(...)`` and posts directly
  to the widget, mirroring the existing
  ``ResearchApp.write_transcript`` contract.
- ``cli.tui.widgets.rail.ActivityRail.write_event``: pass keyword args
  (``status=``, ``duration_ms=``, ``result_summary=``) matching the
  updated ``render_tool_event`` signature.

### Changed (code cleanup)
- Remove 9 unused imports (F401) across ``cli/`` modules.
- Remove 2 unused variables (F841): ``rc`` in ``action_show_help``,
  ``arg_sum`` in ``rail.py``.
- Extract 5 duplicate ``_reset_halt`` fixtures into ``tests/conftest.py``
  (autouse).
- Fix E501 long lines in ``banner.py`` and ``__init__.py``.
- Add ``tui-captures/`` to ``.gitignore``.

### Tests
- +629 new tests (`5683 → 6212`). All CLI modules now have dedicated
  suites including the full Textual TUI lifecycle
  (``run_test`` mount + handler dispatch + LLM stub integration),
  the Unicode ↔ ASCII auto-fallback layer (28 tests in
  ``test_cli_ascii_compat``), and 7 TUI startup capture tests
  (``test_cli_tui_startup_capture``) that save SVG artifacts for CI.
- **P3-B/C/D/E unit test coverage** — 117 new tests.
  - `tests/test_goal_p3b.py`: progress_percent, decompose_goal, sub/parent goals (13).
  - `tests/test_hypothesis_p3c.py`: VALID_TRANSITIONS, derive/link/contradicts (35).
  - `tests/test_hypothesis_store.py`: SQLite CRUD, FTS5 search, JSON migration (37).
  - `tests/test_hypothesis_validator.py`: validate_hypothesis auto-validation pipeline (20).
  - `tests/test_goal_hook_p3d.py`: _on_goal_complete hook, autoresearch CLI helpers (12).
- **HypothesisStore concurrency smoke tests** — 5 tests for parallel create/update/search.
  - `tests/test_hypothesis_store_concurrent.py`.
- **API router behavior tests** — 18 new tests in `tests/test_api.py`: goal
  list/evidence/complete, hypothesis create/list/search/update.

### Fixed
- ``data_source_registry.get_loader_or_fallback``: unknown source names
  (e.g. ``data_fusion``) now raise ``DataSourceNotFoundError`` immediately
  instead of silently falling back to tushare.
- `cli.onboard.run_onboarding`: step 1 now consumes one input (not N), fixing the
  "provider loop reads too many lines" bug.
- `cli.slash_goal`: `append_evidence` switched from positional to keyword-only
  `session_id/goal_id/expected_goal_id/evidence` to match store API.
- TUI dispatch: ``app.post_message(WriteTranscript)`` does not reach
  nested widget children; the streaming bridge resolves the
  ``TranscriptView`` via ``app.query_one(...)`` and posts directly
  to the widget, mirroring the existing
  ``ResearchApp.write_transcript`` contract.
- ``cli.tui.widgets.rail.ActivityRail.write_event``: pass keyword args
  (``status=``, ``duration_ms=``, ``result_summary=``) matching the
  updated ``render_tool_event`` signature.
- **HypothesisStore concurrency safety**: ``create()`` and ``update()``
  now hold ``self._lock`` across the entire method body (SELECT + write).
  Previously, releasing the lock between SELECT and BEGIN IMMEDIATE
  caused ``OperationalError`` under parallel writes.
- **API router error codes**:
  - ``hypothesis_update``: returns 404 for missing ID (was 500).
  - ``goal_complete``: returns 409 for stale goal (was 500), 400 for
    invalid state (was 500).
- **API router alignment with P3 stores**:
  - ``goal_list`` actually calls ``store.list_goals()`` (was hardcoded ``[]``).
  - ``goal_evidence`` uses ``EvidenceInput`` + ``append_evidence()`` (was
    deprecated ``add_evidence``).
  - ``goal_complete`` uses ``update_status()`` (was deprecated
    ``transition_status``).
  - ``hypothesis_create`` accepts ``universe``/``signal_definition`` (was
    legacy ``tags``/``metadata``).
  - Hypothesis serialization uses ``to_dict()`` (was ``__dict__``, which
    broke datetime JSON serialization).

### Changed
- ``GoalStore._on_goal_complete``: added missing ``logger`` import.
- ``HypothesisStore._migrate_from_json``: JSON fallback path now derives
  from ``db_path.parent`` (was hardcoded ``~/.quantnodes-research/``),
  enabling test isolation via ``tmp_path``.
- Remove 9 unused imports (F401) across ``cli/`` modules.
- Remove 2 unused variables (F841): ``rc`` in ``action_show_help``,
  ``arg_sum`` in ``rail.py``.
- Extract 5 duplicate ``_reset_halt`` fixtures into ``tests/conftest.py``
  (autouse).
- Fix E501 long lines in ``banner.py`` and ``__init__.py``.
- Add ``tui-captures/`` to ``.gitignore``.

### Tests
- +629 new tests (`5683 → 6212`). All CLI modules now have dedicated
  suites including the full Textual TUI lifecycle
  (``run_test`` mount + handler dispatch + LLM stub integration),
  the Unicode ↔ ASCII auto-fallback layer (28 tests in
  ``test_cli_ascii_compat``), and 7 TUI startup capture tests
  (``test_cli_tui_startup_capture``) that save SVG artifacts for CI.
- P3-B/C/D/E unit test coverage: goal progress/decompose, hypothesis
  transitions/derive/link, SQLite CRUD/FTS5, auto-validation pipeline,
  goal hook, concurrency smoke, API router behavior (140 new tests).

## [0.3.0] - 2026-07-22

### Added

#### P3-a: Goal subsystem (research-only ledger)
- `core/goal/`: GoalStatus (12 lifecycle values), RiskTier (4 values
  including LIVE_TRADING_OR_EXECUTION which is rejected at create),
  GoalRecord + GoalClaim + GoalCriterion + EvidenceInput/Record + AuditRow
  + StaleGoalError — all frozen dataclasses.
- `core/goal/policy.py`: normalize_required_text + reject_live_execution_objective
  (rejects English + Chinese live-trading phrases).
- `core/goal/store.py`: SQLite-backed GoalStore at
  `~/.quantnodes-research/goals.db` with WAL + busy_timeout + foreign keys.
- `core/goal/context.py`: format_goal_context + format_goal_continuation_prompt.
- `core/goal/cli.py`: 7 subcommands (start / status / evidence / audit /
  complete / list / cancel).
- `quantnodes-research goal ...` CLI surface.

#### P3-b: Hypothesis subsystem
- `core/hypothesis/registry.py`: File-backed JSON registry at
  `~/.quantnodes-research/hypotheses.json` (atomic write, malformed
  raises ValueError).
- `core/hypothesis/auto_create.py`: HypothesisAutoCreator — idempotent
  helper that creates an `exploring` hypothesis on first AgentLoop call
  per (strategy, market).
- 5 statuses: exploring / testing / validated / rejected / monitoring.
- SHA-256-derived `hyp_<12hex>` IDs with collision suffix.
- Token-overlap search across all serialized fields + recency tiebreak.
- `core/hypothesis/cli.py`: 6 subcommands (create / list / show / update
  / search / link).
- `quantnodes-research hypothesis ...` CLI surface.

#### P3-c: Validation toolkit
- `core/validation/market.py`: MarketType enum (7 values) +
  bars_per_year + warn_if_unsupported_market (UserWarning for
  non-supported markets).
- `core/validation/trade_input.py`: Lightweight TradeInput dataclass.
- `core/validation/monte_carlo.py`: Monte Carlo permutation test.
- `core/validation/bootstrap.py`: Bootstrap Sharpe CI.
- `core/validation/walk_forward.py`: Walk-Forward analysis.
- `core/validation/runner.py`: Orchestrator with multi-market support.
- `core/validation/utils.py`: `_json_safe` (NaN/inf → None), `_sharpe`.
- `core/validation/cli.py`: `validate-run <run_dir>` command.
- `quantnodes-research validate-run ...` CLI surface.

#### P3-d: Integration
- AgentLoop gained `session_id`, `strategy_name`,
  `enable_goal_injection`, `enable_hypothesis_auto_create` parameters
  (all optional, backward-compatible).
- AgentLoop.run() now auto-creates an exploring hypothesis on first
  call per (strategy, market) and injects the current
  `<current-research-goal>` block into the user message.
- Workflow layer integration pending (Week 14).

#### Documentation
- `docs/archive/undated/goal-design.md` — comprehensive design doc for the Goal
  subsystem (10 sections, including data model + CLI + limitations).
- `docs/archive/undated/validation-design.md` — multi-market roadmap + current
  support matrix.

### Changed
- AgentLoop __init__ signature now includes 4 new optional params.
- CLI surface gained 14 new subcommands (7 goal + 6 hypothesis + 1 validate-run).

### Tests
- Total tests: 3,770 → 4,053 (+283 new tests).
- New test files: test_goal_* (6), test_hypothesis*.py (2),
  test_validation_*.py (6), test_p3_*.py (3).

## [0.2.0] - 2026-07-22

### Added
- Hook system (P2-a): UnifiedHook (16 events) + AgentHook (13 events).
- Memory FTS5 (P2-c): global index + recency boost + write dedup.
- Session management (P2-d): SQLite + FTS5 + triggers + rate limiter.
- CLI integration (P2-e): session stats / list subcommands.

## [0.1.0] - 2026-07-22

### Added
- Initial release: workspace init, baseline backtest, AgentLoop, 6 tools.

[0.4.0]: https://github.com/sn0wfree/quantnodes-strategy-research/releases/tag/v0.4.0
[0.3.0]: https://github.com/sn0wfree/quantnodes-strategy-research/releases/tag/v0.3.0
[0.2.0]: https://github.com/sn0wfree/quantnodes-strategy-research/releases/tag/v0.2.0
[0.1.0]: https://github.com/sn0wfree/quantnodes-strategy-research/releases/tag/v0.1.0