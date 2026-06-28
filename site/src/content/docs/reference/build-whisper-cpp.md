---
title: Build whisper.cpp (the engine)
description: Build the whisper-server + whisper.dll engine Mynah runs, with Vulkan.
sidebar:
  order: 2
---

You only need this when **running from source** (or replacing the engine pack). The Windows app
downloads a prebuilt Vulkan pack automatically on first run — you don't build anything to use the
installed app.

A "build" is three pieces Mynah uses together:

- **`whisper-server`** — the local HTTP server Mynah sends audio to.
- **`whisper.dll`** — the same library, used **in-process** for language detection and the
  voice-activity splitter (multilingual mode + wake word).
- **`ggml*.dll`** — the GGML backend(s) it loads. The default GPU backend is **Vulkan**
  (`ggml-vulkan.dll`); no cuBLAS/cuDNN.

## Prerequisites (Windows x64, Vulkan)

- **Git**
- **Visual Studio 2022 Build Tools** with the C++ workload — brings MSVC, CMake, and Ninja.
- **Vulkan SDK** — `winget install KhronosGroup.VulkanSDK` (sets `VULKAN_SDK`). The Vulkan
  *runtime* (`vulkan-1.dll`) ships with your GPU driver, so end users need nothing extra.

## Option A — the project's build script (recommended)

The repo includes [`scripts\build_wcpp_vulkan.bat`](https://github.com/RSRaven/mynah/blob/master/scripts/build_wcpp_vulkan.bat),
which clones whisper.cpp at the pinned tag and builds the Vulkan backend for you:

```powershell
scripts\build_wcpp_vulkan.bat
```

Output (~74 MB total): `scripts\_artifacts\wcpp-src\build-vulkan\bin\` containing
`whisper-server.exe`, `whisper-cli.exe`, `whisper.dll`, and `ggml*.dll`.

## Option B — build whisper.cpp by hand

Run these from the **"x64 Native Tools Command Prompt for VS 2022"** (so MSVC + the Vulkan SDK are
on `PATH`):

```powershell
git clone --depth 1 --branch v1.9.1 https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
cmake -B build-vulkan -G Ninja ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DGGML_VULKAN=ON ^
  -DWHISPER_SDL2=OFF ^
  -DWHISPER_BUILD_TESTS=OFF ^
  -DWHISPER_BUILD_EXAMPLES=ON ^
  -DGGML_NATIVE=OFF
cmake --build build-vulkan --config Release -j
```

The runnable files land in `build-vulkan\bin\`: `whisper-server.exe`, `whisper.dll`, `ggml*.dll`.
`WHISPER_BUILD_EXAMPLES=ON` is what builds `whisper-server`.

### CPU-only or CUDA

- **CPU** — drop `-DGGML_VULKAN=ON` for a plain build. It works anywhere; it's slower, so pick a
  smaller model (`medium`/`small`).
- **CUDA** (NVIDIA, optional) — use `-DGGML_CUDA=ON` instead of Vulkan. Needs the CUDA toolkit and
  pulls in cuBLAS/cuDNN (~1.3 GB). Vulkan is the default because it matches CUDA speed on the
  tested hardware with no extra download — see
  [Why whisper.cpp + Vulkan](/mynah/how-it-works/why-whisper-cpp-and-vulkan/).

## Get a model (GGML)

Download a GGML model — e.g. **`ggml-large-v3.bin`** — from the whisper.cpp model repo on
[Hugging Face](https://huggingface.co/ggerganov/whisper.cpp), or with whisper.cpp's
`models\download-ggml-model.cmd large-v3`. Smaller options: `ggml-medium.bin`, `ggml-small.bin`.

## Point Mynah at your build

```powershell
$env:MYNAH_WHISPERCPP_DIR   = "C:\path\to\build-vulkan\bin"
$env:MYNAH_WHISPERCPP_MODEL = "C:\path\to\ggml-large-v3.bin"
mynah --no-tray
```

If you don't set these, Mynah looks in its defaults: `%APPDATA%\mynah\engines\whispercpp` and
`%APPDATA%\mynah\models\ggml-large-v3.bin`. The rest is in
[Command line (CLI)](/mynah/using-mynah/cli/).

:::note[macOS / Linux]
The same CMake build works; outputs are `whisper-server`, `libwhisper.{dylib,so}`, and
`libggml*.{dylib,so}`. On Apple Silicon, Metal is built by default — drop the Vulkan flag.
:::
