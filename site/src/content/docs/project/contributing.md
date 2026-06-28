---
title: Contributing & license
description: How to help, and the license.
sidebar:
  order: 2
---

Mynah is **free and open-source under the [MIT License](https://github.com/RSRaven/mynah/blob/master/LICENSE)**
— no paid tier, no telemetry, local-only.

## Contributing

- **Issues & ideas:** [github.com/RSRaven/mynah/issues](https://github.com/RSRaven/mynah/issues).
- **Code:** the app is Python (tray app + the `Transcriber` interface around whisper.cpp); the
  docs site lives under `site/` (Astro + Starlight). Keep code cross-platform — OS specifics
  (paste key, autostart, app-data dir) sit behind a thin platform layer.
- **Run from source:** see [run from source](/mynah/get-started/run-from-source/).

## Design principles

- **Local-first.** Audio never leaves the machine; no cloud, no account.
- **One engine, many backends.** whisper.cpp with Vulkan by default; CUDA/CPU/Metal behind the
  same interface.
- **Stay out of the way.** A background tray app, not a window you manage.
