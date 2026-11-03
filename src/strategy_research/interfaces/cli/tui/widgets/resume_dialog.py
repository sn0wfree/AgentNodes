"""``ResumeOrNewModal`` — pre-TUI prompt: (r)esume / (n)ew.

Pushes a :class:`textual.screen.ModalScreen` the first time the app
boots. The user can pick ``r``/``resume``/``y``/``yes`` to reload
the most recent session (loading its history into the app
context), ``n``/``new``/(empty) to start fresh, or type any other
content which we route through as a pending prompt after the modal
dismisses.

Mirrors the vibe-trading ``(r)esume / (n)ew`` / ``(default: new)``
prompt verbatim, but rendered via Textual widgets rather than an
inline ``input()``.
"""
from __future__ import annotations

from typing import Optional

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class ResumeOrNewModal(ModalScreen[tuple[bool, Optional[str]]]):
    """Modal that asks ``(r) / (n) / <text>`` and returns a tuple.

    Return value: ``(is_resume, pending_input)``.
    * ``(True, None)``  — resume the latest session.
    * ``(False, None)`` — start a fresh session.
    * ``(False, text)`` — start fresh and route ``text`` as the
      first prompt.
    """

    DEFAULT_CSS = """
    ResumeOrNewModal {
        align: center middle;
    }
    ResumeOrNewModal > Vertical {
        width: 60;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    ResumeOrNewModal Label.title {
        text-style: bold;
        color: $primary;
    }
    ResumeOrNewModal Label.hint {
        color: $text-muted;
    }
    ResumeOrNewModal Input {
        margin-top: 1;
    }
    ResumeOrNewModal Horizontal.buttons {
        margin-top: 1;
        height: 3;
        align-vertical: middle;
    }
    ResumeOrNewModal Button {
        margin-right: 1;
    }
    """

    BINDINGS = [
        # Escape is treated as "n" by the consumer (ModalScreen already
        # pops the screen on escape; we capture the empty input result
        # via the dismiss path).
    ]

    def __init__(self, latest_session: Optional[str] = None) -> None:
        super().__init__()
        # Title shown in the modal. Default "untitled" so we don't
        # render an empty paren if no prior session exists.
        self._latest_title = (latest_session or "untitled")[:60]

    def compose(self) -> object:
        from textual.widgets import Container

        yield Container(
            Vertical(
                Label("Strategy-Research", classes="title"),
                Static(
                    f"Resume last session ({self._latest_title})? "
                    "(r)esume / (n)ew / (text) (default: new)",
                    classes="hint",
                ),
                Input(placeholder="r, n, or your first message…", id="choice"),
                Horizontal(
                    Button("Resume", id="btn-resume", variant="success"),
                    Button("New", id="btn-new", variant="default"),
                    classes="buttons",
                ),
            ),
        )

    def on_mount(self) -> None:
        # Focus the input bar so the user can press r/n immediately.
        self.query_one(Input).focus()

    # ----------------------------------------------------------------- input

    def on_input_submitted(self, event: Input.Submitted) -> None:
        choice = (event.value or "").strip().lower()
        self._resolve(choice)

    # ----------------------------------------------------------------- buttons

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-resume":
            self._resolve("r")
        elif event.button.id == "btn-new":
            self._resolve("n")

    # ----------------------------------------------------------------- helpers

    def _resolve(self, choice: str) -> None:
        if choice in {"r", "resume", "y", "yes"}:
            self.dismiss((True, None))
        elif choice in {"", "n", "new", "no"}:
            self.dismiss((False, None))
        else:
            self.dismiss((False, choice))


__all__ = ["ResumeOrNewModal"]
