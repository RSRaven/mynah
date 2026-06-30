"""Stage whisper.cpp engine packs **into the app bundle** (CI, before PyInstaller).

Mynah ships the per-OS engine packs *inside* the app instead of downloading them on first run:
Windows bundles **Vulkan + CPU**, macOS bundles **Metal**; only the optional NVIDIA **CUDA**
pack is still fetched on demand. This script extracts each pack zip into

    <out>/whispercpp-<backend>/            (whisper-server[.exe] at the root)

which ``mynah.spec`` adds to PyInstaller ``datas`` as ``_engines/whispercpp-<backend>``. At
runtime :func:`mynah.transcriber.bundled_engine_dir` finds them under ``sys._MEIPASS/_engines``.

Each ``--pack backend=zip`` is extracted, its pack root located (the dir holding the server —
the upstream CPU zip nests it under ``Release/``; our Vulkan/Metal zips have it at the root),
and only the **runnable** files (server, whisper lib, ggml backends) copied flat into the
target — trimming the upstream CPU zip's extra tools (parakeet, tests, SDL2, talk-llama).

Usage (CI):
    Windows:  python scripts/stage_engines.py --out build/_engines \
                  --pack vulkan=dist/whispercpp-vulkan-x64.zip \
                  --pack cpu=dist/whispercpp-cpu-x64.zip
    macOS:    python scripts/stage_engines.py --out build/_engines \
                  --pack metal=dist/whispercpp-metal-arm64.zip
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

# Runnable pack files, by OS: the server + the whisper lib (the in-process LID/VAD loads it via
# ctypes) + every ggml backend the lib dispatches to. Everything else in an upstream zip
# (parakeet, *-cli variants, tests, SDL2, talk-llama) is dropped.
WIN_GLOBS = ("whisper-server.exe", "whisper-cli.exe", "whisper.dll", "ggml*.dll")
MAC_GLOBS = ("whisper-server", "whisper-cli", "libwhisper*.dylib", "libggml*.dylib")

SERVER_NAMES = ("whisper-server.exe", "whisper-server")


def _find_pack_root(extracted: Path) -> Path | None:
    """Dir within an extracted tree that holds the whisper-server binary (root, or nested
    under e.g. ``Release/`` for the upstream CPU zip)."""
    for name in SERVER_NAMES:
        if (extracted / name).is_file():
            return extracted
    for name in SERVER_NAMES:
        for p in extracted.rglob(name):
            return p.parent
    return None


def _is_macos_pack(pack_root: Path) -> bool:
    return (pack_root / "whisper-server").is_file()


def stage_pack(backend: str, zip_path: Path, out_root: Path) -> None:
    if not zip_path.is_file():
        sys.exit(f"ERROR: pack zip not found for {backend}: {zip_path}")
    target = out_root / f"whispercpp-{backend}"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        extract = Path(td)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract)
        pack_root = _find_pack_root(extract)
        if pack_root is None:
            sys.exit(f"ERROR: no whisper-server in {zip_path} — wrong/empty archive?")

        globs = MAC_GLOBS if _is_macos_pack(pack_root) else WIN_GLOBS
        members: list[Path] = []
        for pat in globs:
            members.extend(sorted(pack_root.glob(pat)))
        if not any(p.name in SERVER_NAMES for p in members):
            sys.exit(f"ERROR: no server binary matched in {pack_root} for {backend}")

        total = 0
        for src in members:
            dst = target / src.name
            shutil.copy2(src, dst)
            total += dst.stat().st_size
            # zipfile drops the Unix exec bit; restore it on the server + dylibs so the bundled
            # macOS pack can actually run (Windows ignores the bit).
            if os.name != "nt":
                dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    names = sorted(p.name for p in target.iterdir())
    print(f"staged {backend}: {len(names)} files ({total/1e6:.1f} MB) -> {target}")
    print("  " + ", ".join(names))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True,
                    help="output root for staged packs (added to mynah.spec datas as _engines)")
    ap.add_argument("--pack", action="append", default=[], metavar="BACKEND=ZIP",
                    help="a pack to stage, e.g. vulkan=dist/whispercpp-vulkan-x64.zip "
                         "(repeatable)")
    args = ap.parse_args(argv)
    if not args.pack:
        sys.exit("ERROR: pass at least one --pack BACKEND=ZIP")

    args.out.mkdir(parents=True, exist_ok=True)
    for spec in args.pack:
        if "=" not in spec:
            sys.exit(f"ERROR: --pack must be BACKEND=ZIP, got {spec!r}")
        backend, zip_str = spec.split("=", 1)
        stage_pack(backend.strip(), Path(zip_str.strip()), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
