"""Global push-to-talk hotkey (hold to record, release to stop) via pynput.

We track the set of currently-pressed (canonicalized) keys. When the configured
combo becomes fully held, fire `on_activate`; when any key of the combo is released,
fire `on_deactivate`. This is the standard, robust PTT pattern and works for a single
key or a chord like ``ctrl+space``.
"""

from __future__ import annotations

import os
import threading
from typing import Callable

from pynput import keyboard

_DEBUG = bool(os.environ.get("MYNAH_DEBUG_KEYS"))

_MODIFIERS = {
    "ctrl": keyboard.Key.ctrl,
    "control": keyboard.Key.ctrl,
    "alt": keyboard.Key.alt,
    "altgr": keyboard.Key.alt_gr,
    "shift": keyboard.Key.shift,
    "cmd": keyboard.Key.cmd,
    "win": keyboard.Key.cmd,
    "super": keyboard.Key.cmd,
}

_NAMED = {
    "space": keyboard.Key.space,
    "tab": keyboard.Key.tab,
    "enter": keyboard.Key.enter,
    "return": keyboard.Key.enter,
    "esc": keyboard.Key.esc,
    "escape": keyboard.Key.esc,
    "capslock": keyboard.Key.caps_lock,
    "scrolllock": getattr(keyboard.Key, "scroll_lock", None),
    "pause": getattr(keyboard.Key, "pause", None),
    "insert": getattr(keyboard.Key, "insert", None),
    "home": keyboard.Key.home,
    "end": keyboard.Key.end,
    "pageup": keyboard.Key.page_up,
    "pagedown": keyboard.Key.page_down,
}


def parse_hotkey(spec: str) -> set:
    """Parse a combo like ``ctrl+space`` or ``f9`` into a set of canonical keys."""
    keys: set = set()
    tokens = [t for t in spec.lower().replace(" ", "").split("+") if t]
    if not tokens:
        raise ValueError("Empty hotkey spec.")
    for tok in tokens:
        if tok in _MODIFIERS:
            keys.add(_MODIFIERS[tok])
        elif tok in _NAMED and _NAMED[tok] is not None:
            keys.add(_NAMED[tok])
        elif hasattr(keyboard.Key, tok):  # f1..f24, etc.
            keys.add(getattr(keyboard.Key, tok))
        elif len(tok) == 1:
            keys.add(keyboard.KeyCode.from_char(tok))
        else:
            raise ValueError(f"Unrecognized hotkey token: {tok!r} (in {spec!r})")
    return keys


class PushToTalkHotkey:
    """Listen for one or more held hotkey combos and drive start/stop callbacks.

    `specs` is a single combo ("f9") or a list (["f9", "ctrl+space"]); holding *any*
    of them starts recording, and releasing a key of the combo that's currently held
    stops it.
    """

    def __init__(
        self,
        specs: str | list[str],
        on_activate: Callable[[], None],
        on_deactivate: Callable[[], None],
    ) -> None:
        if isinstance(specs, str):
            specs = [specs]
        self.specs = [s for s in specs if s]
        if not self.specs:
            raise ValueError("No push-to-talk hotkey configured.")
        self._expected_sets = [parse_hotkey(s) for s in self.specs]
        self._on_activate = on_activate
        self._on_deactivate = on_deactivate
        self._pressed: set = set()
        self._active = False
        self._active_set: set | None = None  # which combo is currently holding it open
        self._listener: keyboard.Listener | None = None

    @property
    def description(self) -> str:
        return " or ".join(self.specs)

    def _canon(self, key):
        if self._listener is not None:
            try:
                return self._listener.canonical(key)
            except Exception:
                pass
        return key

    def _on_press(self, key) -> None:
        k = self._canon(key)
        self._pressed.add(k)
        if _DEBUG:
            print(f"[keys] press {key!r} -> {k!r}")
        if not self._active:
            for combo in self._expected_sets:
                if combo <= self._pressed:
                    self._active = True
                    self._active_set = combo
                    try:
                        self._on_activate()
                    except Exception as e:  # never let a callback kill the listener
                        print(f"! on_activate error: {e}")
                    break

    def _on_release(self, key) -> None:
        k = self._canon(key)
        self._pressed.discard(k)
        if _DEBUG:
            print(f"[keys] release {key!r} -> {k!r}")
        if self._active and self._active_set is not None and k in self._active_set:
            self._active = False
            self._active_set = None
            try:
                self._on_deactivate()
            except Exception as e:
                print(f"! on_deactivate error: {e}")

    def start(self) -> None:
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        # Canonicalize each combo with the SAME function applied to pressed keys so the
        # two compare equal. pynput's canonical() maps e.g. Key.f9 -> KeyCode(120) and
        # Key.ctrl_l -> Key.ctrl; without this the subset test never matches for
        # function/named keys (verified: canonical(Key.f9) != Key.f9).
        self._expected_sets = [{self._canon(k) for k in s} for s in self._expected_sets]
        if _DEBUG:
            print(f"[keys] expected (canonical): {self._expected_sets!r}")
        self._listener.start()

    def join(self) -> None:
        if self._listener is not None:
            self._listener.join()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


