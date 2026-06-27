"""Pluggable ASR backend. `build_transcriber` constructs the one engine from config.

After the bake-off **whisper.cpp is the single ASR engine** (CUDA parity with the
old faster-whisper path on latency *and* accuracy — see ``bench/results/SINGLE_ENGINE.md``).
Its GPU backend is **Vulkan by default** for every vendor (NVIDIA/AMD/Intel;
CUDA is an optional NVIDIA-only speed upgrade), and there is a **CPU** build for machines with
no usable GPU. Each backend is a self-contained ``whisper.cpp`` build the component
manager installs into its own dir (``engines/whispercpp-{vulkan,cuda,cpu}`` under
``runtime_data_dir()``); the active one is chosen by ``[hardware] backend`` (``auto`` picks the
best installed pack). Everything runs on the **one GGML model format**, resolved from the
shared Hugging Face cache (see :mod:`mynah.models`).

``engine`` is effectively informational now — ``auto`` and every alias resolve to whisper.cpp
(the legacy ``faster-whisper`` / ``cuda`` / ``cpu`` values still parse, for back-compat, and
map to the whisper.cpp build present). The cheap LID + VAD the multilingual gate needs run
in-process against the same ``whisper.dll`` (see :mod:`mynah.whispercpp_native`).
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import Transcriber

__all__ = ["Transcriber", "build_transcriber", "ggml_filename", "installed_ggml_models",
           "whispercpp_binary_dir", "lid_model_path", "vad_model_path",
           "engine_dir", "installed_backends", "resolve_backend", "set_backend"]

# Legacy engine values from the dual-engine era; accepted and remapped to whisper.cpp so
# existing configs keep working after the single-engine rework.
_LEGACY_ALIASES = {"faster-whisper", "faster-whisper-cuda", "cuda", "cpu"}
_WCPP_ENGINES = {"auto", "whispercpp", "whisper.cpp", "whispercpp-cuda",
                 "whispercpp-server", "whispercpp-vulkan", "whispercpp-cpu"}

# whisper.cpp GPU/CPU backends, in `auto`-resolution priority: the **Vulkan** default GPU pack
# first, then the optional NVIDIA CUDA upgrade, then the CPU floor.
_BACKENDS = ("vulkan", "cuda", "cpu")

# Process-wide backend preference set once by the app from `[hardware] backend` (and on a live
# change in Settings). Lets the existing `whispercpp_binary_dir(model_cfg)` call sites resolve
# the right per-backend engine dir without threading `[hardware]` through every signature.
_selected_backend: str | None = None


def set_backend(pref: str | None) -> None:
    """Record the active `[hardware] backend` preference (auto | vulkan | cuda | cpu)."""
    global _selected_backend
    _selected_backend = (pref or None)


def ggml_filename(name: str) -> str:
    """GGML weight filename for a model name, e.g. ``large-v3-turbo-q5_0`` ->
    ``ggml-large-v3-turbo-q5_0.bin``. Accepts an already-qualified filename too."""
    name = str(name).strip()
    if name.startswith("ggml-"):
        return name if name.endswith(".bin") else name + ".bin"
    return f"ggml-{name}.bin"


# --- engine (whisper.cpp build) location --------------------------------------------------

def _engines_root() -> Path:
    from ..platform_layer import runtime_data_dir

    return runtime_data_dir() / "engines"


def engine_dir(backend: str) -> Path:
    """The per-backend whisper.cpp build dir, e.g. ``engines/whispercpp-vulkan``."""
    return _engines_root() / f"whispercpp-{backend}"


def _server_exe(bdir: Path) -> Path:
    name = "whisper-server.exe" if os.name == "nt" else "whisper-server"
    return bdir / name


def _is_file(p: Path) -> bool:
    """``Path.is_file`` that never raises. A locked/ACL-broken file (e.g. a half-written pack
    from a disk-full extraction) is treated as **not** a usable file, so a broken backend is
    skipped rather than offered and then crashing on load."""
    try:
        return p.is_file()
    except OSError:
        return False


def installed_backends() -> list[str]:
    """Backends whose engine pack is actually present (has a whisper-server binary)."""
    return [b for b in _BACKENDS if _is_file(_server_exe(engine_dir(b)))]


def resolve_backend(pref: str | None = None) -> str:
    """Map a backend preference to a concrete backend name.

    ``auto`` (the default) picks the best **installed** pack — Vulkan (default GPU), then the
    optional CUDA, then CPU; if nothing is installed yet it resolves to ``vulkan`` so paths are
    well-defined for setup + error messages. A concrete preference is honoured as-is."""
    pref = (pref or _selected_backend or os.environ.get("MYNAH_BACKEND") or "auto").lower()
    if pref in _BACKENDS:
        return pref
    # auto (or anything unknown): first installed in priority order, else the Vulkan default.
    for b in _BACKENDS:
        if _is_file(_server_exe(engine_dir(b))):
            return b
    return "vulkan"


def whispercpp_binary_dir(model_cfg: dict | None = None) -> Path:
    """The active whisper.cpp build dir (env > config pin > the per-backend pack dir the
    component manager installs into). The same build serves the ASR server, the LID and VAD."""
    model_cfg = model_cfg or {}
    env = os.environ.get("MYNAH_WHISPERCPP_DIR")
    if env:
        return Path(env)
    pin = model_cfg.get("whispercpp_dir")
    if pin:
        return Path(pin)
    return engine_dir(resolve_backend(model_cfg.get("backend")))


# --- model / multilingual weight locations (shared HF cache, see mynah.models) ----------

def _wcpp_model_dir() -> Path:
    """Local drop-in dir for hand-placed GGML weights (a user-placed ``ggml-<name>.bin`` here
    wins over the HF cache). Models normally live in the shared HF cache (:mod:`mynah.models`)."""
    from .. import models

    return models.local_models_dir()


def installed_ggml_models() -> list[str]:
    """ASR model names available to the picker — present in the local drop-in dir **or** the
    shared HF cache. The tiny LID and Silero VAD weights are filtered out (not selectable)."""
    from .. import models

    return models.installed_asr_models()


def lid_model_path(name: str = "tiny") -> Path:
    """GGML weight used by the multilingual LID gate (resolved from env/local/HF cache; falls
    back to the expected local path so a missing-weight error is legible)."""
    from .. import models

    p = models.resolve_lid_model(name)
    return p if p is not None else (models.local_models_dir() / ggml_filename(name))


def vad_model_path() -> Path:
    """GGML Silero VAD weight (env override, else local drop-in, else the shared HF cache)."""
    from .. import models

    p = models.resolve_vad_model()
    return p if p is not None else (models.local_models_dir() / models.VAD_FILENAME)


def _wcpp_paths(model_cfg: dict) -> tuple[Path, Path]:
    """Resolve (binary_dir, model_path) for the ASR server. An explicit single-file pin
    (``MYNAH_WHISPERCPP_MODEL`` / ``whispercpp_model``) wins; otherwise the selected model
    name maps to its GGML weight in the shared HF cache (or the local drop-in dir), so the
    Settings model picker actually switches weights."""
    from .. import models

    bdir = whispercpp_binary_dir(model_cfg)
    explicit = (os.environ.get("MYNAH_WHISPERCPP_MODEL")
                or model_cfg.get("whispercpp_model"))
    if explicit:
        return Path(bdir), Path(explicit)
    name = model_cfg.get("name") or "large-v3"
    resolved = models.resolve_asr_model(name)
    if resolved is None:  # not downloaded yet — expected local path for a legible error
        resolved = models.local_models_dir() / ggml_filename(name)
    return Path(bdir), Path(resolved)


def _wcpp_available(bdir: Path, model: Path) -> bool:
    return _is_file(_server_exe(bdir)) and _is_file(model)


def _build_wcpp(model_cfg: dict) -> Transcriber:
    from .whispercpp_server import WhisperCppServer

    bdir, model = _wcpp_paths(model_cfg)
    return WhisperCppServer(
        model_path=str(model), binary_dir=str(bdir),
        port=int(os.environ.get("MYNAH_WHISPERCPP_PORT", "0")),  # 0 => pick a free port
    )


def build_transcriber(model_cfg: dict) -> Transcriber:
    """Construct the whisper.cpp backend (the only engine).

    ``engine`` is accepted for back-compat — ``auto`` and the whisper.cpp aliases all build
    the server, and the legacy ``faster-whisper`` / ``cuda`` / ``cpu`` values are remapped
    to it (the active acceleration is whatever build ``binary_dir`` holds). Raises with
    install guidance if the binary/model aren't present (no second engine to fall back to).
    """
    engine = str(model_cfg.get("engine", "auto")).lower()
    if engine in _LEGACY_ALIASES:
        print(f"! engine '{engine}' is from the dual-engine era; Mynah is now "
              "single-engine, so it runs whisper.cpp (the build in MYNAH_WHISPERCPP_DIR).")
    elif engine not in _WCPP_ENGINES:
        raise ValueError(
            f"Unknown engine '{engine}'. Supported: auto, whispercpp "
            f"(legacy {sorted(_LEGACY_ALIASES)} are remapped to whisper.cpp).")

    bdir, model = _wcpp_paths(model_cfg)
    if not _wcpp_available(bdir, model):
        raise FileNotFoundError(
            f"whisper.cpp not found: need {_server_exe(bdir).name} in {bdir} and "
            f"{model.name} in {model.parent}. Install an engine pack + model from Settings "
            "(first run opens setup automatically), or point MYNAH_WHISPERCPP_DIR / "
            "MYNAH_WHISPERCPP_MODEL at a whisper.cpp build + GGML model. Mynah is "
            "single-engine — there is no faster-whisper fallback.")
    return _build_wcpp(model_cfg)
