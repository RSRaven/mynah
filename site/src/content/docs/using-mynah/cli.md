---
title: Command line (CLI)
description: Install, configure, and run Mynah from the terminal — including headless mode.
sidebar:
  order: 4
---

Mynah is one program: the same `mynah` command runs the tray app, a **headless console app**
(no tray — good over SSH or on a server), and a few one-off utilities. This page covers
installing it as a CLI, pointing it at an engine + model, configuring it, and running it.

For the exhaustive flag list, see the [CLI reference](/mynah/reference/cli/).

## Install the CLI

There are two ways to get the `mynah` command.

### A — from the Windows app

The installed app ships the same executable; `Mynah.exe` accepts every flag below.

```powershell
& "$env:LOCALAPPDATA\Programs\Mynah\Mynah.exe" --probe
```

The portable zip's `Mynah.exe` works the same way. (`Mynah.exe` is a windowed build, so use
`--no-tray` if you want console output.)

### B — from source (any OS, Python 3.10+)

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows; source .venv/bin/activate elsewhere
pip install -e .
mynah --version
```

`pip install -e .` registers the `mynah` console command (entry point `mynah.cli:main`).

You also need a **whisper.cpp build** (whisper-server + `whisper.dll`) and a **GGML model** —
see [Build whisper.cpp](/mynah/reference/build-whisper-cpp/). The Windows app downloads these on
first run; from source, point Mynah at your own with environment variables:

```powershell
# Windows (PowerShell)
$env:MYNAH_WHISPERCPP_DIR   = "C:\path\to\whisper-server-build"   # has whisper-server + whisper.dll
$env:MYNAH_WHISPERCPP_MODEL = "C:\path\to\ggml-large-v3.bin"
```

```bash
# macOS / Linux
export MYNAH_WHISPERCPP_DIR=/path/to/whisper-server-build
export MYNAH_WHISPERCPP_MODEL=/path/to/ggml-large-v3.bin
```

The GPU backend is whatever that build targets — the default is a **Vulkan** `whisper-server`
build (no cuBLAS/cuDNN download). If you don't set these, Mynah looks in its defaults:
`%APPDATA%\mynah\engines\whispercpp` and `%APPDATA%\mynah\models\ggml-large-v3.bin`.

## Check your setup

```
mynah --probe          # detect the GPU and print the recommended backend + model
mynah --list-devices   # list microphones as "index: name"
mynah --version
```

`mynah --probe` prints something like:

```
Hardware probe:
  GPU     : NVIDIA  (NVIDIA GeForce RTX 2080)
  VRAM    : 8192 MB   [source: nvml]
  backend : vulkan   (vulkan=default GPU backend · cpu=fallback)
  model   : large-v3
  -> ...
```

## Configure it

Precedence is **built-in defaults → `config.toml` → CLI flags** — flags win, for that run only.
Keep your settings in the config file and use flags for one-offs.

Write a starter config (fully commented):

```
mynah --write-config          # writes %APPDATA%\mynah\config.toml
mynah --write-config --force  # overwrite an existing one
```

Edit it (every key is documented under [Configuration](/mynah/using-mynah/configuration/)), or
override per run:

```
mynah -m large-v3-turbo -l en --backend vulkan   # lighter model, English, Vulkan
mynah --hotkey "f9,ctrl+space" --method type     # two PTT keys, type instead of paste
mynah --wakeword --wake-phrase "hey mynah"       # enable hands-free listening mode
mynah --config ./my-config.toml                  # use a specific file
```

## Run it

```
mynah                  # the tray app (default)
mynah --no-tray        # headless console app — no tray icon
```

### Headless / CLI mode

`--no-tray` (alias `--headless`) loads the model, registers your push-to-talk hotkey, and runs a
console loop **until you press Ctrl+C**. There's no tray icon — hold the hotkey and speak exactly
as in the tray app, and the text is inserted at your cursor. This is the mode to use over SSH, in
a container, or on a headless box.

```
mynah --no-tray                          # console app with your config defaults
mynah --no-tray -m small --backend cpu   # CPU-only machine, smaller model
mynah --no-tray -l en --no-sound         # English-only, no sound cues
```

On startup it prints the resolved config path and, once the model is resident, an `OK …` line
with the engine/model and load time. Logs also go to `%APPDATA%\mynah\mynah.log`. Stop it with
**Ctrl+C**.

## Maintenance helpers

These are what the uninstaller calls; you can also run them by hand:

```
mynah --purge-runtime   # remove engine packs + config + logs (keeps the shared model cache)
mynah --purge-ui        # open the per-model delete checklist for the shared model cache
```
