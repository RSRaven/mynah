"""Zip the freshly-built Vulkan whisper.cpp build into the release pack (CI).

Selects only the runnable pack files (server + whisper.dll + ggml backends) from the build's
``bin`` dir and writes a flat ``whispercpp-vulkan-x64.zip`` (whisper-server.exe at the root, the
layout the component manager expects). Standalone — no third-party deps.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

# Files that make up a runnable Vulkan pack (server + the in-process LID/VAD dll + the ggml
# backends it loads). Everything else in the build dir (parakeet, bench, *-cli) is excluded.
PACK_GLOBS = ("whisper-server.exe", "whisper-cli.exe", "whisper.dll", "ggml*.dll")


def build_vulkan_zip(bin_dir: Path, out_zip: Path) -> None:
    members: list[Path] = []
    for pat in PACK_GLOBS:
        members.extend(sorted(bin_dir.glob(pat)))
    if not any(p.name.startswith("whisper-server") for p in members):
        sys.exit(f"ERROR: no whisper-server in {bin_dir}")
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in members:
            zf.write(p, arcname=p.name)  # flat at the zip root
    total = sum(p.stat().st_size for p in members)
    print(f"packed {len(members)} files ({total/1e6:.1f} MB) -> {out_zip}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bin", type=Path, required=True, help="whisper.cpp Vulkan build bin dir")
    ap.add_argument("--out", type=Path, required=True, help="output zip path")
    args = ap.parse_args(argv)
    if not args.bin.is_dir():
        sys.exit(f"ERROR: build dir not found: {args.bin}")
    build_vulkan_zip(args.bin, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
