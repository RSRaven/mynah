---
title: Run from source
description: Run Mynah from source on any OS with Python 3.10+.
sidebar:
  order: 4
---

Mynah runs from source on any OS with **Python 3.10+**.

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows; use source .venv/bin/activate elsewhere
pip install -e .
mynah
```

### macOS extras

On macOS the menu-bar + global-hotkey backends need **pyobjc**, and the Settings window needs
**Tk**:

```bash
brew install python-tk@3.12
pip install -e . pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ApplicationServices
```

Running from source inherits your terminal's privacy grants, so it's the quickest way to test
hotkeys/paste without (re)granting an `.app`.

### Pointing at your own engine + model

You also need a `whisper.cpp` build (whisper-server + the whisper shared lib) and a GGML model —
see [Build whisper.cpp](/mynah/reference/build-whisper-cpp/). On a normal install the app
downloads these for you; to point at your own, set the environment variables:

- `MYNAH_WHISPERCPP_DIR` — the whisper.cpp build directory.
- `MYNAH_WHISPERCPP_MODEL` — a `ggml-*.bin` model file.

The default GPU backend is **Vulkan** on PC and **Metal** on Apple Silicon — point
`MYNAH_WHISPERCPP_DIR` at the matching `whisper-server` build and the backend follows the build.
For the full build recipe (Vulkan / Metal / CPU / CUDA), see
[Build whisper.cpp](/mynah/reference/build-whisper-cpp/).
