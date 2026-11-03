"""LLMConfig — immutable LLM configuration with 4-layer merge.

Layers (high priority overrides low):
    1. CLI overrides      (argparse namespace dict; ``llm_*`` keys)
    2. Environment vars   (OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL /
                           LLM_API_KEY for credentials)
    3. Bridge layer       (``~/.quantnodes/llm.json`` via quantnodes_bridge,
                           which internally reads the canonical QuantNodes
                           config and also applies QUANTNODES__LLM__* env
                           overrides). Translator turns the bridge output
                           into the keys this dataclass uses.
    4. Code defaults      (dataclass field defaults)

Bridge layer sources of truth (lazy-read by the QuantNodes reader):
    - ``~/.quantnodes/llm.json`` (canonical; honoured via
      ``STRATEGY_RESEARCH_LLM_CONFIG`` override for tests).
    - ``QUANTNODES__LLM__*`` env vars (applied inside the bridge).
    - Wizard writes the same JSON file at "~/.quantnodes/llm.json" via
      ``cli/onboard.py`` after init.

Design notes:
    - frozen=True: every override returns a NEW instance (no mutation).
    - api_key resolution priority: OPENAI_API_KEY > LLM_API_KEY
      > bridge-resolved env:VAR.
    - provider→base_url / provider→model fallbacks are applied after the
      bridge load (the canonical JSON may omit base_url).
    - .env loading is best-effort (python-dotenv is optional).
"""

from __future__ import annotations

import dataclasses
import logging
import os
from pathlib import Path
from typing import Any, Mapping

from strategy_research.foundation.compact_config import CompactConfig
from .quantnodes_bridge import (
    CONFIG_PATH as QUANTNODES_LLM_JSON,
)
from .quantnodes_bridge import (
    load_quantnodes_llm_config,
)

logger = logging.getLogger(__name__)

# ── Public constants ────────────────────────────────────────────────

# Legacy alias kept for compatibility: tests / external CLI callers that
# still set STRATEGY_RESEARCH_LLM_CONFIG to point at the LLM config.
DEFAULT_LLM_CONFIG_PATH = QUANTNODES_LLM_JSON

# Env var names (only these are read from environment for direct overrides)
ENV_API_KEY = "OPENAI_API_KEY"
ENV_BASE_URL = "OPENAI_BASE_URL"
ENV_MODEL = "OPENAI_MODEL"
ENV_CONFIG_PATH = "STRATEGY_RESEARCH_LLM_CONFIG"
# Selects an active provider profile from llm.json["llm"]["profiles"].
# Beats the file's "active_profile" field; CLI --llm-profile beats both.
ENV_PROFILE_ACTIVE = "LLM_PROFILE"

# Wizard writes this into ~/.quantnodes/.env; we accept
# it as a credential source so users with the new wizard don't need to
# also export OPENAI_API_KEY (which would defeat the point of putting
# the secret in the local .env). Precedence still prefers OPENAI_API_KEY.
ENV_LLM_API_KEY = "LLM_API_KEY"

# ── Back-compat aliases (legacy yaml/profile knobs kept for tests + 3rd-party) ──
# The old yaml/profile system was retired in favor of the bridge to
# ~/.quantnodes/llm.json. These names are preserved so existing callers
# (and tests) don't have to change simultaneously with the migration.
ENV_PROFILE: str = "STRATEGY_RESEARCH_LLM_PROFILE"     # no longer read

# Provider defaults are now sourced from the provider adapter registry
# (provider/*.py). This dict is kept for backward compatibility — it is
# dynamically rebuilt from the adapter registry on import.
def _build_provider_defaults() -> dict[str, dict[str, Any]]:
    """Build PROVIDER_DEFAULTS from the provider adapter registry.

    Adding a new provider = new file in provider/ + register in __init__.py.
    This dict auto-updates.
    """
    from .provider import _REGISTRY, get_provider_defaults  # noqa: PLC2701
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(_REGISTRY):
        out[name] = get_provider_defaults(name)
    return out


PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = _build_provider_defaults()

# Global conservative fallback when neither user config nor provider
# recommendation supplies ``max_tokens``.  Safe for most chat workloads
# without being so low that long answers get truncated.
_DEFAULT_MAX_TOKENS = 8192

