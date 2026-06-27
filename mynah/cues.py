"""Audible start/stop cues via a persistent ("warm") sounddevice output stream.

Two lessons baked in from validation on the dev box:

  * Cues must be *instant* — the start cue should mean "listening now, go". A one-shot
    beep (winsound) or a freshly-opened stream lags a few hundred ms on Bluetooth
    headphones because the A2DP link is waking from sleep, so you hear it *after* you
    start talking. We avoid that by keeping ONE output stream open for the app's
    lifetime (silence when idle), which keeps the link awake so cues fire immediately.
  * winsound.Beep is inaudible on many machines and PlaySound has no volume control, so
    we mix our own samples into that stream instead.

Sound sources are per-OS system files when present, else synthesized tones:
  * Windows: notification .wav files under %WINDIR%\\Media.
  * macOS/Linux: synthesized tones — the OS sounds are .aiff / vary by distro, and the
    Windows files are neither present nor redistributable. Override per platform with
    `ux.cue_start_file` / `ux.cue_stop_file` (any .wav) in config.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import numpy as np

_SR = 48000


def _synth(freqs: list[int], dur: float = 0.12, amp: float = 0.6) -> np.ndarray:
    parts = []
    for f in freqs:
        t = np.linspace(0, dur, int(_SR * dur), endpoint=False)
        w = np.sin(2 * np.pi * f * t)
        n = int(_SR * 0.012)
        env = np.ones_like(w)
        env[:n] = np.linspace(0, 1, n)
        env[-n:] = np.linspace(1, 0, n)
        parts.append(w * env * amp)
    return np.concatenate(parts).astype(np.float32)


# Fallback tones, used when no system .wav is available (e.g. macOS/Linux).
_SYNTH = {
    "start": lambda: _synth([660, 990]),          # rising
    "stop": lambda: _synth([990, 660]),           # falling
    "error": lambda: _synth([440, 392, 330], 0.1),  # descending triple
}


def _default_files() -> dict[str, Path]:
    """Per-OS system sound files, where they exist (Windows only for now)."""
    if sys.platform == "win32":
        media = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Media"
        return {
            "start": media / "Windows Notify System Generic.wav",
            "stop": media / "Windows Message Nudge.wav",
            "error": media / "Windows Critical Stop.wav",
        }
    return {}


def _load_wav(path: Path) -> np.ndarray:
    """Load a .wav as float32 mono resampled to _SR."""
    import wave

    with wave.open(str(path), "rb") as wf:
        sr, ch, sw = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    dt = {1: np.uint8, 2: np.int16, 4: np.int32}.get(sw)
    if dt is None:
        raise ValueError(f"unsupported sample width: {sw}")
    d = np.frombuffer(raw, dtype=dt).astype(np.float32)
    if sw == 2:
        d /= 32768.0
    elif sw == 4:
        d /= 2147483648.0
    else:  # 8-bit PCM is unsigned
        d = (d - 128) / 128.0
    d = d.reshape(-1, ch).mean(axis=1)  # downmix to mono
    if sr != _SR:
        xo = np.linspace(0, 1, len(d), endpoint=False)
        xn = np.linspace(0, 1, int(len(d) * _SR / sr), endpoint=False)
        d = np.interp(xn, xo, d)
    return d.astype(np.float32)


class CuePlayer:
    """Owns a persistent output stream and plays short cues through it instantly."""

    def __init__(self, device=None, overrides: dict | None = None) -> None:
        self._device = device
        self._overrides = overrides or {}
        self._lock = threading.Lock()
        self._buf = np.zeros(0, np.float32)
        self._stream = None
        self._sounds: dict[str, np.ndarray] = {}

    def _resolve_sounds(self) -> None:
        files = _default_files()
        files.update({k: Path(v) for k, v in self._overrides.items() if v})
        for name, synth in _SYNTH.items():
            arr = None
            path = files.get(name)
            if path and path.is_file() and path.suffix.lower() == ".wav":
                try:
                    arr = _load_wav(path)
                except Exception:
                    arr = None
            self._sounds[name] = arr if arr is not None else synth()

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ARG002
        with self._lock:
            if len(self._buf):
                n = min(frames, len(self._buf))
                outdata[:n, 0] = self._buf[:n]
                outdata[n:, 0] = 0
                self._buf = self._buf[n:]
            else:
                outdata[:, 0] = 0

    def start(self) -> None:
        """Open the warm stream. Raises on failure so the caller can disable cues."""
        import sounddevice as sd

        self._resolve_sounds()
        self._stream = sd.OutputStream(
            samplerate=_SR, channels=1, dtype="float32",
            device=self._device, callback=self._callback,
        )
        self._stream.start()

    def play(self, name: str) -> None:
        if self._stream is None:
            return
        s = self._sounds.get(name)
        if s is None:
            return
        with self._lock:
            self._buf = s.copy()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
