"""First-launch onboarding wizard.

Mirrors ``vibe-trading/cli/onboard.py``. Triggered when
``~/.quantnodes/llm.json`` has no ``"llm"`` top-level key, or when
``quantnodes-research init`` is run with no arguments.

Five back-steppable steps (provider → model → key → timeout → optional
Tushare for China A-share data).

Outputs:

* **LLM structured config** (``provider`` / ``model`` / ``api_key`` /
  ``base_url`` / ``timeout`` / ``max_retries`` / ``enabled``) →
  ``~/.quantnodes/llm.json`` at top-level key ``"llm"``. Other top-level
  keys (``"tools"``, ``"agents"``, ``"cron"``, …) are preserved.

* **Tokens** (``LLM_API_KEY``, ``TUSHARE_TOKEN``) → ``~/.quantnodes/.env``
  (chmod 0600).

Public API:

* :data:`BACK`, :data:`CANCEL` — sentinel objects returned by selectors.
* :data:`PROVIDERS` — provider catalogue.
* :data:`TIMEOUT_CHOICES` — timeout preset offerings.
* :func:`run_onboarding` — full interactive flow.
* :func:`is_onboarded` — check whether ``llm.json["llm"]`` already exists.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Sentinels for back-navigation / cancel returned by selectors.
BACK = object()
CANCEL = object()


# 配置路径改为 PEP 562 模块级 __getattr__ 动态求值（阶段 4）：
# 旧写法 `_QUANTNODES_DIR: Final[Path] = Path.home() / ".quantnodes"` 在
# import 时固化——进程内 HOME/USERPROFILE 之后的任何变化（测试沙箱重定向、
# init 向导自身改 env）都不再生效，测试夹具与运行时行为错位。
# __getattr__ 保证每次访问都重新解析；monkeypatch.setattr 补丁创建真实
# 模块属性、优先于 __getattr__，测试替换仍然有效。


def _patched(name: str) -> Path | None:
    """被 monkeypatch.setattr 替换过的模块属性（存在即优先）。"""
    value = globals().get(name)
    return Path(value) if value is not None else None


def _quantnodes_dir() -> Path:
    patched = _patched("_QUANTNODES_DIR")
    return patched if patched is not None else Path.home() / ".quantnodes"


def _quantnodes_llm_json_path() -> Path:
    patched = _patched("_QUANTNODES_LLM_JSON_PATH")
    return patched if patched is not None else _quantnodes_dir() / "llm.json"


def _quantnodes_llm_json_partial() -> Path:
    patched = _patched("_QUANTNODES_LLM_JSON_PARTIAL")
    if patched is not None:
        return patched
    return _quantnodes_dir() / "llm.json.partial"


def _quantnodes_dotenv_path() -> Path:
    patched = _patched("_QUANTNODES_DOTENV_PATH")
    return patched if patched is not None else _quantnodes_dir() / ".env"


def __getattr__(name: str):
    """PEP 562：外部 ``module._QUANTNODES_*`` 访问调用时动态解析。

    monkeypatch.setattr 创建真实模块属性、优先于 __getattr__，
    测试替换路径仍然有效；模块内部代码请用上面的访问函数。
    """
    mapping = {
        "_QUANTNODES_DIR": _quantnodes_dir,
        "_QUANTNODES_LLM_JSON_PATH": _quantnodes_llm_json_path,
        "_QUANTNODES_LLM_JSON_PARTIAL": _quantnodes_llm_json_partial,
        "_QUANTNODES_DOTENV_PATH": _quantnodes_dotenv_path,
    }
    fn = mapping.get(name)
    if fn is not None:
        return fn()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Field written into llm.json["llm"]["api_key"] so the bridge resolves it
# via the dotenv file. Real key is stored in ~/.quantnodes/.env as
# LLM_API_KEY=<key>.
_LLM_API_KEY_REF: Final[str] = "env:LLM_API_KEY"


# ─── Provider catalogue ────────────────────────────────────────────────


@dataclass(frozen=True)
class Provider:
    """One selectable LLM provider option shown in step 1."""

    key: str
    label: str
    description: str
    default_model: str
    key_prefix: str | None
    suggested_models: tuple[str, ...]
    base_url: str | None     # None for Ollama (could be configured)
    key_required: bool       # False for Ollama


# Default base URLs for each provider. Ollama is None (auto-detected at runtime).
_DEFAULT_BASE_URLS: Final[dict[str, str]] = {
    "openai":     "https://api.openai.com/v1",
    "anthropic":  "https://api.anthropic.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "minimax":    "https://api.minimaxi.com/v1",
    "nvidia":     "https://integrate.api.nvidia.com/v1",
}


PROVIDERS: Final[tuple[Provider, ...]] = (
    Provider(
        "openai", "OpenAI", "GPT-4o direct",
        "gpt-4o", "sk-",
        ("gpt-4o", "gpt-4o-mini", "gpt-4.1"),
        _DEFAULT_BASE_URLS["openai"], True,
    ),
    Provider(
        "anthropic", "Anthropic", "Claude direct",
        "claude-3-5-sonnet-latest", "sk-ant-",
        ("claude-3-5-sonnet-latest", "claude-3-opus-latest"),
        _DEFAULT_BASE_URLS["anthropic"], True,
    ),
    Provider(
        "openrouter", "OpenRouter", "200+ models via single API key",
        "deepseek/deepseek-chat", "sk-or-",
        ("deepseek/deepseek-chat", "openai/gpt-4o", "anthropic/claude-3.5-sonnet"),
        _DEFAULT_BASE_URLS["openrouter"], True,
    ),
    Provider(
        "minimax", "MiniMax", "minimax provider",
        "MiniMax-M3", "sk-",
        ("MiniMax-M3", "MiniMax-Text-01"),
        _DEFAULT_BASE_URLS["minimax"], True,
    ),
    Provider(
        "ollama", "Ollama", "Local — free, no API key",
        "qwen2.5:32b", None,
        ("qwen2.5:32b", "llama3.3:70b", "deepseek-r1:14b"),
        "http://localhost:11434", False,
    ),
    Provider(
        "nvidia", "NVIDIA NIM", "NVIDIA-hosted models (GLM/DeepSeek/… via integrate.api.nvidia.com)",
        "z-ai/glm-5.2", "nvapi-",
        ("z-ai/glm-5.2", "deepseek-ai/deepseek-v4-flash"),
        _DEFAULT_BASE_URLS["nvidia"], True,
    ),
)


TIMEOUT_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("600", "600s (10 min — large backtests / swarm runs)"),
    ("300", "300s (5 min — normal autoresearch, recommended)"),
    ("120", "120s (2 min — quick lookup mode)"),
    ("60",  "60s (1 min — smoke test only)"),
)


# ─── Filesystem helpers ────────────────────────────────────────────────


def _read_llm_json(path: Path) -> dict:
    """Read existing llm.json (or return {} if missing/malformed).

    Never raises; caller gets a fresh dict to mutate.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_partial(
    llm_section: dict[str, object],
    *,
    llm_json_path: Path | None = None,
) -> None:
    """Best-effort write to ``llm.json.partial`` (crash-resilience nicety).

    The partial mirrors the would-be-committed ``"llm"`` top-level
    section (NOT the full file), so on recovery we can resume cleanly.
    """
    path = llm_json_path or _quantnodes_llm_json_path()
    partial = path.parent / f"{path.name}.partial"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text(
            json.dumps({"llm": llm_section}, indent=2),
            encoding="utf-8",
        )
        try:
            partial.chmod(0o600)
        except OSError:
            pass
    except OSError:
        pass


