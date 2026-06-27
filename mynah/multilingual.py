"""Engine-agnostic multilingual dictation coordinator (goals/multilingual-dictation.md).

Wraps the *active* ``Transcriber`` (passed in per call, so model swaps need no rewrapping)
and an LID. A cheap language-ID **gate** keeps single-language clips on today's fast
single-pass path; only genuinely mixed clips pay for the split. For a mixed clip it:

  1. VAD-segments at the pauses people make when switching language (whisper.cpp's bundled
     Silero VAD, in-process via the same whisper.dll — no second runtime);
  2. within each speech segment, runs a sliding LID window to catch *no-pause* code-switches
     and cut there;
  3. groups consecutive same-language segments;
  4. transcribes each group with the strong model **auto-detecting** its language (robust to
     the tiny LID's confusions, e.g. uk→ru); and
  5. stitches the texts in time order — so nothing is dropped or translated.

**Robustness (Definition of Done):** any failure in the LID/VAD/segmentation path falls
back to a single normal pass, so multilingual mode is never worse than mode-off — just
sometimes slower. A pinned language also skips straight to a single pass (the user has said
"I'm speaking X"; there's nothing to split).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .lid import LanguageIdentifier, WhisperCppLID

SR = 16000

# --- gate: sample a few windows across the speech span; >1 confident language => multi ---
# 2 s windows (not longer) so a short leading/trailing segment — e.g. a 2 s English intro
# spoken with no pause before switching — isn't diluted by the next language and missed.
_GATE_WINDOW_S = 2.0      # length of each window handed to the LID
_GATE_STRIDE_S = 1.5      # aim for ~one window per this many seconds of speech
_GATE_MAX_WINDOWS = 8     # cap so a long mono clip still gates in well under 150 ms
_GATE_MIN_WINDOWS = 3
_LID_MIN_PROB = 0.5       # ignore low-confidence windows (silence / unsure)

# --- VAD: split at language-switch pauses (tuned much shorter than the 2 s default) ---
_VAD_MIN_SILENCE_MS = 400
_VAD_SPEECH_PAD_MS = 100
_VAD_MIN_SPEECH_MS = 200

# --- sliding LID inside a VAD segment: catch no-pause code-switches ---
_SUBWIN_S = 2.0
_SUBHOP_S = 1.0
_MIN_SEGMENT_S = 0.6      # don't emit segments shorter than this (merge instead)


@dataclass
class Segment:
    start: int            # sample index into the original clip
    end: int
    lid_label: str | None  # the LID's label (for grouping only — NOT the output language)
    text: str = ""

    @property
    def start_s(self) -> float:
        return round(self.start / SR, 2)

    @property
    def end_s(self) -> float:
        return round(self.end / SR, 2)


def _absorb_singletons(labels: list[str]) -> list[str]:
    """Smooth a per-window LID sequence: drop length-1 runs (isolated flips the tiny model
    produces near a language boundary) by folding them into the previous run — so one noisy
    window can't manufacture a spurious sub-segment. A real switch shows up as a run of ≥2
    windows (with the 1 s hop, that's any segment ≳1.5 s; sub-second switches are out of
    scope per the goal)."""
    if len(labels) < 3:
        return labels
    runs: list[list] = []
    for lab in labels:
        if runs and runs[-1][0] == lab:
            runs[-1][1] += 1
        else:
            runs.append([lab, 1])
    out: list[str] = []
    for idx, (lab, cnt) in enumerate(runs):
        if cnt == 1 and len(runs) > 1:
            lab = runs[idx - 1][0] if idx > 0 else runs[idx + 1][0]
        out.extend([lab] * cnt)
    return out


class MultilingualCoordinator:
    """Coordinates the gate + split-and-stitch around any ``Transcriber``."""

    def __init__(self, lid: LanguageIdentifier | None = None, vad=None,
                 samplerate: int = SR) -> None:
        self.samplerate = samplerate
        self._lid = lid or WhisperCppLID()
        self._vad = vad  # whisper.cpp Silero VAD; built lazily in load() if not injected
        self._loaded = False

    # --- lifecycle ----------------------------------------------------------

    def load(self) -> None:
        self._lid.load()
        if self._vad is None:
            self._vad = self._default_vad()
        self._vad.load()
        # Warm the VAD session (first call is slower than warm ones).
        try:
            self._speech_timestamps(np.zeros(self.samplerate, dtype=np.float32))
        except Exception:
            pass
        self._loaded = True

    @staticmethod
    def _default_vad():
        """whisper.cpp's bundled Silero VAD, resolved from the shared build + models dir."""
        from .transcriber import vad_model_path, whispercpp_binary_dir
        from .whispercpp_native import NativeVad

        return NativeVad(str(vad_model_path()), str(whispercpp_binary_dir()), use_gpu=False)

    @property
    def ready(self) -> bool:
        return self._loaded

    def unload(self) -> None:
        try:
            self._lid.unload()
        except Exception:
            pass
        if self._vad is not None:
            try:
                self._vad.unload()
            except Exception:
                pass
        self._loaded = False

    @property
    def description(self) -> str:
        return self._lid.description

    # --- public API ---------------------------------------------------------

    def transcribe(self, audio: np.ndarray, transcriber, language: str | None = None) -> str:
        """Return the stitched transcript. Falls back to a single pass on any problem."""
        text, _segments = self.transcribe_segments(audio, transcriber, language=language)
        return text

    def transcribe_segments(
        self, audio: np.ndarray, transcriber, language: str | None = None
    ) -> tuple[str, list[Segment]]:
        """Like ``transcribe`` but also returns the per-segment breakdown (for the bench).

        ``segments`` is empty when the clip took the single-pass path (mono / pinned /
        fallback); the returned text is then the single-pass result.
        """
        single = lambda: transcriber.transcribe(audio, language=language)
        # Pinned language, LID not ready, or empty clip → nothing to split.
        if language is not None or not self._loaded or audio is None or len(audio) == 0:
            return single(), []
        try:
            return self._split_and_transcribe(audio, transcriber)
        except Exception as e:
            print(f"! multilingual path failed ({e}); using single pass")
            return transcriber.transcribe(audio, language=None), []

    # --- internals ----------------------------------------------------------

    def _split_and_transcribe(self, audio: np.ndarray, transcriber) -> tuple[str, list[Segment]]:
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        vad = self._speech_timestamps(audio)
        if not vad:  # no speech found — let the engine decide on the whole clip
            return transcriber.transcribe(audio, language=None), []

        sp0, sp1 = vad[0]["start"], vad[-1]["end"]
        # Cheap gate: one language across the speech span → today's fast single pass.
        if not self._looks_multilingual(audio[sp0:sp1]):
            return transcriber.transcribe(audio, language=None), []

        segments = self._segment(audio, vad)
        if len(segments) < 2:  # gate said multi but we couldn't split → single pass
            return transcriber.transcribe(audio, language=None), []

        for seg in segments:
            seg.text = (transcriber.transcribe(audio[seg.start:seg.end], language=None) or "").strip()
        text = " ".join(seg.text for seg in segments if seg.text).strip()
        if not text:  # everything came back empty → fall back rather than return nothing
            return transcriber.transcribe(audio, language=None), []
        return text, segments

    # --- the gate -----------------------------------------------------------

    def _looks_multilingual(self, span: np.ndarray) -> bool:
        """True if the LID sees >1 confident language across windows of the speech span."""
        n = len(span)
        if n < int(1.0 * SR):  # too short to meaningfully contain two languages
            return False
        win = int(_GATE_WINDOW_S * SR)
        k = int(round((n / SR) / _GATE_STRIDE_S))
        k = max(_GATE_MIN_WINDOWS, min(_GATE_MAX_WINDOWS, k))
        labels: list[str] = []
        for i in range(k):
            if n <= win:
                w = span
            else:
                start = int(i * (n - win) / (k - 1)) if k > 1 else 0
                w = span[start:start + win]
            lab, prob = self._lid.detect(w)
            if lab is not None and prob >= _LID_MIN_PROB:
                labels.append(lab)
        return len(set(labels)) >= 2

    # --- segmentation -------------------------------------------------------

    def _segment(self, audio: np.ndarray, vad: list[dict]) -> list[Segment]:
        """VAD segments, sub-split at no-pause LID changes, then merge same-language runs."""
        raw: list[Segment] = []
        for v in vad:
            raw.extend(self._split_vad_segment(audio, int(v["start"]), int(v["end"])))

        # Merge consecutive same-label segments (a language spoken across a pause, or two
        # adjacent sub-windows the LID agreed on).
        merged: list[Segment] = []
        for seg in raw:
            if merged and merged[-1].lid_label == seg.lid_label:
                merged[-1].end = seg.end
            else:
                merged.append(seg)

        # Fold any sub-_MIN_SEGMENT_S sliver into its neighbour so we never transcribe a
        # near-empty crumb (which whisper tends to hallucinate on).
        out: list[Segment] = []
        min_len = int(_MIN_SEGMENT_S * SR)
        for seg in merged:
            if out and (seg.end - seg.start) < min_len:
                out[-1].end = seg.end
            else:
                out.append(seg)
        return out

    def _split_vad_segment(self, audio: np.ndarray, start: int, end: int) -> list[Segment]:
        """Label a speech segment with a sliding LID window; cut where the language changes."""
        length = end - start
        win = int(_SUBWIN_S * SR)
        if length <= win:  # too short to slide — one label for the whole thing
            lab, _ = self._lid.detect(audio[start:end])
            return [Segment(start, end, lab)]

        hop = int(_SUBHOP_S * SR)
        centers: list[int] = []
        labels: list[str] = []
        pos = 0
        while pos < length:
            w = audio[start + pos:start + min(pos + win, length)]
            lab, prob = self._lid.detect(w)
            if lab is not None and prob >= _LID_MIN_PROB:
                centers.append(start + pos + min(win, length - pos) // 2)
                labels.append(lab)
            pos += hop
        if not labels:
            return [Segment(start, end, None)]

        labels = _absorb_singletons(labels)  # kill isolated LID flips at noisy boundaries
        subs: list[Segment] = []
        run_start = start
        for i in range(1, len(labels)):
            if labels[i] != labels[i - 1]:
                boundary = (centers[i - 1] + centers[i]) // 2
                subs.append(Segment(run_start, boundary, labels[i - 1]))
                run_start = boundary
        subs.append(Segment(run_start, end, labels[-1]))
        return subs

    # --- VAD ----------------------------------------------------------------

    def _speech_timestamps(self, audio: np.ndarray) -> list[dict]:
        """Silero VAD speech regions (sample indices), tuned to split at switch pauses.

        Backed by whisper.cpp's bundled Silero VAD (same model the old faster-whisper path
        used), so removing faster-whisper doesn't change the segmentation behaviour."""
        return self._vad.speech_timestamps(
            np.ascontiguousarray(audio, dtype=np.float32),
            samplerate=self.samplerate,
            min_silence_ms=_VAD_MIN_SILENCE_MS,
            speech_pad_ms=_VAD_SPEECH_PAD_MS,
            min_speech_ms=_VAD_MIN_SPEECH_MS,
        )