class ToggleHotkey:
    """Switch-style hotkey: fire `on_toggle` once on each press of the combo.

    Reuses PushToTalkHotkey's edge detection — the "activate" edge (combo fully
    pressed) calls `on_toggle`; the release does nothing — so each tap of the key
    flips recording on/off. Holding the key does not repeat; you must release and
    press again, which is exactly the on/off behaviour we want.
    """

    def __init__(self, specs: str | list[str], on_toggle: Callable[[], None]) -> None:
        self._hk = PushToTalkHotkey(specs, on_toggle, lambda: None)

    @property
    def description(self) -> str:
        return self._hk.description

    def start(self) -> None:
        self._hk.start()

    def stop(self) -> None:
        self._hk.stop()


# --- capturing a new hotkey from the keyboard (tray "Change…") --------------

# pynput key -> the token parse_hotkey() understands (the inverse of the maps above).
_MOD_TOKENS = {
    keyboard.Key.ctrl: "ctrl", keyboard.Key.ctrl_l: "ctrl", keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.alt: "alt", keyboard.Key.alt_l: "alt", keyboard.Key.alt_r: "alt",
    keyboard.Key.shift: "shift", keyboard.Key.shift_l: "shift", keyboard.Key.shift_r: "shift",
    keyboard.Key.cmd: "cmd", keyboard.Key.cmd_l: "cmd", keyboard.Key.cmd_r: "cmd",
}
if getattr(keyboard.Key, "alt_gr", None) is not None:
    _MOD_TOKENS[keyboard.Key.alt_gr] = "altgr"

# pynput's Key.name vs the spelling parse_hotkey() expects.
_NAME_FIXUPS = {
    "page_up": "pageup", "page_down": "pagedown",
    "caps_lock": "capslock", "scroll_lock": "scrolllock",
}
_MOD_ORDER = ["ctrl", "alt", "altgr", "shift", "cmd"]


def _key_to_token(key) -> str | None:
    """Best-effort inverse of parse_hotkey for one pressed key (None if unmappable)."""
    tok = _MOD_TOKENS.get(key)
    if tok:
        return tok
    if isinstance(key, keyboard.KeyCode):
        ch = key.char
        if ch and len(ch) == 1 and ch.isprintable() and not ch.isspace():
            return ch.lower()
        # Ctrl+letter often arrives as a control char; recover it from the virtual key.
        vk = getattr(key, "vk", None)
        if vk is not None:
            if 0x41 <= vk <= 0x5A:   # A-Z
                return chr(vk).lower()
            if 0x30 <= vk <= 0x39:   # 0-9
                return chr(vk)
        return None
    name = getattr(key, "name", None)
    if name:
        return _NAME_FIXUPS.get(name, name)
    return None


def _join_tokens(tokens: list[str]) -> str:
    """Modifiers first (canonical order), then the main key(s)."""
    mods = [m for m in _MOD_ORDER if m in tokens]
    seen: set = set()
    others = [t for t in tokens if t not in _MOD_ORDER and not (t in seen or seen.add(t))]
    return "+".join(mods + others)


class HotkeyCapture:
    """Listen once for a held key-combo and report it as a spec string.

    Records the largest set of simultaneously-held keys, then finalizes on the first
    release — so pressing ``ctrl+alt+k`` yields ``"ctrl+alt+k"``. A bare ``esc`` (or the
    timeout) cancels. Callbacks fire on a fresh thread so the caller can re-arm the PTT
    listener without re-entering this one.
    """

    def __init__(
        self,
        on_captured: Callable[[str], None],
        on_cancel: Callable[[], None] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._on_captured = on_captured
        self._on_cancel = on_cancel
        self._timeout = timeout
        self._pressed: list = []
        self._max: list = []
        self._listener: keyboard.Listener | None = None
        self._timer: threading.Timer | None = None
        self._done = False
        self._lock = threading.Lock()

    def start(self) -> None:
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._listener.start()
        self._timer = threading.Timer(self._timeout, self._cancel)
        self._timer.daemon = True
        self._timer.start()

    def _on_press(self, key):
        if key not in self._pressed:
            self._pressed.append(key)
        if len(self._pressed) > len(self._max):
            self._max = list(self._pressed)
        # Finalize as soon as a *non-modifier* key goes down (modifiers are already held
        # by then). Doing this on press — not release — is what makes F10 work: F10 is a
        # Windows menu/system key whose key-*up* is unreliable, so a release-based capture
        # needed two presses. Press-based capture doesn't touch the key-up at all.
        tok = _key_to_token(key)
        if tok is not None and tok not in _MOD_ORDER:
            self._finalize()
            return False  # stop the listener
        return None

    def _on_release(self, key) -> bool:
        # Fallback: a modifier-only combo never triggers the press path above.
        self._finalize()
        return False

    def _finalize(self) -> None:
        tokens = [t for t in (_key_to_token(k) for k in self._max) if t]
        # Esc cancels — even if other keys were held, so a fumbled press still backs out.
        if not tokens or "esc" in tokens:
            self._finish(None)
        else:
            self._finish(_join_tokens(tokens))

    def cancel(self) -> None:
        """Cancel from outside the keyboard (e.g. a tray 'Cancel' click)."""
        self._finish(None)

    def _cancel(self) -> None:
        self._finish(None)

    def _finish(self, spec: str | None) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
        if self._timer is not None:
            self._timer.cancel()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        if spec is None:
            if self._on_cancel is not None:
                threading.Thread(target=self._on_cancel, daemon=True).start()
        else:
            threading.Thread(target=self._on_captured, args=(spec,), daemon=True).start()
