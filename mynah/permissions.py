"""macOS TCC permissions — detect + guide (no-op on every other OS).

Mynah's core loop needs three macOS privacy grants, and without them the app *silently* does
nothing (no error, no crash — the keystroke just never fires / the mic returns silence):

- **Microphone** — to capture audio. Auto-prompts the first time PortAudio opens the input
  stream *if* the bundle carries ``NSMicrophoneUsageDescription`` (see the .app Info.plist).
- **Input Monitoring** — pynput's global key listener (a CGEventTap) needs it to *see* the
  push-to-talk hotkey. Usually no auto-prompt → the user must enable it by hand.
- **Accessibility** — pynput's ``Controller`` needs it to *send* the Cmd+V keystroke.

Permissions are bound to the app's code identity (bundle id / signature / path), so the .app is
**ad-hoc signed** (``codesign -s -``) to keep grants stable across rebuilds — otherwise every
rebuild looks like a new app and the user has to re-grant.

This module never raises and never blocks: it reports what it can detect and hands back the
System Settings deep links so the caller can surface a clear panel instead of a mystery
no-op. Detection uses pyobjc (Quartz + ApplicationServices); if those aren't importable it
degrades to "unknown" rather than failing.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

# System Settings deep links (Privacy & Security → …). `open` these to drop the user on the
# exact pane. The bundle id form works on Ventura+; the legacy anchor form is the fallback.
SETTINGS_URLS = {
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "input_monitoring":
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    "accessibility":
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
}

# state ∈ {"granted", "denied", "unknown"}. "unknown" = couldn't detect (no pyobjc, or an API
# that has no preflight) — treat as "probably needs a look" in the UI, never as a hard fail.
_GRANTED = "granted"
_DENIED = "denied"
_UNKNOWN = "unknown"


@dataclass
class Permission:
    key: str            # microphone | input_monitoring | accessibility
    label: str          # human name
    state: str          # granted | denied | unknown
    why: str            # what breaks without it
    settings_url: str   # deep link to the right System Settings pane

    @property
    def granted(self) -> bool:
        return self.state == _GRANTED

    @property
    def needs_attention(self) -> bool:
        """True if we should nudge the user (denied, or undetectable so worth checking)."""
        return self.state != _GRANTED


def is_macos() -> bool:
    return sys.platform == "darwin"


# --- individual probes (all best-effort; never raise) -------------------------------------

def _accessibility_state() -> str:
    """Accessibility (send keystrokes). ``AXIsProcessTrusted`` is an exact, non-prompting
    check."""
    try:
        from ApplicationServices import AXIsProcessTrusted

        return _GRANTED if AXIsProcessTrusted() else _DENIED
    except Exception:
        return _UNKNOWN


def _input_monitoring_state() -> str:
    """Input Monitoring (see global key events). ``CGPreflightListenEventAccess`` reports the
    grant without prompting (macOS 10.15+)."""
    try:
        import Quartz

        fn = getattr(Quartz, "CGPreflightListenEventAccess", None)
        if fn is None:
            return _UNKNOWN
        return _GRANTED if fn() else _DENIED
    except Exception:
        return _UNKNOWN


def _microphone_state() -> str:
    """Microphone. Prefer AVFoundation's authorization status if that pyobjc framework is
    present; otherwise we can't cheaply preflight it (PortAudio prompts on first open), so
    report ``unknown`` rather than guess."""
    try:
        from AVFoundation import (  # type: ignore
            AVCaptureDevice,
            AVMediaTypeAudio,
        )

        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        # 3 = authorized, 2 = denied, 1 = restricted, 0 = not-determined
        if status == 3:
            return _GRANTED
        if status in (1, 2):
            return _DENIED
        return _UNKNOWN  # not-determined: will prompt on first capture
    except Exception:
        return _UNKNOWN


# --- public API ----------------------------------------------------------------------------

def check_permissions() -> list[Permission]:
    """Current state of the three grants. Empty list off macOS (nothing to check)."""
    if not is_macos():
        return []
    return [
        Permission("microphone", "Microphone", _microphone_state(),
                   "needed to capture your voice", SETTINGS_URLS["microphone"]),
        Permission("input_monitoring", "Input Monitoring", _input_monitoring_state(),
                   "needed to detect the push-to-talk hotkey",
                   SETTINGS_URLS["input_monitoring"]),
        Permission("accessibility", "Accessibility", _accessibility_state(),
                   "needed to paste the transcribed text (Cmd+V)",
                   SETTINGS_URLS["accessibility"]),
    ]


def missing_permissions() -> list[Permission]:
    """Just the grants that aren't confirmed granted (denied or undetectable)."""
    return [p for p in check_permissions() if p.needs_attention]


def request_accessibility_prompt() -> bool:
    """Ask macOS to show the Accessibility prompt (one-time, per identity). Returns the current
    trust state. Uses the options form so the system surfaces its add-to-list dialog."""
    if not is_macos():
        return False
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
    except Exception:
        return _accessibility_state() == _GRANTED


def request_input_monitoring_prompt() -> bool:
    """Ask macOS to show the Input Monitoring prompt (one-time). Returns whether it's granted."""
    if not is_macos():
        return False
    try:
        import Quartz

        fn = getattr(Quartz, "CGRequestListenEventAccess", None)
        if fn is not None:
            return bool(fn())
    except Exception:
        pass
    return _input_monitoring_state() == _GRANTED


def open_settings_pane(key: str) -> None:
    """Open System Settings at the privacy pane for ``key`` (microphone | input_monitoring |
    accessibility). No-op off macOS / for an unknown key."""
    if not is_macos():
        return
    url = SETTINGS_URLS.get(key)
    if not url:
        return
    try:
        subprocess.Popen(["open", url])
    except Exception:
        pass


def summary_text() -> str:
    """A short multi-line status block for logs / the console (headless) path."""
    perms = check_permissions()
    if not perms:
        return ""
    lines = ["macOS permissions:"]
    mark = {_GRANTED: "OK ", _DENIED: " X ", _UNKNOWN: " ? "}
    for p in perms:
        lines.append(f"  [{mark.get(p.state, ' ? ')}] {p.label} — {p.why}")
        if p.needs_attention:
            lines.append(f"        grant in: System Settings → Privacy & Security → {p.label}")
    return "\n".join(lines)
