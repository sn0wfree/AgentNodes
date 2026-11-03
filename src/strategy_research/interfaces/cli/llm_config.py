"""Extracted from cli.py — LLM configuration helpers.

Contains:
- _LLM_PARENT (argparse parent parser for --llm-* flags)
- _cli_overrides_from_args
- build_llm_config
- _cmd_llm_list_profiles (always prints "no profile system; edit ~/.quantnodes/llm.json")
"""

from __future__ import annotations

import argparse
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strategy_research.foundation.llm.config import LLMConfig

_LLM_PARENT = argparse.ArgumentParser(
    add_help=False,
    prog="quantnodes-research (LLM flags)",
    description="LLM configuration overrides",
)
_llm_g = _LLM_PARENT.add_argument_group("LLM configuration")
_llm_g.add_argument("--llm-model", default=None, help="覆盖 model")
_llm_g.add_argument("--llm-base-url", default=None, help="覆盖 base_url")
_llm_g.add_argument("--llm-temperature", type=float, default=None,
                    help="覆盖 temperature")
_llm_g.add_argument("--llm-max-tokens", type=int, default=None,
                    help="覆盖 max_tokens")
_llm_g.add_argument("--llm-top-p", type=float, default=None, help="覆盖 top_p")
_llm_g.add_argument("--llm-timeout", type=float, default=None,
                    help="覆盖 timeout_s")
_llm_g.add_argument("--llm-max-retries", type=int, default=None,
                    help="覆盖 max_retries")
_llm_g.add_argument("--llm-seed", type=int, default=None, help="覆盖 seed")
_llm_g.add_argument("--llm-stream", dest="llm_stream",
                    action="store_true", default=None, help="强制流式")
_llm_g.add_argument("--llm-no-stream", dest="llm_stream",
                    action="store_false", help="禁用流式")
_llm_g.add_argument("--llm-profile", default=None,
                    help="单次运行使用 llm.json 中的指定 provider profile（不改文件）")


def _cli_overrides_from_args(args: argparse.Namespace | None) -> dict:
    """Extract --llm-* kwargs from argparse Namespace.

    ``--llm-profile`` is handled separately (sets ``LLM_PROFILE`` env so
    the config's profile resolution picks it up); it is NOT a config key.
    """
    if args is None:
        return {}
    profile = getattr(args, "llm_profile", None)
    if profile:
        os.environ["LLM_PROFILE"] = profile
    out = {}
    for key, value in vars(args).items():
        if key == "llm_profile":
            continue
        if key.startswith("llm_") and value is not None:
            out[key] = value
    return out


def build_llm_config(args: argparse.Namespace | None = None,
                     *, cli_overrides: dict | None = None) -> "LLMConfig":  # noqa: F821
    """Build an LLMConfig from CLI args + 4-layer merge.

    Profile concept was retired in v0.5.0 (config now lives in
    ``~/.quantnodes/llm.json``). For backward compatibility, callers
    passing ``profile=`` should drop the kwarg.

    Args:
        args: argparse Namespace (with --llm-* attributes).
        cli_overrides: Explicit override dict (alternative to args).

    Returns:
        Fully merged LLMConfig.
    """
    from strategy_research.foundation.llm import LLMConfig
    overrides = cli_overrides if cli_overrides is not None else _cli_overrides_from_args(args)
    return LLMConfig.load(cli_overrides=overrides)


def _cmd_llm_list_profiles() -> int:
    """Print a notice pointing users to ``~/.quantnodes/llm.json``.

    The yaml/profile system was retired in v0.5.0. This command is kept
    for backward compatibility with any user muscle memory / scripts.
    """
    from strategy_research.foundation.llm.config import find_llm_config_path
    p = find_llm_config_path()
    print(f"# LLM config now lives at: {p}")
    print("# (profile system retired; use `quantnodes-research init` to reconfigure)")
    return 0
