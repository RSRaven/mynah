"""Model manager: GGML weights via the **shared Hugging Face cache**.

Mynah keeps its ASR + multilingual weights in the machine-wide Hugging Face cache
(``~/.cache/huggingface/hub`` or ``$HF_HOME``) — the conventional shareable location these
weights already live in, so they **dedupe across apps** and are reused if another tool (or a
previous Mynah run) already pulled them. We never copy a multi-GB file into a Mynah-private
directory: we resolve the cached path and point ``whisper-server -m`` (and the in-process
LID/VAD) straight at it.

Two repos cover everything we need, all on the **one GGML format**:
  * ``ggerganov/whisper.cpp`` — the ASR models *and* the tiny LID weight (``ggml-tiny.bin``).
  * ``ggml-org/whisper-vad``  — the Silero VAD weight (``ggml-silero-v5.1.2.bin``).

Resolution order for any weight is **env override → local drop-in → HF cache** so a user can
still hand-place a ``ggml-<name>.bin`` in ``runtime_data_dir()/models`` (back-compat with the
pre-download era) and it wins over the cache.

Integrity: Hugging Face stores each LFS file under a blob named by its **sha256** and verifies
the download against it, so a model fetch is sha256-checked by construction (an interrupted or
corrupted download is detected and re-fetched). Engine packs, which we host ourselves, carry
their own pinned sha256 in the manifest (see :mod:`mynah.components`).
"""

from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .platform_layer import runtime_data_dir
from .transcriber import ggml_filename

# On Windows without Developer Mode the HF cache falls back to file copies instead of symlinks
# (uses a bit more disk, works fine) — silence the noisy per-download warning about it.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# --- catalog ------------------------------------------------------------------------------

ASR_REPO = "ggerganov/whisper.cpp"      # ASR models + the tiny LID weight
VAD_REPO = "ggml-org/whisper-vad"       # Silero VAD weight

LID_MODEL = "tiny"                      # multilingual language-ID gate (encoder-only)
VAD_FILENAME = "ggml-silero-v5.1.2.bin"  # whisper.cpp's bundled Silero v5 VAD

# Weights that are *infrastructure*, not selectable ASR models — filtered from the picker.
_NON_ASR = {"tiny", "silero-v5.1.2"}

# Curated ASR catalog (Settings picker). Order: accuracy → efficiency. ``size`` is an approximate
# byte hint used only to render "↓ ~3.0 GB" before download; the real size is read from the cache
# once present. ``recommendable`` mirrors what hardware.recommend_model can pick.
CatalogEntry = dict


@dataclass(frozen=True)
class ModelInfo:
    name: str
    size_hint: int          # approx download size in bytes (display hint)
    note: str = ""


CATALOG: list[ModelInfo] = [
    ModelInfo("large-v3", 3_095_000_000, "best accuracy (~3 GB; needs ~7 GB VRAM)"),
    ModelInfo("large-v3-turbo", 1_624_000_000, "fast, near-large accuracy"),
    ModelInfo("large-v3-turbo-q5_0", 574_000_000, "quantized turbo (low VRAM)"),
    ModelInfo("medium", 1_530_000_000, "low-VRAM fallback"),
    ModelInfo("small", 488_000_000, "CPU / minimal"),
]
_CATALOG_BY_NAME = {m.name: m for m in CATALOG}


def catalog_names() -> list[str]:
    return [m.name for m in CATALOG]


def size_hint(name: str) -> int:
    m = _CATALOG_BY_NAME.get(name)
    return m.size_hint if m else 0


# --- local drop-in dir --------------------------------------------------------------------

def local_models_dir() -> Path:
    """Optional local drop-in dir (``runtime_data_dir()/models``). A user-placed
    ``ggml-<name>.bin`` here wins over the HF cache. Honours ``MYNAH_WHISPERCPP_MODEL_DIR``."""
    return Path(os.environ.get("MYNAH_WHISPERCPP_MODEL_DIR")
                or (runtime_data_dir() / "models"))


def _local_file(filename: str) -> Path | None:
    p = local_models_dir() / filename
    return p if p.is_file() else None


# --- HF cache helpers (all degrade gracefully if huggingface_hub is absent) ----------------

def _hf():
    """Import huggingface_hub lazily; return the module or None if it isn't installed."""
    try:
        import huggingface_hub  # noqa: F401
        return huggingface_hub
    except Exception:
        return None


def hf_available() -> bool:
    return _hf() is not None


