"""Background tray app (Phase 2): the orchestrator that ties the Phase 1 pieces
(transcriber, controller, hotkey) to the system-tray UI.

`MynahApp` owns the lifecycle and implements the small facade the tray calls back
into (switch model, pin language, toggle sound, open config, quit). Slow work — model
loading on a switch — runs on a background thread so the tray menu never freezes.
"""

from __future__ import annotations

import argparse
import copy
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .config import DEFAULTS, update_config_values, write_default_config
from .platform_layer import app_data_dir, config_path

# Every language Whisper transcribes (code → display name), so the picker can offer them
# all. Mirrors whisper.tokenizer.LANGUAGES (kept inline so building the menu needs no heavy
# import). Whisper's code for Ukrainian is "uk"; we badge it "UA" (see _lang_badge) so it
# isn't mistaken for English/United-Kingdom.
WHISPER_LANGUAGES = {
    "en": "English", "zh": "Chinese", "de": "German", "es": "Spanish", "ru": "Russian",
    "ko": "Korean", "fr": "French", "ja": "Japanese", "pt": "Portuguese", "tr": "Turkish",
    "pl": "Polish", "ca": "Catalan", "nl": "Dutch", "ar": "Arabic", "sv": "Swedish",
    "it": "Italian", "id": "Indonesian", "hi": "Hindi", "fi": "Finnish", "vi": "Vietnamese",
    "he": "Hebrew", "uk": "Ukrainian", "el": "Greek", "ms": "Malay", "cs": "Czech",
    "ro": "Romanian", "da": "Danish", "hu": "Hungarian", "ta": "Tamil", "no": "Norwegian",
    "th": "Thai", "ur": "Urdu", "hr": "Croatian", "bg": "Bulgarian", "lt": "Lithuanian",
    "la": "Latin", "mi": "Maori", "ml": "Malayalam", "cy": "Welsh", "sk": "Slovak",
    "te": "Telugu", "fa": "Persian", "lv": "Latvian", "bn": "Bengali", "sr": "Serbian",
    "az": "Azerbaijani", "sl": "Slovenian", "kn": "Kannada", "et": "Estonian",
    "mk": "Macedonian", "br": "Breton", "eu": "Basque", "is": "Icelandic", "hy": "Armenian",
    "ne": "Nepali", "mn": "Mongolian", "bs": "Bosnian", "kk": "Kazakh", "sq": "Albanian",
    "sw": "Swahili", "gl": "Galician", "mr": "Marathi", "pa": "Punjabi", "si": "Sinhala",
    "km": "Khmer", "sn": "Shona", "yo": "Yoruba", "so": "Somali", "af": "Afrikaans",
    "oc": "Occitan", "ka": "Georgian", "be": "Belarusian", "tg": "Tajik", "sd": "Sindhi",
    "gu": "Gujarati", "am": "Amharic", "yi": "Yiddish", "lo": "Lao", "uz": "Uzbek",
    "fo": "Faroese", "ht": "Haitian Creole", "ps": "Pashto", "tk": "Turkmen",
    "nn": "Nynorsk", "mt": "Maltese", "sa": "Sanskrit", "lb": "Luxembourgish",
    "my": "Myanmar", "bo": "Tibetan", "tl": "Tagalog", "mg": "Malagasy", "as": "Assamese",
    "tt": "Tatar", "haw": "Hawaiian", "ln": "Lingala", "ha": "Hausa", "ba": "Bashkir",
    "jw": "Javanese", "su": "Sundanese", "yue": "Cantonese",
}

# Badge shown in parentheses; defaults to the upper-cased code, overridden where that would
# mislead (Ukrainian "uk" → "UA").
_BADGE_OVERRIDE = {"uk": "UA"}


def _lang_badge(code: str) -> str:
    return _BADGE_OVERRIDE.get(code, code.upper())


def _lang_label(code: str) -> str:
    name = WHISPER_LANGUAGES.get(code, code.upper())
    return f"{name} ({_lang_badge(code)})"


def _describe_spec(spec) -> str:
    """Human-readable hotkey spec, e.g. ['f9','ctrl+space'] -> 'f9 or ctrl+space'."""
    return spec if isinstance(spec, str) else " or ".join(spec)


def _normalize_spec(spec) -> set:
    """Compare two specs ignoring order/whitespace/list-vs-string."""
    items = [spec] if isinstance(spec, str) else list(spec)
    return {s.lower().replace(" ", "") for s in items}


_OTHER = {"ptt": "toggle", "toggle": "ptt"}


def _open_path(path) -> None:
    """Open a file/folder with the OS default handler."""
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class _NullTray:
    """A do-nothing tray used in **settings-only** mode (the macOS Settings subprocess).

    The Settings facade calls ``self.tray.notify`` / ``refresh_menu`` / ``set_status`` freely;
    in the subprocess there is no menu-bar icon (the parent process owns it), so these become
    no-ops. ``notify`` falls back to a printed line so download/error feedback isn't lost."""

    def notify(self, message: str, title: str = "Mynah") -> None:
        print(f"[{title}] {message}")

    def refresh_menu(self) -> None:
        pass

    def set_status(self, status: str) -> None:
        pass

    def set_capturing(self, on: bool) -> None:
        pass

    def run(self) -> None:
        pass

    def stop(self) -> None:
        pass


