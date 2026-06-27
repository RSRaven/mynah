"""Mic capture into an in-memory buffer via sounddevice (PortAudio).

Records float32 mono at 16 kHz — exactly what Whisper wants — so there's no resample
step. `start()` opens a stream; `stop()` closes it and returns the captured waveform.
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd


class Recorder:
    def __init__(self, samplerate: int = 16000, device: int | str | None = None) -> None:
        self.samplerate = samplerate
        self.device = device
        self._stream: sd.InputStream | None = None
        self._frames: list[np.ndarray] = []

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        # Runs on the PortAudio thread; copy because indata is reused.
        self._frames.append(indata.copy())

    def start(self) -> None:
        if self._stream is not None:
            return
        self._frames = []
        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        """Stop capture and return the recorded mono waveform (float32, 1-D)."""
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None

        if not self._frames:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(self._frames, axis=0).reshape(-1)
        self._frames = []
        return audio.astype(np.float32, copy=False)

    @property
    def is_recording(self) -> bool:
        return self._stream is not None
