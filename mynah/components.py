"""Component manager: download + install the whisper.cpp **engine packs**.

The base install carries *no* GPU runtime — on first run we fetch only the pack the detected
hardware needs:

  * **Vulkan** pack (~74 MB) — the default GPU runtime for every vendor (NVIDIA/AMD/Intel). We
    build + host it ourselves (upstream ships no Vulkan binary), so it's a **Mynah release**
    asset, MIT, sha256-pinned.
  * **CPU** pack — the no-GPU fallback (single-engine), from the **upstream
    whisper.cpp release** (``whisper-bin-x64.zip``).
  * **CUDA** pack (~700 MB, **optional**, NVIDIA opt-in) — the upstream
    ``whisper-cublas-*-bin-x64.zip``, which **bundles its own cuBLAS** beside
    ``whisper-server.exe`` (no PyPI ``nvidia-*`` wheels, no cuDNN — those left with
    faster-whisper). Self-contained: the pack dir is the server's ``cwd``.

Each pack is described in a pinned **manifest** (URL + version + sha256 + size). Install is
``fetch → verify sha256 → extract → atomic rename`` into ``engines/whispercpp-<backend>``
(:func:`mynah.transcriber.engine_dir`). Downloads are **resumable** (HTTP Range into a
``.part`` file) with backoff, and **never left half-installed** — we extract to a temp dir,
confirm the server binary is present, then swap it into place atomically.

For development/offline testing the manifest can point at ``file://`` URLs (see
``scripts/stage_local_release.py``) and be selected with ``MYNAH_MANIFEST``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .transcriber import active_engine_dir, bundled_engine_dir, engine_dir

# progress callback: cb(done_bytes:int, total_bytes:int|None, status:str)
ProgressCb = Callable[[int, "int | None", str], None]

_MANIFEST_NAME = "manifest.json"
_SERVER_EXE = "whisper-server.exe" if os.name == "nt" else "whisper-server"
_CHUNK = 1024 * 256

_ssl_ctx = None


def _ssl_context():
    """SSL context for HTTPS downloads, backed by the **certifi** CA bundle.

    In a frozen app (PyInstaller) Python can't read the system CA store, so a bare
    ``urllib`` HTTPS request fails with ``CERTIFICATE_VERIFY_FAILED`` — which is exactly why
    the engine-pack download dies on a fresh macOS install (the model download works because
    huggingface_hub/requests already bundle certifi). Build the context from certifi when it's
    available, else fall back to the platform default. Cached after the first build."""
    global _ssl_ctx
    if _ssl_ctx is not None:
        return _ssl_ctx
    import ssl

    try:
        import certifi

        _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        _ssl_ctx = ssl.create_default_context()
    return _ssl_ctx


class ComponentError(RuntimeError):
    pass


# --- manifest -----------------------------------------------------------------------------

def _bundled_manifest_path() -> Path:
    return Path(__file__).parent / _MANIFEST_NAME


def _read_text_source(src: str) -> str:
    """Read a manifest from a path or a URL (http/https/file)."""
    parsed = urllib.parse.urlparse(src)
    if parsed.scheme in ("http", "https", "file"):
        ctx = _ssl_context() if parsed.scheme == "https" else None
        with urllib.request.urlopen(src, timeout=30, context=ctx) as r:  # noqa: S310 (trusted manifest)
            return r.read().decode("utf-8")
    return Path(src).read_text(encoding="utf-8")


def load_manifest() -> dict:
    """Load the pinned manifest. ``MYNAH_MANIFEST`` (a path or URL) overrides the bundled one
    — used for dev/offline testing and swappable mirrors. Returns ``{}`` if none is found."""
    override = os.environ.get("MYNAH_MANIFEST")
    sources = [override] if override else []
    sources.append(str(_bundled_manifest_path()))
    for src in sources:
        if not src:
            continue
        try:
            data = json.loads(_read_text_source(src))
            if isinstance(data, dict) and data.get("components"):
                return data
        except FileNotFoundError:
            continue
        except Exception as e:
            print(f"! couldn't read manifest from {src}: {e}")
    return {}


def component(name: str) -> dict | None:
    return load_manifest().get("components", {}).get(name)


def engine_component_name(backend: str) -> str:
    return f"whispercpp-{backend}"


def has_component(backend: str) -> bool:
    return component(engine_component_name(backend)) is not None


# --- install state ------------------------------------------------------------------------

def _server_present(bdir: Path) -> bool:
    """Is a usable whisper-server binary present in ``bdir``? Never raises. A locked/ACL-broken
    file (e.g. a half-written pack from a disk-full extraction) is treated as **not** present, so
    the app re-installs or falls back rather than trying to run a broken pack."""
    try:
        return (bdir / _SERVER_EXE).is_file()
    except OSError:
        return False


def is_bundled(backend: str) -> bool:
    """Does this backend ship **inside the app** (no download needed)? Windows ships
    Vulkan + CPU, macOS ships Metal; the optional CUDA pack is never bundled."""
    return bundled_engine_dir(backend) is not None


def is_installed(backend: str) -> bool:
    """Is a usable engine pack present for ``backend`` — bundled in the app **or** downloaded?
    A bundled pack counts as installed, so first-run setup and the model download skip the
    engine fetch entirely (only the optional CUDA upgrade is ever pulled on demand)."""
    return _server_present(active_engine_dir(backend))


def installed_size(backend: str) -> int:
    root = active_engine_dir(backend)
    total = 0
    if root.is_dir():
        for p in root.rglob("*"):
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


# --- download -----------------------------------------------------------------------------

def _sha256(path: Path, progress: ProgressCb | None = None, label: str = "") -> str:
    h = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total, f"Verifying {label}…")
    return h.hexdigest()


def _download(url: str, dest: Path, expected_size: int | None,
              label: str, progress: ProgressCb | None,
              retries: int = 4) -> None:
    """Resumable streaming download to ``dest`` (a ``.part`` file). Retries with backoff,
    resuming from the bytes already on disk via an HTTP Range request."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file" or parsed.scheme == "":
        # Local file (dev manifest): just copy, reporting progress.
        src = Path(urllib.request.url2pathname(parsed.path)) if parsed.scheme == "file" else Path(url)
        total = src.stat().st_size
        done = 0
        with open(src, "rb") as fi, open(dest, "wb") as fo:
            for chunk in iter(lambda: fi.read(_CHUNK), b""):
                fo.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total, f"Copying {label}…")
        return

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        resume_from = dest.stat().st_size if dest.exists() else 0
        headers = {"User-Agent": "mynah-component-manager"}
        if resume_from:
            headers["Range"] = f"bytes={resume_from}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            ctx = _ssl_context() if parsed.scheme == "https" else None
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:  # noqa: S310
                # If the server ignored Range (200 not 206), restart from scratch.
                mode = "ab"
                if resume_from and resp.status == 200:
                    resume_from = 0
                    mode = "wb"
                total = None
                clen = resp.headers.get("Content-Length")
                if clen is not None:
                    total = int(clen) + resume_from
                elif expected_size:
                    total = expected_size
                done = resume_from
                with open(dest, mode) as f:
                    while True:
                        chunk = resp.read(_CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total, f"Downloading {label}…")
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = e
            if attempt < retries:
                backoff = min(2 ** attempt, 15)
                if progress:
                    progress(dest.stat().st_size if dest.exists() else 0, expected_size,
                             f"Download interrupted — retrying in {backoff}s…")
                time.sleep(backoff)
            else:
                break
    raise ComponentError(f"download of {label} failed after {retries} attempts: {last_err}")