class MynahApp:
    version = __version__

    def __init__(self, cfg: dict, cfgpath, settings_only: bool = False) -> None:
        self.cfg = cfg
        self.cfgpath = cfgpath
        # settings_only: the macOS Settings subprocess. No controller (so no mic/hotkeys), no
        # menu-bar icon, and downloads persist config without loading the engine in-process —
        # the parent menu-bar process picks the change up via its config watcher and (re)loads.
        self._settings_only = settings_only

        # Heavy imports kept local so `--help` etc. don't drag in the GPU stack.
        from .transcriber import set_backend

        # Tell the engine-dir resolver which whisper.cpp pack to run.
        set_backend(cfg.get("hardware", {}).get("backend", "auto"))

        # Lazy model loading: the app reaches the tray *before* a model is loaded.
        # The configured model loads on a background thread; until then the engine is None and
        # a hotkey press gives a "pick a model first" prompt instead of crashing.
        self.transcriber = None
        self._model_ready = False
        self._loading = False
        self._loading_name: str | None = None   # model name currently being loaded/switched to
        self._model_error: str | None = None

        # Download/install state for the Settings Models panel (read by its after() poll).
        self._busy = threading.Lock()   # one install/download/backend op at a time
        self._progress = {"active": False, "done": 0, "total": None, "text": ""}
        self._progress_lock = threading.Lock()
        self._recommended = None         # cached (backend, model, reason) from the probe
        self._cfg_mtime = self._config_mtime()  # for the darwin external-config watcher

        if settings_only:
            self.controller = None
            self.tray = _NullTray()
        else:
            from .controller import Controller

            self.controller = Controller(cfg, transcriber=None)
            self.controller.on_no_model = self._no_model_prompt

            from .tray import Tray

            self.tray = Tray(self)
            self.controller.set_status_callback(self.tray.set_status)

        self._switching = threading.Lock()  # guards a model swap / first load in progress

        # Two independent hotkeys: a hold-to-talk (PTT) and a tap-to-toggle switch.
        hk, dft = cfg["hotkey"], DEFAULTS["hotkey"]
        self._hk = {
            "ptt": {"spec": hk.get("push_to_talk", dft["push_to_talk"]),
                    "default": dft["push_to_talk"], "key": "push_to_talk",
                    "label": "Hold-to-talk", "listener": None},
            "toggle": {"spec": hk.get("toggle", dft["toggle"]),
                       "default": dft["toggle"], "key": "toggle",
                       "label": "Toggle on/off", "listener": None},
        }
        self._capturing = None  # which hotkey kind is being captured, or None
        self._capture = None
        self._settings = None   # lazily-created SettingsWindow

        # Optional extra hotkey that just toggles multilingual mode (config-file only —
        # "" disables it; not part of the capture UI to keep that surface simple).
        self._multi_hk_spec = cfg["hotkey"].get("multilingual", "")
        self._multi_hk = None

        # Wake-word "listening mode": a tiny VAD-gated spotter that starts dictation
        # hands-free. Off by default; started after a model loads (it shares the engine build).
        self._wakeword = None  # WakeWordListener | None
        self._wakeword_enabled = bool(cfg.get("wakeword", {}).get("enabled", False))
        self._wake_hk_spec = cfg["hotkey"].get("wakeword", "")
        self._wake_hk = None

        self._settings_proc = None  # darwin: the Settings subprocess (Popen), if open

    # --- config watcher (macOS: pick up the Settings subprocess's changes) --

    def _config_mtime(self) -> float:
        try:
            return Path(self.cfgpath).stat().st_mtime
        except OSError:
            return 0.0

    def _watch_config(self) -> None:
        """macOS only: the Settings window runs in a **separate process** (Tk needs its own
        main thread), so it edits ``config.toml`` out-of-band. Poll the file's mtime and, when
        it changes, reload model/language/sound/multilingual into the live menu-bar app so the
        two stay in sync — the same role the in-process Settings poll plays on Windows."""
        while True:
            time.sleep(1.0)
            mt = self._config_mtime()
            if mt and mt != self._cfg_mtime:
                self._cfg_mtime = mt
                try:
                    self._reload_from_disk()
                except Exception as e:
                    print(f"! couldn't apply external config change: {e}")

    def _reload_from_disk(self) -> None:
        """Re-read config.toml and apply any changed runtime settings (model, language, sound,
        multilingual, backend). Called by the darwin config watcher after the Settings
        subprocess saves."""
        from .config import load_config
        from .transcriber import set_backend

        new = load_config(self.cfgpath)
        # Backend / model: re-point the engine resolver and reload if either changed.
        new_backend = new.get("hardware", {}).get("backend", "auto")
        old_backend = self.cfg.get("hardware", {}).get("backend", "auto")
        set_backend(new_backend)
        new_model = new.get("model", {}).get("name")
        model_changed = bool(new_model) and new_model != self.cfg["model"].get("name")
        backend_changed = new_backend != old_backend
        if model_changed or backend_changed:
            if new_model:
                self.cfg["model"]["name"] = new_model
            self.cfg.setdefault("hardware", {}).update(new.get("hardware", {}))
            target = new_model or self.cfg["model"]["name"]
            if self._switching.acquire(blocking=False):
                self._model_ready = False  # backend change needs a rebuild on the new pack
                threading.Thread(target=self._load_and_swap, args=(target,),
                                 daemon=True).start()
        # Language.
        lang = new.get("language", {})
        code = lang.get("fixed") if lang.get("mode") == "fixed" else None
        if code != self.controller.language:
            self.controller.set_language(code)
            self.cfg["language"].update(lang)
        # Sound cues.
        snd = bool(new.get("ux", {}).get("sound_cues", True))
        if snd != self.controller.sound_cues:
            self.controller.set_sound_cues(snd)
            self.cfg["ux"]["sound_cues"] = snd
        # Multilingual.
        ml = bool(new.get("language", {}).get("multilingual", False))
        if ml != self.controller.multilingual:
            self.controller.set_multilingual(ml)
            self.cfg["language"]["multilingual"] = ml
        # Hotkeys — re-arm any that changed (the Settings subprocess writes the new spec to
        # config but can't touch this process's live listeners).
        new_hk = new.get("hotkey", {})
        for kind in self._hk:
            key = self._hk[kind]["key"]
            spec = new_hk.get(key)
            if spec is not None and _normalize_spec(spec) != _normalize_spec(self._hk[kind]["spec"]):
                self._hk[kind]["spec"] = spec
                self._arm(kind)
        # Wake word — the Settings subprocess can only persist intent; start/stop the actual
        # listener here in the live app when the enabled flag flips. Also apply tunables live.
        wk = new.get("wakeword", {})
        new_wake = bool(wk.get("enabled", False))
        self.cfg.setdefault("wakeword", {}).update(wk)
        if new_wake != self._wakeword_enabled:
            self._wakeword_enabled = new_wake
            if new_wake:
                if self.wakeword_available():
                    self._start_wakeword()
                else:
                    self.tray.notify("Download a model first, then enable listening mode.",
                                     "Mynah")
            else:
                self._stop_wakeword()
        elif self._wakeword is not None:
            # already running — push live tunables
            try:
                self._wakeword.set_phrase(wk.get("phrase", self.wakeword_phrase()))
                self._wakeword.set_sensitivity(self.wakeword_sensitivity())
                self._wakeword.set_silence_ms(self.wakeword_silence_ms())
            except Exception:
                pass
        self.tray.refresh_menu()

    def _wait_until_trusted(self, timeout: float = 8.0) -> bool:
        """macOS: block (briefly) until the process is Accessibility-trusted, so the hotkey tap
        is created in a trusted context. Returns the final trust state. Never raises."""
        try:
            from .permissions import _accessibility_state
        except Exception:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _accessibility_state() == "granted":
                return True
            time.sleep(0.25)
        # Not trusted within the window — arm anyway; pynput will warn and the user can grant +
        # the 2.5s re-arm / config watcher will pick it up on the next change.
        print("! Accessibility not granted yet — hotkeys may not fire until it's enabled in "
              "System Settings → Privacy & Security → Accessibility (then they re-arm).")
        return False

    def _rearm_hotkeys(self) -> None:
        """Restart the PTT/toggle listeners (and the optional multilingual/wakeword ones). Used
        on macOS shortly after launch so a tap created before TCC trust settled is replaced by a
        working one."""
        try:
            self._arm("ptt")
            self._arm("toggle")
        except Exception as e:
            print(f"! re-arm hotkeys failed: {e}")

    # --- lifecycle ----------------------------------------------------------

    def run(self) -> int:
        self.controller.start()
        # macOS: keep the menu-bar app in sync with the out-of-process Settings window.
        if sys.platform == "darwin":
            threading.Thread(target=self._watch_config, daemon=True).start()
            # When launched via LaunchServices (Finder / `open` / menu bar), TCC's
            # Accessibility/Input-Monitoring trust for the fresh process can lag a beat. pynput's
            # global key listener checks trust *once* at start and silently produces no event tap
            # if it reads False — so the hotkeys never fire even though the grant is in place.
            # Wait until the process is actually trusted before arming, then re-arm on a short
            # delay as a belt-and-braces against the cache reading stale-False at t=0.
            self._wait_until_trusted(timeout=8.0)

        self._arm("ptt")
        self._arm("toggle")
        self._arm_multilingual_hotkey()
        self._arm_wakeword_hotkey()
        if sys.platform == "darwin":
            # Re-arm shortly after launch in case the very first tap was created before trust
            # settled (it would silently receive nothing). Cheap and idempotent.
            threading.Timer(2.5, self._rearm_hotkeys).start()
        if not any(info["listener"] for info in self._hk.values()):
            print("X No usable hotkey could be registered.")
            self.controller.stop()
            return 1

        lang = self.cfg["language"]
        lang_desc = "auto-detect" if lang.get("mode") == "auto" else f"pinned {lang.get('fixed')}"
        parts = []
        if self._hk["ptt"]["listener"] is not None:
            parts.append(f"hold [{self.hotkey_desc('ptt')}] (push-to-talk)")
        if self._hk["toggle"]["listener"] is not None:
            parts.append(f"tap [{self.hotkey_desc('toggle')}] (toggle on/off)")
        print()
        print(f"Ready. To dictate ({lang_desc}): " + " or ".join(parts) + ".")
        if self.controller.multilingual:
            print("Multilingual mode is ON (mixed-language clips are split & transcribed).")
        if self._multi_hk is not None:
            print(f"Tap [{_describe_spec(self._multi_hk_spec)}] to toggle multilingual mode.")
        if self._wakeword_enabled:
            print(f'Wake-word listening mode is ON (say "{self.wakeword_phrase()}" to dictate).')
        if self._wake_hk is not None:
            print(f"Tap [{_describe_spec(self._wake_hk_spec)}] to toggle listening mode.")
        print("Mynah is in the system tray — right-click it for the menu, or Quit there.")

        # Lazy load: the tray is up; now load the configured model in the background,
        # or open first-run setup if nothing is installed yet. Never blocks reaching the tray.
        threading.Thread(target=self._startup_model, daemon=True).start()

        try:
            self.tray.run()  # blocks on the tray event loop until quit()
        finally:
            for info in self._hk.values():
                if info["listener"] is not None:
                    try:
                        info["listener"].stop()
                    except Exception:
                        pass
            if self._multi_hk is not None:
                try:
                    self._multi_hk.stop()
                except Exception:
                    pass
            if self._wake_hk is not None:
                try:
                    self._wake_hk.stop()
                except Exception:
                    pass
            self._stop_wakeword()
            self.controller.stop()
            try:  # release the model / stop any child server process (whisper.cpp)
                self.transcriber.unload()
            except Exception:
                pass
        return 0

    def quit(self) -> None:
        print("\nShutting down...")
        if self._settings is not None:
            self._settings.close()
        # macOS: the Settings window is a separate process — terminate it so it doesn't linger
        # after the menu-bar app quits.
        proc = self._settings_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        self.tray.stop()  # unblocks run(); cleanup happens in its finally

    def open_settings(self, setup: bool = False) -> None:
        """Open (or focus) the persistent settings window. ``setup=True`` shows the first-run
        welcome banner + the recommended model.

        On macOS the window runs in a **separate process** (``mynah --settings``): the menu-bar
        app owns this process's main thread (pystray/AppKit), and macOS can't run a Tk loop off
        the main thread or alongside another UI run loop in one process. The subprocess edits
        the same ``config.toml``; the running app reflects those changes via :meth:`_watch_config`.
        Elsewhere the window opens in-process on its own thread (the Windows behaviour)."""
        if sys.platform == "darwin":
            self._open_settings_subprocess(setup=setup)
            return
        from .settings_window import SettingsWindow

        if self._settings is None:
            self._settings = SettingsWindow(self)
        self._settings.show(setup=setup)

    def _open_settings_subprocess(self, setup: bool = False) -> None:
        """macOS: launch (or focus) the Settings window as its own process."""
        proc = self._settings_proc
        if proc is not None and proc.poll() is None:
            return  # already open — leave it; the user can switch to its window
        if getattr(sys, "frozen", False):
            # In the .app bundle, re-exec the same executable with --settings.
            cmd = [sys.executable, "--settings"]
        else:
            cmd = [sys.executable, "-m", "mynah", "--settings"]
        if setup:
            cmd.append("--first-run-setup")
        if self.cfgpath:
            cmd += ["--config", str(self.cfgpath)]
        try:
            self._settings_proc = subprocess.Popen(cmd)
        except Exception as e:
            print(f"! couldn't open Settings window: {e}")

    # --- facade used by the tray --------------------------------------------

    def current_model(self) -> str:
        return self.cfg["model"]["name"]

    # --- lazy load / startup ----------------------------------------

    def model_ready(self) -> bool:
        return self._model_ready and self.transcriber is not None

    def is_loading(self) -> bool:
        return self._loading

    def loading_model(self) -> str | None:
        """Name of the model currently being loaded/switched to (None when idle). Differs from
        ``current_model()`` mid-switch, since the config name only flips after the swap."""
        return self._loading_name

    def model_error(self) -> str | None:
        return self._model_error

    def _model_available(self) -> bool:
        """Cheap check: is an engine pack + the configured model present (no server start)?"""
        from .transcriber import build_transcriber

        try:
            build_transcriber(self.cfg["model"])  # constructs only; raises if files missing
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return True  # other issues surface during the real load

    def _startup_model(self) -> None:
        """Background: load the configured model, or open setup if nothing is installed."""
        if self._model_available():
            self.load_configured_model_async()
        else:
            self._enter_setup()

    def _enter_setup(self) -> None:
        """First run / nothing installed: open Settings in setup mode."""
        print("No engine/model installed yet — opening first-run setup.")
        self.tray.notify("Welcome to Mynah — finish setup to start dictating.",
                         "Mynah · setup")
        try:
            self.open_settings(setup=True)
        except Exception as e:
            print(f"! couldn't open setup window: {e}")

    def load_configured_model_async(self) -> None:
        """Load the currently-configured model on a background thread (no-op if ready/loading)."""
        if self._model_ready or self._loading:
            return
        if not self._switching.acquire(blocking=False):
            return
        threading.Thread(target=self._load_and_swap,
                         args=(self.current_model(),), daemon=True).start()

    def _no_model_prompt(self) -> None:
        """Controller callback: a hotkey fired before any model is loaded."""
        if self._loading:
            msg = f"Still loading {self.current_model()}… one moment."
        else:
            msg = "No model yet — open Settings to download one."
        self.tray.notify(msg, "Mynah")
        print(f"! {msg}")

    def select_model(self, name: str) -> None:
        # Settings-only (macOS subprocess): don't load the model here — that's the live
        # menu-bar app's job. Just persist the choice; its config watcher reloads. (Loading in
        # this process would spawn a second whisper-server and crash on the absent controller.)
        if self.controller is None:
            self.cfg["model"]["name"] = name
            try:
                update_config_values({"model": {"name": name}}, self.cfgpath)
            except Exception as e:
                print(f"! couldn't save model choice: {e}")
            self._progress_done(f"{name} selected — the app will switch to it.")
            return
        if name == self.current_model() and self._model_ready:
            return
        if not self._switching.acquire(blocking=False):
            print("! still switching models — ignoring")
            return
        threading.Thread(target=self._load_and_swap, args=(name,), daemon=True).start()

    def _load_and_swap(self, name: str) -> None:
        """Build + load `name`, swap it in, persist the choice. Holds `self._switching` (the
        caller acquires it). Used for both the first lazy load and later model switches."""
        from .tray import ERROR, IDLE, LOADING
        from .transcriber import build_transcriber

        self._loading = True
        self._loading_name = name
        self._model_error = None
        try:
            self.tray.set_status(LOADING)
            self.tray.refresh_menu()
            model_cfg = copy.deepcopy(self.cfg["model"])
            model_cfg["name"] = name
            verb = "Loading" if not self._model_ready else "Switching →"
            print(f"{verb} model '{name}' ...")
            t0 = time.time()
            # Free the current engine's VRAM *before* loading the replacement. A single 8 GB
            # GPU can't hold two large models at once — loading the new one first (≈6 GB peak
            # for two large-v3) makes whisper-server abort. Accepts a brief no-engine window
            # (a hotkey press then just says "loading"); the model lock keeps it safe.
            if self.transcriber is not None:
                self.controller.swap_transcriber(None)  # clears the ref + unloads under lock
                self.transcriber = None
                self._model_ready = False
            new = build_transcriber(model_cfg)
            new.load()
            self.controller.swap_transcriber(new)
            self.transcriber = new
            self._model_ready = True
            changed = self.cfg["model"]["name"] != name
            self.cfg["model"]["name"] = name
            print(f"OK {new.description} — loaded in {time.time() - t0:.2f}s")
            if changed:
                try:
                    update_config_values({"model": {"name": name}}, self.cfgpath)
                except Exception as e:
                    print(f"! couldn't save model choice: {e}")
            # Now that the engine build is present, preload multilingual (if enabled) and
            # start wake-word listening (if enabled — it shares the engine build + tiny model).
            self.controller.start_multilingual_preload()
            self._maybe_start_wakeword()
            self.tray.set_status(IDLE)
        except FileNotFoundError as e:
            self._model_error = str(e)
            print(f"X Model '{name}' isn't installed: {e}")
            self.tray.set_status(IDLE)
            self._enter_setup()
        except Exception as e:
            self._model_error = str(e)
            keep = self.current_model() if self._model_ready else "none"
            print(f"X Failed to load '{name}': {e} — keeping {keep}")
            self.tray.set_status(ERROR)
            time.sleep(1.5)
            self.tray.set_status(IDLE)
        finally:
            self._loading = False
            self._loading_name = None
            self.tray.refresh_menu()
            self._switching.release()

    # --- Settings: backend + Models panel + downloads --------

    def _progress_cb(self, done: int, total: int | None, text: str) -> None:
        with self._progress_lock:
            self._progress = {"active": True, "done": done, "total": total, "text": text}

    def _progress_done(self, text: str = "") -> None:
        with self._progress_lock:
            self._progress = {"active": False, "done": 0, "total": None, "text": text}

    def progress_state(self) -> dict:
        with self._progress_lock:
            return dict(self._progress)

    def is_busy(self) -> bool:
        return self._busy.locked()

    def recommended(self) -> tuple[str, str, str]:
        """Cached hardware recommendation ``(backend, model, reason)`` (the probe is slowish)."""
        if self._recommended is None:
            from .hardware import recommend_backend

            try:
                self._recommended = recommend_backend()
            except Exception as e:
                self._recommended = ("vulkan", "large-v3", f"probe failed: {e}")
        return self._recommended

    def cuda_optional(self) -> bool:
        from .hardware import cuda_is_optional, probe_gpu

        try:
            return cuda_is_optional(probe_gpu())
        except Exception:
            return False

    def cuda_license(self) -> tuple[str, str] | None:
        """The NVIDIA license note + link shown before a CUDA download (from the manifest)."""
        from . import components

        comp = components.component("whispercpp-cuda")
        if not comp:
            return None
        return (comp.get("license_note", ""), comp.get("license_url", ""))

    # backend selector ----------------------------------------------------

    # macOS exposes only Metal (the Apple GPU backend) + CPU; Windows/Linux expose Vulkan
    # (default) + the optional NVIDIA CUDA upgrade + CPU. "Auto" picks the best installed.
    _BACKEND_LABELS_DARWIN = [("Auto (recommended)", "auto"),
                              ("Metal — Apple GPU", "metal"), ("CPU", "cpu")]
    _BACKEND_LABELS_PC = [("Auto (recommended)", "auto"), ("Vulkan — any GPU", "vulkan"),
                          ("NVIDIA CUDA", "cuda"), ("CPU", "cpu")]

    def backend_choices(self) -> list[tuple[str, str]]:
        if sys.platform == "darwin":
            return list(self._BACKEND_LABELS_DARWIN)
        return list(self._BACKEND_LABELS_PC)

    def current_backend(self) -> str:
        return self.cfg.get("hardware", {}).get("backend", "auto")

    def backend_installed(self, backend: str) -> bool:
        """Whether the engine pack for ``backend`` is already downloaded (so the UI can skip
        the install/license prompt)."""
        from . import components

        return components.is_installed(backend)

    def _target_backend(self, pref: str | None = None) -> str:
        """Concrete backend for installs: a pinned choice, else the probe's recommendation."""
        from .transcriber import _BACKENDS  # ("vulkan","cuda","cpu")

        pref = (pref or self.current_backend()).lower()
        if pref in _BACKENDS:
            return pref
        return self.recommended()[0]  # auto → vulkan on a GPU, cpu otherwise

    def select_backend(self, value: str) -> None:
        from .transcriber import set_backend

        value = (value or "auto").lower()
        prev = self.current_backend()
        self.cfg.setdefault("hardware", {})["backend"] = value
        set_backend(value)
        # Persist only once the engine is actually in place (see _apply_backend) — so a pack
        # that can't be fetched (e.g. an unpublished release → 404) doesn't permanently rewrite
        # the config to a backend that never installed.
        threading.Thread(target=self._apply_backend, args=(value, prev), daemon=True).start()

    def _apply_backend(self, value: str, prev: str) -> None:
        from .transcriber import resolve_backend, set_backend

        if not self._busy.acquire(blocking=False):
            print("! busy — backend change ignored")
            self.cfg.setdefault("hardware", {})["backend"] = prev  # roll back optimistic switch
            set_backend(prev)
            return
        # Replace any stale status (e.g. a previous "vulkan engine installed") right away so the
        # label reflects this action, not the last one.
        self._progress_cb(0, None, f"Switching to {value} backend…")
        try:
            backend = self._target_backend(value)
            # An explicit (non-auto) pick must NOT silently degrade to CPU — surface the failure
            # so the user knows Vulkan/CUDA didn't actually install (only "auto" may fall back).
            self._ensure_engine(backend, allow_cpu_fallback=(value == "auto"))
            # If a model is already chosen, restart the engine on the new backend — but only in
            # the live app. In the settings-only subprocess there's no engine to restart; persist
            # the choice and let the menu-bar app's config watcher reload on the new backend.
            if self.controller is not None and (self._model_ready or self._model_available()):
                self._model_ready = False  # force a reload on the new build
                self._reload_current()
            # Engine is in place — now it's safe to persist the choice.
            try:
                update_config_values({"hardware": {"backend": value}}, self.cfgpath)
            except Exception as e:
                print(f"! couldn't save backend choice: {e}")
            actual = resolve_backend(None if value == "auto" else value)
            self._progress_done(f"Using {actual} backend.")
        except Exception as e:
            print(f"X couldn't switch backend: {e}")
            # Keep the previously-working backend rather than leaving a broken choice active.
            self.cfg.setdefault("hardware", {})["backend"] = prev
            set_backend(prev)
            self._progress_done(f"Backend change failed: {e}")
        finally:
            self._busy.release()
            self.tray.refresh_menu()

    # models panel --------------------------------------------------------

    def model_catalog(self) -> list[str]:
        """Catalog models ∪ any installed extras — the rows shown in the Models panel."""
        from . import models

        names = list(models.catalog_names())
        for n in models.installed_asr_models():
            if n not in names:
                names.append(n)
        cur = self.current_model()
        if cur not in names:
            names.insert(0, cur)
        return names

    def model_status_text(self, name: str) -> str:
        from . import models

        installed, size = models.model_status(name)
        gb = size / 1e9
        if installed:
            return f"● Installed ({gb:.1f} GB)"
        return f"↓ ~{gb:.1f} GB"

    def model_is_installed(self, name: str) -> bool:
        from . import models

        return models.model_status(name)[0]

    def active_model_name(self) -> str:
        """The model **actually loaded** in the engine (from its model file), which can differ
        from the configured name when a `MYNAH_WHISPERCPP_MODEL` env pin overrides it. Falls
        back to the configured name when nothing is loaded yet."""
        t = self.transcriber
        if self._model_ready and t is not None and hasattr(t, "model_path"):
            try:
                stem = t.model_path.stem  # e.g. "ggml-large-v3"
                return stem[len("ggml-"):] if stem.startswith("ggml-") else stem
            except Exception:
                pass
        return self.current_model()

    def is_active_model(self, name: str) -> bool:
        # Settings-only (macOS subprocess) has no live engine, so "active" = the model the
        # config selects (what the menu-bar app runs). In the live app it's the loaded one.
        if self.controller is None:
            return name == self.current_model()
        return self._model_ready and name == self.active_model_name()

    def download_model(self, name: str) -> None:
        """Install the right engine pack (if missing) + the model + multilingual weights, then
        load it — all on a background thread with progress. Idempotent / cache-aware."""
        if not self._busy.acquire(blocking=False):
            self.tray.notify("A download is already in progress.", "Mynah")
            return
        threading.Thread(target=self._download_and_load, args=(name,), daemon=True).start()

    def _download_and_load(self, name: str) -> None:
        from . import models

        try:
            backend = self._target_backend()
            self._ensure_engine(backend)
            self._progress_cb(0, models.size_hint(name), f"Downloading {name}…")
            models.download_model(name, progress=self._progress_cb)
            models.ensure_multilingual_weights(progress=self._progress_cb)
            self.cfg["model"]["name"] = name
            try:
                update_config_values({"model": {"name": name}}, self.cfgpath)
            except Exception:
                pass
            # Settings-only (macOS subprocess): don't load the engine here — the live menu-bar
            # app does that when its config watcher sees the new model. Loading here would spawn
            # a second whisper-server and touch the absent controller.
            if self.controller is None:
                self._progress_done(f"{name} installed — the app will load it.")
                return
            self._progress_done(f"{name} ready — loading…")
            self._model_ready = False
            self._reload_current()
            self._progress_done(f"{name} installed.")
        except Exception as e:
            print(f"X download/install of '{name}' failed: {e}")
            self._progress_done(f"Failed: {e}")
            self.tray.notify(f"Couldn't install {name}: {e}", "Mynah")
        finally:
            self._busy.release()
            self.tray.refresh_menu()

    def _ensure_engine(self, backend: str, allow_cpu_fallback: bool = True) -> None:
        """Install the engine pack for ``backend`` if missing.

        When ``allow_cpu_fallback`` (the unattended first-run / model-download path), a failed
        **GPU** pack falls back to the always-available CPU pack so the app stays usable
        — but only for **this session**: the fallback is *not* written to config,
        so a later valid manifest/release lets the chosen GPU backend install on the next launch
        instead of being stuck on CPU. An explicit Settings backend pick passes
        ``allow_cpu_fallback=False`` so the failure surfaces instead of silently degrading."""
        from . import components

        if components.is_installed(backend):
            return
        try:
            components.install_engine(backend, progress=self._progress_cb)
        except Exception as e:
            print(f"! {backend} engine pack failed ({e})")
            if backend != "cpu" and allow_cpu_fallback:
                self.tray.notify(f"{backend} pack unavailable — using CPU for now.", "Mynah")
                self._progress_cb(0, None, f"{backend} pack unavailable — trying CPU…")
                components.install_engine("cpu", progress=self._progress_cb)
                # Session-only: run CPU now, but DON'T persist — keep the user's chosen backend
                # in config so the GPU pack is retried next launch.
                from .transcriber import set_backend

                set_backend("cpu")
            else:
                raise

    def _reload_current(self) -> None:
        """(Re)load the configured model synchronously on this (already-busy) thread."""
        if not self._switching.acquire(blocking=False):
            return
        self._load_and_swap(self.current_model())  # releases _switching in its finally

    def remove_model(self, name: str) -> None:
        from . import models

        if self.is_active_model(name):
            self.tray.notify(f"{name} is the active model — switch first, then remove.",
                             "Mynah")
            return
        freed = models.remove_model(name)
        self.tray.notify(f"Removed {name} (freed {freed/1e9:.1f} GB).", "Mynah")
        self.tray.refresh_menu()

    # run at login --------------------------------------------------------

    def run_at_login(self) -> bool:
        from .platform_layer import is_run_at_login

        return is_run_at_login()

    def set_run_at_login(self, enabled: bool) -> bool:
        from .platform_layer import set_run_at_login

        state = set_run_at_login(enabled)
        print(f"Run at login: {'on' if state else 'off'}")
        return state

    def language_choices(self) -> list[tuple[str, str | None]]:
        """Auto-detect, then every Whisper language alphabetically by name (the Settings
        combobox scrolls)."""
        choices: list[tuple[str, str | None]] = [("Auto-detect", None)]
        choices += [(_lang_label(c), c)
                    for c in sorted(WHISPER_LANGUAGES, key=lambda c: WHISPER_LANGUAGES[c])]
        return choices

    def current_language(self) -> str | None:
        if self.controller is None:
            lang = self.cfg.get("language", {})
            return lang.get("fixed") if lang.get("mode") == "fixed" else None
        return self.controller.language

    def select_language(self, code: str | None) -> None:
        if self.controller is not None:
            self.controller.set_language(code)
        if code is None:
            self.cfg["language"]["mode"] = "auto"
            updates = {"language": {"mode": "auto"}}
        else:
            self.cfg["language"]["mode"] = "fixed"
            self.cfg["language"]["fixed"] = code
            updates = {"language": {"mode": "fixed", "fixed": code}}
        try:
            update_config_values(updates, self.cfgpath)
        except Exception as e:
            print(f"! couldn't save language choice: {e}")
        self.tray.refresh_menu()

    def sound_enabled(self) -> bool:
        if self.controller is None:
            return bool(self.cfg.get("ux", {}).get("sound_cues", True))
        return self.controller.sound_cues

    def toggle_sound(self) -> None:
        enabled = not self.sound_enabled()
        if self.controller is not None:
            self.controller.set_sound_cues(enabled)
            enabled = self.controller.sound_cues
        self.cfg["ux"]["sound_cues"] = enabled
        try:
            update_config_values({"ux": {"sound_cues": enabled}}, self.cfgpath)
        except Exception as e:
            print(f"! couldn't save sound setting: {e}")
        self.tray.refresh_menu()

    def multilingual_enabled(self) -> bool:
        if self.controller is None:
            return bool(self.cfg.get("language", {}).get("multilingual", False))
        return self.controller.multilingual

    def toggle_multilingual(self) -> None:
        enabled = not self.multilingual_enabled()
        if self.controller is not None:
            self.controller.set_multilingual(enabled)
        self.cfg["language"]["multilingual"] = enabled
        try:
            update_config_values({"language": {"multilingual": enabled}}, self.cfgpath)
        except Exception as e:
            print(f"! couldn't save multilingual setting: {e}")
        self.tray.refresh_menu()

    # --- wake-word "listening mode" -------------------------------

    def wakeword_enabled(self) -> bool:
        """User's persisted intent (the tray/Settings checkbox), independent of whether the
        listener is currently running."""
        return self._wakeword_enabled

    def wakeword_active(self) -> bool:
        """The listener is actually up and listening on the mic."""
        return self._wakeword is not None and self._wakeword.is_ready

    def wakeword_available(self) -> bool:
        """Engine build + the tiny spotter model are present, so listening mode can start."""
        from . import models
        from .transcriber import whispercpp_binary_dir

        try:
            bdir = whispercpp_binary_dir(self.cfg["model"])
            exe = bdir / ("whisper-server.exe" if os.name == "nt" else "whisper-server")
            return exe.is_file() and models.resolve_lid_model("tiny") is not None
        except Exception:
            return False

    def wakeword_phrase(self) -> str:
        return self.cfg.get("wakeword", {}).get("phrase", "hey mynah")

    def wakeword_sensitivity(self) -> float:
        try:
            return float(self.cfg.get("wakeword", {}).get("sensitivity", 0.5))
        except (TypeError, ValueError):
            return 0.5

    def wakeword_silence_ms(self) -> int:
        try:
            return int(self.cfg.get("wakeword", {}).get("silence_ms", 900))
        except (TypeError, ValueError):
            return 900

    def toggle_wakeword(self) -> None:
        enabled = not self._wakeword_enabled
        self._wakeword_enabled = enabled
        self.cfg.setdefault("wakeword", {})["enabled"] = enabled
        try:
            update_config_values({"wakeword": {"enabled": enabled}}, self.cfgpath)
        except Exception as e:
            print(f"! couldn't save wake-word setting: {e}")
        # Settings-only (macOS subprocess): just persist intent — the live menu-bar process
        # starts/stops the actual listener when it picks up the config change.
        if self.controller is None:
            self.tray.refresh_menu()
            return
        if enabled:
            if self.wakeword_available():
                self._start_wakeword()
            else:
                self.tray.notify("Download a model first, then enable listening mode.",
                                 "Mynah")
                print("! wake word needs an installed engine + model first.")
        else:
            self._stop_wakeword()
            print("Wake-word listening mode off.")
        self.tray.refresh_menu()

    def set_wakeword_phrase(self, phrase: str) -> None:
        phrase = (phrase or "").strip() or "hey mynah"
        self.cfg.setdefault("wakeword", {})["phrase"] = phrase
        try:
            update_config_values({"wakeword": {"phrase": phrase}}, self.cfgpath)
        except Exception as e:
            print(f"! couldn't save wake phrase: {e}")
        if self._wakeword is not None:
            self._wakeword.set_phrase(phrase)
        print(f'Wake phrase set to "{phrase}".')

    def set_wakeword_sensitivity(self, value: float) -> None:
        try:
            value = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return
        self.cfg.setdefault("wakeword", {})["sensitivity"] = value
        try:
            update_config_values({"wakeword": {"sensitivity": value}}, self.cfgpath)
        except Exception as e:
            print(f"! couldn't save wake sensitivity: {e}")
        if self._wakeword is not None:
            self._wakeword.set_sensitivity(value)

    def set_wakeword_silence_ms(self, value: int) -> None:
        try:
            value = int(value)
        except (TypeError, ValueError):
            return
        value = max(300, min(3000, value))
        self.cfg.setdefault("wakeword", {})["silence_ms"] = value
        try:
            update_config_values({"wakeword": {"silence_ms": value}}, self.cfgpath)
        except Exception as e:
            print(f"! couldn't save wake stop-delay: {e}")
        if self._wakeword is not None:
            self._wakeword.set_silence_ms(value)

    def _maybe_start_wakeword(self) -> None:
        """Start listening mode if the user wants it and the engine/tiny model are present."""
        if self._wakeword_enabled and self._wakeword is None and self.wakeword_available():
            self._start_wakeword()

    def _start_wakeword(self) -> None:
        if self._wakeword is not None:
            return
        from .transcriber import lid_model_path, whispercpp_binary_dir
        from .wakeword import TinyWhisperSpotter, WakeWordListener

        wk = self.cfg.get("wakeword", {})
        try:
            bdir = str(whispercpp_binary_dir(self.cfg["model"]))
            tiny = str(lid_model_path("tiny"))
            spotter = TinyWhisperSpotter(bdir, tiny, samplerate=self.controller.samplerate)
            # Mute the mic only long enough to swallow the start cue; if sound cues are off,
            # there's nothing to swallow, so listen immediately (mute = 0).
            from .wakeword import _POST_WAKE_MUTE_S

            mute_s = _POST_WAKE_MUTE_S if self.controller.sound_cues else 0.0
            listener = WakeWordListener(
                spotter=spotter,
                phrase=wk.get("phrase", "hey mynah"),
                sensitivity=self.wakeword_sensitivity(),
                samplerate=self.controller.samplerate,
                device=self.controller.recorder.device,
                on_wake=self.controller.on_wakeword_begin,
                on_dictation=self.controller.on_wakeword_clip,
                on_abort=self.controller.on_wakeword_abort,
                on_ready=self._on_wakeword_ready,
                is_blocked=self.controller.is_busy,
                silence_ms=int(wk.get("silence_ms", 1500)),
                max_dictation_s=float(wk.get("max_seconds", 120)),
                post_wake_mute_s=mute_s,
            )
            listener.start()
            self._wakeword = listener
            # Let F9/F10 stop an in-progress wake-word dictation (instead of starting a
            # second recording) while listening mode is on.
            self.controller.wake_is_capturing = listener.is_capturing
            self.controller.wake_interrupt = listener.interrupt
        except Exception as e:
            print(f"X couldn't start wake word: {e}")
            self._wakeword = None

    def _stop_wakeword(self) -> None:
        lis = self._wakeword
        self._wakeword = None
        if self.controller is not None:
            self.controller.wake_is_capturing = None
            self.controller.wake_interrupt = None
        if lis is not None:
            try:
                lis.stop()
            except Exception:
                pass

    def _on_wakeword_ready(self, ok: bool, err: str) -> None:
        if ok:
            self.tray.notify(f'Listening for "{self.wakeword_phrase()}" — say it, pause, '
                             "then dictate.", "Mynah")
        else:
            self.tray.notify(f"Wake word couldn't start: {err}", "Mynah")
            self._wakeword = None
        self.tray.refresh_menu()

    def info_text(self) -> str:
        if self.transcriber is None:
            if self._loading:
                return f"Loading {self._loading_name or self.current_model()}…"
            return "No model — open Settings to set one up"
        return self.transcriber.description

    def open_config(self) -> None:
        path = write_default_config(self.cfgpath)  # create from template if missing
        _open_path(path)

    def open_config_dir(self) -> None:
        d = app_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        _open_path(d)

    # --- hotkeys: two independent triggers, live rebind + capture -----------

    def hotkey_desc(self, kind: str) -> str:
        return _describe_spec(self._hk[kind]["spec"])

    def default_hotkey_desc(self, kind: str) -> str:
        return _describe_spec(self._hk[kind]["default"])

    def hotkey_is_default(self, kind: str) -> bool:
        info = self._hk[kind]
        return _normalize_spec(info["spec"]) == _normalize_spec(info["default"])

    def _arm(self, kind: str) -> bool:
        """(Re)start one hotkey listener from its current spec. "" disables it.

        Settings-only mode has no controller to bind to and doesn't own the live hotkeys (the
        menu-bar process does), so arming is a no-op success — the spec is still validated by
        the capture step and persisted to config for the live process to pick up."""
        if self.controller is None:
            return True
        from .hotkey import PushToTalkHotkey, ToggleHotkey

        info = self._hk[kind]
        if info["listener"] is not None:
            try:
                info["listener"].stop()
            except Exception:
                pass
            info["listener"] = None
        spec = info["spec"]
        if not spec:
            return True  # intentionally disabled
        try:
            if kind == "ptt":
                hk = PushToTalkHotkey(
                    spec, self.controller.on_activate, self.controller.on_deactivate
                )
            else:
                hk = ToggleHotkey(spec, self.controller.on_toggle)
            hk.start()
        except Exception as e:
            print(f"X Couldn't register {info['label']} hotkey {spec!r}: {e}")
            return False
        info["listener"] = hk
        return True

    def _arm_multilingual_hotkey(self) -> None:
        """Start the optional 'toggle multilingual' hotkey (no-op if unset)."""
        from .hotkey import ToggleHotkey

        spec = self._multi_hk_spec
        if not spec:
            return
        try:
            hk = ToggleHotkey(spec, self._on_multilingual_hotkey)
            hk.start()
        except Exception as e:
            print(f"X Couldn't register multilingual hotkey {spec!r}: {e}")
            return
        self._multi_hk = hk

    def _on_multilingual_hotkey(self) -> None:
        self.toggle_multilingual()
        state = "on" if self.controller.multilingual else "off"
        self.tray.notify(f"Multilingual mode {state}.", "Mynah")
        print(f"Multilingual mode {state}.")

    def _arm_wakeword_hotkey(self) -> None:
        """Start the optional 'toggle listening mode' hotkey (no-op if unset)."""
        from .hotkey import ToggleHotkey

        spec = self._wake_hk_spec
        if not spec:
            return
        try:
            hk = ToggleHotkey(spec, self._on_wakeword_hotkey)
            hk.start()
        except Exception as e:
            print(f"X Couldn't register wake-word hotkey {spec!r}: {e}")
            return
        self._wake_hk = hk

    def _on_wakeword_hotkey(self) -> None:
        self.toggle_wakeword()
        state = "on" if self._wakeword_enabled else "off"
        self.tray.notify(f"Listening mode {state}.", "Mynah")
        print(f"Listening mode {state}.")

    def begin_hotkey_capture(self, kind: str) -> None:
        if self._capturing is not None:
            return  # already waiting for a combo
        self._capturing = kind
        # Pause BOTH hotkeys so neither fires while we press keys to set one.
        for k in self._hk:
            lis = self._hk[k]["listener"]
            if lis is not None:
                try:
                    lis.stop()
                except Exception:
                    pass
                self._hk[k]["listener"] = None
        label = self._hk[kind]["label"]
        self.tray.set_capturing(True)
        self.tray.notify(f"Press the keys for {label}. Press Esc to cancel "
                         f"(or 'Cancel hotkey change' in the tray menu).",
                         "Mynah · set hotkey")
        print(f"Press the new {label} combo (Esc to cancel)…")

        from .hotkey import HotkeyCapture

        self._capture = HotkeyCapture(
            lambda spec: self._on_captured(kind, spec),
            lambda: self._on_capture_cancel(kind),
        )
        try:
            self._capture.start()
        except Exception as e:
            # Starting the global key listener can fail (e.g. macOS Input Monitoring not granted
            # for this process). Don't let it bubble into the Tk button callback and crash the
            # Settings window — back out cleanly and tell the user.
            print(f"X couldn't start hotkey capture: {e}")
            self._capture = None
            self._capturing = None
            self.tray.set_capturing(False)
            self.tray.notify(
                "Couldn't capture a shortcut — grant Input Monitoring to Mynah in "
                "System Settings → Privacy & Security, then try again.", "Mynah · hotkey")

    def set_hotkey(self, kind: str, spec: str) -> bool:
        """Validate + persist a hotkey spec directly (no key-capture listener). Used by the
        macOS Settings window's Tk-native capture, which already produced the spec string.
        Returns True if accepted. Arms the live listener too when a controller is present."""
        from .hotkey import parse_hotkey

        if kind not in self._hk or not spec:
            return False
        try:
            parse_hotkey(spec)  # reject an unparseable combo before saving
        except Exception as e:
            print(f"! ignoring invalid hotkey {spec!r}: {e}")
            self.tray.notify(f"Couldn't use '{spec}'.", "Mynah · hotkey")
            return False
        info = self._hk[kind]
        prev = info["spec"]
        info["spec"] = spec
        if self.controller is not None and not self._arm(kind):  # invalid for the live listener
            info["spec"] = prev
            self._arm(kind)
            self.tray.notify(f"Couldn't use that shortcut — kept {_describe_spec(prev)}.",
                             "Mynah · hotkey")
            return False
        try:
            update_config_values({"hotkey": {info["key"]: spec}}, self.cfgpath)
        except Exception as e:
            print(f"! couldn't save hotkey: {e}")
        self.tray.notify(f"{info['label']} set to {_describe_spec(spec)}.", "Mynah · hotkey")
        print(f"{info['label']} set to [{_describe_spec(spec)}].")
        self.tray.refresh_menu()
        return True

    def _on_captured(self, kind: str, spec: str) -> None:
        self._capture = None
        self._capturing = None
        self.tray.set_capturing(False)
        info = self._hk[kind]
        prev = info["spec"]
        info["spec"] = spec
        if not self._arm(kind):                 # invalid → revert
            info["spec"] = prev
            self._arm(kind)
            self._arm(_OTHER[kind])
            self.tray.notify(f"Couldn't use that shortcut — kept {_describe_spec(prev)}.",
                             "Mynah · hotkey")
            self.tray.refresh_menu()
            return
        self._arm(_OTHER[kind])                 # re-arm the one we paused
        try:
            update_config_values({"hotkey": {info["key"]: spec}}, self.cfgpath)
        except Exception as e:
            print(f"! couldn't save hotkey: {e}")
        self.tray.notify(f"{info['label']} set to {_describe_spec(spec)}.", "Mynah · hotkey")
        print(f"{info['label']} set to [{_describe_spec(spec)}].")
        self.tray.refresh_menu()

    def _on_capture_cancel(self, kind: str) -> None:
        self._capture = None
        self._capturing = None
        self.tray.set_capturing(False)
        self._arm("ptt")        # re-arm both with their remembered specs, unchanged
        self._arm("toggle")
        self.tray.notify("Hotkey change cancelled — kept the previous shortcuts.",
                         "Mynah · hotkey")
        print("Hotkey change cancelled.")
        self.tray.refresh_menu()

    def is_capturing(self) -> bool:
        return self._capturing is not None

    def capturing_kind(self) -> str | None:
        return self._capturing

    def is_switching_model(self) -> bool:
        return self._switching.locked()

    def cancel_hotkey_capture(self) -> None:
        """Cancel an in-progress capture (from the tray/settings 'Cancel')."""
        if self._capture is not None:
            self._capture.cancel()

    def reset_hotkey(self, kind: str) -> None:
        info = self._hk[kind]
        info["spec"] = copy.deepcopy(info["default"])
        self._arm(kind)
        try:
            update_config_values({"hotkey": {info["key"]: info["spec"]}}, self.cfgpath)
        except Exception as e:
            print(f"! couldn't save hotkey: {e}")
        self.tray.notify(f"{info['label']} reset to {_describe_spec(info['spec'])}.",
                         "Mynah · hotkey")
        self.tray.refresh_menu()


