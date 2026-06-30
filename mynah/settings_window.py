"""A small persistent settings window (Tkinter, stdlib).

Why this exists: a native tray menu closes after every click, so changing several things in a
row means re-opening it each time. This window stays open and is also the **onboarding +
download UI**: a **Models panel** (per-model status/size, Download / Use /
Remove with a shared progress bar), a **Backend** selector (Auto / Vulkan / NVIDIA CUDA / CPU),
a **run-at-login** toggle, plus the language / sound / multilingual / hotkey controls and the
inline "press a shortcut…" capture prompt. On first run it opens in **setup mode** with a
welcome banner and the recommended model.

Threading: Tk isn't thread-safe, so the whole window lives on its own thread with its own root
+ mainloop; every widget call happens on that thread (the build, the button callbacks, and the
`after` poll). The poll reads app state and reflects it, so changes made elsewhere (downloads
on a background thread, the tray menu) stay in sync without any cross-thread Tk calls.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

_ASSETS = Path(__file__).parent / "assets"
_ICO = _ASSETS / "mynah.ico"             # multi-size icon (crisp Windows titlebar)
_ICON_PNG = _ASSETS / "tray-idle.png"      # blue VT badge (fallback for non-Windows)


class SettingsWindow:
    def __init__(self, app) -> None:
        self.app = app
        self._root = None
        self._open = False
        self._starting = False               # a _run thread is spawning the window
        self._lifecycle_lock = threading.Lock()  # serialize show()/teardown decisions
        self._setup = False                  # opened in first-run setup mode
        self._icon_img = None               # keep a ref so the window icon isn't GC'd
        self._last_geometry = None          # remember position across re-opens (session)
        self._w: dict = {}                  # widget handles
        self._model_rows: dict = {}         # name -> {status, action, remove}
        self._perm_rows: dict = {}          # macOS: key -> {status label}
        self._lang_to_code: dict = {}
        self._code_to_lang: dict = {}
        self._pulsing = False
        self._tk_capturing = False  # darwin: a Tk-native hotkey capture is in progress

    # --- lifecycle ----------------------------------------------------------

    def run_blocking(self, setup: bool = False) -> None:
        """Build the window and run Tk's mainloop on the **current** thread, blocking until it
        closes. Used by the macOS ``mynah --settings`` subprocess, where Tk must own the
        process's main thread (a secondary-thread Tk root hangs / crashes on macOS, and would
        also fight pystray's AppKit run loop if both lived in one process). On Windows/Linux the
        normal in-process :meth:`show` path is used instead."""
        if setup:
            self._setup = True
        with self._lifecycle_lock:
            self._starting = True
        self._run()  # builds + mainloop() here, on this (main) thread; returns on close

    def show(self, setup: bool = False) -> None:
        if setup:
            self._setup = True
        # Serialize the open decision: a second rapid open (e.g. a tray double-click) must NOT
        # spawn a second _run thread — two `tk.Tk()` roots on two threads corrupt each other
        # (empty window that dies, "main thread is not in main loop", then a wedged state that
        # never reopens). "root exists OR a thread is starting one" ⇒ already opening, just raise.
        with self._lifecycle_lock:
            if self._starting or self._root is not None:
                root = self._root
                if root is not None:
                    try:
                        root.after(0, self._raise)
                    except Exception:
                        pass
                return
            self._starting = True
        threading.Thread(target=self._run, daemon=True).start()

    def close(self) -> None:
        if self._root is not None:
            try:
                self._root.after(0, self._on_close)
            except Exception:
                pass

    def _raise(self) -> None:
        try:
            self._root.deiconify()
            self._root.lift()
            self._root.focus_force()
        except Exception:
            pass

    def _run(self) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk
        except Exception as e:  # tkinter not available on this Python
            print(f"! settings window unavailable ({e}); opening the config file instead")
            self.app.open_config()
            return
        self._messagebox = messagebox
        try:
            self._build(tk, ttk)
            self._open = True
            self._root.mainloop()
        except Exception as e:
            print(f"! settings window error: {e}")
        finally:
            self._open = False
            self._starting = False
            self._root = None
            self._w = {}
            self._model_rows = {}
            self._perm_rows = {}

    # --- build --------------------------------------------------------------

    def _build(self, tk, ttk) -> None:
        root = tk.Tk()
        self._root = root
        root.title("Mynah Settings")
        root.resizable(False, False)
        # Build the whole window HIDDEN, then show it once at its final (centered) position.
        # Otherwise Tk maps it at the top-left default first and `_place` jerks it to centre —
        # which looked like a window flashing in the corner then a second one opening.
        root.withdraw()
        # The process is DPI-aware (see cli._set_dpi_awareness), so scale Tk's point→pixel
        # metric to the real DPI — keeps the window a sensible physical size *and* crisp on
        # high-DPI / scaled displays (instead of Windows bitmap-stretching it blurry).
        try:
            dpi = root.winfo_fpixels("1i")  # px/inch (reflects the monitor DPI when DPI-aware)
            if dpi > 0:
                root.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass
        try:  # use the Mynah badge instead of Tk's default feather icon
            if sys.platform == "win32" and _ICO.exists():
                root.iconbitmap(default=str(_ICO))   # baseline + dialog/taskbar-group icon
                self._set_win_icons(root)            # crisp titlebar + taskbar (see method)
            else:
                self._icon_img = tk.PhotoImage(file=str(_ICON_PNG))
                root.iconphoto(True, self._icon_img)
        except Exception:
            pass
        frm = ttk.Frame(root, padding=14)
        frm.grid(sticky="nsew")
        pad = {"padx": 8, "pady": 4}
        r = 0

        # --- setup-mode welcome banner -------------------------------------
        if self._setup:
            backend, model, reason = self.app.recommended()
            banner = ttk.Label(
                frm, justify="left", wraplength=420, foreground="#0a5",
                text=("Welcome to Mynah! Pick a model below to download and start dictating.\n"
                      f"Recommended for your hardware: {model}  —  {reason}"))
            banner.grid(row=r, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 8))
            r += 1

        # --- Models panel --------------------------------------------------
        models_lf = ttk.LabelFrame(frm, text="Models", padding=8)
        models_lf.grid(row=r, column=0, columnspan=4, sticky="ew", padx=6, pady=(0, 6))
        r += 1
        self._build_models_panel(tk, ttk, models_lf)

        # --- Backend selector ----------------------------------------------
        ttk.Label(frm, text="Backend").grid(row=r, column=0, sticky="w", **pad)
        backend_choices = self.app.backend_choices()
        self._backend_to_value = {lbl: val for lbl, val in backend_choices}
        self._value_to_backend = {val: lbl for lbl, val in backend_choices}
        backend_cb = ttk.Combobox(frm, state="readonly", width=22,
                                  values=[lbl for lbl, _ in backend_choices])
        backend_cb.set(self._value_to_backend.get(self.app.current_backend(),
                                                  backend_choices[0][0]))
        backend_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_backend())
        backend_cb.grid(row=r, column=1, columnspan=2, sticky="w", **pad)
        r += 1
        if self.app.cuda_optional():
            ttk.Label(frm, text="NVIDIA detected — CUDA is an optional speed upgrade "
                                "(~700 MB, self-contained). Vulkan is the default and needs "
                                "no extra download.",
                      foreground="#777", wraplength=420, justify="left").grid(
                row=r, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 4))
            r += 1

        # --- macOS permissions --------------------------------------------
        # Only on macOS, where three TCC grants gate the core loop and a stale grant after an
        # update silently breaks the Cmd+V paste. The "Reset & re-grant" button clears the grants
        # tied to the old build's code identity so the next launch re-prompts cleanly.
        self._perm_rows = {}
        if self.app.permissions_supported():
            perm_lf = ttk.LabelFrame(frm, text="Permissions (macOS)", padding=8)
            perm_lf.grid(row=r, column=0, columnspan=4, sticky="ew", padx=6, pady=(0, 6))
            r += 1
            self._build_permissions_panel(tk, ttk, perm_lf)

        autostart_var = tk.BooleanVar(value=self.app.run_at_login())
        ttk.Checkbutton(frm, text="Run Mynah at login", variable=autostart_var,
                        command=self._on_autostart).grid(
            row=r, column=0, columnspan=2, sticky="w", **pad)
        r += 1

        ttk.Separator(frm, orient="horizontal").grid(
            row=r, column=0, columnspan=4, sticky="ew", pady=8)
        r += 1

        # --- language ------------------------------------------------------
        ttk.Label(frm, text="I'm speaking").grid(row=r, column=0, sticky="w", **pad)
        choices = self.app.language_choices()
        self._lang_to_code = {lbl: code for lbl, code in choices}
        self._code_to_lang = {code: lbl for lbl, code in choices}
        lang_cb = ttk.Combobox(frm, state="readonly", width=22, height=10,
                               values=[lbl for lbl, _ in choices])
        lang_cb.set(self._code_to_lang.get(self.app.current_language(), choices[0][0]))
        lang_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_lang())
        lang_cb.grid(row=r, column=1, columnspan=2, sticky="w", **pad)
        self._install_typeahead(lang_cb, self._on_lang)
        r += 1
        ttk.Label(frm, text="Leave on Auto-detect. Pin a language only if auto-detect picks "
                            "the wrong one — a pinned language you don't speak gets translated.",
                  foreground="#777", wraplength=420, justify="left").grid(
            row=r, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 4))
        r += 1

        sound_var = tk.BooleanVar(value=self.app.sound_enabled())
        ttk.Checkbutton(frm, text="Sound cues", variable=sound_var,
                        command=self._on_sound).grid(row=r, column=0, columnspan=2,
                                                     sticky="w", **pad)
        r += 1

        multi_var = tk.BooleanVar(value=self.app.multilingual_enabled())
        ttk.Checkbutton(frm, text="Multilingual (split mixed-language clips)",
                        variable=multi_var, command=self._on_multi).grid(
            row=r, column=0, columnspan=4, sticky="w", **pad)
        r += 1

        # --- wake word (listening mode) ------------------------------------
        wake_lf = ttk.LabelFrame(frm, text="Listening mode (wake word)", padding=8)
        wake_lf.grid(row=r, column=0, columnspan=4, sticky="ew", padx=6, pady=(2, 6))
        r += 1
        wake_var = tk.BooleanVar(value=self.app.wakeword_enabled())
        ttk.Checkbutton(wake_lf, text="Say a wake word to start dictating hands-free",
                        variable=wake_var, command=self._on_wakeword).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=2)
        ttk.Label(wake_lf, text="Wake phrase").grid(row=1, column=0, sticky="w", pady=2)
        phrase_var = tk.StringVar(value=self.app.wakeword_phrase())
        phrase_entry = ttk.Entry(wake_lf, textvariable=phrase_var, width=20)
        phrase_entry.grid(row=1, column=1, columnspan=2, sticky="w", padx=6)
        phrase_entry.bind("<Return>", lambda _e: self._on_wake_phrase())
        phrase_entry.bind("<FocusOut>", lambda _e: self._on_wake_phrase())
        ttk.Label(wake_lf, text="Sensitivity (0–1)").grid(row=2, column=0, sticky="w", pady=2)
        sens_var = tk.DoubleVar(value=self.app.wakeword_sensitivity())
        sens_val = ttk.Label(wake_lf, width=7, text=f"{sens_var.get():.2f}")
        sens_val.grid(row=2, column=2, sticky="w")
        sens_scale = ttk.Scale(wake_lf, from_=0.0, to=1.0, orient="horizontal", length=150,
                               variable=sens_var,
                               command=lambda v: sens_val.config(text=f"{float(v):.2f}"))
        sens_scale.grid(row=2, column=1, sticky="w", padx=6)
        sens_scale.bind("<ButtonRelease-1>", lambda _e: self._on_wake_sensitivity())
        ttk.Label(wake_lf, text="Stop delay (0.4–3.0 s)").grid(row=3, column=0, sticky="w", pady=2)
        sil_var = tk.DoubleVar(value=self.app.wakeword_silence_ms())
        sil_val = ttk.Label(wake_lf, width=7, text=f"{sil_var.get() / 1000:.1f} s")
        sil_val.grid(row=3, column=2, sticky="w")
        sil_scale = ttk.Scale(wake_lf, from_=400, to=3000, orient="horizontal", length=150,
                              variable=sil_var,
                              command=lambda v: sil_val.config(text=f"{float(v) / 1000:.1f} s"))
        sil_scale.grid(row=3, column=1, sticky="w", padx=6)
        sil_scale.bind("<ButtonRelease-1>", lambda _e: self._on_wake_silence())
        ttk.Label(wake_lf, justify="left", wraplength=420, foreground="#777",
                  text='Say the phrase, pause, then speak. Raise "stop delay" if it cuts you '
                       'off mid-phrase. A two-word carrier phrase ("hey mynah") triggers far '
                       "more reliably than one bare word. The mic is read locally while this is on "
                       "— nothing is uploaded.").grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(2, 0))

        ttk.Separator(frm, orient="horizontal").grid(
            row=r, column=0, columnspan=4, sticky="ew", pady=8)
        r += 1

        # --- hotkeys -------------------------------------------------------
        ttk.Label(frm, text="Hold-to-talk").grid(row=r, column=0, sticky="w", **pad)
        ptt_val = ttk.Label(frm, text=self.app.hotkey_desc("ptt"), width=14)
        ptt_val.grid(row=r, column=1, sticky="w", **pad)
        ptt_change = ttk.Button(frm, text="Change", command=lambda: self._on_change("ptt"))
        ptt_change.grid(row=r, column=2)
        ptt_reset = ttk.Button(frm, text="Reset", command=lambda: self.app.reset_hotkey("ptt"))
        ptt_reset.grid(row=r, column=3)
        r += 1

        ttk.Label(frm, text="Toggle on/off").grid(row=r, column=0, sticky="w", **pad)
        tog_val = ttk.Label(frm, text=self.app.hotkey_desc("toggle"), width=14)
        tog_val.grid(row=r, column=1, sticky="w", **pad)
        tog_change = ttk.Button(frm, text="Change", command=lambda: self._on_change("toggle"))
        tog_change.grid(row=r, column=2)
        tog_reset = ttk.Button(frm, text="Reset", command=lambda: self.app.reset_hotkey("toggle"))
        tog_reset.grid(row=r, column=3)
        r += 1

        status = ttk.Label(frm, text="", foreground="#1769aa", wraplength=320)
        status.grid(row=r, column=0, columnspan=3, sticky="w", **pad)
        cancel_btn = ttk.Button(frm, text="Cancel", command=self.app.cancel_hotkey_capture)
        cancel_btn.grid(row=r, column=3)
        cancel_btn.grid_remove()
        r += 1

        ttk.Separator(frm, orient="horizontal").grid(
            row=r, column=0, columnspan=4, sticky="ew", pady=8)
        r += 1
        ttk.Button(frm, text="Open config file…", command=self.app.open_config).grid(
            row=r, column=0, columnspan=2, sticky="w", **pad)
        ttk.Button(frm, text="Close", command=self._on_close).grid(
            row=r, column=3, sticky="e", **pad)

        self._w.update({
            "backend": backend_cb, "autostart": autostart_var,
            "lang": lang_cb, "sound": sound_var, "multi": multi_var,
            "wake": wake_var, "wake_phrase": phrase_var, "wake_sens": sens_var,
            "wake_sil": sil_var,
            "ptt_val": ptt_val, "tog_val": tog_val, "status": status, "cancel": cancel_btn,
            "buttons": [ptt_change, ptt_reset, tog_change, tog_reset],
        })
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._place(root)
        self._raise()
        self._poll()

    def _build_models_panel(self, tk, ttk, parent) -> None:
        """One row per catalog model: name · status · Download/Use · Remove, with a shared
        progress bar + status line below."""
        for i, name in enumerate(self.app.model_catalog()):
            ttk.Label(parent, text=name, width=20).grid(row=i, column=0, sticky="w", pady=2)
            st = ttk.Label(parent, text="", width=18, foreground="#555")
            st.grid(row=i, column=1, sticky="w", padx=6)
            action = ttk.Button(parent, text="…", width=10,
                                command=lambda n=name: self._on_model_action(n))
            action.grid(row=i, column=2, padx=2)
            remove = ttk.Button(parent, text="Remove", width=8,
                                command=lambda n=name: self._on_remove(n))
            remove.grid(row=i, column=3, padx=2)
            self._model_rows[name] = {"status": st, "action": action, "remove": remove}

        n = len(self._model_rows)
        pb = ttk.Progressbar(parent, mode="determinate", maximum=100, length=320)
        pb.grid(row=n, column=0, columnspan=4, sticky="ew", pady=(8, 2))
        pstatus = ttk.Label(parent, text="", foreground="#1769aa", wraplength=360)
        pstatus.grid(row=n + 1, column=0, columnspan=4, sticky="w")
        self._w["progress"] = pb
        self._w["pstatus"] = pstatus

    def _build_permissions_panel(self, tk, ttk, parent) -> None:
        """macOS-only: one row per TCC grant (status + 'Open Settings'), plus a 'Reset &
        re-grant' button + a one-line hint. Status is refreshed live in :meth:`_sync`."""
        rows = self.app.permission_rows()
        for i, p in enumerate(rows):
            ttk.Label(parent, text=p["label"], width=18).grid(
                row=i, column=0, sticky="w", pady=2)
            st = ttk.Label(parent, text="", width=12)
            st.grid(row=i, column=1, sticky="w", padx=6)
            ttk.Button(parent, text="Open Settings", width=13,
                       command=lambda k=p["key"]: self.app.open_permission_pane(k)).grid(
                row=i, column=2, padx=2)
            self._perm_rows[p["key"]] = st

        n = len(rows)
        ttk.Button(parent, text="Reset & re-grant",
                   command=self._on_reset_permissions).grid(
            row=n, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Label(parent, justify="left", wraplength=360, foreground="#777",
                  text="Updated and dictation stopped pasting? macOS ties each grant to the app "
                       "version. Click Reset, relaunch Mynah, then re-enable it under "
                       "Accessibility and Input Monitoring.").grid(
            row=n + 1, column=0, columnspan=4, sticky="w", pady=(0, 2))

    def _on_reset_permissions(self) -> None:
        self.app.reset_permissions()

    def _set_win_icons(self, root) -> None:
        """Crisp titlebar (top-left) + taskbar-button icons on Windows.

        Tk's ``iconbitmap`` loads a single frame from the .ico and lets Windows rescale it for
        the small titlebar icon and the larger taskbar button — which looks blurry on scaled /
        HiDPI displays. Instead we hand Windows the **exact** small and big frames straight from
        the multi-size .ico (``LoadImageW`` + ``WM_SETICON``), so each is pre-rendered at its
        target size and never rescaled. All handle params are prototyped as pointers so 64-bit
        HWND/HICON values aren't truncated (the usual ctypes-on-Win64 footgun)."""
        try:
            import ctypes

            root.update_idletasks()
            u = ctypes.windll.user32
            u.GetAncestor.restype = ctypes.c_void_p
            u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            u.LoadImageW.restype = ctypes.c_void_p
            u.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            u.SendMessageW.restype = ctypes.c_void_p
            u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.c_void_p, ctypes.c_void_p]

            # The toplevel HWND that owns the titlebar / taskbar button (Tk's winfo_id is the
            # content child; its GA_ROOT ancestor is the frame). Falls back to winfo_id itself.
            hwnd = u.GetAncestor(root.winfo_id(), 2) or root.winfo_id()  # GA_ROOT = 2

            dpi = 96
            try:
                u.GetDpiForWindow.restype = ctypes.c_uint
                u.GetDpiForWindow.argtypes = [ctypes.c_void_p]
                dpi = int(u.GetDpiForWindow(hwnd)) or 96
            except Exception:
                pass

            def metric(idx: int, default: int) -> int:
                try:
                    u.GetSystemMetricsForDpi.restype = ctypes.c_int
                    u.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, ctypes.c_uint]
                    v = int(u.GetSystemMetricsForDpi(idx, dpi))
                    return v if v > 0 else default
                except Exception:
                    return default

            IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
            WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
            path = str(_ICO)
            self._hicons = []  # keep refs so the HICONs aren't freed while the window uses them
            for size, which in ((metric(49, 16), ICON_SMALL),   # SM_CXSMICON
                                (metric(11, 32), ICON_BIG)):     # SM_CXICON
                h = u.LoadImageW(None, path, IMAGE_ICON, size, size, LR_LOADFROMFILE)
                if h:
                    u.SendMessageW(hwnd, WM_SETICON, ctypes.c_void_p(which), ctypes.c_void_p(h))
                    self._hicons.append(h)
        except Exception:
            pass

    def _place(self, root) -> None:
        """Restore last session position, else center on screen (not top-left)."""
        root.update_idletasks()
        if self._last_geometry:
            try:
                root.geometry(self._last_geometry)
                return
            except Exception:
                pass
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 4)
        root.geometry(f"+{x}+{y}")

    # --- callbacks ----------------------------------------------------------

    def _on_model_action(self, name: str) -> None:
        if self.app.is_busy():
            self._messagebox.showinfo("Mynah", "A download is already in progress.")
            return
        if self.app.model_is_installed(name):
            if not self.app.is_active_model(name):
                self.app.select_model(name)
            return
        gb = self.app.model_status_text(name).lstrip("↓ ~").rstrip()
        if self._messagebox.askyesno(
                "Download model",
                f"Download {name} ({gb}) into the shared model cache?\n\n"
                "This also fetches the engine pack and the small multilingual weights if "
                "they aren't present yet."):
            self.app.download_model(name)

    def _on_remove(self, name: str) -> None:
        if not self.app.model_is_installed(name):
            return
        if self.app.is_active_model(name):
            self._messagebox.showinfo(
                "Mynah", f"{name} is the active model — switch to another first.")
            return
        if self._messagebox.askyesno(
                "Remove model",
                f"Delete {name} from the shared Hugging Face cache?\n\n"
                "Note: the cache may be shared with other apps."):
            self.app.remove_model(name)

    def _on_backend(self) -> None:
        label = self._w["backend"].get()
        value = self._backend_to_value.get(label, "auto")
        # Show the NVIDIA license + download prompt only the **first** time (pack not installed
        # yet). Once installed, selecting CUDA just switches — no install/license dialog.
        if value == "cuda" and not self.app.backend_installed("cuda"):
            lic = self.app.cuda_license()
            note = lic[0] if lic else "Installs NVIDIA cuBLAS under NVIDIA's CUDA Toolkit EULA."
            url = lic[1] if lic else ""
            if not self._messagebox.askokcancel(
                    "Optional NVIDIA CUDA pack",
                    f"{note}\n\n{url}\n\nDownload and use the CUDA backend?"):
                # reverted: restore the previous selection
                self._w["backend"].set(self._value_to_backend.get(
                    self.app.current_backend(), label))
                return
        self.app.select_backend(value)

    def _on_autostart(self) -> None:
        state = self.app.set_run_at_login(self._w["autostart"].get())
        self._w["autostart"].set(state)

    def _on_lang(self) -> None:
        self.app.select_language(self._lang_to_code.get(self._w["lang"].get()))

    def _on_sound(self) -> None:
        if self._w["sound"].get() != self.app.sound_enabled():
            self.app.toggle_sound()

    def _on_multi(self) -> None:
        if self._w["multi"].get() != self.app.multilingual_enabled():
            self.app.toggle_multilingual()

    def _on_wakeword(self) -> None:
        if self._w["wake"].get() != self.app.wakeword_enabled():
            self.app.toggle_wakeword()

    def _on_wake_phrase(self) -> None:
        self.app.set_wakeword_phrase(self._w["wake_phrase"].get())

    def _on_wake_sensitivity(self) -> None:
        self.app.set_wakeword_sensitivity(self._w["wake_sens"].get())

    def _on_wake_silence(self) -> None:
        self.app.set_wakeword_silence_ms(self._w["wake_sil"].get())

    def _on_change(self, kind: str) -> None:
        # macOS: capture the shortcut with Tk's own key events, NOT pynput. pynput's global
        # listener resolves keycodes via macOS TSM (Text Input Source) APIs on its listener
        # thread, and those are main-thread-only — pressing a key during capture trips a
        # dispatch_assert_queue / BPT-trap and crashes the process (the "reopen?" dialog). Tk
        # delivers keypresses on the main thread, needs no Input Monitoring grant, and the
        # captured spec is written to config the same way (the menu-bar app re-arms it).
        if sys.platform == "darwin":
            self._capture_hotkey_tk(kind)
            return
        self.app.begin_hotkey_capture(kind)  # poll reflects the prompt + disables controls

    # Tk keysym -> the token parse_hotkey() understands.
    _TK_KEYSYM_TOKENS = {
        "space": "space", "Tab": "tab", "Return": "enter", "Escape": "esc",
        "Caps_Lock": "capslock", "Home": "home", "End": "end",
        "Prior": "pageup", "Next": "pagedown", "Insert": "insert", "Pause": "pause",
    }

    def _capture_hotkey_tk(self, kind: str) -> None:
        """Capture one key-combo via Tk and persist it (darwin Settings subprocess).

        Grabs the keyboard, shows the prompt, and resolves the first non-modifier keypress to a
        spec string ("f9", "ctrl+space", "cmd+shift+d"). Esc cancels."""
        root = self._root
        if root is None or self._tk_capturing:
            return
        self._tk_capturing = True  # tell the poll to leave the prompt + buttons alone
        label = "Hold-to-talk" if kind == "ptt" else "Toggle on/off"
        self._w["status"].config(text=f"Press a shortcut for {label} now…  (Esc to cancel)")
        for b in self._w["buttons"]:
            b.config(state="disabled")
        self._w["cancel"].grid()

        def finish(spec):
            self._tk_capturing = False
            try:
                root.unbind("<KeyPress>")
            except Exception:
                pass
            try:
                root.grab_release()
            except Exception:
                pass
            self._w["cancel"].grid_remove()
            for b in self._w["buttons"]:
                b.config(state="normal")
            if spec:
                ok = self.app.set_hotkey(kind, spec)
                self._w["status"].config(
                    text=(f"{label} set to {spec}." if ok else f"Couldn't use '{spec}'."))
                # refresh the shown value immediately
                self._conf(self._w["ptt_val"], text=self.app.hotkey_desc("ptt"))
                self._conf(self._w["tog_val"], text=self.app.hotkey_desc("toggle"))
            else:
                self._w["status"].config(text="")

        # Let the on-screen Cancel button back out too.
        self._w["cancel"].config(command=lambda: finish(None))

        def on_key(event):
            ks = event.keysym
            if ks in ("Escape",):
                finish(None)
                return "break"
            # Ignore a bare modifier press — wait for the real key.
            if ks in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
                      "Meta_L", "Meta_R", "Super_L", "Super_R", "Command", "Option"):
                return "break"
            mods = []
            s = event.state
            # Tk modifier bitmask: Control=0x4, Shift=0x1; on macOS Command=0x8 (Mod1)/0x10,
            # Option(Alt)=0x10/0x20. Cover the common bits.
            if s & 0x4:
                mods.append("ctrl")
            if s & 0x20000 or s & 0x8:    # Command (macOS)
                mods.append("cmd")
            if s & 0x10 or s & 0x80000:   # Option/Alt (macOS)
                mods.append("alt")
            if s & 0x1:
                mods.append("shift")
            # The main key token.
            tok = self._TK_KEYSYM_TOKENS.get(ks)
            if tok is None:
                if len(ks) == 1 and ks.isprintable():
                    tok = ks.lower()
                elif len(ks) >= 2 and ks[0] in "fF" and ks[1:].isdigit():
                    tok = ks.lower()              # F1..F24
                else:
                    tok = ks.lower()              # best effort
            spec = "+".join(mods + [tok])
            finish(spec)
            return "break"

        try:
            root.bind("<KeyPress>", on_key)
            root.grab_set()
            root.focus_force()
        except Exception as e:
            print(f"! Tk hotkey capture couldn't start: {e}")
            finish(None)

    def _install_typeahead(self, cb, on_commit) -> None:
        """First-letter / prefix type-ahead for a readonly ``ttk.Combobox`` (language list)."""
        import time as _time

        values = list(cb.cget("values"))
        st = {"buf": "", "t": 0.0}

        def first(prefix: str) -> int:
            p = prefix.lower()
            return next((i for i, v in enumerate(values) if v.lower().startswith(p)), -1)

        def nxt(ch: str, after: int) -> int:
            c, n = ch.lower(), len(values)
            for off in range(1, n + 1):
                i = (after + off) % n
                if values[i].lower().startswith(c):
                    return i
            return -1

        def pick(ch: str, after: int) -> int:
            now = _time.time()
            if now - st["t"] > 0.8:
                st["buf"] = ""
            st["t"] = now
            ext = st["buf"] + ch
            if len(ext) > 1:
                i = first(ext)
                if i >= 0:
                    st["buf"] = ext
                    return i
            st["buf"] = ch
            return nxt(ch, after)

        def cur_index() -> int:
            try:
                return values.index(cb.get())
            except ValueError:
                return -1

        def listbox():
            try:
                lb = f'{cb.tk.call("ttk::combobox::PopdownWindow", cb)}.f.l'
                return lb if cb.tk.call("winfo", "exists", lb) else None
            except Exception:
                return None

        def on_key(event):
            ch = event.char
            if not ch or not ch.isprintable() or ch.isspace():
                return None  # leave Enter/Space/Tab/arrows to default handling
            i = pick(ch, cur_index())
            if i < 0:
                return "break"
            cb.current(i)
            on_commit()
            return "break"

        def on_listbox_key(ch):  # raw-Tcl bound; receives the %A char
            if not ch or not ch.isprintable() or ch.isspace():
                return
            lb = listbox()
            try:
                active = int(cb.tk.call(lb, "index", "active")) if lb else cur_index()
            except Exception:
                active = cur_index()
            i = pick(ch, active)
            if i < 0 or not lb:
                return
            cb.tk.call(lb, "selection", "clear", 0, "end")
            cb.tk.call(lb, "selection", "set", i)
            cb.tk.call(lb, "activate", i)
            cb.tk.call(lb, "see", i)

        cb.bind("<KeyPress>", on_key, add="+")
        lb = listbox()
        if lb:
            try:
                cb.tk.call("bind", lb, "<KeyPress>", cb.register(on_listbox_key) + " %A")
            except Exception:
                pass

    def _on_close(self) -> None:
        if self.app.is_capturing():
            self.app.cancel_hotkey_capture()
        root = self._root
        if root is not None:
            try:  # remember only the POSITION, not the size — so a later non-setup open (no
                  # welcome banner) sizes to its own content instead of inheriting the taller
                  # setup-mode height and leaving empty padding below the buttons.
                self._last_geometry = f"+{root.winfo_x()}+{root.winfo_y()}"
            except Exception:
                pass
        self._open = False
        self._starting = False
        self._setup = False
        self._root = None
        self._icon_img = None
        self._w = {}
        self._model_rows = {}
        self._perm_rows = {}
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass

    # --- poll: keep the UI in sync with app state ---------------------------

    def _poll(self) -> None:
        root = self._root
        if root is None:
            return
        try:
            self._sync()
        except Exception:
            pass
        try:
            root.after(200, self._poll)
        except Exception:
            pass

    @staticmethod
    def _conf(widget, **opts) -> None:
        """Apply only the options that actually changed. The 200 ms poll reflects app state onto
        every widget; calling ``.config()`` on a ttk button each tick (even with the same value)
        resets its hover/active visual — the bug where a button's hover outline vanished almost
        as soon as it appeared, while the never-polled bottom buttons stayed fine. Skipping
        unchanged options leaves the hover state untouched."""
        changed = {k: v for k, v in opts.items() if str(widget.cget(k)) != str(v)}
        if changed:
            widget.config(**changed)

    def _sync(self) -> None:
        w = self._w
        # While a Tk-native hotkey capture is running (darwin), leave the prompt + buttons +
        # hotkey labels exactly as the capture set them — otherwise this 200ms poll instantly
        # clears the "Press a shortcut…" prompt and re-enables the buttons.
        if self._tk_capturing:
            self._sync_models()
            return
        self._conf(w["ptt_val"], text=self.app.hotkey_desc("ptt"))
        self._conf(w["tog_val"], text=self.app.hotkey_desc("toggle"))

        self._sync_models()

        kind = self.app.capturing_kind()
        if kind is not None:
            label = "Hold-to-talk" if kind == "ptt" else "Toggle on/off"
            self._conf(w["status"], text=f"Press a shortcut for {label} now…  (Esc to cancel)")
            for b in w["buttons"]:
                self._conf(b, state="disabled")
            self._conf(w["lang"], state="disabled")
            w["cancel"].grid()
            return

        for b in w["buttons"]:
            self._conf(b, state="normal")
        w["cancel"].grid_remove()
        if self.app.is_switching_model() or self.app.is_loading():
            # Show the model being loaded, not the still-configured one (the config name only
            # flips to the target *after* the swap completes, so current_model() lags here).
            target = self.app.loading_model() or self.app.current_model()
            self._conf(w["status"], text=f"Loading {target}…")
        else:
            self._conf(w["status"], text="")
            if str(w["lang"]["state"]) == "disabled":
                self._conf(w["lang"], state="readonly")

        cur_lang = self._code_to_lang.get(self.app.current_language())
        if cur_lang and w["lang"].get() != cur_lang:
            w["lang"].set(cur_lang)
        if w["sound"].get() != self.app.sound_enabled():
            w["sound"].set(self.app.sound_enabled())
        if w["multi"].get() != self.app.multilingual_enabled():
            w["multi"].set(self.app.multilingual_enabled())
        if w.get("wake") is not None and w["wake"].get() != self.app.wakeword_enabled():
            w["wake"].set(self.app.wakeword_enabled())

        self._sync_permissions()

    def _sync_permissions(self) -> None:
        """Refresh the macOS permission status labels (granted/denied/unknown). No-op when the
        panel isn't built (off macOS)."""
        if not self._perm_rows:
            return
        marks = {"granted": ("● Granted", "#0a7"),
                 "denied": ("✕ Not granted", "#c0392b"),
                 "unknown": ("? Check", "#b8860b")}
        for p in self.app.permission_rows():
            label = self._perm_rows.get(p["key"])
            if label is None:
                continue
            text, color = marks.get(p["state"], ("? Check", "#b8860b"))
            self._conf(label, text=text, foreground=color)

    def _sync_models(self) -> None:
        # "Busy" for the Models panel = any download/backend op (the _busy lock) **or** a model
        # load/switch in flight. A plain load/switch doesn't take _busy, so without this the
        # Use/Download/Remove buttons stay clickable while a model is loading (the bug where the
        # buttons are live during the startup load and during a switch).
        busy = (self.app.is_busy() or self.app.is_loading()
                or self.app.is_switching_model())
        loading = self.app.loading_model()  # the model being loaded/switched to (None when idle)
        for name, row in self._model_rows.items():
            installed = self.app.model_is_installed(name)
            active = self.app.is_active_model(name)
            text = self.app.model_status_text(name)          # "● Installed (3.1 GB)" / "↓ ~3.0 GB"
            # Mid-switch no model is "active" yet, so without this the target *and* the previous
            # model both read "Use". Flag the row being loaded so the switch is unambiguous.
            if name == loading:
                text = "Loading…"
            elif active:
                text = text.replace("Installed", "Active")    # mark the loaded one
            self._conf(row["status"], text=text,
                       foreground="#0a7" if (installed or name == loading) else "#777")
            if name == loading:
                self._conf(row["action"], text="Loading…", state="disabled")
            elif active:
                self._conf(row["action"], text="Active", state="disabled")
            elif installed:
                self._conf(row["action"], text="Use", state=("disabled" if busy else "normal"))
            else:
                self._conf(row["action"], text="Download",
                           state=("disabled" if busy else "normal"))
            self._conf(row["remove"],
                       state=("normal" if (installed and not active and name != loading
                                           and not busy) else "disabled"))

        st = self.app.progress_state()
        pb, ps = self._w.get("progress"), self._w.get("pstatus")
        if pb is None:
            return
        if st["active"]:
            if st["total"]:
                if self._pulsing:
                    pb.stop()
                    self._pulsing = False
                pb.config(mode="determinate")
                pb["value"] = max(0, min(100, 100 * st["done"] / st["total"]))
            else:
                if not self._pulsing:
                    pb.config(mode="indeterminate")
                    pb.start(60)
                    self._pulsing = True
            ps.config(text=st["text"])
        else:
            if self._pulsing:
                pb.stop()
                self._pulsing = False
            pb.config(mode="determinate")
            pb["value"] = 0
            ps.config(text=st.get("text", ""))
