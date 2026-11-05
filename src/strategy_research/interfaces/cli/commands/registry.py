"""CLI command registration framework (Phase 2.2).

Each command module exposes a ``register(subparsers)`` function that adds
its argument parser and binds a handler via ``set_defaults(handler=cmd_X)``.

The dispatcher at the end of ``cli.main()`` simply does::

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)

This replaces the 100+ lines of ``elif args.command == "..."`` branches.

Public API
----------
    @cli_command("name", help="...")
        Decorator. Marks a function as a CLI command handler. The decorated
        function is automatically registered into the global command table.
        Use ``register_commands(subparsers)`` to wire everything into an
        argparse parser.

    register_command(name, register_fn, handler_fn)
        Manual registration. Used by subcommands that need to add their own
        argparse subparsers before binding the handler.

    dispatch(args, parser)
        Look up the handler from ``args.handler`` (set by argparse
        ``set_defaults``) and invoke it. Returns 0 on no handler.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── Registry state ──────────────────────────────────────────────────


# Maps command name → handler function.
_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {}

# Maps command name → register function (called by ``wire_commands``).
_REGISTRARS: dict[str, Callable[[argparse._SubParsersAction], None]] = {}


# ── Public decorators ──────────────────────────────────────────────


def register_command(
    name: str,
    register_fn: Callable[[argparse._SubParsersAction], None],
    handler_fn: Callable[[argparse.Namespace], int],
) -> None:
    """Manually register a command's setup + handler.

    Args:
        name: Command name as it appears in argv (e.g. ``"init"``).
        register_fn: Callable that takes a ``subparsers`` action and adds
            its argparse subparser. Usually ``_add_init_parser(subparsers)``.
        handler_fn: The function invoked when this command is selected.
    """
    _HANDLERS[name] = handler_fn
    _REGISTRARS[name] = register_fn


def cli_command(
    name: str,
    *,
    help: str,
    description: str | None = None,
    parents: list[argparse.ArgumentParser] | None = None,
    add: Callable[[argparse.ArgumentParser], Any] | None = None,
) -> Callable[[Callable[[argparse.Namespace], int]], Callable[[argparse.Namespace], int]]:
    """Decorator that registers a command handler.

    The decorated function becomes both the argparse handler (via
    ``set_defaults``) and is added to the dispatcher table.

    Args:
        name: Command name.
        help: Short help text.
        description: Optional long description (defaults to help).
        parents: Optional parent argument parsers (for shared flags).
        add: Optional callable that adds custom arguments to the subparser.
            Receives the freshly-created ``argparse.ArgumentParser``.

    Example::

        @cli_command("status", help="查看工作区状态",
                    add=lambda p: p.add_argument("path", nargs="?", default="."))
        def cmd_status(args):
            ...
    """
    def decorator(
        handler_fn: Callable[[argparse.Namespace], int],
    ) -> Callable[[argparse.Namespace], int]:
        def register(subparsers: argparse._SubParsersAction) -> None:
            parser = subparsers.add_parser(
                name,
                help=help,
                description=description or help,
                parents=parents or [],
            )
            if add is not None:
                add(parser)
            parser.set_defaults(handler=handler_fn, command=name)

        register_command(name, register, handler_fn)
        return handler_fn

    return decorator


# ── Wiring + dispatch ──────────────────────────────────────────────


def wire_commands(
    subparsers: argparse._SubParsersAction,
    *,
    extra_registrars: list[Callable[[argparse._SubParsersAction], None]] | None = None,
) -> None:
    """Add every registered command to the given subparsers action.

    Also invokes ``extra_registrars`` (used for subcommands that haven't been
    migrated yet, e.g. legacy modules that still call ``subparsers.add_parser``
    directly with no decorator).
    """
    # Call each registered registrar in deterministic order (sorted by name
    # for stability; helps test reproducibility).
    for name in sorted(_REGISTRARS):
        try:
            _REGISTRARS[name](subparsers)
        except Exception:
            logger.exception("Failed to register command %s", name)
    for reg in extra_registrars or []:
        try:
            reg(subparsers)
        except Exception:
            logger.exception("Failed to register legacy command")


def dispatch(
    args: argparse.Namespace,
    *,
    parser: argparse.ArgumentParser | None = None,
) -> int:
    """Dispatch to the handler registered for ``args.command``.

    If no handler is set (e.g. ``--help`` was passed, or an unknown command
    was given), prints parser help and returns 0.

    Returns:
        Process exit code (whatever the handler returned).
    """
    handler: Callable[[argparse.Namespace], int] | None = getattr(args, "handler", None)
    if handler is None:
        if parser is not None:
            parser.print_help()
        return 0
    return handler(args)


def reset_registry() -> None:
    """Clear the registry (for tests).

    **同时清空 ``_BUILTIN_LOADED``**：否则后续 ``ensure_builtin_commands()``
    会认为内置命令已注册过而跳过，registry 永久为空。
    2026-11-05 实测：``test_cli_registry.py`` 的 ``test_reset_clears``
    调用本函数后，同进程内的 ``test_cli_build_parser.py`` 拿到的
    registry 是空的（``llm`` / ``init`` 全部 0 次）。
    """
    _HANDLERS.clear()
    _REGISTRARS.clear()
    _BUILTIN_LOADED.clear()


#: 已执行过 side-effect 注册的模块名。``reset_registry()`` 会清空它，
#: 使 ``ensure_builtin_commands()`` 可以重新加载。
_BUILTIN_LOADED: set[str] = set()

#: 内置命令模块（import 即通过 ``@cli_command`` 完成注册）。
_BUILTIN_MODULES = (
    "strategy_research.interfaces.cli.commands.core_commands",
    "strategy_research.interfaces.cli.commands.llm",
)


def ensure_builtin_commands() -> None:
    """确保内置命令已注册（幂等）。

    **为什么需要**：注册依赖 import 副作用，而 Python 的模块缓存意味着
    ``import`` 只执行一次。``reset_registry()`` 清空注册表后，模块不会
    重新执行——registry 就永久空了。测试里串行跑多个文件时必然踩到。

    这里按 ``_BUILTIN_LOADED`` 逐个 reload（不是「registry 空才 reload」：
    一个模块的 reload 会让 registry 非空，从而挡住后续模块的补注册）。
    """
    import importlib

    for mod_name in _BUILTIN_MODULES:
        if mod_name in _BUILTIN_LOADED:
            continue
        try:
            mod = importlib.import_module(mod_name)
            importlib.reload(mod)
            _BUILTIN_LOADED.add(mod_name)
        except Exception:  # noqa: BLE001 —— 单个模块失败不阻断其余
            logger.exception("Failed to (re)load builtin commands: %s", mod_name)


def registered_commands() -> list[str]:
    """Return the list of registered command names (for tests / introspection)."""
    return sorted(_HANDLERS)


__all__ = [
    "cli_command",
    "dispatch",
    "ensure_builtin_commands",
    "register_command",
    "registered_commands",
    "reset_registry",
    "wire_commands",
]