def _cached_file(repo: str, filename: str) -> Path | None:
    """Path to ``filename`` from ``repo`` if already in the HF cache, else None (no download)."""
    hf = _hf()
    if hf is None:
        return None
    try:
        from huggingface_hub import try_to_load_from_cache

        # Returns a str path (cached), None (unknown), or a non-str sentinel (cached as
        # "does not exist") — the isinstance check below excludes the sentinel across versions.
        hit = try_to_load_from_cache(repo_id=repo, filename=filename)
        if isinstance(hit, str) and hit:
            p = Path(hit)
            return p if p.exists() else None
    except Exception:
        pass
    return None


def resolve_file(repo: str, filename: str, env_var: str | None = None) -> Path | None:
    """env override → local drop-in → HF cache. None if nowhere yet (caller may download)."""
    if env_var:
        val = os.environ.get(env_var)
        if val and Path(val).is_file():
            return Path(val)
    local = _local_file(filename)
    if local is not None:
        return local
    return _cached_file(repo, filename)


# --- public resolution used by the engine + multilingual gate -----------------------------

def resolve_asr_model(name: str) -> Path | None:
    return resolve_file(ASR_REPO, ggml_filename(name), env_var="MYNAH_WHISPERCPP_MODEL")


def resolve_lid_model(name: str = LID_MODEL) -> Path | None:
    return resolve_file(ASR_REPO, ggml_filename(name))


def resolve_vad_model() -> Path | None:
    return resolve_file(VAD_REPO, VAD_FILENAME, env_var="MYNAH_VAD_MODEL")


# --- availability for the Settings Models panel -------------------------------------------

def installed_asr_models() -> list[str]:
    """ASR model names present **anywhere** (local drop-in ∪ HF cache), tiny/VAD excluded."""
    names: set[str] = set()
    # local drop-in
    try:
        for p in local_models_dir().glob("ggml-*.bin"):
            stem = p.stem[len("ggml-"):]
            if stem not in _NON_ASR and not stem.startswith("silero"):
                names.add(stem)
    except Exception:
        pass
    # HF cache
    for stem in _cached_asr_stems():
        names.add(stem)
    return sorted(names)


def _cached_asr_stems() -> set[str]:
    hf = _hf()
    if hf is None:
        return set()
    out: set[str] = set()
    try:
        from huggingface_hub import scan_cache_dir

        for repo in scan_cache_dir().repos:
            if repo.repo_id != ASR_REPO:
                continue
            for rev in repo.revisions:
                for f in rev.files:
                    fn = f.file_name
                    if fn.startswith("ggml-") and fn.endswith(".bin"):
                        stem = fn[len("ggml-"):-len(".bin")]
                        if stem not in _NON_ASR and not stem.startswith("silero"):
                            out.add(stem)
    except Exception:
        pass
    return out


def model_status(name: str) -> tuple[bool, int]:
    """``(installed, size_bytes)`` for an ASR model. size_bytes is the on-disk size when
    installed, else the catalog download hint."""
    fn = ggml_filename(name)
    local = _local_file(fn)
    if local is not None:
        try:
            return True, local.stat().st_size
        except OSError:
            return True, size_hint(name)
    p = _cached_file(ASR_REPO, fn)
    if p is not None:
        try:
            return True, p.stat().st_size  # follows the symlink to the blob
        except OSError:
            return True, size_hint(name)
    return False, size_hint(name)


# --- download -----------------------------------------------------------------------------

# progress callback: cb(done_bytes:int, total_bytes:int|None, status:str)
ProgressCb = Callable[[int, "int | None", str], None]


def _repo_blobs_dir(repo: str) -> Path | None:
    """``<hf-cache>/models--<repo>/blobs`` — where in-flight ``*.incomplete`` files live."""
    hf = _hf()
    if hf is None:
        return None
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        try:
            from huggingface_hub.file_download import repo_folder_name
            folder = repo_folder_name(repo_id=repo, repo_type="model")
        except Exception:
            folder = "models--" + repo.replace("/", "--")
        return Path(HF_HUB_CACHE) / folder / "blobs"
    except Exception:
        return None


def _file_total(repo: str, filename: str) -> int | None:
    """Best-effort total size from HF metadata (for a determinate progress fraction)."""
    hf = _hf()
    if hf is None:
        return None
    try:
        from huggingface_hub import get_hf_file_metadata, hf_hub_url

        meta = get_hf_file_metadata(hf_hub_url(repo, filename))
        return int(meta.size) if meta.size else None
    except Exception:
        return None


