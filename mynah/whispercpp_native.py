"""In-process whisper.cpp via ctypes — the cheap LID + VAD the multilingual gate needs.

After the single-engine rework whisper.cpp is the *only* ASR runtime, so the
language detector and the voice-activity segmenter that ``mynah/multilingual.py`` relies
on can no longer borrow faster-whisper/CTranslate2 + Silero. This module binds the same
``whisper.dll`` the ASR ``whisper-server`` already ships and exposes two tiny, resident
helpers, both on the **one GGML model format**:

  * :class:`NativeLID` — ``whisper_lang_auto_detect`` on a tiny GGML model. That call runs
    the encoder + one SOT-token decode only (no transcription), so a warm detect is cheap —
    the encoder-only path the goal asks for.
  * :class:`NativeVad` — whisper.cpp's bundled Silero VAD (``whisper_vad_*``) to find speech
    regions, replacing the Silero VAD that used to come in with faster-whisper.

Both drive whatever ``whisper.dll`` build ``binary_dir`` points at (CUDA / Vulkan / CPU), so
this stays backend-agnostic like :mod:`mynah.transcriber.whispercpp_server`.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path

import numpy as np

_C_FLOAT_P = ctypes.POINTER(ctypes.c_float)


# --- struct layouts (mirror bench/_artifacts/wcpp-src/include/whisper.h) ------------------

class _Aheads(ctypes.Structure):
    _fields_ = [("n_heads", ctypes.c_size_t), ("heads", ctypes.c_void_p)]


class WhisperContextParams(ctypes.Structure):
    _fields_ = [
        ("use_gpu", ctypes.c_bool),
        ("flash_attn", ctypes.c_bool),
        ("gpu_device", ctypes.c_int),
        ("dtw_token_timestamps", ctypes.c_bool),
        ("dtw_aheads_preset", ctypes.c_int),
        ("dtw_n_top", ctypes.c_int),
        ("dtw_aheads", _Aheads),
        ("dtw_mem_size", ctypes.c_size_t),
    ]


class WhisperVadParams(ctypes.Structure):
    _fields_ = [
        ("threshold", ctypes.c_float),
        ("min_speech_duration_ms", ctypes.c_int),
        ("min_silence_duration_ms", ctypes.c_int),
        ("max_speech_duration_s", ctypes.c_float),
        ("speech_pad_ms", ctypes.c_int),
        ("samples_overlap", ctypes.c_float),
    ]


class WhisperVadContextParams(ctypes.Structure):
    _fields_ = [
        ("n_threads", ctypes.c_int),
        ("use_gpu", ctypes.c_bool),
        ("gpu_device", ctypes.c_int),
    ]


# log levels we always drop (whisper.cpp is chatty on model load)
_LOG_CB_T = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)


def _silent_log(level, text, user_data):  # pragma: no cover - trivial callback
    return None


# --- the shared DLL handle ----------------------------------------------------------------

_libs: dict[str, "_WhisperLib"] = {}
_libs_lock = threading.Lock()


class _WhisperLib:
    """Loaded ``whisper.dll`` with prototypes set. One per ``binary_dir`` (cached)."""

    def __init__(self, binary_dir: Path) -> None:
        self.binary_dir = Path(binary_dir)
        dll = self._dll_path()
        if sys.platform == "win32":
            # whisper.dll pulls in ggml*.dll + the backend (cuda/vulkan/cpu) from the same
            # dir — put it on the search path so the dependents resolve.
            try:
                os.add_dll_directory(str(self.binary_dir))
            except (OSError, AttributeError):
                pass
        # whisper.cpp ships its compute backends as separate ggml-<name>.dll files that
        # ggml.dll loads at runtime; nothing does that automatically when the library is
        # dlopen'd (the CLI/server call ggml_backend_load_all themselves). Without it the
        # device registry is empty and model load asserts. Load them from binary_dir.
        self._load_ggml_backends()
        self.lib = ctypes.CDLL(str(dll))
        self._set_prototypes()
        # keep a ref so the no-op logger isn't GC'd while the DLL holds it
        self._log_cb = _LOG_CB_T(_silent_log)
        try:
            self.lib.whisper_log_set(self._log_cb, None)
        except Exception:
            pass

    def _dll_path(self) -> Path:
        for name in ("whisper.dll", "libwhisper.so", "libwhisper.dylib"):
            p = self.binary_dir / name
            if p.is_file():
                return p
        raise FileNotFoundError(f"whisper shared library not found in {self.binary_dir}")

    def _load_ggml_backends(self) -> None:
        """Make ggml register its compute-backend DLLs (cpu/cuda/vulkan) from binary_dir.

        ggml's own ``ggml_backend_load_all_from_path`` uses a restricted DLL search that
        can't resolve a GPU backend's runtime deps (cudart/cublas, the Vulkan loader) out
        of ``binary_dir`` — leaving only the CPU backend registered (and the LID stuck on a
        slow CPU pass). So preload the backend DLLs ourselves first (the default loader
        honours ``add_dll_directory``, pinning their deps), then let ggml enumerate them.
        """
        self._backend_handles = []
        for name in ("ggml.dll", "libggml.so", "libggml.dylib"):
            p = self.binary_dir / name
            if p.is_file():
                self._ggml = ctypes.CDLL(str(p))
                break
        else:
            return

        core = {"ggml", "ggml-base"}
        for pat in ("ggml-*.dll", "libggml-*.so", "libggml-*.dylib"):
            for p in sorted(self.binary_dir.glob(pat)):
                stem = p.name.split(".")[0]
                if stem in core or stem.removeprefix("lib") in core:
                    continue
                try:
                    self._backend_handles.append(ctypes.CDLL(str(p)))
                except OSError:
                    pass  # a backend that can't load on this host is simply skipped

        if hasattr(self._ggml, "ggml_backend_load_all_from_path"):
            self._ggml.ggml_backend_load_all_from_path.argtypes = [ctypes.c_char_p]
            self._ggml.ggml_backend_load_all_from_path.restype = None
            self._ggml.ggml_backend_load_all_from_path(str(self.binary_dir).encode("utf-8"))
        elif hasattr(self._ggml, "ggml_backend_load_all"):
            self._ggml.ggml_backend_load_all()

    def _set_prototypes(self) -> None:
        L = self.lib
        # logging
        L.whisper_log_set.argtypes = [_LOG_CB_T, ctypes.c_void_p]
        L.whisper_log_set.restype = None
        # context / model
        L.whisper_context_default_params.restype = WhisperContextParams
        L.whisper_init_from_file_with_params.argtypes = [ctypes.c_char_p, WhisperContextParams]
        L.whisper_init_from_file_with_params.restype = ctypes.c_void_p
        L.whisper_free.argtypes = [ctypes.c_void_p]
        L.whisper_free.restype = None
        # LID
        L.whisper_pcm_to_mel.argtypes = [ctypes.c_void_p, _C_FLOAT_P, ctypes.c_int, ctypes.c_int]
        L.whisper_pcm_to_mel.restype = ctypes.c_int
        L.whisper_lang_auto_detect.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, _C_FLOAT_P]
        L.whisper_lang_auto_detect.restype = ctypes.c_int
        L.whisper_lang_max_id.argtypes = []
        L.whisper_lang_max_id.restype = ctypes.c_int
        L.whisper_lang_str.argtypes = [ctypes.c_int]
        L.whisper_lang_str.restype = ctypes.c_char_p
        # VAD
        L.whisper_vad_default_context_params.restype = WhisperVadContextParams
        L.whisper_vad_default_params.restype = WhisperVadParams
        L.whisper_vad_init_from_file_with_params.argtypes = [ctypes.c_char_p, WhisperVadContextParams]
        L.whisper_vad_init_from_file_with_params.restype = ctypes.c_void_p
        L.whisper_vad_segments_from_samples.argtypes = [
            ctypes.c_void_p, WhisperVadParams, _C_FLOAT_P, ctypes.c_int]
        L.whisper_vad_segments_from_samples.restype = ctypes.c_void_p
        L.whisper_vad_segments_n_segments.argtypes = [ctypes.c_void_p]
        L.whisper_vad_segments_n_segments.restype = ctypes.c_int
        L.whisper_vad_segments_get_segment_t0.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.whisper_vad_segments_get_segment_t0.restype = ctypes.c_float
        L.whisper_vad_segments_get_segment_t1.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.whisper_vad_segments_get_segment_t1.restype = ctypes.c_float
        L.whisper_vad_free_segments.argtypes = [ctypes.c_void_p]
        L.whisper_vad_free_segments.restype = None
        L.whisper_vad_free.argtypes = [ctypes.c_void_p]
        L.whisper_vad_free.restype = None


def _get_lib(binary_dir: str | os.PathLike) -> _WhisperLib:
    key = str(Path(binary_dir).resolve())
    with _libs_lock:
        lib = _libs.get(key)
        if lib is None:
            lib = _WhisperLib(Path(binary_dir))
            _libs[key] = lib
        return lib


def _as_float_ptr(audio: np.ndarray):
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    return audio, audio.ctypes.data_as(_C_FLOAT_P)


# --- LID ----------------------------------------------------------------------------------

class NativeLID:
    """Spoken-language detector backed by a resident tiny GGML model via ``whisper.dll``.

    ``detect()`` runs ``whisper_pcm_to_mel`` + ``whisper_lang_auto_detect`` (encoder + one
    SOT-token decode — no transcription), the cheapest language-ID whisper.cpp offers.
    """

    def __init__(self, model_path: str, binary_dir: str, use_gpu: bool = True,
                 gpu_device: int = 0, n_threads: int = 4) -> None:
        self.model_path = Path(model_path)
        self.binary_dir = Path(binary_dir)
        self.use_gpu = use_gpu
        self.gpu_device = gpu_device
        self.n_threads = n_threads
        self._lib: _WhisperLib | None = None
        self._ctx = None
        self._n_lang = 0
        self._lock = threading.Lock()

    def load(self) -> None:
        if not self.model_path.is_file():
            raise FileNotFoundError(f"LID GGML model not found: {self.model_path}")
        self._lib = _get_lib(self.binary_dir)
        L = self._lib.lib
        params = L.whisper_context_default_params()
        params.use_gpu = bool(self.use_gpu)
        params.gpu_device = int(self.gpu_device)
        params.flash_attn = False
        ctx = L.whisper_init_from_file_with_params(
            str(self.model_path).encode("utf-8"), params)
        if not ctx:
            raise RuntimeError(f"whisper_init failed for {self.model_path}")
        self._ctx = ctx
        self._n_lang = L.whisper_lang_max_id() + 1

    def detect(self, audio: np.ndarray) -> tuple[str | None, float]:
        if self._ctx is None or self._lib is None:
            raise RuntimeError("NativeLID.load() must be called before detect().")
        if audio is None or len(audio) == 0:
            return None, 0.0
        L = self._lib.lib
        arr, ptr = _as_float_ptr(audio)
        probs = (ctypes.c_float * self._n_lang)()
        with self._lock:  # one whisper_context is not thread-safe across calls
            if L.whisper_pcm_to_mel(self._ctx, ptr, len(arr), self.n_threads) != 0:
                return None, 0.0
            lang_id = L.whisper_lang_auto_detect(self._ctx, 0, self.n_threads, probs)
        if lang_id < 0:
            return None, 0.0
        code = L.whisper_lang_str(lang_id)
        if not code:
            return None, 0.0
        return code.decode("utf-8"), float(probs[lang_id])

    def unload(self) -> None:
        if self._ctx is not None and self._lib is not None:
            try:
                self._lib.lib.whisper_free(self._ctx)
            except Exception:
                pass
        self._ctx = None


# --- VAD ----------------------------------------------------------------------------------

class NativeVad:
    """whisper.cpp's bundled Silero VAD — speech regions from a float32 16 kHz clip."""

    def __init__(self, model_path: str, binary_dir: str, use_gpu: bool = False,
                 gpu_device: int = 0, n_threads: int = 4) -> None:
        self.model_path = Path(model_path)
        self.binary_dir = Path(binary_dir)
        self.use_gpu = use_gpu  # VAD is cheap on CPU; keep VRAM for the ASR model
        self.gpu_device = gpu_device
        self.n_threads = n_threads
        self._lib: _WhisperLib | None = None
        self._vctx = None
        self._lock = threading.Lock()

    def load(self) -> None:
        if not self.model_path.is_file():
            raise FileNotFoundError(f"VAD GGML model not found: {self.model_path}")
        self._lib = _get_lib(self.binary_dir)
        L = self._lib.lib
        cparams = L.whisper_vad_default_context_params()
        cparams.n_threads = int(self.n_threads)
        cparams.use_gpu = bool(self.use_gpu)
        cparams.gpu_device = int(self.gpu_device)
        vctx = L.whisper_vad_init_from_file_with_params(
            str(self.model_path).encode("utf-8"), cparams)
        if not vctx:
            raise RuntimeError(f"whisper_vad_init failed for {self.model_path}")
        self._vctx = vctx

    def speech_timestamps(
        self,
        audio: np.ndarray,
        samplerate: int = 16000,
        threshold: float = 0.5,
        min_speech_ms: int = 200,
        min_silence_ms: int = 400,
        speech_pad_ms: int = 100,
        max_speech_s: float = 1e8,
        samples_overlap: float = 0.1,
    ) -> list[dict]:
        """Speech regions as ``[{"start": sample, "end": sample}, ...]`` (matches the shape
        the multilingual coordinator expected from faster-whisper's ``get_speech_timestamps``)."""
        if self._vctx is None or self._lib is None:
            raise RuntimeError("NativeVad.load() must be called before speech_timestamps().")
        if audio is None or len(audio) == 0:
            return []
        L = self._lib.lib
        arr, ptr = _as_float_ptr(audio)
        params = L.whisper_vad_default_params()
        params.threshold = float(threshold)
        params.min_speech_duration_ms = int(min_speech_ms)
        params.min_silence_duration_ms = int(min_silence_ms)
        params.speech_pad_ms = int(speech_pad_ms)
        params.max_speech_duration_s = float(max_speech_s)
        params.samples_overlap = float(samples_overlap)
        with self._lock:
            segs = L.whisper_vad_segments_from_samples(self._vctx, params, ptr, len(arr))
            if not segs:
                return []
            try:
                n = L.whisper_vad_segments_n_segments(segs)
                out = []
                for i in range(n):
                    # segment times are in centiseconds (1/100 s)
                    t0 = L.whisper_vad_segments_get_segment_t0(segs, i)
                    t1 = L.whisper_vad_segments_get_segment_t1(segs, i)
                    start = max(0, int(round(t0 / 100.0 * samplerate)))
                    end = min(len(arr), int(round(t1 / 100.0 * samplerate)))
                    if end > start:
                        out.append({"start": start, "end": end})
            finally:
                L.whisper_vad_free_segments(segs)
        return out

    def unload(self) -> None:
        if self._vctx is not None and self._lib is not None:
            try:
                self._lib.lib.whisper_vad_free(self._vctx)
            except Exception:
                pass
        self._vctx = None
