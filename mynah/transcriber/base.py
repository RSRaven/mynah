"""The pluggable ASR backend interface.

Everything above this line in the app is platform-neutral: it hands the backend a
float32, 16 kHz, mono audio array and gets text back. New platforms (Metal/MLX,
Vulkan, CPU) are added as new `Transcriber` implementations without touching the
recorder, hotkey, insertion, or controller.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class Transcriber(ABC):
    """A speech-to-text engine that stays resident between dictations."""

    @abstractmethod
    def load(self) -> None:
        """Load the model into memory/VRAM. Called once at startup."""

    @abstractmethod
    def transcribe(self, audio: np.ndarray, language: str | None = None) -> str:
        """Transcribe a float32 mono 16 kHz waveform to text.

        `language` is an ISO code (e.g. "en") to pin, or None to auto-detect.
        Returns trimmed text (may be empty for silence).
        """

    def unload(self) -> None:
        """Release resources. Optional; default is a no-op."""

    @property
    def description(self) -> str:
        """Human-readable backend/device summary for logs and the tray."""
        return self.__class__.__name__