# --- extract + atomic install -------------------------------------------------------------

def _find_pack_root(extracted: Path) -> Path | None:
    """Locate the dir within an extracted tree that holds the whisper-server binary (upstream
    zips nest it under e.g. ``Release/``; our Vulkan zip has it at the root)."""
    if (extracted / _SERVER_EXE).is_file():
        return extracted
    for p in extracted.rglob(_SERVER_EXE):
        return p.parent
    return None


def _prepare_macos_pack(pack_root: Path) -> None:
    """macOS-only post-extract fixups (no-op elsewhere).

    ``zipfile`` drops the Unix exec bit, so the freshly-extracted ``whisper-server`` (and the
    dylibs) come out non-executable — restore +x. And a downloaded zip's contents inherit
    ``com.apple.quarantine``; on an **unsigned** pack that makes Gatekeeper *silently* block the
    server/dylibs (the loop just does nothing). Strip the attribute recursively so the pack
    runs without a per-file "are you sure" gate."""
    if sys.platform != "darwin":
        return
    import stat as _stat
    import subprocess

    # Restore the exec bit on the server binary and every dylib.
    for p in [pack_root / _SERVER_EXE, *pack_root.rglob("*.dylib")]:
        try:
            if p.is_file():
                p.chmod(p.stat().st_mode | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)
        except OSError:
            pass

    # Strip quarantine from the whole pack dir (recursive). Best-effort — never fatal.
    try:
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(pack_root)],
                       capture_output=True, timeout=30)
    except Exception:
        pass


