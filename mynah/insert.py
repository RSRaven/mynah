"""Insert transcribed text at the cursor.

Default: clipboard-paste (set clipboard → simulate the paste chord → restore the prior
clipboard). Most reliable in Windows Terminal and for Unicode/multilingual text.
Optional: "type" mode that simulates keystrokes.
"""

from __future__ import annotations

import time

import pyperclip
from pynput.keyboard import Controller, KeyCode

from .platform_layer import paste_modifier

_keyboard = Controller()


def _press_paste() -> None:
    mod = paste_modifier()
    with _keyboard.pressed(mod):
        _keyboard.press(KeyCode.from_char("v"))
        _keyboard.release(KeyCode.from_char("v"))


def insert_text(
    text: str,
    method: str = "paste",
    restore_clipboard: bool = True,
    paste_settle: float = 0.12,
) -> None:
    """Insert `text` at the focused cursor.

    method="paste" uses the clipboard; method="type" simulates keystrokes.
    """
    if not text:
        return

    if method == "type":
        _keyboard.type(text)
        return

    previous = None
    if restore_clipboard:
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None

    pyperclip.copy(text)
    # Tiny settle so the clipboard write lands before we paste.
    time.sleep(0.03)
    _press_paste()

    if restore_clipboard:
        # Wait for the target app to consume the paste, then restore.
        time.sleep(paste_settle)
        try:
            pyperclip.copy(previous if previous is not None else "")
        except Exception:
            pass
