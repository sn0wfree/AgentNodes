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
    """Clear the registry (for tests)."""
    _HANDLERS.clear()
    _REGISTRARS.clear()


def registered_commands() -> list[str]:
    """Return the list of registered command names (for tests / introspection)."""
    return sorted(_HANDLERS)


__all__ = [
    "cli_command",
    "dispatch",
    "register_command",
    "registered_commands",
    "reset_registry",
    "wire_commands",
]