def _atomic_swap(pack_root: Path, target: Path) -> None:
    """Replace ``target`` with ``pack_root`` atomically-ish: rename any existing target aside,
    move the new one in, then delete the old. Never leaves the target missing-and-broken."""
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(target.name + ".old")
    shutil.rmtree(backup, ignore_errors=True)
    try:
        target_there = target.exists()
    except OSError:
        target_there = True  # inaccessible but present
    if target_there:
        try:
            os.replace(target, backup)
        except OSError as e:
            raise ComponentError(
                f"can't replace the existing engine at {target} ({e}). It's likely locked or "
                "has broken permissions from an earlier disk-full install — delete that folder "
                "manually (an elevated `rd /s /q`, or after a reboot) and retry.") from e
    try:
        os.replace(pack_root, target)
    except OSError:
        # Cross-volume (temp on another drive): fall back to a copy.
        shutil.copytree(pack_root, target)
        shutil.rmtree(pack_root, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)


def install_engine(backend: str, progress: ProgressCb | None = None,
                   force: bool = False) -> Path:
    """Download + install the engine pack for ``backend`` into ``engine_dir(backend)``.

    Returns the install dir. Idempotent: a no-op (returns immediately) if already installed and
    not ``force``. Raises :class:`ComponentError` on a missing manifest entry or a failed
    download/verify — the caller is expected to fall back to a smaller/CPU path rather than
    dead-end."""
    # A pack bundled inside the app is always "installed" and never downloaded — return its
    # in-app dir (ignores ``force``; there's nothing to (re)fetch for a bundled backend).
    bundled = bundled_engine_dir(backend)
    if bundled is not None:
        if progress:
            progress(1, 1, f"{backend} engine bundled with the app")
        return bundled

    target = engine_dir(backend)
    if is_installed(backend) and not force:
        if progress:
            progress(1, 1, f"{backend} engine already installed")
        return target

    comp = component(engine_component_name(backend))
    if comp is None:
        raise ComponentError(
            f"no manifest entry for the {backend} engine pack — can't download it.")
    url = comp.get("url")
    if not url:
        raise ComponentError(f"manifest entry for {backend} has no url")
    expected_sha = (comp.get("sha256") or "").lower()
    expected_size = comp.get("size")

    _check_disk_space(expected_size, backend)

    work = Path(tempfile.mkdtemp(prefix=f"mynah-{backend}-", dir=str(_staging_dir())))
    part = work / "pack.zip"
    try:
        _download(url, part, expected_size, f"{backend} engine", progress)

        if expected_sha:
            actual = _sha256(part, progress, f"{backend} engine")
            if actual.lower() != expected_sha:
                raise ComponentError(
                    f"sha256 mismatch for the {backend} pack "
                    f"(expected {expected_sha[:12]}…, got {actual[:12]}…) — refusing to install")

        if progress:
            progress(expected_size or 0, expected_size, f"Extracting {backend} engine…")
        extract_dir = work / "x"
        with zipfile.ZipFile(part) as zf:
            zf.extractall(extract_dir)
        pack_root = _find_pack_root(extract_dir)
        if pack_root is None:
            raise ComponentError(
                f"the {backend} pack has no {_SERVER_EXE} — wrong/empty archive?")
        _prepare_macos_pack(pack_root)
        _atomic_swap(pack_root, target)
        if progress:
            progress(1, 1, f"{backend} engine installed")
        return target
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _staging_dir() -> Path:
    """A scratch dir on the *same volume* as the engines dir, so the final rename is atomic."""
    d = engine_dir("vulkan").parent / ".staging"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _check_disk_space(expected_size: int | None, backend: str) -> None:
    """Refuse to start an install that can't fit — a disk that fills mid-extract leaves a
    half-written, sometimes permission-broken pack (exactly the failure we want to avoid).
    Needs room for the zip *and* the extracted files, plus headroom."""
    if not expected_size:
        return
    try:
        free = shutil.disk_usage(str(_staging_dir())).free
    except OSError:
        return
    need = int(expected_size * 2.5)
    if free < need:
        raise ComponentError(
            f"not enough free disk space for the {backend} engine pack: need ~"
            f"{need // 1_000_000} MB, only {free // 1_000_000} MB free. Free up space and retry.")


# --- removal (uninstall / purge) ----------------------------------------------------------

def remove_engine(backend: str) -> int:
    """Delete an installed engine pack. Returns bytes freed."""
    target = engine_dir(backend)
    freed = installed_size(backend)
    shutil.rmtree(target, ignore_errors=True)
    return freed


def purge_all_engines() -> int:
    """Remove every installed engine pack + staging scratch (uninstall step). Bytes freed."""
    freed = 0
    root = engine_dir("vulkan").parent  # .../engines
    if root.is_dir():
        for p in root.rglob("*"):
            try:
                if p.is_file():
                    freed += p.stat().st_size
            except OSError:
                pass
        shutil.rmtree(root, ignore_errors=True)
    return freed
