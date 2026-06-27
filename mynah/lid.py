"""Lightweight spoken-language identification (LID) for the multilingual gate.

The multilingual coordinator (``mynah/multilingual.py``) uses an LID for two cheap jobs,
*not* transcription:

  1. **the gate** — decide whether a clip holds more than one language, so single-language
     dictation stays on today's fast single-pass path (the heavy split work only runs when
     there really is a switch);
  2. **boundaries** — find where the language changes so a mixed clip can be split.

Only the LID's *changes* matter, never the absolute label — so a consistent mislabel (the
``tiny`` model reliably calls Ukrainian "ru" on this box) is harmless: the gate still sees
one language and the strong transcriber auto-detects the real one per segment.

Backend: whisper.cpp's ``whisper_lang_auto_detect`` on a resident **tiny GGML** model, via
the in-process ctypes binding (:mod:`mynah.whispercpp_native`). That call runs the encoder
plus one SOT-token decode only (no transcription), so a warm detect is ~15 ms on the RTX
2080 — the few windows the gate samples cost well under the 150 ms budget. This is the
single-engine replacement for the old faster-whisper ``tiny`` detector; ``LanguageIdentifier``
stays tiny and swappable so a frame-level LID (e.g. SpeechBrain VoxLingua107) can replace it
later without touching callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class LanguageIdentifier(ABC):
    """Labels a short waveform with a language code. Stays resident between calls."""

    @abstractmethod
    def load(self) -> None:
        """Load the model. Called once before any detect()."""

    @abstractmethod
    def detect(self, audio: np.ndarray) -> tuple[str | None, float]:
        """Return ``(iso_code, probability)`` for a float32 mono 16 kHz window.

        Returns ``(None, 0.0)`` for empty/failed input — callers treat that as
        "no confident language" and skip the window.
        """

    def unload(self) -> None:
        """Release resources. Optional; default is a no-op."""

    @property
    def description(self) -> str:
        return self.__class__.__name__


class WhisperCppLID(LanguageIdentifier):
    """whisper.cpp ``tiny`` used purely as a fast language detector.

    Loads its own tiny GGML model in-process via ``whisper.dll`` — the same build the ASR
    ``whisper-server`` uses, so it runs wherever the engine does (CUDA / Vulkan / CPU) and on
    the one GGML format. A separate, tiny (~75 MB) resident model, independent of the active
    ASR model. Paths default to the shared whisper.cpp build + models dir (env/config aware)
    so ``WhisperCppLID()`` works standalone; pass them explicitly to override.
    """

    def __init__(
        self,
        model_name: str = "tiny",
        device: str = "auto",
        binary_dir: str | None = None,
        model_path: str | None = None,
    ) -> None:
        from .transcriber import lid_model_path, whispercpp_binary_dir

        self.model_name = model_name
        self.device = device
        self.binary_dir = str(binary_dir or whispercpp_binary_dir())
        self.model_path = str(model_path or lid_model_path(model_name))
        self._lid = None

    @property
    def _use_gpu(self) -> bool:
        # auto/cuda -> GPU (falls to CPU automatically on a CPU-only build); cpu -> CPU.
        return str(self.device).lower() != "cpu"

    def load(self) -> None:
        from .whispercpp_native import NativeLID

        lid = NativeLID(self.model_path, self.binary_dir, use_gpu=self._use_gpu)
        lid.load()
        self._lid = lid
        # Warm the GPU graph so the first real detect isn't the slow one.
        try:
            self.detect(np.zeros(16000, dtype=np.float32))
        except Exception:
            pass

    def detect(self, audio: np.ndarray) -> tuple[str | None, float]:
        if self._lid is None:
            raise RuntimeError("WhisperCppLID.load() must be called before detect().")
        if audio is None or len(audio) == 0:
            return None, 0.0
        try:
            return self._lid.detect(audio)
        except Exception:
            return None, 0.0

    def unload(self) -> None:
        if self._lid is None:
            return
        try:
            self._lid.unload()
        finally:
            self._lid = None

    @property
    def description(self) -> str:
        if self._lid is None:
            return f"whisper.cpp {self.model_name} LID (not loaded)"
        accel = "GPU" if self._use_gpu else "CPU"
        return f"whisper.cpp {self.model_name} LID ({accel})"