# Floor for max_tokens when a caller asks for something absurdly small.
# Generic non-thinking adapters may go lower via ``ProviderAdapter.min_output_tokens``.
_MIN_OUTPUT_TOKENS = 256

# Thinking providers override ``min_output_tokens`` to at least this.
_THINKING_MIN_OUTPUT_TOKENS = 4096


# ── LLMConfig dataclass ─────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class LLMConfig:
    """Immutable LLM configuration.

    Construct directly, or use LLMConfig.load() to apply 4-layer merge.
    Use .with_config(**kwargs) to derive a tweaked instance.
    """

    # ── Endpoint ─────────────────────────────────
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    provider: str = "auto"                         # auto|openai|deepseek|kimi|qwen|minimax|nvidia|custom

    # ── Sampling ────────────────────────────────
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int | None = None
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    stop: tuple[str, ...] | None = None
    seed: int | None = None

    # ── Network ──────────────────────────────────
    timeout_s: float = 60.0
    # Wall-clock ceiling for a single LLM request (streaming included).
    # Guards against "slow-trickle" streams that keep httpx's per-read
    # timeout from ever firing while producing no finish_reason. 0/None
    # disables. Configurable via env SR_AGENT_WALLCLOCK_TIMEOUT (seconds,
    # default 1800 = 30min).
    wallclock_timeout_s: float = 1800.0
    max_retries: int = 3                            # total attempts (default 3)
    retry_backoff_s: float = 1.0
    proxy: str | None = None

    # ── Behaviour ───────────────────────────────
    stream: bool = True
    parallel_tool_calls: bool = True
    tool_choice: str = "auto"                      # auto|required|none|{"name":..}

    # ── Iteration budget ─────────────────────────
    # AgentLoop iteration caps. Chat mode is bounded (default 50) so a
    # failing tool loop cannot grow the prompt unboundedly; agent/goal
    # workflows get a much higher ceiling. Read from llm.json; falls back
    # to these defaults when absent.
    max_iterations: int = 50                        # chat / send_message
    agent_max_iterations: int = 9999                # agent / goal workflows

    # ── Thinking ─────────────────────────────────
    enable_thinking: bool = True                   # emit thinking tokens (when provider supports them)

    # ── Model metadata overrides ─────────────────
    # When set, these values override whatever the ModelCatalog would
    # otherwise derive (from models.dev fetch or bundled fallback). When
    # None, the catalog resolution path is used as-is.
    model_context_tokens: int | None = None        # e.g. 2000000
    model_max_output_tokens: int | None = None     # e.g. 32000
    model_supports_vision: bool | None = None
    model_supports_reasoning: bool | None = None

    # ── Compaction ───────────────────────────────
    compact_config: CompactConfig | None = None

    # ── Methods ──────────────────────────────────

    def with_config(self, **kwargs: Any) -> "LLMConfig":
        """Return a new LLMConfig with the given fields overridden."""
        return dataclasses.replace(self, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict representation (api_key included for debug)."""
        return dataclasses.asdict(self)

    def masked_dict(self) -> dict[str, Any]:
        """Return a dict with api_key masked (for logging)."""
        d = self.to_dict()
        if d.get("api_key"):
            key = d["api_key"]
            if len(key) > 8:
                d["api_key"] = key[:4] + "***" + key[-4:]
            else:
                d["api_key"] = "***"
        return d

    # ── Factory ──────────────────────────────────

    @classmethod
    def load(
        cls,
        *,
        cli_overrides: Mapping[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
        yaml_path: Path | None = None,
        load_dotenv: bool = True,
    ) -> "LLMConfig":
        """Construct an LLMConfig by merging 4 layers.

        Args:
            cli_overrides: argparse-namespace dict (e.g. vars(args)); only
                           truthy keys applied.
            env:           Custom env mapping (defaults to os.environ).
            yaml_path:     Legacy kwarg, kept for compatibility. When given,
                           it overrides the bridge path (otherwise defaults
                           to ``$STRATEGY_RESEARCH_LLM_CONFIG`` → ``~/.quantnodes/llm.json``).
            load_dotenv:   Whether to call dotenv.load_dotenv() (no-op if
                           not installed).

        Returns:
            Fully merged LLMConfig instance.

        Raises:
            OSError: If the bridge path exists but is unreadable and the
                     underlying error is not JSON/OS decode related (the
                     reader swallows JSON/OS errors silently by design).
        """
        env_map = dict(env if env is not None else os.environ)

        if load_dotenv:
            _try_load_dotenv()

        # 1) Code defaults
        cfg = cls()

        # 2) Bridge layer (~/.quantnodes/llm.json)
        eff_bridge_path = (
            Path(yaml_path).expanduser()
            if yaml_path is not None
            else _resolve_bridge_path(env_map)
        )
        bridge_data = _load_bridge_dict(eff_bridge_path)
        if bridge_data:
            cfg = cfg._merge_flat(bridge_data)

        # 3) Env var layer (only the documented env vars)
        env_overrides = _env_to_overrides(env_map)
        if env_overrides:
            cfg = cfg._merge_flat(env_overrides)

        # 4) CLI override layer (only truthy keys; values pass through as-is)
        if cli_overrides:
            cli_flat = _cli_to_overrides(cli_overrides)
            if cli_flat:
                cfg = cfg._merge_flat(cli_flat)

        # api_key handled separately: prefer direct OPENAI_API_KEY, fall
        # back to LLM_API_KEY (QuantNodes convention / wizard output),
        # then to whatever the bridge resolved. Never from yaml.
        if not cfg.api_key:
            cfg = cfg.with_config(api_key=load_api_key_from_env(env_map))

        # Auto max_tokens when no layer supplied it (model-aware).
        if cfg.max_tokens is None:
            cfg = cfg.with_config(max_tokens=resolve_max_tokens(cfg))

        return cfg

    # ── Internal helpers ─────────────────────────

    def _merge_flat(self, data: Mapping[str, Any]) -> "LLMConfig":
        """Return new LLMConfig with dataclass fields updated from data.

        Unknown keys are silently ignored (forward-compat with new fields).
        Tuple fields (stop) are reconstructed as tuple.
        Int fields guard against non-int values (e.g. a mistyped
        max_iterations in llm.json) by falling back to the current value.
        """
        valid_fields = {f.name for f in dataclasses.fields(self)}
        field_types = {f.name: f.type for f in dataclasses.fields(self)}
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key in valid_fields and value is not None:
                if key == "stop" and isinstance(value, list):
                    kwargs[key] = tuple(value)
                elif field_types.get(key) == "int" and not isinstance(value, int):
                    # Keep the current (default) value rather than injecting
                    # a non-int that would break int-typed consumers.
                    logger.debug(
                        "llm.json %s=%r not an int; keeping default", key, value,
                    )
                else:
                    kwargs[key] = value
        if not kwargs:
            return self
        return dataclasses.replace(self, **kwargs)


# ── Helpers ─────────────────────────────────────────────────────────


def _resolve_bridge_path(env: Mapping[str, str]) -> Path:
    """Resolve the bridge (llm.json) path.

    Precedence:
      1. ``STRATEGY_RESEARCH_LLM_CONFIG`` env var (legacy override kept for tests/tooling).
      2. ``~/.quantnodes/llm.json`` (canonical QuantNodes location).
    """
    p = env.get(ENV_CONFIG_PATH)
    if p:
        return Path(p).expanduser()
    return QUANTNODES_LLM_JSON


def _try_load_dotenv() -> None:
    """Best-effort .env load; no-op if python-dotenv is not installed.

    Loads ``.env`` from the current directory first, then the canonical
    secrets file ``~/.quantnodes/.env`` (wizard-written API keys) when it
    exists. Explicit process env vars always win (override=False).
    """
    try:
        from dotenv import load_dotenv as _ld
    except ImportError:
        logger.debug("python-dotenv not installed; skipping .env load")
        return
    try:
        _ld()  # find_dotenv from the caller frame (best-effort)
        # Explicitly load cwd's .env: bare load_dotenv() searches from the
        # library file's location, not the process cwd, so a workspace
        # .env (e.g. qn-research/.env with SR_ALLOW_SHELL_TOOLS) would
        # otherwise never be picked up in the serve process.
        cwd_env = Path.cwd() / ".env"
        if cwd_env.exists():
            _ld(cwd_env)
        quantnodes_env = Path.home() / ".quantnodes" / ".env"
        if quantnodes_env.exists():
            _ld(quantnodes_env)
    except Exception as exc:                       # noqa: BLE001
        logger.debug("dotenv load failed: %s", exc)


def _bridge_env_override_fields() -> set[str]:
    """Names of llm.json fields that QUANTNODES__LLM__* env vars set.

    Used by profile resolution so a profile never clobbers a runtime
    env override (env priority is higher than the file/profile layer).
    """
    from .quantnodes_bridge import _ENV_OVERRIDE_KEYS
    fields: set[str] = set()
    for key in _ENV_OVERRIDE_KEYS:
        if os.environ.get(key):
            fields.add(key.rsplit("__", 1)[-1].lower())
    return fields


def _load_bridge_dict(path: Path) -> dict[str, Any]:
    """Read the bridge config and translate into LLMConfig field names.

    Plus: provider→base_url fallback when the JSON omitted base_url.

    Unknown keys are passed through verbatim so user-config overrides
    (model_context_tokens, etc.) survive the bridge translation.

    Returns {} silently when the file is missing or the section is empty —
    callers (the load() cascade) then fall through to env vars and code
    defaults.
    """
    raw = load_quantnodes_llm_config(path)
    if not raw:
        return {}
    if raw.get("enabled") is False:
        return {}

    # ── Provider profile resolution ─────────────────────────────────
    raw = _resolve_active_profile(raw)

    out: dict[str, Any] = {}

    # Pass through all non-empty fields. _merge_flat in LLMConfig silently
    # ignores unknown keys; this preserves user-config extension fields.
    for key, value in raw.items():
        if key in ("profiles", "active_profile"):
            continue
        if value is None or value == "":
            continue
        out[key] = value

    # api_key may have been re-written by the profile after the bridge
    # already expanded the base section's env:VAR reference.
    api_key = out.get("api_key")
    if isinstance(api_key, str) and api_key.startswith("env:"):
        out["api_key"] = os.environ.get(api_key[4:], "")

    # Typed conversions
    _coerce_int(out, "timeout_s", raw.get("timeout"))
    _coerce_int(out, "max_retries", raw.get("max_retries"))
    _coerce_int(out, "max_tokens", raw.get("max_tokens"))
    _coerce_int(out, "max_iterations", raw.get("max_iterations"))
    _coerce_int(out, "agent_max_iterations", raw.get("agent_max_iterations"))
    if "timeout_s" in out:
        out.pop("timeout", None)

    for int_field in (
        "model_context_tokens",
        "model_max_output_tokens",
    ):
        _coerce_int(out, int_field, raw.get(int_field))

    for bool_field in ("model_supports_vision", "model_supports_reasoning"):
        _coerce_bool(out, bool_field, raw.get(bool_field))

    # ── CompactConfig from "compact" section ───────────────────────
    _build_compact_config(out, raw)

    # Provider→base_url/model/max_tokens fallback when the JSON omitted them.
    _apply_provider_defaults(out)

    return out


def _resolve_active_profile(raw: dict[str, Any]) -> dict[str, Any]:
    """Merge the active provider profile on top of the base llm fields.

    Priority: env overrides > profile > base.
    """
    profile_name = os.environ.get(ENV_PROFILE_ACTIVE) or raw.get("active_profile")
    if not profile_name:
        return raw
    profiles = raw.get("profiles")
    profile = profiles.get(profile_name) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        logger.warning(
            "LLM profile %r not found in llm.json profiles; "
            "using base config", profile_name,
        )
        return raw
    env_overridden = _bridge_env_override_fields()
    merged = dict(raw)
    for key, value in profile.items():
        if key in ("profiles", "active_profile"):
            continue
        if key in env_overridden:
            continue
        if value is None or value == "":
            continue
        merged[key] = value
    return merged


def _build_compact_config(out: dict[str, Any], raw: dict[str, Any]) -> None:
    """Populate ``out[\"compact_config\"]`` from the raw ``compact`` section."""
    compact_raw = raw.get("compact")
    if not isinstance(compact_raw, dict):
        return
    valid_fields = {f.name for f in dataclasses.fields(CompactConfig)}
    compact_kwargs: dict[str, Any] = {}
    for k, v in compact_raw.items():
        if k in valid_fields and v is not None:
            compact_kwargs[k] = v
    if compact_kwargs:
        out["compact_config"] = CompactConfig(**compact_kwargs)


def _apply_provider_defaults(out: dict[str, Any]) -> None:
    """Fill provider→base_url/model/max_tokens/window fallbacks."""
    p = out.get("provider")
    if not p:
        return
    defaults = PROVIDER_DEFAULTS.get(p)
    if not defaults:
        return
    if not out.get("base_url"):
        out["base_url"] = defaults["base_url"]
    if not out.get("model") and defaults.get("model"):
        out["model"] = defaults["model"]
    if out.get("max_tokens") is None and defaults.get("max_tokens"):
        out["max_tokens"] = defaults["max_tokens"]
    # Context/output window fallback chain (profile → catalog → adapter default).
    if out.get("model_context_tokens") is None:
        ctx = _catalog_window(p, out.get("model") or defaults.get("model"))
        if ctx is None and defaults.get("context_tokens"):
            ctx = defaults["context_tokens"]
        if ctx:
            out["model_context_tokens"] = ctx
    if out.get("model_max_output_tokens") is None:
        out_ctx = _catalog_window(
            p, out.get("model") or defaults.get("model"), output=True
        )
        if out_ctx:
            out["model_max_output_tokens"] = out_ctx


def _catalog_window(
    provider: str, model: str | None, *, output: bool = False
) -> int | None:
    """Query the model catalog's bundled/cached window data (no network).

    Returns the model's context (or max-output) window from the
    bundled TOML / disk cache, or None when the catalog has no real
    data for this model. Callers then use the adapter default or stay
    conservative.

    Heuristic: entries that carry the exact placeholder defaults
    (8192 context / 4096 output) are treated as "unknown model" —
    models.dev returns no entry for such models, and refresh_async
    persists the placeholder as ``fetched``. The ``source`` field is
    unreliable (a real fetch, a user-config override, and a bundled
    hit all produce different sources), so we trust the VALUE only:
    any context window that is not the placeholder is real data,
    regardless of where it came from.
    """
    if not model:
        return None
    try:
        from .model_catalog import get_model_info

        info = get_model_info(provider, model)
    except Exception:
        return None
    if output:
        value = info.max_output_tokens
        if value == 4096:
            return None  # placeholder default — no real data
    else:
        value = info.context_tokens
        if value == 8192:
            return None  # placeholder default — no real data
    if isinstance(value, int) and value > 0:
        return value
    return None


def _coerce_int(out: dict[str, Any], dst: str, src: Any) -> None:
    """Convert ``src`` to int and write to ``out[dst]``. No-op on failure."""
    if src is None or src == "":
        return
    try:
        out[dst] = int(src)
    except (TypeError, ValueError):
        logger.debug("bridge: cannot parse %s %r as int", dst, src)


def _coerce_bool(out: dict[str, Any], dst: str, src: Any) -> None:
    """Convert ``src`` to bool and write to ``out[dst]``. No-op on failure."""
    if src is None or src == "":
        return
    if isinstance(src, bool):
        out[dst] = src
    elif isinstance(src, str):
        out[dst] = src.lower() in ("1", "true", "yes", "on")
    else:
        out[dst] = bool(src)


def _env_to_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    """Translate the documented env vars to config overrides.

    Handles only the direct env vars (OPENAI_*). QUANTNODES__LLM__* is
    applied inside the bridge layer; STRATEGY_RESEARCH_LLM_PROFILE is
    retired (no profile concept anymore).
    """
    overrides: dict[str, Any] = {}
    if ENV_BASE_URL in env and env[ENV_BASE_URL]:
        overrides["base_url"] = env[ENV_BASE_URL]
    if ENV_MODEL in env and env[ENV_MODEL]:
        overrides["model"] = env[ENV_MODEL]
    wc_raw = env.get("SR_AGENT_WALLCLOCK_TIMEOUT", "").strip()
    if wc_raw:
        try:
            overrides["wallclock_timeout_s"] = float(wc_raw)
        except ValueError:
            pass
    return overrides


def _cli_to_overrides(cli: Mapping[str, Any]) -> dict[str, Any]:
    """Translate argparse namespace to config overrides.

    Mapping rule: --llm-foo-bar  →  cli["llm_foo_bar"]
    Only keys starting with 'llm_' and with non-None values are mapped.
    """
    overrides: dict[str, Any] = {}
    for key, value in cli.items():
        if not key.startswith("llm_"):
            continue
        if value is None:
            continue
        # Strip 'llm_' prefix and map to dataclass field name
        field = key[len("llm_"):]
        # Handle special cases
        if field == "stream":
            overrides["stream"] = bool(value)
            continue
        if field == "no_stream":
            overrides["stream"] = not bool(value)
            continue
        if field == "list_profiles":
            # legacy CLI flag; no profile concept anymore — ignored
            continue
        if field == "temperature":
            overrides["temperature"] = float(value)
        elif field == "max_tokens":
            overrides["max_tokens"] = int(value)
        elif field == "top_p":
            overrides["top_p"] = float(value)
        elif field == "timeout":
            overrides["timeout_s"] = float(value)
        elif field == "max_retries":
            overrides["max_retries"] = int(value)
        elif field == "seed":
            overrides["seed"] = int(value) if value != "" else None
        elif field == "model":
            overrides["model"] = str(value)
        elif field == "base_url":
            overrides["base_url"] = str(value)
        else:
            # Pass through unknown llm_* keys (forward compat)
            overrides[field] = value
    return overrides


def resolve_max_tokens(
    config: LLMConfig,
    requested: int | None = None,
    *,
    adapter: Any | None = None,
) -> int:
    """Pick the optimal ``max_tokens`` — full model budget unless explicit.

    Policy:

    - **Unset** (``None`` / non-positive / non-int): use the model's full
      output ceiling. This is the most reasonable default — never truncates,
      and providers bill on actual usage, not the cap.
    - **Explicit** (positive int): honour it, but clamp into
      ``[floor, ceiling]`` so tiny budgets cannot starve thinking models
      and oversized values cannot exceed the model's output window.

    Ceiling: ``model_max_output_tokens`` → adapter ``default_max_tokens``
    → ``_DEFAULT_MAX_TOKENS``.
    Floor: adapter ``min_output_tokens`` (thinking models need CoT room)
    → ``_MIN_OUTPUT_TOKENS``.
    """
    if adapter is None:
        try:
            from .provider import get_provider  # noqa: PLC2701

            adapter = get_provider(config.provider)
        except Exception:
            adapter = None

    floor = getattr(adapter, "min_output_tokens", None)
    if not isinstance(floor, int) or floor <= 0:
        floor = _MIN_OUTPUT_TOKENS

    ceiling = config.model_max_output_tokens
    if not isinstance(ceiling, int) or ceiling <= 0:
        ceiling = getattr(adapter, "default_max_tokens", None)
    if not isinstance(ceiling, int) or ceiling <= 0:
        ceiling = _DEFAULT_MAX_TOKENS

    if ceiling < floor:
        ceiling = floor

    # Unset / invalid → optimal default (full output budget).
    if requested is None:
        return ceiling
    try:
        value = int(requested)
    except (TypeError, ValueError):
        return ceiling
    if value <= 0:
        return ceiling

    # Explicit: clamp into the model's safe range.
    return max(floor, min(value, ceiling))


def load_api_key_from_env(env: Mapping[str, str] | None = None) -> str:
    """Load the API key.

    Precedence:
      1. ``OPENAI_API_KEY`` (legacy / direct).
      2. ``LLM_API_KEY`` (QuantNodes convention; written by the wizard
         into ``~/.quantnodes/strategy_research/.env``).
    Returns "" if neither is set.
    """
    env_map = env if env is not None else os.environ
    return env_map.get(ENV_API_KEY) or env_map.get(ENV_LLM_API_KEY, "")


def apply_api_key(cfg: LLMConfig, env: Mapping[str, str] | None = None) -> LLMConfig:
    """Return a new LLMConfig with api_key set from env (if cfg.api_key is empty)."""
    if cfg.api_key:
        return cfg
    key = load_api_key_from_env(env)
    if not key:
        return cfg
    return cfg.with_config(api_key=key)


def find_llm_config_path() -> Path:
    """Return the path the bridge layer will read from.

    Useful for tooling to display "you can edit this file" hints.
    """
    return _resolve_bridge_path(os.environ)


