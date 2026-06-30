---
title: Install (Windows)
description: Download and run Mynah on Windows — installer or portable zip.
sidebar:
  order: 1
---

On **macOS (Apple Silicon)?** See [Install (macOS)](/mynah/get-started/install-macos/) instead.

Grab the latest build from the [**Releases**](https://github.com/RSRaven/mynah/releases) page:

- **`Mynah-Setup-X.Y.Z.exe`** — installer (per-user, no admin prompt). Creates Start-menu and
  desktop shortcuts and an optional "run at login".
- **`Mynah-X.Y.Z-portable.zip`** — unzip and run `Mynah.exe`, no install.

:::caution[SmartScreen]
The app is unsigned, so Windows SmartScreen may warn on first run — click **More info → Run
anyway**.
:::

## First run

The first launch opens a short setup screen: Mynah detects your hardware, then downloads the
speech model with a progress bar. The GPU engine (Vulkan, plus a CPU fallback) ships inside the
app, so there's no engine download — only the model is fetched. After that it lives in the tray
and starts quietly. The optional NVIDIA CUDA upgrade is the one engine pulled on demand, if you
choose it.

The tray icon colour reflects state: **blue** idle · **red** recording · **amber** transcribing
· **purple** loading.

Next: [activation modes](/mynah/using-mynah/activation/) · [configuration](/mynah/using-mynah/configuration/).
