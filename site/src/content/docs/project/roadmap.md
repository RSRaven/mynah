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
- **macOS (Apple Silicon)** — first-class target, validated on an M2 Pro: a **Metal** engine pack
  (bundled in the app), a `.dmg` installer, the menu-bar app, Cmd+V paste, TCC permission handling
  (with an in-app **Reset & re-grant** for stale grants after an update), and LaunchAgent
  autostart. The full push-to-talk loop, wake word, and multilingual gate run on Metal. The
  `Mynah.app` is **ad-hoc signed** (unsigned distribution) — see below.
- Push-to-talk, toggle, and the optional wake word; multilingual auto-detect; tray / menu-bar app
  + Settings.

## Planned

- **Any computer** — broader GPU/CPU coverage so it runs everywhere (Linux packaging, non-Windows
  Vulkan, Intel Macs / a universal2 build).
- **Apple Developer ID signing + notarization** — ship the `.app` signed with a stable Developer
  ID and notarized, so there's no Gatekeeper bypass **and** macOS privacy grants (Microphone /
  Input Monitoring / Accessibility) survive every update. Today the build is **ad-hoc signed**,
  which gives it a *new* code identity on each release — so after an update the old grants stop
  applying and dictation can transcribe but fail to paste until you re-grant (the in-app **Reset &
  re-grant** button is the current workaround). A persistent Developer ID is the real fix; it needs
  an Apple Developer account ($99/yr) and signing secrets in CI. An optional Windows code-signing
  cert (to drop the SmartScreen warning) is the equivalent ask on Windows.
- An optional MCP `dictate` / `transcribe_file` tool, as an extra on top of the tray app.

## Won't do

- Cloud transcription, accounts, or telemetry. Mynah stays local-only and MIT-licensed.
