---
title: Run from source
description: Run Mynah from source on any OS with Python 3.10+.
sidebar:
  order: 2
---

Mynah runs from source on any OS with **Python 3.10+**.

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows; use source .venv/bin/activate elsewhere
pip install -e .
mynah
```

You also need a `whisper.cpp` build (whisper-server + whisper.dll) and a GGML model — see
[Build whisper.cpp](/mynah/reference/build-whisper-cpp/). On a normal install the app downloads
these for you; to point at your own, set the environment variables:

- `MYNAH_WHISPERCPP_DIR` — the whisper.cpp build directory.
- `MYNAH_WHISPERCPP_MODEL` — a `ggml-*.bin` model file.

The default GPU backend is **Vulkan** — point `MYNAH_WHISPERCPP_DIR` at a Vulkan `whisper-server`
build and the backend follows the build. For the full build recipe (Vulkan / CPU / CUDA), see
[Build whisper.cpp](/mynah/reference/build-whisper-cpp/).