class _BlobPoller:
    """Watches a repo's ``blobs/*.incomplete`` and reports growing bytes to a progress cb.

    Decoupled from huggingface_hub's tqdm: we just observe the partial blob the downloader
    streams to, so progress works across hub versions. Best-effort — any failure here never
    affects the actual download."""

    def __init__(self, repo: str, total: int | None, label: str, cb: ProgressCb) -> None:
        self._dir = _repo_blobs_dir(repo)
        self._total = total
        self._label = label
        self._cb = cb
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._preexisting: set[str] = set()

    def __enter__(self) -> "_BlobPoller":
        if self._dir is not None and self._dir.is_dir():
            try:
                self._preexisting = {p.name for p in self._dir.glob("*.incomplete")}
            except Exception:
                self._preexisting = set()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.wait(0.3):
            done = self._incomplete_size()
            if done is not None:
                try:
                    self._cb(done, self._total, self._label)
                except Exception:
                    pass

    def _incomplete_size(self) -> int | None:
        if self._dir is None:
            return None
        try:
            cands = [p for p in self._dir.glob("*.incomplete")]
        except Exception:
            return None
        if not cands:
            return None
        # Prefer a newly-created incomplete file; fall back to the largest.
        fresh = [p for p in cands if p.name not in self._preexisting]
        pick = max(fresh or cands, key=lambda p: _safe_size(p))
        return _safe_size(pick)


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def download_file(repo: str, filename: str, label: str,
                  progress: ProgressCb | None = None) -> Path:
    """Fetch one file into the shared HF cache (resumable + sha256-verified by HF). Returns
    the cached path. Raises if huggingface_hub is missing or the download fails."""
    hf = _hf()
    if hf is None:
        raise RuntimeError(
            "huggingface_hub is not installed — can't download models. "
            "Install it (pip install huggingface_hub) or drop a ggml-*.bin into "
            f"{local_models_dir()}.")
    from huggingface_hub import hf_hub_download

    total = _file_total(repo, filename) if progress else None
    if progress:
        progress(0, total, f"Downloading {label}…")

    def _do() -> str:
        return hf_hub_download(repo_id=repo, filename=filename)

    if progress:
        with _BlobPoller(repo, total, f"Downloading {label}…", progress):
            path = _do()
        size = _safe_size(Path(path)) or (total or 0)
        progress(size, size or None, f"{label} ready")
    else:
        path = _do()
    return Path(path)


def download_model(name: str, progress: ProgressCb | None = None) -> Path:
    """Download the selected ASR model (no-op fetch if already cached)."""
    return download_file(ASR_REPO, ggml_filename(name), name, progress)


def ensure_multilingual_weights(progress: ProgressCb | None = None) -> None:
    """Fetch the tiny LID + Silero VAD weights (~78 MB together) so multilingual works out of
    the box. Skips whichever is already present. Best-effort: never raises — multilingual just
    falls back to single-pass if a weight is missing."""
    try:
        if resolve_lid_model() is None:
            download_file(ASR_REPO, ggml_filename(LID_MODEL), "language detector", progress)
    except Exception as e:
        print(f"! couldn't fetch LID weight: {e}")
    try:
        if resolve_vad_model() is None:
            download_file(VAD_REPO, VAD_FILENAME, "voice-activity model", progress)
    except Exception as e:
        print(f"! couldn't fetch VAD weight: {e}")


# --- removal (Settings Remove + uninstaller per-model checklist) --------------------------

def remove_model(name: str) -> int:
    """Delete a single ASR model from the local drop-in dir and/or the shared HF cache. Returns
    bytes freed. Caller guards against removing the active model.

    Per-*file* deletion (not ``delete_revisions``, which is revision-granular and would wipe
    every ggml weight sharing ``ggerganov/whisper.cpp``'s revision). Works whether the cache
    uses symlinks or, on Windows without Developer Mode, plain copies (blob + snapshot both)."""
    freed = 0
    fn = ggml_filename(name)
    # local drop-in
    local = _local_file(fn)
    if local is not None:
        freed += _safe_size(local)
        try:
            local.unlink()
        except OSError:
            pass
    # HF cache: delete this file's blob + snapshot entry (both, for the no-symlink copy case)
    freed += _remove_cached_file(ASR_REPO, fn)
    return freed


def _remove_cached_file(repo: str, filename: str) -> int:
    hf = _hf()
    if hf is None:
        return 0
    freed = 0
    try:
        from huggingface_hub import scan_cache_dir

        for r in scan_cache_dir().repos:
            if r.repo_id != repo:
                continue
            for rev in r.revisions:
                for f in rev.files:
                    if f.file_name != filename:
                        continue
                    for attr in ("blob_path", "file_path"):
                        p = getattr(f, attr, None)
                        if p is None:
                            continue
                        p = Path(p)
                        try:
                            if p.is_file() or p.is_symlink():
                                if attr == "blob_path":
                                    freed += _safe_size(p)
                                p.unlink()
                        except OSError:
                            pass
    except Exception:
        pass
    return freed
