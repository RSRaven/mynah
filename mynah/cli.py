"""Mynah MVP CLI (Phase 1): hold hotkey → record → transcribe → paste.

Loads the model once (resident in VRAM), then runs a global push-to-talk loop until
Ctrl+C. Settings come from the config file with optional command-line overrides.
"""

from __future__ import annotations

import argparse
import sys
import time

from . import __version__
from .config import load_config, write_default_config
from .platform_layer import config_path

# Note: heavy imports (controller, transcriber → numpy/sounddevice) are deferred into
# main() so `--write-config`, `--version`, and `--help` work on a bare Python without the
# full audio/engine stack installed.


def _fix_windows_console() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _fix_pyobjc_hiservices() -> None:
    """macOS frozen-app fix: make ``HIServices.AXIsProcessTrusted`` resolvable.

    pynput's global key listener calls ``HIServices.AXIsProcessTrusted()`` (the Accessibility
    check) via pyobjc's lazy ``__getattr__``. In a PyInstaller bundle that lazy function lookup
    raises ``KeyError: 'AXIsProcessTrusted'`` and kills the listener thread — so the hotkey
    silently never fires. The symbol resolves fine from the ``ApplicationServices`` umbrella
    framework (our permissions.py uses it), so seed it into the ``HIServices`` module up front:
    try the native lazy load once, and if that fails, alias the working ApplicationServices
    callable into HIServices' namespace before pynput imports it. No-op off macOS."""
    if sys.platform != "darwin":
        return
    try:
        import HIServices

        try:
            HIServices.AXIsProcessTrusted  # force the lazy resolve (works unfrozen)
            return
        except Exception:
            pass
        from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions

        HIServices.AXIsProcessTrusted = AXIsProcessTrusted
        HIServices.AXIsProcessTrustedWithOptions = AXIsProcessTrustedWithOptions
    except Exception:
        pass  # best-effort; pynput will still warn if it truly can't check the grant


