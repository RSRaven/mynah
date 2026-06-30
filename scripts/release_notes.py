"""Generate the GitHub Release body — a guide to every published asset (CI).

Both release jobs publish to the same tag; the macOS job runs last (``needs: build``), so it
writes the notes. The point is to demystify the asset list: which file a user actually wants per
OS, and what the engine-pack / manifest files are for. Asset names carry the version, so the
body is generated rather than static.

Usage:  python scripts/release_notes.py --version 0.5.1 > notes.md
"""

from __future__ import annotations

import argparse


def render(version: str) -> str:
    v = version
    return f"""\
## Mynah {v}

Local, free push-to-talk voice typing. Hold a hotkey, speak, and the text is pasted at your
cursor — fully on-device.

### Which file do I download?

| Your OS | Download | Notes |
|---|---|---|
| **Windows** | `Mynah-Setup-{v}.exe` | Installer (recommended). Per-user, no admin prompt. |
| **Windows** (portable) | `Mynah-{v}-portable.zip` | No installer — unzip and run `Mynah.exe`. |
| **macOS** (Apple Silicon) | `Mynah-{v}-macos-arm64.dmg` | Open it, then **drag Mynah into Applications**. |
| **macOS** (zip) | `Mynah-{v}-macos-arm64.zip` | Same app as the DMG; unzips in place (move it to Applications yourself). |

The GPU engine ships **inside** the app (Vulkan + CPU on Windows, Metal on macOS), so first run
downloads only the speech model. The optional NVIDIA **CUDA** pack is the one engine fetched on
demand.

> **macOS is unsigned** (notarization deferred): the DMG/zip aren't from an identified developer,
> so Gatekeeper blocks the first launch. **Right-click Mynah → Open**, then confirm once — or run
> `xattr -dr com.apple.quarantine /Applications/Mynah.app`. After an update, if dictation types
> but doesn't paste, open **Settings → Permissions → Reset & re-grant** and re-enable Mynah under
> Accessibility + Input Monitoring.

### What are the other files?

- **`whispercpp-vulkan-x64.zip`** / **`whispercpp-metal-arm64.zip`** — the prebuilt whisper.cpp
  engine packs. You **don't** need to download these: they're already bundled in the app. They're
  published so a corrupted in-app engine can self-heal, and for anyone building from source.
- **`manifest.json`** — the pinned component manifest (engine pack URLs + checksums) the app
  reads to know what to download/verify. Internal; not something you install.

### Install docs

- Windows: https://rsraven.github.io/mynah/get-started/install/
- macOS: https://rsraven.github.io/mynah/get-started/install-macos/
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True)
    args = ap.parse_args(argv)
    print(render(args.version), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
