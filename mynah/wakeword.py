"""Wake-word "listening mode": say a phrase to start hands-free dictation.

The design point is **never run full Whisper continuously** to listen — that would
pin the GPU and false-trigger constantly. Instead this is a cheap, VAD-gated spotter:

  * an energy-based :class:`Endpointer` finds utterance boundaries on the CPU (no model);
  * each *completed* short utterance is transcribed by a **tiny** whisper.cpp model
    (:class:`TinyWhisperSpotter`, the same ``ggml-tiny.bin`` the multilingual LID gate
    already downloads) and fuzzy-matched against the wake phrase;
  * on a match we play the start cue and capture the *next* utterance (two-shot: say the
    phrase, pause, then dictate), handing the clip to the normal transcribe→insert path.

So the heavy ASR model only runs on the dictation itself, and the tiny spotter only runs on
detected speech — never continuously. The whole thing sits behind the small
:class:`WakeWordSpotter` interface so a dedicated engine (e.g. openWakeWord) can replace the
tiny-whisper spotter later without touching the listener.

Phrase matching is deliberately lenient: a tiny model mishears an unusual word like
"mynah" as "my nah" / "miner" / "mina", so we compare on de-spaced, fuzzy
(difflib) similarity rather than requiring an exact transcript.
"""

from __future__ import annotations

import difflib
import queue
import re
import threading
import time
from typing import Callable

import numpy as np

# --- phrase normalization + fuzzy matching ------------------------------------------------

