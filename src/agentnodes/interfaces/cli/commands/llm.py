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
from agentnodes.foundation.config import paths as _paths
from agentnodes.foundation.llm.config import _try_load_dotenv as _load_dotenv

# **2026-11-16**：原先这里有一句模块级 ``_load_dotenv()``。它的唯一作用点是
# ``_key_is_set()``（直接读 os.environ 判断 provider 的 key 是否可用）。
# 模块导入副作用不可见，已改为在 ``_key_is_set()`` 内部显式调用
# （``_try_load_dotenv`` 幂等 + override=False，重复调用安全）。
# 其它走 ``LLMConfig.load()`` 的路径不需要外部 dotenv —— ``load()`` 内部会做。

# M9-2（批 3c）：公式单源到 foundation.config.paths；旧常量名保留作测试锚。
QUANTNODES_DIR = _paths.data_dir()
LLM_JSON_PATH = _paths.llm_json_path()
DOTENV_PATH = _paths.dotenv_path()


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
    """该 provider 的 API key 是否可用（进程 env 或 .env 里任一有值）。

    **2026-11-16**：这里**直接读 os.environ**，所以必须保证 dotenv 已载入，
    否则「已写进 ``~/.agentnodes/.env`` 的 key」会被显示成未配置。
    原实现靠模块级 ``_load_dotenv()`` 隐式保证；改为此处显式确保
    （``_try_load_dotenv`` 幂等且 override=False，重复调用安全）。
    这样依赖关系在调用点可见，而不是藏在模块导入副作用里。
    """
    _load_dotenv()  # 幂等；保证 .env 里的 key 对下面的 os.environ 可见
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


def reload_user_dotenv() -> None:
    """Load the wizard-written user dotenv into ``os.environ`` (override=True).

    M9-4（批 3c）：``cli/__main__.py``（``_build_llm_client``）、
    ``cli/__init__.py``（``cmd_run_onboarding``）、
    ``cli/_auto_onboard.py``（``_maybe_run_onboarding``）三处各自的
    ``load_dotenv(_QUANTNODES_DOTENV_PATH, override=True)`` 收敛到此单点。

    **override=True 是显式裁决保留的语义，不是疏漏**，与 R-006 的服务
    路径（``foundation.llm.config._try_load_dotenv``，override=False，
    「显式 env > 文件」）**有意不同**：本函数的调用点全部位于「向导刚
    把密钥写进 .env」之后——override=True 的语义是「向导写盘后立即让
    密钥可见」。若沿用 override=False，进程里残留的旧 env 值会遮住刚
    写盘的新密钥，向导完成后的首次 LLM 调用直接失败。两条路径的裁决：
    服务路径用户显式 export 赢；向导路径刚写盘的向导结果赢。

    Best-effort：python-dotenv 缺失或加载失败时静默跳过（与旧三处的
    try/except 行为一致）。幂等：重复调用安全（后一次覆盖前一次）。
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    try:
        # 读「向导实际写盘的文件」：onboard 的 PEP 562 动态访问器（测试
        # monkeypatch 仍生效）。默认与 paths.dotenv_path() 相同
        # （~/.agentnodes/.env）；AGENTNODES_DATA_DIR 设置时 onboard 的
        # 公式仍指向 ~/.agentnodes/.env——重载必须跟向导的写路径一致，
        # 所以不用 paths.dotenv_path()（M6 收敛 onboard 路径公式时同步）。
        from agentnodes.interfaces.cli.onboard import _QUANTNODES_DOTENV_PATH
        load_dotenv(_QUANTNODES_DOTENV_PATH, override=True)
    except Exception:                          # noqa: BLE001
        pass


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
        print(f"# 来源: llm.json profiles.{active}（env AGENTNODES_LLM_PROFILE / --llm-profile 可覆盖）")
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
