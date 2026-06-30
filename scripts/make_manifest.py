"""Generate the release ``manifest.json`` — run by CI on a tag.

Produces the pinned manifest the component manager reads: for each engine pack a URL + sha256
+ size. The **Vulkan** pack is our own build (hashed from the local zip CI just produced); the
**CPU** and optional **CUDA** packs are upstream whisper.cpp release assets (their URLs + sizes
resolved from the GitHub API, and — with ``--hash-upstream`` — downloaded and sha256-pinned).

As of 0.5.0 the per-OS packs ship **bundled inside the app** (Windows: Vulkan + CPU; macOS:
Metal), so on a healthy install **nothing here is downloaded** except the optional NVIDIA
**CUDA** upgrade. The bundled packs are still listed (and published as release assets) with a
``"bundled": true`` marker, so a *corrupted* in-app pack can self-heal by re-downloading into
the runtime dir. Only ``whispercpp-cuda`` is a genuine fetch-on-demand.

The output is written both into the build (``mynah/manifest.json``, so it ships inside the
app) and uploaded as a release asset.

Usage (CI):
    python scripts/make_manifest.py \
        --version 0.1.0 \
        --release-base https://github.com/RSRaven/mynah/releases/download/v0.1.0 \
        --vulkan-zip dist/whispercpp-vulkan-x64.zip \
        --upstream-tag v1.9.1 \
        --out mynah/manifest.json \
        --hash-upstream
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

UPSTREAM_REPO = "ggml-org/whisper.cpp"
# Asset-name predicates for the upstream Windows x64 packs.
CPU_MATCH = lambda n: n == "whisper-bin-x64.zip"  # noqa: E731
CUDA_MATCH = lambda n: n.startswith("whisper-cublas-") and n.endswith("-bin-x64.zip")  # noqa: E731


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _get_json(url: str) -> dict:
    headers = {"User-Agent": "mynah-make-manifest",
               "Accept": "application/vnd.github+json"}
    # Authenticate the GitHub API call when a token is available (CI): unauthenticated requests
    # are rate-limited to 60/hour per runner IP and intermittently 403 ("rate limit exceeded").
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "mynah-make-manifest"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:  # noqa: S310
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def _find_asset(assets: list[dict], match) -> dict | None:
    for a in assets:
        if match(a.get("name", "")):
            return a
    return None


def _upstream_entry(asset: dict, hash_upstream: bool, extra: dict) -> dict:
    entry = {
        "url": asset["browser_download_url"],
        "size": int(asset.get("size", 0)),
        "sha256": "",
        "source": "upstream-whisper.cpp",
    }
    entry.update(extra)
    if hash_upstream:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / asset["name"]
            print(f"  downloading {asset['name']} ({entry['size']/1e6:.0f} MB) to hash…")
            _download(asset["browser_download_url"], tmp)
            entry["sha256"] = _sha256(tmp)
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True)
    ap.add_argument("--release-base", required=True,
                    help="base URL of this release's assets")
    ap.add_argument("--vulkan-zip", type=Path, default=None,
                    help="path to the freshly-built whispercpp-vulkan-x64.zip (required unless "
                         "--merge-into already carries the vulkan component)")
    ap.add_argument("--metal-zip", type=Path, default=None,
                    help="path to the freshly-built whispercpp-metal-arm64.zip (macOS CI; "
                         "merged into the manifest when present)")
    ap.add_argument("--merge-into", type=Path, default=None,
                    help="an existing manifest.json to start from (keeps its components, e.g. "
                         "the vulkan/cpu/cuda packs from the Windows job) and overlay onto. Used "
                         "by the macOS job to add the Metal pack without re-fetching upstream.")
    ap.add_argument("--upstream-tag", default="v1.9.1")
    ap.add_argument("--out", type=Path, default=Path("mynah/manifest.json"))
    ap.add_argument("--hash-upstream", action="store_true",
                    help="download upstream CPU/CUDA packs to pin their sha256 (slow)")
    args = ap.parse_args(argv)

    base = args.release_base.rstrip("/")

    # --merge-into: start from an existing manifest (e.g. the Windows job's vulkan+cpu+cuda)
    # and only overlay what this run produces. Skips the upstream API fetch entirely.
    if args.merge_into is not None:
        if not args.merge_into.is_file():
            sys.exit(f"ERROR: --merge-into manifest not found: {args.merge_into}")
        prior = json.loads(args.merge_into.read_text(encoding="utf-8"))
        components: dict = dict(prior.get("components", {}))
        if args.metal_zip is None:
            sys.exit("ERROR: --merge-into is for adding a pack — pass --metal-zip")
    else:
        components = {}
        if args.vulkan_zip is None:
            sys.exit("ERROR: --vulkan-zip is required (or use --merge-into)")
        if not args.vulkan_zip.is_file():
            sys.exit(f"ERROR: vulkan zip not found: {args.vulkan_zip}")
        # Our Vulkan pack (hashed from the local build).
        components["whispercpp-vulkan"] = {
            "kind": "engine", "backend": "vulkan", "bundled": True,
            "url": f"{base}/whispercpp-vulkan-x64.zip",
            "sha256": _sha256(args.vulkan_zip),
            "size": args.vulkan_zip.stat().st_size,
            "license": "MIT", "source": "mynah-release",
            "note": "Mynah-built Vulkan whisper.cpp runtime — the default GPU backend. "
                    "Bundled in the Windows app; listed here only for self-heal re-download.",
        }

    # Our Metal pack (Apple Silicon, arm64) — built on the macOS runner; merged here when its
    # zip is available. No upstream Metal server asset exists, so we host it like Vulkan.
    if args.metal_zip is not None:
        if not args.metal_zip.is_file():
            sys.exit(f"ERROR: metal zip not found: {args.metal_zip}")
        components["whispercpp-metal"] = {
            "kind": "engine", "backend": "metal", "bundled": True,
            "url": f"{base}/whispercpp-metal-arm64.zip",
            "sha256": _sha256(args.metal_zip),
            "size": args.metal_zip.stat().st_size,
            "license": "MIT", "source": "mynah-release",
            "note": "Mynah-built Metal whisper.cpp runtime for Apple Silicon (arm64). "
                    "Bundled in the macOS app; listed here only for self-heal re-download.",
        }

    # Upstream CPU + optional CUDA packs (skipped in --merge-into mode — the base manifest
    # already carries them from the Windows job).
    if args.merge_into is None:
        rel = _get_json(
            f"https://api.github.com/repos/{UPSTREAM_REPO}/releases/tags/{args.upstream_tag}")
        assets = rel.get("assets", [])
        cpu = _find_asset(assets, CPU_MATCH)
        cuda = _find_asset(assets, CUDA_MATCH)
        if cpu is None:
            sys.exit(f"ERROR: no CPU asset matching whisper-bin-x64.zip in {args.upstream_tag}")
        components["whispercpp-cpu"] = {
            "kind": "engine", "backend": "cpu", "bundled": True,
            **_upstream_entry(cpu, args.hash_upstream, {
                "license": "MIT",
                "note": "Upstream CPU build — the no-GPU fallback (single-engine). Bundled in "
                        "the Windows app; listed here only for self-heal re-download.",
            })}
        if cuda is not None:
            components["whispercpp-cuda"] = {
                "kind": "engine", "backend": "cuda", "optional": True,
                **_upstream_entry(cuda, args.hash_upstream, {
                    "license": "MIT + NVIDIA CUDA Toolkit EULA (bundled cuBLAS)",
                    "license_note": ("Bundles NVIDIA cuBLAS under NVIDIA's CUDA Toolkit EULA, "
                                     "fetched from the upstream whisper.cpp release (NVIDIA's "
                                     "distribution channel); Mynah does not host NVIDIA "
                                     "binaries."),
                    "license_url": "https://docs.nvidia.com/cuda/eula/index.html",
                    "note": "Optional NVIDIA speed upgrade; self-contained cuBLAS.",
                })}
        else:
            print("! no CUDA asset found upstream — manifest will omit the optional CUDA pack")

    manifest = {
        "schema": 1,
        "version": args.version,
        "generated": f"ci (upstream {args.upstream_tag})",
        "release_base": args.release_base,
        "components": components,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out} with {len(components)} components")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