_PUNCT_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — so 'My-nah!' == 'my nah'."""
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", (text or "").lower())).strip()


def _nospace(s: str) -> str:
    return s.replace(" ", "")


def phrase_score(transcript: str, phrase: str) -> float:
    """Best similarity (0..1) of `phrase` against the **start** of `transcript`.

    A de-spaced substring hit scores 1.0 (handles 'my nah' vs 'mynah'); otherwise we
    take the best difflib ratio of the phrase against a few leading word-windows of the
    transcript (wake words come first) and the whole transcript.
    """
    t, p = normalize(transcript), normalize(phrase)
    if not t or not p:
        return 0.0
    tn, pn = _nospace(t), _nospace(p)
    if pn and pn in tn:
        return 1.0
    words, pwords = t.split(), p.split()
    n_p = len(pwords)
    best = difflib.SequenceMatcher(None, pn, tn).ratio()
    for n in {n_p, n_p + 1, max(1, n_p - 1)}:
        window = _nospace(" ".join(words[:n]))
        if window:
            best = max(best, difflib.SequenceMatcher(None, pn, window).ratio())
    return best


def phrase_matches(transcript: str, phrase: str, threshold: float) -> bool:
    return phrase_score(transcript, phrase) >= threshold


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def rms_threshold(sensitivity: float) -> float:
    """Absolute RMS safety floor below which audio is never treated as speech (so silence
    can't trigger even when the noise floor is ~0). Kept **low** — real detection is the
    adaptive noise-floor-relative test (:func:`onset_ratio`); this is just a floor. Higher
    sensitivity lowers it. Tuned for normalized float32 mic audio."""
    return 0.010 - 0.0085 * _clamp01(sensitivity)  # 0.010 (insensitive) .. 0.0015 (sensitive)


def onset_ratio(sensitivity: float) -> float:
    """How many times the running **noise floor** an incoming frame must exceed to count as
    speech onset. This is what makes detection self-calibrate to the mic: a quiet webcam mic
    has a low noise floor, so quiet speech still clears it. Higher sensitivity = lower ratio."""
    return 5.0 - 3.0 * _clamp01(sensitivity)  # 5.0 (insensitive) .. 2.0 (sensitive)


def match_threshold(sensitivity: float) -> float:
    """Map sensitivity to the fuzzy-match acceptance ratio (higher sensitivity = looser).
    Loosened so a carrier-word phrase like "hey mynah" (which the tiny model hears as
    "hey miner"/"hey my nah", ~0.67) matches at the **default** sensitivity."""
    return 0.72 - 0.14 * _clamp01(sensitivity)  # 0.72 (strict) .. 0.58 (loose)


# --- streaming energy endpointer ----------------------------------------------------------

class Endpointer:
    """Streaming voice-activity endpointer with an **adaptive noise floor**.

    Feed it audio with :meth:`process`; it returns completed utterances (float32 arrays) once
    each is followed by ``silence_ms`` of quiet. Rather than a fixed energy threshold (which is
    wrong for every mic — a quiet webcam vs a loud headset), it tracks the background noise
    level and treats a frame as speech when it rises clearly above it (``onset_ratio`` ×). That
    self-calibrates to the mic gain. **Hysteresis** (a lower bar to *stay* in speech) stops it
    chopping a phrase at soft syllables. A small pre-roll keeps the onset; an absolute floor
    (``threshold``) stops silence triggering. Pure/CPU and model-free, so it's unit-testable.
    """

    def __init__(self, samplerate: int = 16000, threshold: float = 0.006,
                 silence_ms: int = 900, min_speech_ms: int = 200, max_s: float = 30.0,
                 preroll_ms: int = 300, frame_ms: int = 30, onset_ratio: float = 3.5,
                 offset_ratio: float = 1.8, noise_alpha: float = 0.06,
                 noise_floor_min: float = 1e-4) -> None:
        self.sr = int(samplerate)
        self.threshold = float(threshold)
        self.onset_ratio = float(onset_ratio)
        self.offset_ratio = float(offset_ratio)
        self.noise_alpha = float(noise_alpha)
        self.noise_floor_min = float(noise_floor_min)
        self.frame = max(1, int(frame_ms / 1000 * self.sr))
        self.set_silence_ms(silence_ms)
        self.min_speech_frames = max(1, int(min_speech_ms / 1000 * self.sr / self.frame))
        self.max_frames = max(1, int(max_s * self.sr / self.frame))
        self.preroll_frames = max(0, int(preroll_ms / 1000 * self.sr / self.frame))
        self._noise = self.noise_floor_min
        self.reset()

    def reset(self) -> None:
        self._in_speech = False
        self._utt: list[np.ndarray] = []
        self._speech_frames = 0
        self._silence_run = 0
        self._pre: list[np.ndarray] = []
        self._tail = np.zeros(0, np.float32)
        self._noise = self.noise_floor_min

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def set_threshold(self, t: float) -> None:
        self.threshold = float(t)

    def set_silence_ms(self, silence_ms: float) -> None:
        self.silence_frames = max(1, int(silence_ms / 1000 * self.sr / self.frame))

    def process(self, samples) -> list[np.ndarray]:
        buf = np.asarray(samples, dtype=np.float32).reshape(-1)
        if len(self._tail):
            buf = np.concatenate([self._tail, buf])
        out: list[np.ndarray] = []
        i, n = 0, len(buf)
        while i + self.frame <= n:
            out.extend(self._feed_frame(buf[i:i + self.frame]))
            i += self.frame
        self._tail = buf[i:].copy()
        return out

    def _feed_frame(self, frame: np.ndarray) -> list[np.ndarray]:
        rms = float(np.sqrt(np.mean(frame * frame))) if len(frame) else 0.0
        done: list[np.ndarray] = []
        if not self._in_speech:
            # Track the noise floor only while NOT speaking, so speech can't inflate it.
            self._noise = max(self.noise_floor_min,
                              (1.0 - self.noise_alpha) * self._noise + self.noise_alpha * rms)
            onset = max(self.threshold, self._noise * self.onset_ratio)
            self._pre.append(frame)
            if len(self._pre) > self.preroll_frames + 1:
                self._pre.pop(0)
            if rms > onset:
                self._in_speech = True
                self._utt = self._pre  # pre-roll already includes this onset frame
                self._pre = []
                self._speech_frames = 1
                self._silence_run = 0
        else:
            # Hysteresis: a lower bar to *stay* in speech, so soft syllables/short gaps don't
            # end the phrase prematurely.
            offset = max(self.threshold * 0.5, self._noise * self.offset_ratio)
            self._utt.append(frame)
            if rms > offset:
                self._speech_frames += 1
                self._silence_run = 0
            else:
                self._silence_run += 1
            if self._silence_run >= self.silence_frames or len(self._utt) >= self.max_frames:
                if self._speech_frames >= self.min_speech_frames:
                    done.append(np.concatenate(self._utt).astype(np.float32))
                self._reset_utterance()
        return done

    def _reset_utterance(self) -> None:
        self._in_speech = False
        self._utt = []
        self._speech_frames = 0
        self._silence_run = 0
        self._pre = []

    def flush(self) -> "np.ndarray | None":
        """Finish the in-progress utterance *now* (an external 'stop', e.g. a hotkey ending a
        wake-word dictation early), returning it if it had real speech, else None. Resets."""
        out = None
        if self._in_speech and self._utt and self._speech_frames >= self.min_speech_frames:
            out = np.concatenate(self._utt).astype(np.float32)
        self._reset_utterance()
        return out


# --- the spotter (tiny-whisper, behind a small interface) ---------------------------------

class WakeWordSpotter:
    """Interface: load a tiny model, transcribe a short clip to text, unload. A dedicated
    keyword engine (openWakeWord, Porcupine) can implement this later instead."""

    def load(self) -> None: ...
    def transcribe(self, audio: np.ndarray) -> str: ...
    def unload(self) -> None: ...

    @property
    def description(self) -> str:
        return self.__class__.__name__


class TinyWhisperSpotter(WakeWordSpotter):
    """Spotter backed by a resident ``ggml-tiny`` whisper.cpp server (its own child process /
    free port, separate from the main ASR model). Tiny on the GPU is ~75 MB VRAM and a few
    tens of ms per VAD-gated clip, so spotting is cheap."""

    def __init__(self, binary_dir, model_path, samplerate: int = 16000) -> None:
        from .transcriber.whispercpp_server import WhisperCppServer

        self._server = WhisperCppServer(
            model_path=str(model_path), binary_dir=str(binary_dir), samplerate=samplerate)

    def load(self) -> None:
        self._server.load()

    def transcribe(self, audio: np.ndarray) -> str:
        # Language-agnostic: we only need the phonetic text to match the phrase.
        return self._server.transcribe(audio, language=None)

    def unload(self) -> None:
        self._server.unload()

    @property
    def description(self) -> str:
        return f"tiny-whisper wake spotter [{self._server.description}]"


# --- the listener -------------------------------------------------------------------------

_WAKE = "wake"          # listening for the wake phrase
_CAPTURE = "capture"    # wake heard; recording the dictation utterance


class WakeWordListener:
    """Owns a continuous mic stream and drives the wake → dictate state machine.

    Callbacks (all run on the listener's worker thread):
      * ``on_wake()``      — wake phrase matched; start cue + recording status.
      * ``on_dictation(audio)`` — a captured dictation clip ready to transcribe + insert.
      * ``on_abort()``     — wake heard but no dictation followed (timeout); back to idle.

    ``is_blocked()`` (optional) lets the app pause the listener while a manual PTT/toggle
    recording is in progress, so the two don't fight over the mic or double-submit.
    """

    def __init__(self, *, spotter: WakeWordSpotter, phrase: str = "hey mynah",
                 sensitivity: float = 0.5, samplerate: int = 16000, device=None,
                 on_wake: Callable[[], None] | None = None,
                 on_dictation: Callable[[np.ndarray], None] | None = None,
                 on_abort: Callable[[], None] | None = None,
                 on_ready: Callable[[bool, str], None] | None = None,
                 is_blocked: Callable[[], bool] | None = None,
                 silence_ms: int = 900, min_speech_ms: int = 200,
                 max_wake_s: float = 3.0, max_dictation_s: float = 30.0,
                 wake_timeout_s: float = 6.0, log: Callable[[str], None] = print) -> None:
        self.spotter = spotter
        self.phrase = phrase
        self.sensitivity = sensitivity
        self.samplerate = int(samplerate)
        self.device = device
        self._on_wake = on_wake
        self._on_dictation = on_dictation
        self._on_abort = on_abort
        self._on_ready = on_ready
        self._is_blocked = is_blocked or (lambda: False)
        self.silence_ms = int(silence_ms)
        self.min_speech_ms = int(min_speech_ms)
        self.max_wake_s = float(max_wake_s)
        self.max_dictation_s = float(max_dictation_s)
        self.wake_timeout_s = float(wake_timeout_s)
        self._log = log

        self._audio_q: "queue.Queue[np.ndarray | None]" = queue.Queue()
        self._stream = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._ready = False
        self._capturing = False              # True while recording a dictation after the wake
        self._interrupt = threading.Event()  # set by interrupt() to stop a capture early

    # --- live config (thread-safe simple attribute writes) ---------------------------------

    def set_phrase(self, phrase: str) -> None:
        self.phrase = phrase

    def set_sensitivity(self, sensitivity: float) -> None:
        self.sensitivity = float(sensitivity)

    def set_silence_ms(self, silence_ms: int) -> None:
        """End-of-phrase pause (ms) before an utterance is considered finished — the
        'stop delay'. Applied live to the running endpointer."""
        self.silence_ms = int(silence_ms)

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def is_running(self) -> bool:
        return self._running

    def is_capturing(self) -> bool:
        """True while a wake-word dictation is actively being recorded (after the phrase fired,
        before end-of-speech). Lets the app route a hotkey press to 'stop this' instead of
        starting a separate recording."""
        return self._capturing

    def interrupt(self) -> None:
        """Stop an in-progress wake-word dictation early (e.g. the user pressed F9/F10). The
        buffered audio so far is transcribed, then we return to listening. No-op if not
        capturing. Thread-safe: just flags the worker loop."""
        self._interrupt.set()

    # --- lifecycle -------------------------------------------------------------------------

    def start(self) -> None:
        """Begin listening (non-blocking). Loads the spotter + opens the mic on a worker
        thread; ``on_ready`` reports success/failure."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._audio_q.put(None)  # unblock the worker
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=5.0)
        self._thread = None

    def _run(self) -> None:
        try:
            self._log("Wake word: loading spotter…")
            self.spotter.load()
        except Exception as e:
            self._running = False
            self._log(f"X wake word disabled — couldn't load spotter: {e}")
            if self._on_ready is not None:
                self._on_ready(False, str(e))
            return
        try:
            import sounddevice as sd

            self._stream = sd.InputStream(
                samplerate=self.samplerate, channels=1, dtype="float32",
                device=self.device, callback=self._audio_cb)
            self._stream.start()
        except Exception as e:
            self._running = False
            try:
                self.spotter.unload()
            except Exception:
                pass
            self._log(f"X wake word disabled — couldn't open mic: {e}")
            if self._on_ready is not None:
                self._on_ready(False, str(e))
            return

        self._ready = True
        self._log(f"Wake word: listening for \"{self.phrase}\" "
                  f"({self.spotter.description}).")
        if self._on_ready is not None:
            self._on_ready(True, "")
        try:
            self._loop()
        finally:
            self._ready = False
            try:
                if self._stream is not None:
                    self._stream.stop()
                    self._stream.close()
            except Exception:
                pass
            self._stream = None
            try:
                self.spotter.unload()
            except Exception:
                pass
            # drain the queue so a later restart starts clean
            try:
                while True:
                    self._audio_q.get_nowait()
            except queue.Empty:
                pass

    def _audio_cb(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        # PortAudio thread: copy + enqueue only (keep it cheap).
        if self._running:
            self._audio_q.put(indata.reshape(-1).copy())

    # --- the state machine -----------------------------------------------------------------

    def _loop(self) -> None:
        ep = Endpointer(samplerate=self.samplerate, threshold=rms_threshold(self.sensitivity),
                        onset_ratio=onset_ratio(self.sensitivity), silence_ms=self.silence_ms,
                        min_speech_ms=self.min_speech_ms, max_s=self.max_dictation_s)
        state = _WAKE
        capture_started = 0.0

        def _enter(new_state: str) -> str:
            self._capturing = (new_state == _CAPTURE)
            return new_state

        while self._running:
            chunk = self._audio_q.get()
            if chunk is None:
                break
            # A hotkey (F9/F10) asked to stop the current wake-word dictation: finish it now
            # (transcribe what was said), or just drop back to listening if nothing was said.
            if self._interrupt.is_set():
                self._interrupt.clear()
                if state == _CAPTURE:
                    buffered = ep.flush()
                    state = _enter(_WAKE)
                    if buffered is not None and self._on_dictation is not None:
                        self._on_dictation(buffered)
                    elif self._on_abort is not None:
                        self._on_abort()
            if self._is_blocked():
                # A manual recording is happening — ignore audio + reset so we don't capture
                # the same speech twice or trip on it.
                if state != _WAKE or ep.in_speech:
                    ep.reset()
                    state = _enter(_WAKE)
                continue
            # Apply live sensitivity / stop-delay changes (Settings sliders) on the fly.
            ep.set_threshold(rms_threshold(self.sensitivity))
            ep.onset_ratio = onset_ratio(self.sensitivity)
            ep.set_silence_ms(self.silence_ms)
            for utt in ep.process(chunk):
                if state == _WAKE:
                    if self._is_wake(utt):
                        state = _enter(_CAPTURE)
                        capture_started = time.time()
                        ep.reset()
                        if self._on_wake is not None:
                            self._on_wake()
                else:  # _CAPTURE: this utterance is the dictation
                    state = _enter(_WAKE)
                    ep.reset()
                    if self._on_dictation is not None:
                        self._on_dictation(utt)
            # Wake heard but the user never spoke → abort so a false trigger doesn't hang.
            if (state == _CAPTURE and not ep.in_speech
                    and time.time() - capture_started > self.wake_timeout_s):
                state = _enter(_WAKE)
                ep.reset()
                if self._on_abort is not None:
                    self._on_abort()
        self._capturing = False

    def _is_wake(self, audio: np.ndarray) -> bool:
        """True if a (short) candidate utterance transcribes to the wake phrase."""
        if len(audio) > self.max_wake_s * self.samplerate:
            return False  # too long to be just the wake word — ignore
        try:
            text = self.spotter.transcribe(audio)
        except Exception as e:
            self._log(f"! wake spotter transcribe failed: {e}")
            return False
        if not text:
            return False
        score = phrase_score(text, self.phrase)
        hit = score >= match_threshold(self.sensitivity)
        if hit:
            self._log(f'Wake word heard: "{text}" (score {score:.2f}) → dictate.')
        return hit
