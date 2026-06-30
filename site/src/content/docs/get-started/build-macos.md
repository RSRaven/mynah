---
title: Build the macOS app
description: Build the Metal engine pack and the Mynah.app bundle yourself, step by step.
sidebar:
  order: 6
---

This builds the exact artifacts the [Releases](https://github.com/RSRaven/mynah/releases) page
ships for macOS: the **Metal whisper.cpp engine pack** (`whispercpp-metal-arm64.zip`) and the
**`Mynah.app`** bundle (PyInstaller). Like the Windows build it's small — it bundles the app and
light dependencies, but **no model**; the model is fetched on first run into the shared Hugging
Face cache.

Apple Silicon (**arm64**) only. The Apple toolchain doesn't cross-compile and PyInstaller can't
cross-build a `.app`, so this must run on a Mac (or a `macos-14` CI runner).

## Prerequisites

```bash
brew install cmake python@3.12 python-tk@3.12
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e . pyinstaller pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ApplicationServices
```

- **Xcode command-line tools** (`xcode-select --install`) — clang + the Metal toolchain.
- **CMake** — to build whisper.cpp.
- **python-tk** — the Settings window is Tkinter; on macOS it runs in its own process.
- **pyobjc** frameworks — pystray's + pynput's macOS backends, and the permissions helper.

## 1. Build + pack the Metal engine pack

No upstream Metal `whisper-server` asset exists, so Mynah builds and hosts it (just like the
Vulkan pack on Windows).

```bash
bash scripts/build_wcpp_metal.sh
python scripts/pack_metal.py \
  --bin scripts/_artifacts/wcpp-src/build-metal/bin \
  --out dist/whispercpp-metal-arm64.zip
```

[`build_wcpp_metal.sh`](https://github.com/RSRaven/mynah/blob/master/scripts/build_wcpp_metal.sh)
clones whisper.cpp at the pinned tag and builds with `GGML_METAL=ON` +
`GGML_METAL_EMBED_LIBRARY=ON` (the shader lib is embedded, so the pack is relocatable).
[`pack_metal.py`](https://github.com/RSRaven/mynah/blob/master/scripts/pack_metal.py) flattens the
versioned dylib symlink chain to bare names and rewrites every inter-library dependency to
`@loader_path/…`, so the shipped pack resolves its own `libwhisper`/`libggml*` dylibs wherever it
lands — the in-process LID + VAD load via `ctypes` from an arbitrary install dir.

Output (~4 MB): `whisper-server`, `libwhisper.dylib`, and the `libggml*.dylib` set, flat in the zip.

## 2. Build the .app (PyInstaller)

Stage the Metal pack into the bundle first (so the shipped `.app` needs no engine download),
then build:

```bash
python scripts/stage_engines.py --out build/_engines \
  --pack metal=dist/whispercpp-metal-arm64.zip
pyinstaller --noconfirm mynah.spec        # -> dist/Mynah.app
```

If you skip the staging step, the build still works — it just falls back to downloading the
Metal pack on first run instead of carrying it inside the `.app`.

The darwin branch of [`mynah.spec`](https://github.com/RSRaven/mynah/blob/master/mynah.spec):

- Pins the `_darwin` backends of pynput/pystray and `collect_all`s the pyobjc frameworks
  (including **`HIServices`** — pynput's Accessibility check needs it, or the hotkey listener
  dies in a frozen build).
- Uses an `.icns` icon and emits a `BUNDLE` with `LSUIElement=True` (menu-bar agent, no Dock
  icon), `NSMicrophoneUsageDescription` (required before macOS will show the mic prompt), and the
  bundle identifier `com.mynah.mynah`.

## 3. Sign the .app

macOS ties privacy grants (Microphone / Input Monitoring / Accessibility) to the app's **code
identity**, so the bundle must be signed.

```bash
codesign -s - --deep --force dist/Mynah.app   # ad-hoc — what CI ships (unsigned distribution)
```

:::tip[Iterating? Use a stable self-signed certificate]
**Ad-hoc signing produces a new identity (cdhash) on every build**, so each rebuild invalidates
your privacy grants and you have to re-enable Mynah in System Settings. A stable self-signed cert
fixes that — grant **once**, then every rebuild keeps it:

1. **Keychain Access → Certificate Assistant → Create a Certificate…**
   - Name: `Mynah Dev Signing` · Identity Type: **Self Signed Root** · Certificate Type: **Code
     Signing**.
   - After creating it, double-click it → **Trust → Code Signing: Always Trust**.
2. Sign with that identity after each build (same identity every time):
   ```bash
   codesign -s "Mynah Dev Signing" --deep --force dist/Mynah.app
   ```

This is a **local dev convenience only** — it isn't committed and CI doesn't use it. If grants
ever get into a weird state, reset them: `tccutil reset Accessibility com.mynah.mynah` (and
`ListenEvent`, `Microphone`).
:::

## 4. Zip for distribution

```bash
ditto -c -k --keepParent dist/Mynah.app "dist/Mynah-0.4.0-macos-arm64.zip"
```

Distribute the zip. The app is unsigned, so users do a one-time Gatekeeper bypass and grant the
three permissions — see [Install (macOS)](/mynah/get-started/install-macos/).

## What's bundled vs. downloaded at first run

A `Mynah.app` built with the staging step above carries the **Metal engine** inside the bundle,
so on first launch it downloads only the **speech model** into the shared Hugging Face cache. The
`.app` still carries no model — that stays a first-run download.

## CI builds this for you

The `build-macos` job (on `macos-14`) in
[`release.yml`](https://github.com/RSRaven/mynah/blob/master/.github/workflows/release.yml) runs
all of the above on a tag: builds the pack, stages it into the bundle, builds + ad-hoc signs the
`.app`, zips, merges the Metal component into `manifest.json`, and uploads everything to the
GitHub Release.
