# Architecture Review & Refactoring Roadmap

> ⚠ **历史文档**：早期架构评审（332 行）。结论已被 v6.2 取代。

> **Status:** Draft (Phase 0, 2026-08-01)
> **Author:** Architecture review
> **Scope:** `quantnodes-strategy-research` v0.6.0

## Executive Summary

`quantnodes-strategy-research` is a **mid-sized Python framework** (~87k LoC, 755 files) for
quantitative strategy research, built around a multi-agent autoresearch loop. The codebase
shows **strong test coverage** (~6,071 tests, ~81k LoC test code, ~0.93:1 ratio) and **mature
adoption of design patterns** (Adapter, Registry, Strategy, Composite, Observer).

The principal areas for improvement are:
1. **Code duplication** that can be collapsed by ~800-1000 LoC through pattern application
2. **Architectural consistency** (splitting oversized CLI / API entry points)
3. **Test infrastructure** (centralize scattered fixtures)
4. **Type / logging / error hygiene** (Phase 4)

This document captures the findings, the proposed phased refactoring plan, and the success
criteria for each phase.

---

## 1. Project Snapshot

| Metric | Value |
| --- | --- |
| Source code (Python) | 755 files, ~87,000 LoC |
| Test code | 309 files, ~81,000 LoC, **6,071 test functions** |
| Test : source ratio | **~0.93 : 1** |
| Major layers | `core` / `cli` / `api` / `webui` |
| Version | 0.6.0 (Alpha) |
| Recent history | Event-sourced migration Level 3 (B-series, ~50 commits) |

---

## 2. Design-Pattern Inventory

### 2.1 Patterns Applied Correctly (✅ Keep)

| Pattern | Location | Notes |
| --- | --- | --- |
| **Adapter** | `core/llm/provider/` (5 providers + fallback) | Adding a new provider requires no core-file changes |
| **Registry** | `core/llm/provider/__init__.py`, `core/data_source/registry.py` | Lazy import + descriptor decorator |
| **Strategy** | `core/utils/strategy_engine.py:BaseStrategy` | Clean override hooks (`compute_weights`, `on_risk_check`) |
| **Composite** | `core/hooks/composite.py:CompositeHook` | Per-hook error isolation |
| **Observer** | `core/goal/event_bus.py:WorkflowEventObserver` | Multiple implementations (Logger, Collecting, GoalPanel) |
| **Factory** | `core/llm/provider/__init__.py:get_provider`, `core/goal/completion_strategy.py:CompletionStrategyFactory` | |
| **Chain of Responsibility** | `core/data_source/registry.py:FALLBACK_CHAINS` | Per-market fallback chains |
| **Protocol** | `core/data_source/base.py:DataLoader` | `@runtime_checkable` Protocol |

### 2.2 Patterns With Implementation Debt (⚠️ Improve)

| Pattern | Issue | Affected Files |
| --- | --- | --- |
| **Adapter** | `deepseek.py` and `qwen.py` are ~95% identical | `core/llm/provider/{deepseek,qwen}.py` |
| **Adapter** | `name` is `@property` everywhere; should be class constant | All 6 providers |
| **Template Method / Config Loader** | 3 independent 4-layer config loaders | `llm/config.py`, `strategy_acceptance/__init__.py`, `goal/workflow_config.py` |
| **Registry** | 2 alpha-zoo loader implementations diverge | `alpha_zoo/__init__.py` + `alpha_zoo_adapter.py` |
| **Command** | CLI entry mixes subcommand parsing + handler logic (1073 LoC) | `cli/__init__.py` |
| **Command** | Slash commands duplicate handler / router functions | `cli/commands/slash_*.py` |

---

## 3. Quantitative Findings

### 3.1 Duplicated Logic

| Location | LOC | Duplication |
| --- | --- | --- |
| `core/llm/provider/deepseek.py` vs `qwen.py` | 73 | **5-line diff** (name / base_url / model) |
| `alpha_zoo/__init__.py:compute_alpha` vs `alpha_zoo_adapter.py:compute_as_wide` | ~150 | 3× repeated `inf ≤ 30% / NaN ≤ 98% / shape` checks |
| 3× 4-layer config loaders | ~50 | Repeated merge loop |
| `cli/__init__.py` | 1073 | 68 `cmd_*` / `add_parser` patterns; should be split |
| `api/routers/chat.py` + `api/routers/web_session.py` | 2064 | Overlapping SessionService / EventBusV2 wiring |

### 3.2 Test-Footprint Density

| Module | LOC | Test LOC | Tests |
| --- | --- | --- | --- |
| `core/llm/provider/` | 588 | 626 + 88 | Adapter tests are denser than the impl |
| `core/llm/openai_client.py` | 626 | 295 | Strong coverage of edge cases |
| `core/strategy_acceptance/` | ~600 | 24 | Sparser — opportunity |
| `alpha_zoo/` | 25,338 | — | Many zoo files have zero direct tests; covered via integration |

---

## 4. Technical Debt Scorecard

