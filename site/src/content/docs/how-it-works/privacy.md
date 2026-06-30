---
title: Privacy model
description: What's local, what's downloaded, and where files live.
sidebar:
  order: 4
---

Mynah is **local-only**. Audio is processed on your device and never uploaded; there is no
telemetry and no account.

## What stays on your machine

- **Your voice.** Recording, transcription, language detection, and the wake-word spotter all run
  on-device. Nothing is sent to a server.
- **Your text.** Transcribed text goes straight to the clipboard / your cursor.
- **Your settings.** Config lives in a local `config.toml`.

## What's downloaded (once)

The GPU engine ships **inside the app** (Vulkan + CPU on Windows, Metal on macOS), so on first
run the app fetches, with a progress bar, only:

- the **speech model** (e.g. `large-v3`, ~3 GB) into the shared Hugging Face cache.

The optional NVIDIA **CUDA** pack (~1.3 GB) is the one engine fetched on demand, and only if you
opt into it. These are static downloads of the model (and optional CUDA engine) — not your data
going out. After that the app works fully offline.

## Where files live

- Config + logs: `%APPDATA%\mynah\`
- Bundled engine: inside the app (e.g. `%LOCALAPPDATA%\Programs\Mynah\_engines\`)
- Downloaded engine packs (CUDA / self-heal): `%LOCALAPPDATA%\mynah\engines\`
- Models: `~/.cache/huggingface/hub`

Free and open-source under the [MIT License](https://github.com/RSRaven/mynah/blob/master/LICENSE).
