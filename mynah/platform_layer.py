"""Thin per-OS shim: app-data dir + paste keystroke.

Keep every OS-specific bit here so the rest of the app stays platform-neutral. The
MVP targets Windows; the macOS/Linux branches are filled in so later ports only touch
this file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "mynah"


def app_data_dir() -> Path:
    """Per-OS application data directory for **config + logs** (created on demand).

    On Windows this is roaming ``%APPDATA%`` — fine for the tiny ``config.toml`` and log.
    Large downloaded runtime (engine packs) lives in :func:`runtime_data_dir` instead, which
    is *non*-roaming so GB-sized artifacts don't sync across domain machines."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def runtime_data_dir() -> Path:
    """Per-OS dir for **large downloaded runtime** — the whisper.cpp engine packs.

    Deliberately the *local* (non-roaming) app dir: ``%LOCALAPPDATA%`` on Windows,
    ``~/.local/share`` (XDG data) on Linux, Application Support on macOS. Roaming profiles
    sync ``%APPDATA%`` across domain PCs — syncing the engine packs (and, if a user drops them
    here, models) is exactly what we want to avoid. Overridable with ``MYNAH_DATA_DIR`` (for
    tests / portable installs). Models themselves live in the shared Hugging Face cache, not
    here."""
    override = os.environ.get("MYNAH_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def config_path() -> Path:
    """Default config file location."""
    return app_data_dir() / "config.toml"


def log_path() -> Path:
    """Where a windowed/frozen build redirects stdout+stderr (no console there)."""
    return app_data_dir() / "mynah.log"


def paste_modifier():
    """The modifier key used for paste: Cmd on macOS, Ctrl elsewhere."""
    from pynput.keyboard import Key

    return Key.cmd if sys.platform == "darwin" else Key.ctrl


# --- "run at login" ----------------------------------------------------------
#
# Windows: a per-user ``HKCU\...\Run`` registry value. The Inno installer can set the same
# value at install time; this lets the Settings toggle flip it live and survive reboot.
# macOS: a per-user **LaunchAgent** plist in ``~/Library/LaunchAgents``. Linux (.desktop
# autostart) is a later phase — stubbed to a no-op there.

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "Mynah"

# macOS LaunchAgent (per-user; runs on login, survives reboot).
_LAUNCH_AGENT_LABEL = "com.mynah.mynah"


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCH_AGENT_LABEL}.plist"


def autostart_command() -> str | None:
    """The command to relaunch Mynah at login. Prefer the frozen build (PyInstaller); else
    fall back to a dev/pip launcher so an editable install can still autostart."""
    if sys.platform == "darwin":
        # Frozen: the bundle's ``MacOS/Mynah`` executable. Dev: ``python -m mynah`` with the
        # current interpreter (a .venv python keeps the deps on import).
        if getattr(sys, "frozen", False):
            return sys.executable
        return f'{sys.executable} -m mynah'
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    launcher = str(pyw) if pyw.exists() else sys.executable
    return f'"{launcher}" -m mynah'


def _autostart_program_args() -> list[str]:
    """The LaunchAgent ``ProgramArguments`` array (argv form, no shell quoting)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "mynah"]


def _launch_agent_plist() -> str:
    args = "".join(f"    <string>{a}</string>\n" for a in _autostart_program_args())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f'  <key>Label</key><string>{_LAUNCH_AGENT_LABEL}</string>\n'
        '  <key>ProgramArguments</key><array>\n'
        f'{args}'
        '  </array>\n'
        '  <key>RunAtLoad</key><true/>\n'
        '  <key>ProcessType</key><string>Interactive</string>\n'
        '</dict></plist>\n'
    )


def is_run_at_login() -> bool:
    if sys.platform == "darwin":
        return _launch_agent_path().is_file()
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _RUN_VALUE)
            return bool(val)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _set_run_at_login_darwin(enabled: bool) -> bool:
    import subprocess

    plist = _launch_agent_path()
    try:
        if enabled:
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(_launch_agent_plist(), encoding="utf-8")
            # Load it now so it's active this session too (best-effort; ignore if already
            # loaded or launchctl is unhappy — the plist alone makes it run next login).
            subprocess.run(["launchctl", "load", "-w", str(plist)],
                           capture_output=True, timeout=10)
            return True
        if plist.is_file():
            subprocess.run(["launchctl", "unload", "-w", str(plist)],
                           capture_output=True, timeout=10)
            plist.unlink(missing_ok=True)
        return False
    except OSError as e:
        print(f"! couldn't update run-at-login: {e}")
        return is_run_at_login()


def set_run_at_login(enabled: bool) -> bool:
    """Enable/disable launch at login. Returns the resulting state. No-op (False) on Linux."""
    if sys.platform == "darwin":
        return _set_run_at_login_darwin(enabled)
    if sys.platform != "win32":
        return False
    try:
        import winreg

        if enabled:
            cmd = autostart_command()
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                winreg.SetValueEx(k, _RUN_VALUE, 0, winreg.REG_SZ, cmd)
            return True
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, _RUN_VALUE)
            except FileNotFoundError:
                pass
        return False
    except OSError as e:
        print(f"! couldn't update run-at-login: {e}")
        return is_run_at_login()