| Dimension | Score | Rationale |
| --- | --- | --- |
| **Maintainability** | 7 / 10 | Strong tests, but CLI/API entry files oversized |
| **Reusability** | 6 / 10 | Patterns used, but 3-4 obvious duplications |
| **Testability** | 9 / 10 | 6,071 tests, clear injection points, deferred imports |
| **Extensibility** | 8 / 10 | Adapter / Registry / Strategy give clean extension points |
| **Performance** | 7 / 10 | Lazy loading used; alpha_zoo import still heavy |
| **Type Safety** | 7 / 10 | Heavy dataclass + Protocol + slots use; `Any` still common |
| **Documentation** | 8 / 10 | CHANGELOG detailed, docstrings consistent |
| **Overall** | **7.4 / 10** | Solid skeleton, room for convergence |

---

## 5. Refactoring Roadmap

### Phase 1 — Eliminate Duplication (3 days)

#### Phase 1.1: Provider Adapter Consolidation
**Goal:** Reduce `core/llm/provider/` from 6 files / 588 LoC to ~5 files / ~400 LoC.

1. Introduce `core/llm/provider/_reasoning_field.py`:
   - `OpenAIReasoningFieldAdapter(ProviderAdapter)` base for `reasoning_content`-based providers
2. Refactor `deepseek.py` and `qwen.py` to inherit from it
3. Promote `name` from `@property` to `ClassVar[str]` in **all** adapters
4. Ensure `test_provider_adapter.py` (626 tests) + `test_minimax_adapter.py` (88 tests) still pass

#### Phase 1.2: Alpha Zoo Loader Unification
**Goal:** Collapse `alpha_zoo/__init__.py:compute_alpha` (217) + `alpha_zoo_adapter.py:compute_as_wide` (233) → single `alpha_zoo/loader.py` (~250).

1. Extract `_validate_result(result, alpha_id, expected_shape, source)` helper
2. Merge YAML→py fallback into one flow
3. Make `AlphaZooAdapter` a thin wrapper around the loader
4. Update callers (~5-8 import sites)

#### Phase 1.3: Test Fixture Infrastructure
**Goal:** Grow `tests/_fixtures/` from 1 file to ~6 files.

New files:
- `tests/_fixtures/market.py` — `make_ohlcv_panel()`, `make_random_panel(seed)`
- `tests/_fixtures/alpha.py` — `make_panel_for_alpha()`, `make_alpha_result()`
- `tests/_fixtures/session.py` — `make_test_session()`, `make_session_dir()`
- `tests/_fixtures/llm.py` — `MockLLMClient`, `make_mock_llm_response()`
- `tests/_fixtures/cli.py` — `make_argv()`
- `tests/_fixtures/asyncio.py` — async test helpers

Refactor ~50 inline `np.random.seed(42) + pd.Series(...).cumsum()` patterns.

---

### Phase 2 — Architectural Upgrades (3 days)

#### Phase 2.1: Unified Layered Config Loader
**Goal:** Replace 3 independent 4-layer config loaders with one shared utility.

1. New `core/config_loader.py`:
   ```python
   def load_layered_config(
       *,
       cli_overrides: Mapping[str, Any] | None = None,
       workspace_path: Path | None = None,
       user_path: Path | None = None,
       defaults: dict[str, Any],
       allowed_keys: set[str] | None = None,
   ) -> dict[str, Any]:
       """4-layer merge: cli > workspace > user > defaults."""
   ```
2. Refactor `LLMConfig.load` (keep dotenv + bridge logic, delegate YAML merge)
3. Refactor `strategy_acceptance.load_config`
4. `goal/workflow_config.load_goal_workflow` keeps its path resolution; delegates YAML parsing

#### Phase 2.2: CLI Subcommand Registration
**Goal:** Reduce `cli/__init__.py` from 1073 to ~200 LoC.

Each command module exports `register(subparsers) -> None`:
```python
# cli/commands/autoresearch.py
def register(subparsers) -> None:
    p = subparsers.add_parser("autoresearch", help="...")
    p.add_argument("workspace")
    p.set_defaults(handler=cmd_autoresearch)

def cmd_autoresearch(args) -> int: ...

# cli/__init__.py
def build_parser():
    parser = argparse.ArgumentParser(prog="quantnodes-research")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for module in ALL_COMMANDS:
        module.register(sub)
    return parser
```

19 subcommands × `register()` functions.

#### Phase 2.3: Slash Command Decorator (New Pattern)
```python
@slash_command("/model", description="Show current LLM provider/model")
def cmd_model(ctx) -> int:
    ...
```

Eliminates 6 `cmd_*` / `run_*` mirrors in `slash_chat.py`.

#### Phase 2.4: Builder Pattern for Complex Configs
Applied to `LLMConfig.load()` + `AcceptanceConfig`:
```python
cfg = (ConfigBuilder(defaults=LLM_DEFAULTS)
       .with_env(prefix="OPENAI_")
       .with_yaml_file(path)
       .with_cli_overrides(args)
       .build())
```

---

### Phase 3 — API & DI (3 days)

#### Phase 3.1: API Router Service Sharing
1. New `api/dependencies.py` with FastAPI `Depends` providers
2. `api/app.py` registers singletons in `app.state`
3. `chat.py` and `web_session.py` consume via `Depends`

