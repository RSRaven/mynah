"""System-tray UI (Phase 2): a pystray icon whose colour reflects the live status
plus a menu for switching model/language, toggling sound, and opening config.

The tray is deliberately "dumb": it renders state and forwards clicks. All the real
work (loading models, persisting config, etc.) lives in `mynah.app.MynahApp`,
which this module talks to through a small, documented set of methods. Keeping the UI
this thin is what lets the same orchestration drive a future macOS menu-bar (pystray
already targets it) without rewriting the logic.

The icon is a per-state "VT" waveform badge bundled under ``assets/`` (one colour per
status, so the tray doubles as the "visual cue"). If an asset is
missing we fall back to a VT monogram drawn with Pillow, so the app never lacks an icon.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

import pystray
from PIL import Image, ImageDraw

from .controller import IDLE, RECORDING, TRANSCRIBING

LOADING = "loading"
ERROR = "error"

_ASSETS = Path(__file__).parent / "assets"
_ICON_PX = 64  # tray icons are tiny; 64 is plenty and matches the drawn fallback

# State → (icon tint, short label shown in the tooltip).
_STATES = {
    IDLE: ((76, 110, 245), "idle"),
    RECORDING: ((225, 29, 72), "recording"),
    TRANSCRIBING: ((245, 158, 11), "transcribing"),
    LOADING: ((124, 58, 237), "loading model"),
    ERROR: ((153, 27, 27), "error"),
}


class AppFacade(Protocol):
    """What the tray needs from the app. `mynah.app.MynahApp` implements it.

    (Model/language/hotkey/sound are driven from the Settings window, not the tray, so
    they're not part of this surface.)
    """

    version: str

    def info_text(self) -> str: ...
    def is_capturing(self) -> bool: ...
    def cancel_hotkey_capture(self) -> None: ...
    def open_settings(self) -> None: ...
    def open_config(self) -> None: ...
    def open_config_dir(self) -> None: ...
    def quit(self) -> None: ...


def _vt_image(color: tuple[int, int, int]) -> Image.Image:
    """Draw the Mynah "VT" monogram in `color`: a T crossbar with a V converging
    below from its ends. Supersampled then downscaled for clean, rounded edges."""
    scale = 4
    s = 64 * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = color + (255,)
    w = int(s * 0.16)  # stroke width

    top, bot = s * 0.20, s * 0.82
    lx, rx, mid = s * 0.16, s * 0.84, s * 0.50

    def stroke(p0, p1):
        d.line([p0, p1], fill=fill, width=w)
        r = w / 2  # round the joints/caps
        for cx, cy in (p0, p1):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)

    stroke((lx, top), (rx, top))         # T crossbar
    stroke((lx, top), (mid, bot))        # V left arm
    stroke((rx, top), (mid, bot))        # V right arm
    stroke((mid, top), (mid, top + s * 0.16))  # short T stem

    return img.resize((64, 64), Image.LANCZOS)


def _state_image(state: str, color: tuple[int, int, int]) -> Image.Image:
    """The bundled waveform-VT badge for `state`, or the drawn VT mark as a fallback."""
    path = _ASSETS / f"tray-{state}.png"
    if path.is_file():
        try:
            return Image.open(path).convert("RGBA").resize((_ICON_PX, _ICON_PX), Image.LANCZOS)
        except Exception:
            pass
    return _vt_image(color)


class Tray:
    def __init__(self, app: AppFacade) -> None:
        self.app = app
        self._images = {state: _state_image(state, color) for state, (color, _) in _STATES.items()}
        self._status = IDLE
        self._icon = pystray.Icon(
            "mynah",
            icon=self._images[IDLE],
            title=self._title(IDLE),
            menu=self._build_menu(),
        )

    # --- rendering ----------------------------------------------------------

    def _title(self, status: str) -> str:
        label = _STATES.get(status, _STATES[IDLE])[1]
        return f"Mynah {self.app.version} — {label}"

    def _build_menu(self) -> pystray.Menu:
        Item, Menu = pystray.MenuItem, pystray.Menu

        # The tray stays minimal: model/language/hotkey/sound all live in the Settings
        # window (a native tray menu closes after every click, which made changing
        # several things tedious). Left-click the icon opens Settings (the default item).
        return Menu(
            # Shown only while waiting for a new hotkey — a no-toast way to back out.
            Item(
                "Cancel hotkey change (or press Esc)",
                (lambda _i, _it: self.app.cancel_hotkey_capture()),
                visible=(lambda _it: self.app.is_capturing()),
            ),
            Menu.SEPARATOR,
            Item(lambda _it: self.app.info_text(), None, enabled=False),
            Menu.SEPARATOR,
            Item("Settings…", lambda _i, _it: self.app.open_settings(), default=True),
            Item("Open config file…", lambda _i, _it: self.app.open_config()),
            Item("Open config folder…", lambda _i, _it: self.app.open_config_dir()),
            Menu.SEPARATOR,
            Item("Quit", lambda _i, _it: self.app.quit()),
        )

    # --- live updates (safe to call from worker/control threads) ------------

    def set_status(self, status: str) -> None:
        self._status = status
        img = self._images.get(status, self._images[IDLE])
        try:
            self._icon.icon = img
            self._icon.title = self._title(status)
        except Exception:
            pass  # icon not running yet (or already stopped)

    def refresh_menu(self) -> None:
        """Re-render checkmarks/labels after a menu-driven change."""
        try:
            self._icon.update_menu()
        except Exception:
            pass

    def set_capturing(self, on: bool) -> None:
        """Show a 'press a combo' prompt while waiting for a new hotkey."""
        try:
            if on:
                self._icon.icon = self._images[LOADING]
                self._icon.title = f"Mynah {self.app.version} — press a hotkey… (Esc cancels)"
            else:
                self.set_status(self._status)  # restore whatever the live status is
        except Exception:
            pass

    def notify(self, message: str, title: str = "Mynah") -> None:
        """Show a transient OS notification (used for hotkey-capture feedback)."""
        try:
            self._icon.notify(message, title)
        except Exception:
            pass

    # --- lifecycle ----------------------------------------------------------

    def run(self) -> None:
        """Block on the tray event loop (must run on the main thread)."""
        self._icon.run()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            pass


def tray_available() -> bool:
    """True if a tray backend looks usable (lets the app fall back to headless)."""
    if sys.platform not in ("win32", "darwin") and not _has_linux_tray():
        return False
    return True


def _has_linux_tray() -> bool:  # pragma: no cover - Linux only
    import os

    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
