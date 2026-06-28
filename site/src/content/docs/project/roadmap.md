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
- Push-to-talk, toggle, and the optional wake word; multilingual auto-detect; tray app + Settings.

## Planned

- **macOS (Apple Silicon)** as a first-class target — Metal / MLX, validated on an M2 Pro.
- **Any computer** — broader GPU/CPU coverage so it runs everywhere.
- Auto-download of engine packs and models (today some setups point at a prebuilt build).
- An optional MCP `dictate` / `transcribe_file` tool, as an extra on top of the tray app.

## Won't do

- Cloud transcription, accounts, or telemetry. Mynah stays local-only and MIT-licensed.