def _set_dpi_awareness() -> None:
    """Tell Windows we render at native resolution, so Tk windows + the icon aren't
    bitmap-stretched (blurry) on scaled / high-DPI displays. Must run before any window is
    created. No-op off Windows / if the call isn't available."""
    if sys.platform != "win32":
        return
    import ctypes

    try:  # Per-Monitor-v2 (best); fall back to system-DPI aware, then the legacy call.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _set_app_user_model_id() -> None:
    """Give the process an explicit AppUserModelID so Windows uses our window/exe icon for the
    taskbar button (and groups Mynah as one app) instead of a generic, blurry default — the
    common cause of a fuzzy taskbar icon for a windowed Python/PyInstaller app. No-op off
    Windows / if the call isn't available."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Mynah")
    except Exception:
        pass


def _redirect_logs_if_windowed() -> None:
    """A windowed (PyInstaller --windowed) / pythonw build has no console — ``sys.stdout`` is
    ``None``. Route stdout+stderr to ``app_data_dir()/mynah.log`` so the app's prints are
    still captured."""
    if sys.stdout is not None and sys.stderr is not None and not getattr(sys, "frozen", False):
        return
    try:
        from .platform_layer import log_path

        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        f = open(p, "a", buffering=1, encoding="utf-8", errors="replace")
        sys.stdout = f
        sys.stderr = f
        from datetime import datetime

        print(f"\n=== Mynah {__version__} started {datetime.now():%Y-%m-%d %H:%M:%S} ===")
    except Exception:
        pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mynah",
        description="Local push-to-talk voice typing. Hold the hotkey, speak, release.",
    )
    p.add_argument("--config", metavar="PATH", help="Use a specific config file.")
    p.add_argument("--write-config", action="store_true",
                   help="Write a default config file to the app-data dir and exit.")
    p.add_argument("--force", action="store_true",
                   help="With --write-config, overwrite an existing file.")
    p.add_argument("--list-devices", action="store_true",
                   help="List audio input devices and exit.")
    p.add_argument("--probe", action="store_true",
                   help="Detect the GPU (NVIDIA/AMD/Intel/Apple) and print the recommended "
                        "backend + model, then exit.")
    p.add_argument("--permissions", action="store_true",
                   help="(macOS) Print the Microphone / Input Monitoring / Accessibility grant "
                        "status and the System Settings links, then exit.")
    p.add_argument("--backend", choices=["auto", "vulkan", "cuda", "metal", "cpu"],
                   help="Engine pack to run (auto = best installed; default GPU = Metal on "
                        "Apple Silicon, Vulkan elsewhere).")
    p.add_argument("--purge-runtime", action="store_true",
                   help="Uninstall step: silently remove engine packs + config + logs (never "
                        "touches the shared model cache), then exit.")
    p.add_argument("--purge-ui", action="store_true",
                   help="Uninstall step: open the per-model delete checklist for the shared "
                        "model cache (nothing checked by default), then exit.")

    # Overrides (take precedence over the config file).
    p.add_argument("--engine", help="ASR engine: auto | whispercpp (single engine; legacy "
                                     "faster-whisper/cuda/cpu accepted).")
    p.add_argument("--model", "-m", help="Model name (e.g. medium, large-v3-turbo).")
    p.add_argument("--device", choices=["auto", "cuda", "cpu"], help="Compute device.")
    p.add_argument("--compute-type", help="CTranslate2 compute type (e.g. float16).")
    p.add_argument("--language", "-l",
                   help='Pin a language code (e.g. en). Use "auto" to auto-detect.')
    p.add_argument("--multilingual", action=argparse.BooleanOptionalAction, default=None,
                   help="Split mixed-language clips and transcribe each part in its own "
                        "language (--no-multilingual to force off). Default: config value.")
    p.add_argument("--hotkey",
                   help='Push-to-talk combo(s), comma-separated, e.g. "f9" or "f9,ctrl+space".')
    p.add_argument("--wakeword", action=argparse.BooleanOptionalAction, default=None,
                   help="Enable hands-free wake-word listening mode (--no-wakeword to force "
                        "off). Default: config value.")
    p.add_argument("--wake-phrase",
                   help='Wake phrase for listening mode (e.g. "mynah", "hey mynah").')
    p.add_argument("--method", choices=["paste", "type"], help="Text insertion method.")
    p.add_argument("--no-sound", action="store_true", help="Disable sound cues.")
    p.add_argument("--no-tray", "--headless", dest="headless", action="store_true",
                   help="Run as a console app without the system tray (Phase 1 behaviour).")
    p.add_argument("--settings", action="store_true",
                   help=argparse.SUPPRESS)  # internal: open the Settings window as its own
                   # process (macOS), so Tk owns this process's main thread.
    p.add_argument("--first-run-setup", action="store_true",
                   help=argparse.SUPPRESS)  # internal: open --settings in first-run setup mode.
    p.add_argument("--selftest-paste", action="store_true",
                   help=argparse.SUPPRESS)  # internal: diagnostic for the clipboard-paste path.
    p.add_argument("--selftest-keys", action="store_true",
                   help=argparse.SUPPRESS)  # internal: diagnostic for the global key listener.
    p.add_argument("--selftest-engine", action="store_true",
                   help=argparse.SUPPRESS)  # internal: diagnostic for the engine-pack download.
    p.add_argument("--version", action="version", version=f"mynah {__version__}")
    return p


def _apply_overrides(cfg: dict, args: argparse.Namespace) -> None:
    if args.engine:
        cfg["model"]["engine"] = args.engine
    if args.backend:
        cfg.setdefault("hardware", {})["backend"] = args.backend
    if args.model:
        cfg["model"]["name"] = args.model
    if args.device:
        cfg["model"]["device"] = args.device
    if args.compute_type:
        cfg["model"]["compute_type"] = args.compute_type
    if args.language:
        if args.language.lower() == "auto":
            cfg["language"]["mode"] = "auto"
        else:
            cfg["language"]["mode"] = "fixed"
            cfg["language"]["fixed"] = args.language
    if args.multilingual is not None:
        cfg["language"]["multilingual"] = args.multilingual
    if args.hotkey:
        specs = [h.strip() for h in args.hotkey.split(",") if h.strip()]
        cfg["hotkey"]["push_to_talk"] = specs
    if args.method:
        cfg["insertion"]["method"] = args.method
    if args.no_sound:
        cfg["ux"]["sound_cues"] = False
    if args.wakeword is not None:
        cfg.setdefault("wakeword", {})["enabled"] = args.wakeword
    if args.wake_phrase:
        cfg.setdefault("wakeword", {})["phrase"] = args.wake_phrase


def _list_devices() -> None:
    import sounddevice as sd

    print("Audio input devices (index: name):")
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            print(f"  {idx}: {dev['name']}")


def _print_probe() -> None:
    """Print the hardware probe + recommended backend/model."""
    from .hardware import probe_gpu, recommend_backend

    from .hardware import cuda_is_optional

    gpu = probe_gpu()
    backend, model, reason = recommend_backend(gpu)
    print("Hardware probe:")
    print(f"  GPU     : {gpu.vendor or 'none'}  ({gpu.name})")
    print(f"  VRAM    : {str(gpu.vram_mb) + ' MB' if gpu.vram_mb else 'unknown'}"
          f"   [source: {gpu.source}]")
    if len(gpu.devices) > 1:
        for d in gpu.devices:
            print(f"    - {d.get('vendor', '?'):8} {d.get('name', '')}"
                  f"  {d.get('vram_mb', 0)} MB")
    _hint = ("metal=default GPU backend · cpu=fallback" if gpu.vendor == "apple"
             else "vulkan=default GPU backend · cpu=fallback")
    print(f"  backend : {backend}   ({_hint})")
    print(f"  model   : {model}")
    if cuda_is_optional(gpu):
        print("  note    : NVIDIA detected — the CUDA pack is an optional setup upgrade "
              "for max speed (~1.3 GB).")
    print(f"  -> {reason}")


def main(argv: list[str] | None = None) -> int:
    _set_dpi_awareness()
    _set_app_user_model_id()
    _redirect_logs_if_windowed()
    _fix_windows_console()
    _fix_pyobjc_hiservices()
    args = _build_parser().parse_args(argv)

    if args.write_config:
        path = write_default_config(args.config, force=args.force)
        print(f"Config written to: {path}")
        return 0

    if args.list_devices:
        _list_devices()
        return 0

    if args.probe:
        _print_probe()
        return 0

    if args.permissions:
        if sys.platform != "darwin":
            print("--permissions is macOS-only (no TCC gates on this OS).")
            return 0
        from .permissions import check_permissions, summary_text

        print(summary_text())
        return 0 if all(p.granted for p in check_permissions()) else 1

    if getattr(args, "selftest_keys", False):
        # Diagnostic: start a pynput global key listener from THIS identity and report whether
        # macOS considers the process trusted + whether key events actually arrive. Press any
        # keys for ~8s; each is logged. Reveals Input-Monitoring/Accessibility issues in the .app.
        import time as _t

        import HIServices
        from pynput import keyboard
        print(f"selftest-keys: AXIsProcessTrusted()={HIServices.AXIsProcessTrusted()}")
        got = {"n": 0}

        def _p(key):
            got["n"] += 1
            print(f"selftest-keys: press {key!r}")

        lis = keyboard.Listener(on_press=_p)
        lis.start()
        _t.sleep(8)
        lis.stop()
        print(f"selftest-keys: received {got['n']} key events "
              f"({'OK' if got['n'] else 'NONE — listener is not receiving input'})")
        return 0

    if getattr(args, "selftest_engine", False):
        # Diagnostic: download + install the engine pack from THIS process/identity (used to
        # confirm the frozen .app can fetch over HTTPS — the certifi SSL-context fix).
        from . import components
        from .transcriber import resolve_backend

        backend = resolve_backend("auto")
        print(f"selftest-engine: installing '{backend}' pack…")
        try:
            path = components.install_engine(backend, force=True)
            ok = components.is_installed(backend)
            print(f"selftest-engine: {'OK' if ok else 'FAILED'} — installed at {path}")
            return 0 if ok else 1
        except Exception as e:
            print(f"selftest-engine: FAILED: {e!r}")
            return 1

    if getattr(args, "selftest_paste", False):
        # Diagnostic: exercise the exact clipboard-paste insert path from THIS process/identity
        # (used to debug why the frozen .app doesn't insert). Waits, then pastes a marker into
        # whatever is frontmost.
        import time as _t

        from .insert import insert_text
        print("selftest-paste: focus a text field; pasting in 4s…")
        _t.sleep(4)
        try:
            insert_text("MYNAH_SELFTEST_PASTE", method="paste", restore_clipboard=True)
            print("selftest-paste: insert_text returned without error")
        except Exception as e:
            print(f"selftest-paste: FAILED: {e!r}")
        return 0

    if args.purge_runtime:
        from .cleanup import purge_runtime

        purge_runtime()
        return 0

    if args.purge_ui:
        from .cleanup import run_model_cleanup

        run_model_cleanup()
        return 0

    cfg = load_config(args.config)
    _apply_overrides(cfg, args)

    cfgpath = args.config or config_path()

    if args.settings:
        # Internal entry point: the Settings window as its own process. On macOS the menu-bar
        # app (pystray, AppKit) owns the parent process's main thread, so Tk can't also run
        # there — we spawn this child where Tk owns the main thread (see app.open_settings).
        from .app import run_settings_process

        return run_settings_process(cfg, cfgpath, setup=args.first_run_setup)

    print(f"Mynah {__version__}")
    print(f"Config: {cfgpath}" + ("" if (args.config or config_path().is_file())
                                  else " (not found — using defaults; --write-config to create)"))

    if args.headless:
        return run_headless(cfg, args)

    from .app import run_tray
    return run_tray(cfg, args)


def run_headless(cfg: dict, args: argparse.Namespace) -> int:
    """Phase 1 console loop: load model, register hotkey, run until Ctrl+C.

    Kept as the no-UI path (`--no-tray`) and as the fallback when no tray is available.
    """
    # Heavy imports happen here, after the early-exit branches in main().
    from .controller import Controller
    from .transcriber import build_transcriber, set_backend

    # On macOS the core loop silently no-ops without the TCC grants — warn early so the user
    # knows why the hotkey/paste might not fire, with the exact panes to enable.
    if sys.platform == "darwin":
        from .permissions import missing_permissions, summary_text

        if missing_permissions():
            print(summary_text())

    # Honour the configured engine pack (auto = best installed; default GPU = Metal on Apple
    # Silicon, Vulkan elsewhere).
    set_backend(cfg.get("hardware", {}).get("backend", "auto"))

    # Load the model once; keep it resident.
    transcriber = build_transcriber(cfg["model"])
    print("Loading model ...")  # actual engine/model is reported on the OK line below
    t0 = time.time()
    try:
        transcriber.load()
    except Exception as e:
        print(f"X Failed to load model: {e}")
        return 1
    print(f"OK {transcriber.description} — loaded in {time.time() - t0:.2f}s")

    controller = Controller(cfg, transcriber)
    controller.start()

    # Import here so a missing display/input backend surfaces a clear error.
    from .hotkey import PushToTalkHotkey

    hotkey_spec = cfg["hotkey"]["push_to_talk"]
    try:
        hotkey = PushToTalkHotkey(
            hotkey_spec, controller.on_activate, controller.on_deactivate
        )
        hotkey.start()
    except Exception as e:
        print(f"X Failed to register hotkey {hotkey_spec!r}: {e}")
        controller.stop()
        return 1

    # Optional wake-word "listening mode": a tiny VAD-gated spotter on its own mic
    # stream. Headless supports it too so it can be validated by tailing the log.
    wake_listener = None
    wk = cfg.get("wakeword", {})
    if wk.get("enabled"):
        from .transcriber import lid_model_path, whispercpp_binary_dir
        from .wakeword import TinyWhisperSpotter, WakeWordListener

        try:
            bdir = str(whispercpp_binary_dir(cfg["model"]))
            tiny = str(lid_model_path("tiny"))
            spotter = TinyWhisperSpotter(bdir, tiny, samplerate=controller.samplerate)
            wake_listener = WakeWordListener(
                spotter=spotter, phrase=wk.get("phrase", "hey mynah"),
                sensitivity=float(wk.get("sensitivity", 0.5)),
                samplerate=controller.samplerate, device=controller.recorder.device,
                on_wake=controller.on_wakeword_begin,
                on_dictation=controller.on_wakeword_clip,
                on_abort=controller.on_wakeword_abort,
                is_blocked=controller.is_busy,
                silence_ms=int(wk.get("silence_ms", 900)),
                max_dictation_s=float(wk.get("max_seconds", 120)))
            wake_listener.start()
        except Exception as e:
            print(f"X couldn't start wake word: {e}")
            wake_listener = None

    lang = cfg["language"]
    lang_desc = "auto-detect" if lang.get("mode") == "auto" else f"pinned {lang.get('fixed')}"
    print()
    print(f"Ready. Hold [{hotkey.description}] to dictate ({lang_desc}). Ctrl+C to quit.")
    if wake_listener is not None:
        print(f'Wake-word listening mode ON — say "{wk.get("phrase", "hey mynah")}", pause, '
              "then dictate.")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        if wake_listener is not None:
            wake_listener.stop()
        hotkey.stop()
        controller.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