#### Phase 3.2: Lightweight DI Container
New `api/container.py`:
```python
@dataclass
class AppContainer:
    session_store: SessionStore
    event_bus: EventBusV2
    projector: Projector
    session_service: SessionService

def build_container(settings) -> AppContainer: ...
```

Replaces implicit globals in `service.py`.

#### Phase 3.3: Unified Error Hierarchy
New `core/errors.py`:
```python
class StrategyResearchError(Exception): pass
class ConfigError(StrategyResearchError): pass
class ProviderError(StrategyResearchError): pass
class SessionError(StrategyResearchError): pass
```

---

### Phase 4 — Code Quality (mandatory, included in this refactor)

#### Phase 4.1: Type Hint Unification
- `Dict[str, Any]` → `dict[str, Any]`
- `Optional[T]` → `T | None`
- Remove unconstrained `Any` (replace with `Protocol` where ~50 sites)

#### Phase 4.2: Centralised Logging
- New `core/logging_config.py`
- Replace ~30 `print()` calls with `logger`
- Uniform format

#### Phase 4.3: Documentation Sync
- Update `docs/CHANGELOG.md` with `BREAKING CHANGE` markers
- Add "Why this pattern?" paragraph to each module docstring
- Reference this roadmap from `README.md`

---

## 6. Success Criteria (per Phase)

| Criterion | Target |
| --- | --- |
| Test pass rate | 100% (existing 541+ tests + new tests) |
| Source LoC reduction | ≥ 5% overall (target 87,000 → ~82,500) |
| Ruff lint | 0 errors |
| New tests per Phase | ≥ 20 |
| CHANGELOG | All changes recorded |

---

## 7. Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| Breaking public API | Per-Phase git branch; explicit `BREAKING CHANGE` in CHANGELOG |
| Test regressions | Run full suite per Phase (541+ tests) |
| Provider edge cases | Existing `test_provider_adapter.py` covers 5 providers × 626 assertions |
| Long refactor | Each Phase → 1 PR; easy to revert |
| CLI behaviour drift | Existing 19 tests + manual smoke test |

---

## 8. Timeline

| Week | Phases |
| --- | --- |
| 1 | Phase 1.1 + 1.2 + 1.3 |
| 2 | Phase 2.1 + 2.2 + 2.3 |
| 3 | Phase 3.1 + 3.2 + 3.3 |
| 4 | Phase 4.1 + 4.2 + 4.3 |

---

## 9. Decision Log

- **2026-08-01:** Roadmap approved. Phase 1 to begin immediately. Each Phase = 1 git branch.
- **2026-08-01:** Breaking changes accepted (`CHANGELOG` will note `BREAKING CHANGE`).
- **2026-08-01:** New design patterns (Builder, Decorator) approved.

## 10. Progress

| Phase | Status | Branch | Notes |
| --- | --- | --- | --- |
| Phase 0 (doc) | ✅ | main | `docs/architecture-review.md` (297 LoC) |
| Phase 1.1 | ✅ | refactor/phase-1-duplication | Provider Adapter consolidation (`OpenAIReasoningFieldAdapter`) |
| Phase 1.2 | ✅ | refactor/phase-1-duplication | Alpha Zoo loader unification (`AlphaLoader`) |
| Phase 1.3 | ✅ | refactor/phase-1-duplication | Test fixtures infrastructure (6 modules, 35 tests) |
| Phase 2.1 | ✅ | refactor/phase-2-architecture | Unified 4-layer config loader (`load_layered_config`, `ConfigBuilder`) |
| Phase 2.2 | ✅ | refactor/phase-2-architecture | CLI subcommand registration (`@cli_command` decorator) |
| Phase 2.3 | ✅ | refactor/phase-2-architecture | Slash command decorator (`@slash_command`) |
| Phase 2.4 | ✅ | refactor/phase-2-architecture | LLMConfigBuilder for fluent 4-layer composition |
| Phase 3.1 | ✅ | refactor/phase-3-api-di | FastAPI Depends providers (`api/dependencies.py`) |
| Phase 3.2 | ✅ | refactor/phase-3-api-di | Lightweight DI container (`AppContainer`) |
| Phase 3.3 | ✅ | refactor/phase-3-api-di | Unified error hierarchy (`StrategyResearchError`) |

### Phase 3 Outcome

| Phase | Files | Insertions | Deletions | New tests |
| --- | --- | --- | --- | --- |
| 3.1 | 2 | +361 | -0 | +10 |
| 3.2 | 2 | +410 | -0 | +11 |
| 3.3 | 3 | +385 | -2 | +15 |
| **Total** | **7** | **+1156** | **-2** | **+36** |

### Phase 2 Outcome

| Phase | Files | Insertions | Deletions | New tests |
| --- | --- | --- | --- | --- |
| 2.1 | 3 | +453 | -36 | +17 |
| 2.2 | 4 | +645 | -31 | +12 |
| 2.3 | 2 | +229 | -0 | +8 |
| 2.4 | 2 | +393 | -0 | +15 |
| **Total** | **11** | **+1720** | **-67** | **+52** |