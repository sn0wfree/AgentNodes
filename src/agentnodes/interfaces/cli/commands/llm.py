"""``llm`` command — quick LLM provider switching.

Usage::

    agentnodes llm --list            # show providers + key status
    agentnodes llm --use minimax     # switch active profile
    agentnodes llm --show            # effective config (masked)
    agentnodes llm --add-key nvidia  # store <NAME>_API_KEY in ~/.quantnodes/.env

Persistent state lives in ``~/.quantnodes/llm.json`` (``profiles`` +
``active_profile``) and ``~/.quantnodes/.env`` (``<NAME>_API_KEY``).
All writes are atomic (tmp file + os.replace) and preserve other
top-level keys / dotenv lines.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from agentnodes.interfaces.cli.commands.registry import cli_command
from agentnodes.foundation.llm.config import _try_load_dotenv as _load_dotenv

_load_dotenv()

QUANTNODES_DIR = Path.home() / ".agentnodes"
LLM_JSON_PATH = QUANTNODES_DIR / "llm.json"
DOTENV_PATH = QUANTNODES_DIR / ".env"


# ── Providers / profiles helpers ────────────────────────────────────


def _registry_providers() -> dict[str, dict[str, str]]:
    """provider name → {base_url, model} from adapter defaults."""
    from agentnodes.foundation.llm.provider import (
        _REGISTRY,  # noqa: PLC2701
        get_provider_defaults,
    )

    out: dict[str, dict[str, str]] = {}
    for name in sorted(_REGISTRY):
        if name in ("auto", "fallback"):
            continue
        d = get_provider_defaults(name)
        out[name] = {
            "base_url": d.get("base_url") or "",
            "model": d.get("model") or "",
        }
    return out


def _load_llm_json(llm_json_path: Path | None = None) -> dict:
    path = llm_json_path or LLM_JSON_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_profiles(llm_json_path: Path | None = None) -> dict[str, dict]:
    data = _load_llm_json(llm_json_path)
    profiles = (data.get("llm") or {}).get("profiles")
    return profiles if isinstance(profiles, dict) else {}


def _key_var_for(name: str) -> str:
    return f"{name.upper()}_API_KEY"


def _key_is_set(name: str) -> bool:
    return bool(os.environ.get(_key_var_for(name)))


def _active_profile(llm_json_path: Path | None = None) -> str | None:
    data = _load_llm_json(llm_json_path)
    llm = data.get("llm")
    return llm.get("active_profile") if isinstance(llm, dict) else None


# ── Dotenv helpers (atomic, preserve other lines) ───────────────────


def _read_dotenv(path: Path) -> dict[str, str]:
    """Parse a .env file (shared impl)."""
    from agentnodes.foundation.utils.io_utils import read_dotenv
    return read_dotenv(path)


def _write_dotenv(tokens: dict[str, str], *,
                  dotenv_path: Path | None = None) -> Path:
    """Merge KEY=value lines into .env (atomic, 0600, shared impl)."""
    from agentnodes.foundation.utils.io_utils import (
        atomic_write_text,
        read_dotenv,
    )

    path = dotenv_path or DOTENV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = read_dotenv(path)
    for k, v in tokens.items():
        if v:
            merged[k] = v
    content = "\n".join(f"{k}={v}" for k, v in merged.items()) + "\n"
    atomic_write_text(path, content, mode=0o600)
    return path


# ── llm.json write (atomic, preserves other top-level keys) ─────────


def _atomic_write_llm_json(data: dict, *,
                           llm_json_path: Path | None = None) -> Path:
    """Atomic pretty-JSON write with 0600 (shared impl)."""
    from agentnodes.foundation.utils.io_utils import atomic_write_json

    path = llm_json_path or LLM_JSON_PATH
    atomic_write_json(path, data)
    return path


def _backup_llm_json(llm_json_path: Path | None = None) -> Path | None:
    """Copy llm.json to llm.json.bak-<timestamp> before mutation."""
    path = llm_json_path or LLM_JSON_PATH
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, dest)
    return dest


# ── Sub-actions ─────────────────────────────────────────────────────


def _profile_defaults(name: str) -> dict:
    """Build a profile dict for ``name`` from adapter defaults + key var."""
    from agentnodes.foundation.llm.provider import get_provider_defaults

    d = get_provider_defaults(name)
    profile: dict = {"provider": name}
    if d.get("base_url"):
        profile["base_url"] = d["base_url"]
    if d.get("model"):
        profile["model"] = d["model"]
    profile["api_key"] = f"env:{_key_var_for(name)}"
    return profile


def _action_list() -> int:
    providers = _registry_providers()
    profiles = _load_profiles()
    active = _active_profile()
    name_width = max(len(p) for p in providers) if providers else 8
    print(f"{'':<{name_width}}  {'模型':<24}  {'密钥':<18}  状态")
    for name in providers:
        model = (profiles.get(name) or providers[name]).get("model") or providers[name]["model"]
        key_var = _key_var_for(name)
        key_state = "✓" if _key_is_set(name) else "✗"
        marker = "*" if name == active else " "
        print(f"{marker} {name:<{name_width}}  {model:<24}  {key_var:<18} {key_state}")
    missing = sorted(set(profiles) - set(providers))
    for name in missing:
        key_var = _key_var_for(name)
        key_state = "✓" if _key_is_set(name) else "✗"
        marker = "*" if name == active else " "
        print(f"{marker} {name:<{name_width}}  {'(profile only)':<24}  {key_var:<18} {key_state}")
    if not providers and not missing:
        print("(no providers registered)")
    print(f"\nactive_profile = {active or '(none)'}")
    print("Tip: `llm --use <name>` 切换；`llm --add-key <name>` 录入密钥；")
    print("     `--llm-profile <name>` 为单次运行覆盖（无需改文件）。")
    return 0


def _action_show() -> int:
    from agentnodes.foundation.llm import LLMConfig

    cfg = LLMConfig.load()
    masked = cfg.masked_dict()
    print("# 生效配置（密钥已掩码）")
    for key in ("provider", "model", "base_url", "api_key", "timeout_s",
                "max_retries", "temperature", "max_tokens", "top_p",
                "seed", "model_context_tokens", "model_max_output_tokens"):
        if key in masked:
            print(f"  {key:<24} {masked[key]}")
    active = _active_profile()
    print(f"\n# active_profile = {active or '(none)'}")
    if active:
        print(f"# 来源: llm.json profiles.{active}（env LLM_PROFILE / --llm-profile 可覆盖）")
    return 0


def _action_use(name: str) -> int:
    known = _registry_providers()
    profiles = _load_profiles()
    if name not in profiles and name not in known:
        print(f"error: 未知 provider {name!r}（可用: {', '.join(sorted(known))}）")
        return 1
    if name not in profiles:
        profiles[name] = _profile_defaults(name)

    data = _load_llm_json()
    llm = data.get("llm")
    if not isinstance(llm, dict):
        llm = {}
    llm = dict(llm)
    llm["profiles"] = profiles
    llm["active_profile"] = name
    data["llm"] = llm

    backup = _backup_llm_json()
    _atomic_write_llm_json(data)
    print(f"已切换到 provider: {name}")
    if backup:
        print(f"（备份: {backup.name}）")

    key_var = _key_var_for(name)
    if not _key_is_set(name):
        print(f"warning: {key_var} 未设置 — 运行 `llm --add-key {name}` 或"
              f" export {key_var}=...")
    return 0


def _action_add_key(name: str) -> int:
    import getpass

    key_var = _key_var_for(name)
    value = getpass.getpass(f"输入 {key_var}（输入将不回显）: ").strip()
    if not value:
        print("error: 未输入任何内容，已取消")
        return 1
    path = _write_dotenv({key_var: value})
    print(f"已写入 {path}（0600）")
    print(f"提示: 将 profile {name!r} 的 api_key 指向 env:{key_var} 即可自动使用。")
    return 0


# ── Command registration ────────────────────────────────────────────


@cli_command(
    "llm",
    help="LLM 供应商快速切换（list / use / show / add-key）",
    description=(
        "管理 ~/.quantnodes/llm.json 的 provider profiles 与 "
        "~/.quantnodes/.env 的每供应商密钥。所有写入均为原子操作。"
    ),
    add=lambda p: (
        p.add_argument("--list", action="store_true",
                       help="列出所有 provider 及密钥配置状态"),
        p.add_argument("--use", metavar="NAME",
                       help="切换到指定 provider（自动创建缺失的 profile）"),
        p.add_argument("--show", action="store_true",
                       help="显示当前生效配置（密钥掩码）"),
        p.add_argument("--add-key", metavar="NAME",
                       help="录入 <NAME>_API_KEY 到 ~/.quantnodes/.env"),
    ),
)
def cmd_llm(args: argparse.Namespace) -> int:
    """LLM config management: list profiles, switch active profile."""
    if args.list:
        return _action_list()
    if args.use:
        return _action_use(args.use)
    if args.show:
        return _action_show()
    if args.add_key:
        return _action_add_key(args.add_key)
    print((__doc__ or "").strip())
    return 0
