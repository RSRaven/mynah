"""Zip the freshly-built Metal whisper.cpp build into the release pack (macOS CI / local).

Mirror of ``pack_vulkan.py`` for Apple Silicon. Selects the runnable pack files (server +
``libwhisper`` + the ``libggml`` backends the in-process LID/VAD loads) from the build's ``bin``
dir and writes a flat ``whispercpp-metal-arm64.zip`` (``whisper-server`` at the root — the
layout ``components._find_pack_root`` expects).

Two macOS specifics this handles that the Windows packer doesn't need:

* **Symlink flattening.** The CMake build emits a versioned chain
  (``libwhisper.dylib`` -> ``libwhisper.1.dylib`` -> ``libwhisper.1.9.1.dylib``). ``zipfile``
  can't carry symlinks portably, so we copy the *real* file under its **bare** name
  (``libwhisper.dylib``) — exactly the name the native loader globs and the deps reference.
* **Relocatable install names.** Each shipped dylib gets its ``id`` set to ``@rpath/<bare>``
  and every inter-lib dependency rewritten to ``@loader_path/<bare>`` (via ``install_name_tool``),
  and ``whisper-server``'s deps the same — so the pack resolves its own libs wherever it's
  installed, with no leftover absolute build-dir ``@rpath``. This is the dylib-resolution fix
  the goal flags as the #1 risk for the in-process ``ctypes.CDLL`` path.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# Real build outputs we ship (resolved through their symlinks). whisper-cli is handy for
# debugging; everything else in bin/ (parakeet, bench, *-cli variants) is excluded.
SERVER = "whisper-server"
LIB_PREFIXES = ("libwhisper", "libggml")

# strip the version suffix: libwhisper.1.9.1.dylib / libggml-metal.0.dylib -> libX.dylib
_VER_RE = re.compile(r"^(lib[a-z0-9-]+?)(?:\.[0-9]+)*\.dylib$")


def _bare_name(filename: str) -> str | None:
    """Map any versioned dylib filename to its bare ``libX.dylib`` form, or None if not a
    whisper/ggml dylib we ship."""
    m = _VER_RE.match(filename)
    if not m:
        return None
    stem = m.group(1)
    if not stem.startswith(LIB_PREFIXES):
        return None
    return f"{stem}.dylib"


def _real_dylibs(bin_dir: Path) -> dict[str, Path]:
    """Bare-name -> real (symlink-resolved) file, one per logical lib. Picks the symlink that
    resolves to a regular file so we copy actual bytes, not a dangling link."""
    out: dict[str, Path] = {}
    for p in sorted(bin_dir.glob("*.dylib")):
        bare = _bare_name(p.name)
        if bare is None:
            continue
        real = p.resolve()
        if real.is_file():
            out[bare] = real
    return out


def _otool_deps(path: Path) -> list[str]:
    out = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True).stdout
    deps = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if line:
            deps.append(line.split(" ")[0])
    return deps


def _rpaths(path: Path) -> list[str]:
    """LC_RPATH entries of a Mach-O file (via ``otool -l``)."""
    out = subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True).stdout
    paths, in_rpath = [], False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("cmd LC_RPATH"):
            in_rpath = True
        elif in_rpath and s.startswith("path "):
            paths.append(s.split("path ", 1)[1].rsplit(" (offset", 1)[0])
            in_rpath = False
    return paths


def _relocate(path: Path, bare_self: str | None) -> None:
    """Rewrite ``path``'s install id (if a dylib) + every whisper/ggml dependency to the
    relocatable bare form. Idempotent."""
    if bare_self is not None:
        subprocess.run(["install_name_tool", "-id", f"@rpath/{bare_self}", str(path)],
                       capture_output=True)
    for dep in _otool_deps(path):
        dep_bare = _bare_name(Path(dep).name)
        if dep_bare is None:
            continue
        subprocess.run(
            ["install_name_tool", "-change", dep, f"@loader_path/{dep_bare}", str(path)],
            capture_output=True)


def build_metal_zip(bin_dir: Path, out_zip: Path) -> None:
    server = bin_dir / SERVER
    if not server.is_file():
        sys.exit(f"ERROR: no {SERVER} in {bin_dir}")
    libs = _real_dylibs(bin_dir)
    if not any(name.startswith("libwhisper") for name in libs):
        sys.exit(f"ERROR: no libwhisper*.dylib in {bin_dir}")

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        stage = Path(td)
        members: list[Path] = []

        # Copy each dylib under its bare name, then make it relocatable.
        for bare, real in libs.items():
            dst = stage / bare
            shutil.copy2(real, dst)
            dst.chmod(0o755)
            _relocate(dst, bare)
            members.append(dst)

        # Copy the server, rewrite its deps to the bare libs, then normalise its rpaths:
        # drop every absolute build-dir rpath (leaks the build path, doesn't exist on a user
        # machine) and keep exactly one @loader_path so it finds its siblings post-install.
        srv = stage / SERVER
        shutil.copy2(server, srv)
        srv.chmod(0o755)
        _relocate(srv, None)
        existing = _rpaths(srv)
        for rp in existing:
            if rp != "@loader_path":
                subprocess.run(["install_name_tool", "-delete_rpath", rp, str(srv)],
                               capture_output=True)
        if "@loader_path" not in existing:
            subprocess.run(["install_name_tool", "-add_rpath", "@loader_path", str(srv)],
                           capture_output=True)
        members.insert(0, srv)

        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in members:
                zf.write(p, arcname=p.name)  # flat at the zip root
        total = sum(p.stat().st_size for p in members)
        names = sorted(p.name for p in members)

    print(f"packed {len(members)} files ({total/1e6:.1f} MB) -> {out_zip}")
    print("  " + ", ".join(names))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bin", type=Path, required=True, help="whisper.cpp Metal build bin dir")
    ap.add_argument("--out", type=Path, required=True, help="output zip path")
    args = ap.parse_args(argv)
    if not args.bin.is_dir():
        sys.exit(f"ERROR: build dir not found: {args.bin}")
    build_metal_zip(args.bin, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
