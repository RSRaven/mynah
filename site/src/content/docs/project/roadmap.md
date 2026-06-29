---
title: Roadmap
description: What's built, what's planned.
sidebar:
  order: 1
---

Mynah is built and validated in phases, hardware by hardware.

## Now

- **Windows + NVIDIA** — built and validated first (RTX 2080, 8 GB VRAM, Windows 11).
- **Vulkan default** on any GPU (NVIDIA / AMD / Intel), with CUDA as an optional NVIDIA-only speed
  pack and CPU as the universal fallback.
- **macOS (Apple Silicon)** — first-class target, validated on an M2 Pro: a **Metal** engine pack,
  the menu-bar app, Cmd+V paste, TCC permission handling, LaunchAgent autostart, and a signed
  `Mynah.app`. The full push-to-talk loop, wake word, and multilingual gate run on Metal.
- Push-to-talk, toggle, and the optional wake word; multilingual auto-detect; tray / menu-bar app
  + Settings.

## Planned

- **Any computer** — broader GPU/CPU coverage so it runs everywhere (Linux packaging, non-Windows
  Vulkan, Intel Macs / a universal2 build).
- **Code signing + notarization** — ship a signed, notarized `.app` (and an optional Windows cert)
  so there's no Gatekeeper / SmartScreen step. Currently shipped unsigned.
- Auto-download of engine packs and models (today some setups point at a prebuilt build).
- An optional MCP `dictate` / `transcribe_file` tool, as an extra on top of the tray app.

## Won't do

- Cloud transcription, accounts, or telemetry. Mynah stays local-only and MIT-licensed.