def run_tray(cfg: dict, args: argparse.Namespace) -> int:
    """Entry point for the tray app, called from `cli.main`."""
    from .tray import tray_available

    if not tray_available():
        print("! No usable system tray here; falling back to headless mode.")
        from .cli import run_headless

        return run_headless(cfg, args)

    cfgpath = args.config or config_path()
    try:
        app = MynahApp(cfg, cfgpath)
    except Exception as e:
        print(f"X Failed to start: {e}")
        return 1
    return app.run()


def run_settings_process(cfg: dict, cfgpath, setup: bool = False) -> int:
    """Entry point for the macOS Settings subprocess (``mynah --settings``).

    Builds a settings-only :class:`MynahApp` (no controller, no menu-bar icon — see
    :class:`_NullTray`) and runs the Tk window on **this process's main thread** (where macOS
    requires it). Model downloads / backend installs run here exactly as they do from the live
    app; the result is persisted to ``config.toml``, which the running menu-bar app picks up via
    its config watcher. Returns when the window closes."""
    from .settings_window import SettingsWindow

    try:
        app = MynahApp(cfg, cfgpath, settings_only=True)
    except Exception as e:
        print(f"X Failed to open Settings: {e}")
        return 1
    window = SettingsWindow(app)
    window.run_blocking(setup=setup)
    # A download/install runs on a background thread in THIS process. If the user closes the
    # window mid-download, don't exit and kill it — wait for the in-flight op to finish (the
    # menu-bar app picks up the result via its config watcher). The op holds `_busy`.
    if app.is_busy():
        print("Settings closed — finishing the download in the background…")
        while app.is_busy():
            time.sleep(0.5)
    return 0
