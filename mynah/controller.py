"""Orchestrates one push-to-talk dictation: record → transcribe → insert.

Critical Windows constraint: the push-to-talk callbacks run on the OS low-level
keyboard-hook thread. If that callback does any slow work (e.g. opening the audio
stream), Windows silently drops the hook after `LowLevelHooksTimeout` (~300 ms) and
the app goes deaf. So `on_activate`/`on_deactivate` must return *instantly* — they
only drop a command on a queue. Two daemon threads do the real work:

  * control thread  — starts/stops the recorder (kept off the hook thread)
  * worker thread   — transcribes + inserts (slow; mustn't block start/stop)

Phase 2 adds runtime reconfiguration so the tray can drive it live: a status
callback (idle/recording/transcribing) for the tray icon, a model swap guarded by a
lock so it can't race an in-flight transcription, and live language / sound toggles.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

import numpy as np

from .cues import CuePlayer
from .insert import insert_text
from .recorder import Recorder
from .transcriber import Transcriber

# Status values surfaced to the tray (and anyone else watching).
IDLE = "idle"
RECORDING = "recording"
TRANSCRIBING = "transcribing"


class Controller:
    def __init__(self, config: dict, transcriber: Transcriber | None = None) -> None:
        self.cfg = config
        self.transcriber = transcriber
        # Called when a dictation is attempted before any model is loaded (lazy load).
        self.on_no_model: Callable[[], None] | None = None
        # Set by the app when wake-word listening mode is active, so a PTT/toggle keypress
        # *stops* an in-progress wake-word dictation instead of starting a second recording.
        self.wake_is_capturing: Callable[[], bool] | None = None
        self.wake_interrupt: Callable[[], None] | None = None
        self._suppress_ptt_release = False

        audio_cfg = config["audio"]
        self.samplerate = int(audio_cfg.get("sample_rate", 16000))
        self.recorder = Recorder(
            samplerate=self.samplerate,
            device=_resolve_device(audio_cfg.get("input_device", "default")),
        )

        self.sound_cues = bool(config["ux"].get("sound_cues", True))
        self.min_clip_ms = int(config["ux"].get("min_clip_ms", 300))
        self.cue_device = _resolve_device(config["ux"].get("cue_device", "default"))
        self._cue_overrides = {
            "start": config["ux"].get("cue_start_file", ""),
            "stop": config["ux"].get("cue_stop_file", ""),
        }
        self._cues: CuePlayer | None = None
        if self.sound_cues:
            self._cues = self._build_cues()

        self.insert_method = config["insertion"].get("method", "paste")
        self.restore_clipboard = bool(config["insertion"].get("restore_clipboard", True))

        self.language = _resolve_language(config["language"])

        # Multilingual dictation (default off): a cheap LID gate keeps single-language
        # clips on the fast single-pass path; only mixed clips are split. The coordinator
        # owns its own tiny LID model, loaded lazily (off the startup path) on first enable.
        lang_cfg = config["language"]
        self.multilingual = bool(lang_cfg.get("multilingual", False))
        self._lid_model = str(lang_cfg.get("lid_model", "tiny"))
        self._lid_device = config["model"].get("device", "auto")
        self._multi = None  # MultilingualCoordinator | None
        self._multi_lock = threading.Lock()
        # Preload the LID/VAD only once an ASR model is present (it shares the engine build).
        # In the lazy-load path (transcriber=None) the app calls start_multilingual_preload()
        # after the model finishes loading, so we don't spam a missing-engine error at startup.
        if self.multilingual and transcriber is not None:
            threading.Thread(target=self._ensure_multi, daemon=True).start()

        # Status + the optional listener (the tray) that mirrors it to its icon.
        self.status = IDLE
        self._on_status: Callable[[str], None] | None = None

        # Held during transcription *and* during a model swap, so swapping the model
        # never frees one out from under an in-flight transcribe() call.
        self._model_lock = threading.Lock()

        self._cmd_q: "queue.Queue[str | None]" = queue.Queue()
        self._audio_q: "queue.Queue[np.ndarray | None]" = queue.Queue()
        self._control_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._running = False

    # --- status -------------------------------------------------------------

    def set_status_callback(self, cb: Callable[[str], None] | None) -> None:
        """Register a listener (e.g. the tray) notified on every status change."""
        self._on_status = cb

    def _set_status(self, status: str) -> None:
        if status == self.status:
            return
        self.status = status
        if self._on_status is not None:
            try:
                self._on_status(status)
            except Exception as e:  # never let the UI callback break the loop
                print(f"! status callback error: {e}")

    # --- hotkey callbacks: run on the keyboard-hook thread; MUST be instant ---

    def _wake_capturing(self) -> bool:
        return self.wake_is_capturing is not None and self.wake_is_capturing()

    def on_activate(self) -> None:
        # If a wake-word dictation is in progress, F9 *stops* it (and we swallow the matching
        # key-release) rather than starting a parallel push-to-talk recording.
        if self._wake_capturing():
            if self.wake_interrupt is not None:
                self.wake_interrupt()
            self._suppress_ptt_release = True
            return
        self._cmd_q.put("start")

    def on_deactivate(self) -> None:
        if self._suppress_ptt_release:
            self._suppress_ptt_release = False
            return
        self._cmd_q.put("stop")

    def on_toggle(self) -> None:
        """Switch-style trigger: start if idle, stop+transcribe if recording. While a wake-word
        dictation is active, instead stop *that* (don't start a separate toggle recording)."""
        if self._wake_capturing():
            if self.wake_interrupt is not None:
                self.wake_interrupt()
            return
        self._cmd_q.put("toggle")

    # --- wake-word "listening mode" hooks -------------------------
    # The WakeWordListener owns its own mic stream and end-of-speech (VAD) detection, so it
    # bypasses the recorder/hotkey path and feeds the same transcribe→insert worker directly.

    def on_wakeword_begin(self) -> None:
        """Wake phrase matched: cue + show recording while the user dictates."""
        self._cue("start")
        self._set_status(RECORDING)

    def on_wakeword_clip(self, audio: np.ndarray) -> None:
        """A captured hands-free dictation clip → transcribe + insert (same path as PTT)."""
        self._cue("stop")
        duration_ms = (len(audio) / self.samplerate) * 1000 if len(audio) else 0
        if duration_ms < self.min_clip_ms:
            print(f"(ignored {duration_ms:.0f} ms wake clip)")
            self._set_status(IDLE)
            return
        self._set_status(TRANSCRIBING)
        self._audio_q.put(audio)

    def on_wakeword_abort(self) -> None:
        """Wake heard but no dictation followed (timeout) — return to idle quietly."""
        self._set_status(IDLE)

    def _cue(self, name: str) -> None:
        if self._cues is not None:
            self._cues.play(name)

    # --- control thread: owns the recorder, off the hook thread ---

    def _begin_recording(self) -> None:
        if not self.recorder.is_recording:
            self._cue("start")
            self.recorder.start()
            self._set_status(RECORDING)

    def _end_recording(self) -> None:
        if not self.recorder.is_recording:
            return
        audio = self.recorder.stop()
        self._cue("stop")
        duration_ms = (len(audio) / self.samplerate) * 1000 if len(audio) else 0
        if duration_ms < self.min_clip_ms:
            print(f"(ignored {duration_ms:.0f} ms tap)")
            self._set_status(IDLE)
        else:
            self._set_status(TRANSCRIBING)
            self._audio_q.put(audio)

    def _control_loop(self) -> None:
        while True:
            cmd = self._cmd_q.get()
            if cmd is None:
                break
            try:
                if cmd == "start":
                    self._begin_recording()
                elif cmd == "stop":
                    self._end_recording()
                elif cmd == "toggle":
                    self._end_recording() if self.recorder.is_recording else self._begin_recording()
            except Exception as e:
                self._cue("error")
                self._set_status(IDLE)
                print(f"! recording error: {e}")

    # --- worker thread: transcription + insertion (slow) ---

    def _worker_loop(self) -> None:
        while True:
            audio = self._audio_q.get()
            if audio is None:
                break
            if self.transcriber is None:
                # Lazy load: a hotkey was pressed before a model exists. Surface a
                # clear "pick a model first" prompt instead of crashing on a None engine.
                if self.on_no_model is not None:
                    try:
                        self.on_no_model()
                    except Exception:
                        pass
                if self._audio_q.empty() and not self.recorder.is_recording:
                    self._set_status(IDLE)
                continue
            try:
                t0 = time.time()
                with self._model_lock:
                    if self.multilingual and self._multi is not None and self._multi.ready:
                        # Coordinator holds the lock for the whole (multi-pass) clip, so a
                        # model swap can't free the engine out from under it mid-stitch.
                        text = self._multi.transcribe(
                            audio, self.transcriber, language=self.language
                        )
                    else:
                        text = self.transcriber.transcribe(audio, language=self.language)
                elapsed = time.time() - t0
                clip_s = len(audio) / self.samplerate
                if text:
                    print(f'[{clip_s:.1f}s clip → {elapsed:.2f}s] "{text}"')
                    insert_text(
                        text,
                        method=self.insert_method,
                        restore_clipboard=self.restore_clipboard,
                    )
                else:
                    print(f"[{clip_s:.1f}s clip → {elapsed:.2f}s] (no speech detected)")
            except Exception as e:
                self._cue("error")
                print(f"! transcription/insert failed: {e}")
            finally:
                # Back to idle once the backlog is drained and we're not recording
                # the next clip (don't stomp a RECORDING status the user just started).
                if self._audio_q.empty() and not self.recorder.is_recording:
                    self._set_status(IDLE)

    # --- runtime reconfiguration (driven by the tray) -----------------------

    def swap_transcriber(self, new_transcriber: Transcriber | None) -> None:
        """Replace the live model with an already-loaded one (or ``None`` to just free the
        current one before a reload) and unload the old.

        Caller is responsible for loading `new_transcriber` first (it's slow). The
        model lock makes this wait for any in-flight transcription, so we never
        unload a model mid-clip.
        """
        with self._model_lock:
            old = self.transcriber
            self.transcriber = new_transcriber
        if old is not None and old is not new_transcriber:
            try:
                old.unload()
            except Exception as e:
                print(f"! failed to unload previous model: {e}")

    def set_language(self, code: str | None) -> None:
        """Pin a language ISO code, or None to auto-detect per clip."""
        self.language = code

    def set_multilingual(self, enabled: bool) -> None:
        """Turn multilingual dictation on/off live, loading the LID lazily on first use."""
        enabled = bool(enabled)
        self.multilingual = enabled
        if enabled and (self._multi is None or not self._multi.ready):
            threading.Thread(target=self._ensure_multi, daemon=True).start()

    def start_multilingual_preload(self) -> None:
        """Preload the multilingual LID/VAD if enabled — called by the app once the ASR model
        is loaded (the lazy-load path defers it until the engine pack is present)."""
        if self.multilingual and (self._multi is None or not self._multi.ready):
            threading.Thread(target=self._ensure_multi, daemon=True).start()

    def _ensure_multi(self):
        """Build + load the multilingual coordinator (slow; runs on a background thread).

        Idempotent and lock-guarded so the live toggle and the startup preload can't
        double-load. On failure the coordinator stays not-ready and the worker simply uses
        the normal single pass — multilingual is never worse than mode-off.
        """
        with self._multi_lock:
            if self._multi is not None and self._multi.ready:
                return self._multi
            if self._multi is None:
                from .lid import WhisperCppLID
                from .multilingual import MultilingualCoordinator
                from .transcriber import lid_model_path, whispercpp_binary_dir

                # Point the LID at the *same* whisper.cpp build the ASR server uses.
                bdir = str(whispercpp_binary_dir(self.cfg["model"]))
                self._multi = MultilingualCoordinator(
                    lid=WhisperCppLID(model_name=self._lid_model, device=self._lid_device,
                                      binary_dir=bdir,
                                      model_path=str(lid_model_path(self._lid_model))),
                    samplerate=self.samplerate,
                )
            try:
                t0 = time.time()
                self._multi.load()
                print(f"OK multilingual ready ({self._multi.description}) "
                      f"— loaded in {time.time() - t0:.2f}s")
            except Exception as e:
                print(f"! couldn't load multilingual LID: {e} — "
                      "multilingual will fall back to single-pass")
            return self._multi

    def set_sound_cues(self, enabled: bool) -> None:
        """Turn cue sounds on/off live, building or tearing down the warm stream."""
        enabled = bool(enabled)
        if enabled == self.sound_cues:
            return
        self.sound_cues = enabled
        if enabled:
            cues = self._build_cues()
            try:
                cues.start()
                self._cues = cues
            except Exception as e:
                print(f"! sound cues disabled ({e})")
                self.sound_cues = False
                self._cues = None
        elif self._cues is not None:
            self._cues.stop()
            self._cues = None

    def _build_cues(self) -> CuePlayer:
        return CuePlayer(device=self.cue_device, overrides=self._cue_overrides)

    def start(self) -> None:
        if self._running:
            return
        # Open the warm cue stream (keeps a Bluetooth link awake for instant cues).
        # Non-fatal: if it fails, run without sound rather than refusing to start.
        if self._cues is not None:
            try:
                self._cues.start()
            except Exception as e:
                print(f"! sound cues disabled ({e})")
                self._cues = None
        self._running = True
        self._control_thread = threading.Thread(target=self._control_loop, daemon=True)
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._control_thread.start()
        self._worker_thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._cmd_q.put(None)
        self._audio_q.put(None)
        for t in (self._control_thread, self._worker_thread):
            if t is not None:
                t.join(timeout=2.0)
        if self._cues is not None:
            self._cues.stop()
        if self._multi is not None:
            try:
                self._multi.unload()
            except Exception:
                pass


def _resolve_language(language_cfg: dict) -> str | None:
    """None => auto-detect per clip; otherwise the pinned ISO code."""
    if str(language_cfg.get("mode", "auto")).lower() == "fixed":
        return language_cfg.get("fixed", "en")
    return None


def _resolve_device(value):
    """Map a config value to a sounddevice device selector (None = system default)."""
    if value in (None, "", "default"):
        return None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value