def _finalize_llm_json(
    llm_section: dict[str, object],
    *,
    llm_json_path: Path | None = None,
) -> Path:
    """Atomically merge ``llm_section`` into ``llm.json["llm"]``.

    Preserves any other top-level keys (``"tools"``, ``"agents"``,
    ``"cron"``, …) that QuantNodes may have written.

    Returns the final llm.json path.
    """
    path = llm_json_path or _quantnodes_llm_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = _read_llm_json(path)
    existing["llm"] = dict(llm_section)

    fd, tmp_name = tempfile.mkstemp(prefix=".llm.json.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    # Tidy: drop the live partial now that .json is committed.
    partial = path.parent / f"{path.name}.partial"
    if partial.exists():
        try:
            partial.unlink()
        except OSError:
            pass

    return path


def _save_tokens_to_dotenv(
    tokens: dict[str, str],
    *,
    dotenv_path: Path | None = None,
) -> Path:
    """Write key=value lines to ``~/.quantnodes/.env`` (chmod 0600).

    Existing keys with non-empty values are preserved; new keys are
    appended. Atomic write via mkstemp + os.replace.
    """
    path = dotenv_path or _quantnodes_dotenv_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, str] = {}
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                existing[k.strip()] = v
        except OSError:
            existing = {}

    for k, v in tokens.items():
        if v:
            existing[k] = v

    content = "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n"

    fd, tmp_name = tempfile.mkstemp(prefix=".env.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def is_onboarded(*, llm_json_path: Path | None = None) -> bool:
    """True iff ``llm.json`` exists and has a non-empty ``"llm"`` section."""
    p = llm_json_path or _quantnodes_llm_json_path()
    if not p.exists():
        return False
    data = _read_llm_json(p)
    llm_section = data.get("llm")
    return isinstance(llm_section, dict) and bool(llm_section)


# ─── Plaintext-key migration (K3) ──────────────────────────────────────


def _detect_plaintext_api_key(
    llm_json_path: Path | None = None,
) -> str | None:
    """Return the plaintext api_key in llm.json["llm"] if present and not
    an ``env:VAR`` reference. Returns None otherwise.
    """
    p = llm_json_path or _quantnodes_llm_json_path()
    data = _read_llm_json(p)
    section = data.get("llm")
    if not isinstance(section, dict):
        return None
    key = section.get("api_key")
    if not isinstance(key, str) or not key:
        return None
    if key.startswith("env:"):
        return None
    return key


def _prompt_migrate_plaintext(
    existing_plaintext: str, *, inputs: list[str] | None = None
) -> bool:
    """Return True if user agrees to migrate plaintext → env:VAR.

    In test mode (``inputs`` provided), pops the next item. Accepts
    ``"y"/"yes"/"true"/"1"`` → True, anything else → False.
    """
    if inputs is not None:
        if not inputs:
            raise RuntimeError("ran out of onboarding inputs")
        answer = inputs.pop(0).strip().lower()
        return answer in ("y", "yes", "true", "1")

    # TTY mode
    try:
        from rich.prompt import Confirm
        return Confirm.ask(
            "Existing plaintext API key found in llm.json — migrate to "
            "env:LLM_API_KEY (safer, reference only)?",
            default=True,
        )
    except ImportError:
        # stdin fallback
        print(
            "? Existing plaintext API key found in llm.json — migrate to "
            "env:LLM_API_KEY? [Y/n]"
        )
        try:
            raw = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return raw in ("", "y", "yes", "true", "1")


# ─── TTY selectors (prompt_toolkit) ──────────────────────────────────


def _select_with_back(
    prompt: str,
    choices: list[tuple[str, str]],
    *,
    default_index: int = 0,
) -> str | object:
    """Vertically-scrollable selector. Returns chosen value, BACK, or CANCEL.

    Keybindings: ↑/↓ navigate, Enter confirm, Esc/← back, Ctrl+C cancel.
    Falls back to a numeric stdin prompt if prompt_toolkit is unavailable.
    """
    from rich.console import Console

    from strategy_research.cli.theme import Theme

    console = Console()
    console.print()
    console.print(f"? {prompt}", style=Theme.label)

    try:
        from prompt_toolkit import Application
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import HSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style as PTStyle
    except ImportError:
        return _select_numeric(choices, default_index)

    state = {"index": max(0, min(default_index, len(choices) - 1)), "result": None}

    def _format() -> FormattedText:
        out: list[tuple[str, str]] = []
        for i, (_, label) in enumerate(choices):
            if i == state["index"]:
                out.append(("class:cursor", "  > "))
                out.append(("class:selected", f"{label}\n"))
            else:
                out.append(("", "    "))
                out.append(("class:option", f"{label}\n"))
        out.append(
            ("class:hint", "\n  ↑/↓ navigate · Enter select · Esc/← back · Ctrl+C cancel")
        )
        return FormattedText(out)

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("c-p")
    def _(event):  # type: ignore[no-redef]
        state["index"] = (state["index"] - 1) % len(choices)
        event.app.invalidate()

    @kb.add("down")
    @kb.add("c-n")
    def _(event):  # type: ignore[no-redef]
        state["index"] = (state["index"] + 1) % len(choices)
        event.app.invalidate()

    @kb.add("enter")
    def _(event):  # type: ignore[no-redef]
        state["result"] = choices[state["index"]][0]
        event.app.exit()

    @kb.add("escape", eager=True)
    @kb.add("left")
    def _(event):  # type: ignore[no-redef]
        state["result"] = BACK
        event.app.exit()

    @kb.add("c-c")
    @kb.add("c-d")
    def _(event):  # type: ignore[no-redef]
        state["result"] = CANCEL
        event.app.exit()

    brand_hex = Theme.brand_hex if hasattr(Theme, "brand_hex") else "#258BFF"
    style = PTStyle.from_dict(
        {
            "cursor": f"{brand_hex} bold",
            "selected": f"{brand_hex} bold",
            "option": "",
            "hint": "#808080",
        }
    )
    layout = Layout(HSplit([Window(FormattedTextControl(_format), wrap_lines=False)]))
    app: Application = Application(
        layout=layout, key_bindings=kb, style=style, full_screen=False
    )
    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        return CANCEL
    return state["result"] if state["result"] is not None else CANCEL


def _select_numeric(
    choices: list[tuple[str, str]], default_index: int
) -> str | object:
    """Stdin-only fallback selector (no BACK support)."""
    import sys

    for i, (_, label) in enumerate(choices, start=1):
        marker = ">" if (i - 1) == default_index else " "
        print(f"  {marker} [{i}] {label}")
    print("  (type number, q=cancel)")
    try:
        raw = sys.stdin.readline().strip()
    except (EOFError, KeyboardInterrupt):
        return CANCEL
    if raw in ("q", "quit", ""):
        return CANCEL
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(choices):
            return choices[idx][0]
    except ValueError:
        pass
    return CANCEL


def _prompt_secret(prompt: str) -> str | object:
    """Read masked input. Returns str, BACK, or CANCEL."""
    from rich.console import Console

    from strategy_research.cli.theme import Theme

    console = Console()
    console.print()
    console.print(f"? {prompt}", style=Theme.label)
    console.print(
        "  (input hidden · Enter to submit · Esc to go back · Ctrl+C to cancel)",
        style=Theme.muted,
    )
    try:
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.key_binding import KeyBindings

        kb = KeyBindings()
        sentinel: dict[str, object] = {"action": None}

        @kb.add("escape", eager=True)
        def _(event):  # type: ignore[no-redef]
            sentinel["action"] = BACK
            event.app.exit(result="")

        try:
            value = pt_prompt("> ", is_password=True, key_bindings=kb)
        except (EOFError, KeyboardInterrupt):
            return CANCEL
        if sentinel["action"] is BACK:
            return BACK
        return value.strip()
    except ImportError:
        import getpass

        try:
            return getpass.getpass("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return CANCEL


def _prompt_text(prompt: str, *, default: str = "") -> str | object:
    """Read plain text. Returns str, BACK, or CANCEL."""
    from rich.console import Console

    from strategy_research.cli.theme import Theme

    console = Console()
    console.print()
    console.print(f"? {prompt}", style=Theme.label)
    if default:
        console.print(
            f"  (Enter for default: {default} · Esc to go back)", style=Theme.muted
        )
    else:
        console.print("  (Enter to skip · Esc to go back)", style=Theme.muted)

    try:
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.key_binding import KeyBindings

        kb = KeyBindings()
        sentinel: dict[str, object] = {"action": None}

        @kb.add("escape", eager=True)
        def _(event):  # type: ignore[no-redef]
            sentinel["action"] = BACK
            event.app.exit(result="")

        try:
            value = pt_prompt("> ", key_bindings=kb)
        except (EOFError, KeyboardInterrupt):
            return CANCEL
        if sentinel["action"] is BACK:
            return BACK
        v = value.strip()
        return v if v else default
    except ImportError:
        try:
            raw = input("> ").strip()
            return raw if raw else default
        except (EOFError, KeyboardInterrupt):
            return CANCEL


def _validate_key(prefix: str | None, key: str) -> str | None:
    """Return error message or None if key looks plausible."""
    if not key:
        return "API key cannot be empty."
    if prefix and not key.startswith(prefix):
        return f"Expected key to start with '{prefix}'."
    if len(key) < 12:
        return "That key looks too short."
    return None


# ─── TTY step functions ──────────────────────────────────────────────


def _step_provider(
    llm: dict[str, object], state: dict, skip_tushare: bool
) -> object:
    """Step 1: select LLM provider."""
    choices = [(p.key, f"{p.label:<14}  {p.description}") for p in PROVIDERS]
    result = _select_with_back("Pick a model provider", choices)
    if result in (BACK, CANCEL):
        return result
    provider = next(p for p in PROVIDERS if p.key == result)

    # When switching providers via BACK, drop fields that belong to the
    # previously selected provider so the final llm.json doesn't keep
    # stale credentials for the old backend.
    old_key = state.get("provider_key")
    if old_key is not None and old_key != provider.key:
        for k in ("api_key", "base_url", "model"):
            llm.pop(k, None)

    llm["provider"] = provider.key
    if provider.base_url:
        llm["base_url"] = provider.base_url
    state["provider"] = provider
    state["provider_key"] = provider.key
    _save_partial(llm)
    return "ok"


def _step_model(
    llm: dict[str, object], state: dict, skip_tushare: bool
) -> object:
    """Step 2: select model."""
    provider: Provider = state["provider"]
    choices: list[tuple[str, str]] = [
        (m, f"{m}{' (default)' if m == provider.default_model else ''}")
        for m in provider.suggested_models
    ]
    choices.append(("__custom__", "other (type custom model id)"))
    default_idx = next(
        (i for i, (v, _) in enumerate(choices) if v == provider.default_model), 0
    )
    result = _select_with_back("Pick a model", choices, default_index=default_idx)
    if result in (BACK, CANCEL):
        return result
    if result == "__custom__":
        custom = _prompt_text("Type the model id", default=provider.default_model)
        if custom in (BACK, CANCEL):
            return custom
        model = str(custom) or provider.default_model
    else:
        model = str(result)
    llm["model"] = model
    _save_partial(llm)
    return "ok"


def _step_key(
    llm: dict[str, object], state: dict, skip_tushare: bool
) -> object:
    """Step 3: enter API key (skip for providers with no key)."""
    provider: Provider = state["provider"]
    if not provider.key_required:
        from rich.console import Console

        from strategy_research.cli.theme import Theme

        msg = (
            "Ollama runs locally — no API key needed."
            if provider.key == "ollama"
            else "This provider does not require an API key."
        )
        Console().print(f"  {msg}", style=Theme.muted)
        return "ok"
    while True:
        key = _prompt_secret(
            f"Paste your {provider.label} API key "
            "(saved to ~/.quantnodes/.env, never logged)"
        )
        if key in (BACK, CANCEL):
            return key
        err = _validate_key(provider.key_prefix, str(key))
        if err is None:
            llm["api_key"] = _LLM_API_KEY_REF
            state["api_key_value"] = str(key)
            _save_partial(llm)
            return "ok"
        from rich.console import Console

        from strategy_research.cli.theme import Theme

        Console().print(
            f"  {err}  Try again, or press Esc to go back.", style=Theme.danger
        )


def _step_timeout(
    llm: dict[str, object], state: dict, skip_tushare: bool
) -> object:
    """Step 4: select request timeout."""
    choices = [(v, label) for v, label in TIMEOUT_CHOICES]
    result = _select_with_back("Default request timeout", choices, default_index=1)
    if result in (BACK, CANCEL):
        return result
    llm["timeout"] = int(str(result))
    llm["max_retries"] = 2
    _save_partial(llm)
    return "ok"


def _step_tushare(
    llm: dict[str, object], state: dict, skip_tushare: bool
) -> object:
    """Step 5: optional Tushare token (China A-share data)."""
    if skip_tushare:
        return "ok"
    choices = [
        ("__skip__", "No, skip (most users)"),
        ("__paste__", "Yes — paste my Tushare token"),
    ]
    result = _select_with_back(
        "Enable Tushare for China A-share data? (optional)", choices
    )
    if result in (BACK, CANCEL):
        return result
    if result == "__paste__":
        token = _prompt_secret("Tushare token")
        if token in (BACK, CANCEL):
            return token
        if str(token).strip():
            state["tushare_token_value"] = str(token).strip()
    return "ok"


# ─── Public flow ──────────────────────────────────────────────────────


def _attach_profile(llm: dict, chosen: Provider) -> dict:
    """Attach a provider profile + ``active_profile`` to an llm section.

    The profile mirrors the top-level keys the wizard just collected
    (provider / base_url / model / api_key), so the ``llm --use`` /
    ``--llm-profile`` switching machinery works right after onboarding.
    Values fall back to the top-level section, keeping old files
    compatible.
    """
    profile: dict[str, object] = {"provider": chosen.key}
    if chosen.base_url:
        profile["base_url"] = chosen.base_url
    if llm.get("model"):
        profile["model"] = llm["model"]
    if llm.get("api_key"):
        profile["api_key"] = llm["api_key"]
    llm["profiles"] = {chosen.key: profile}
    llm["active_profile"] = chosen.key
    return llm


def run_onboarding(
    *,
    llm_json_path: Path | None = None,
    dotenv_path: Path | None = None,
    inputs: list[str] | None = None,
    skip_tushare: bool = False,
) -> Path | None:
    """Run the onboarding wizard.

    Two modes:

    * **Test mode** (``inputs`` provided): pops items from the list, no
      TTY interaction. Raises ``RuntimeError`` on empty list.
    * **TTY mode** (``inputs=None``): drives prompt_toolkit selectors
      with BACK/CANCEL support. Returns ``None`` when the user cancels.

    Args:
        llm_json_path: Override the llm.json path (used by tests).
        dotenv_path:   Override the .env path (used by tests).
        inputs:        Optional pre-canned sequence of user inputs.
        skip_tushare:  If True, omit the optional Tushare step.

    Returns:
        Path of the final ``llm.json`` file, or ``None`` on cancel.
    """
    llm_path = llm_json_path or _quantnodes_llm_json_path()
    env_path = dotenv_path or _quantnodes_dotenv_path()

    if inputs is not None:
        return _run_onboarding_test_mode(
            inputs, llm_path, env_path, skip_tushare, llm_json_path, dotenv_path
        )
    return _run_onboarding_tty(llm_path, env_path, skip_tushare, llm_json_path, dotenv_path)


def _run_onboarding_test_mode(
    inputs: list[str],
    llm_path: Path,
    env_path: Path,
    skip_tushare: bool,
    llm_json_path: Path | None,
    dotenv_path: Path | None,
) -> Path | None:
    """Test-mode onboarding: pops items from ``inputs``, no TTY interaction."""
    # ── Step 0: config audit (auto-apply in test mode) ──────────────
    from strategy_research.foundation.llm.config_audit import (
        detect_issues,
        fix_issues,
    )
    _issues = detect_issues(llm_json_path=llm_path, env_path=env_path)
    if _issues:
        fix_issues(_issues, llm_json_path=llm_path, env_path=env_path)

    llm: dict[str, object] = {}
    collected: dict[str, object] = {}

    def _next() -> str:
        if not inputs:
            raise RuntimeError("ran out of onboarding inputs")
        return inputs.pop(0)

    # Step 1: provider
    chosen_label = _next().strip()
    chosen = next((p for p in PROVIDERS if chosen_label == p.label), None)
    if chosen is None:
        raise ValueError(f"provider not selected: {chosen_label!r}")
    llm["provider"] = chosen.key
    if chosen.base_url:
        llm["base_url"] = chosen.base_url
    collected["provider"] = chosen

    # Step 2: model
    model = _next().strip() or chosen.default_model
    llm["model"] = model

    # Step 3: API key (skip for providers with no key)
    if chosen.key_required:
        key = _next().strip()
        if key:
            llm["api_key"] = _LLM_API_KEY_REF
            collected["api_key_value"] = key

    # Step 4: timeout
    timeout = _next().strip() or "300"
    llm["timeout"] = int(timeout)
    llm["max_retries"] = 2

    # Optional Step 5: Tushare (China A-share)
    if not skip_tushare:
        tushare = _next().strip()
        if tushare:
            collected["tushare_token_value"] = tushare

    # K3: plaintext-key migration (test mode: read next input)
    existing_plain = _detect_plaintext_api_key(llm_path)
    if existing_plain:
        _apply_k3_migration(llm, collected, existing_plain, inputs=inputs)

    # Write tokens to .env (always)
    tokens: dict[str, str] = {}
    if "api_key_value" in collected:
        tokens["LLM_API_KEY"] = collected["api_key_value"]
    if "tushare_token_value" in collected:
        tokens["TUSHARE_TOKEN"] = collected["tushare_token_value"]
    if tokens:
        _save_tokens_to_dotenv(tokens, dotenv_path=env_path)

    _attach_profile(llm, chosen)
    return _finalize_llm_json(llm, llm_json_path=llm_path)


def _apply_k3_migration(
    llm: dict[str, object],
    collected: dict[str, object],
    existing_plain: str,
    *,
    inputs: list[str] | None = None,
) -> None:
    """Prompt whether to migrate a plaintext key; write env: or plaintext."""
    migrate = _prompt_migrate_plaintext(existing_plain, inputs=inputs)
    if migrate and "api_key_value" in collected:
        return  # agreed → env:LLM_API_KEY form (already set)
    if not migrate and "api_key_value" in collected:
        # declined → use plaintext form in llm.json
        llm["api_key"] = collected["api_key_value"]


def _run_onboarding_tty(
    llm_path: Path,
    env_path: Path,
    skip_tushare: bool,
    llm_json_path: Path | None,
    dotenv_path: Path | None,
) -> Path | None:
    """TTY-mode onboarding: prompt_toolkit selectors with BACK/CANCEL."""
    import sys

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise RuntimeError(
            "init wizard requires a TTY (both stdin and stdout must be "
            "real terminals); redirecting or piping prevents it from running"
        )

    # ── Step 0: config audit (detect + fix C1-C5) ──────────────────────
    from strategy_research.foundation.llm.config_audit import (
        detect_issues,
        fix_issues,
        format_report,
    )

    _issues = detect_issues(llm_json_path=llm_path, env_path=env_path)
    if _issues:
        from rich.console import Console
        from rich.panel import Panel


        console = Console()
        console.print(
            Panel(
                format_report(_issues, use_color=console.is_terminal),
                title="Config audit",
                border_style="yellow",
                padding=(0, 1),
            )
        )
        from prompt_toolkit import prompt as _pt_prompt
        _ans = _pt_prompt(
            "Apply auto-fixes? [Y/n]: ",
            default="y",
        ).strip().lower()
        if _ans in ("", "y", "yes"):
            _issues = fix_issues(
                _issues, llm_json_path=llm_path, env_path=env_path
            )
            console.print(
                "[green]Auto-fixes applied.[/green]"
            )
        else:
            console.print("[dim]Skipped auto-fixes.[/dim]")

    llm = {}
    state: dict[str, object] = {}
    steps = [
        _step_provider,
        _step_model,
        _step_key,
        _step_timeout,
        _step_tushare,
    ]
    i = 0
    while i < len(steps):
        result = steps[i](llm, state, skip_tushare)
        if result is CANCEL:
            return None
        if result is BACK:
            if i == 0:
                return None  # back at step 0 = cancel
            i -= 1
            continue
        i += 1

    # K3: plaintext-key migration prompt (TTY)
    existing_plain = _detect_plaintext_api_key(llm_path)
    if existing_plain and "api_key_value" in state:
        if _prompt_migrate_plaintext(existing_plain):
            # agreed → keep llm["api_key"] = "env:LLM_API_KEY" (already set)
            pass
        else:
            # declined → write the plaintext back to llm.json
            llm["api_key"] = state["api_key_value"]
            _save_partial(llm)

    # Write tokens to .env (always)
    tokens: dict[str, str] = {}
    if "api_key_value" in state:
        tokens["LLM_API_KEY"] = state["api_key_value"]
    if "tushare_token_value" in state:
        tokens["TUSHARE_TOKEN"] = state["tushare_token_value"]
    if tokens:
        _save_tokens_to_dotenv(tokens, dotenv_path=env_path)

    _attach_profile(llm, state["provider"])
    return _finalize_llm_json(llm, llm_json_path=llm_path)


__all__ = [
    "BACK",
    "CANCEL",
    "Provider",
    "PROVIDERS",
    "TIMEOUT_CHOICES",
    "_select_with_back",
    "_prompt_secret",
    "_prompt_text",
    "_validate_key",
    "_step_provider",
    "_step_model",
    "_step_key",
    "_step_timeout",
    "_step_tushare",
    "is_onboarded",
    "run_onboarding",
]
