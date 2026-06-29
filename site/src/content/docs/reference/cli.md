---
title: Command-line reference
description: Every mynah CLI command and flag.
sidebar:
  order: 1
---

`mynah` runs the tray app. Flags override the config file for that run. For a step-by-step guide
to installing, configuring, and running it (including headless mode), see
[Command line (CLI)](/mynah/using-mynah/cli/).

## Commands

```
mynah                          # start the tray / menu-bar app
mynah --no-tray                # console-only (no tray), e.g. over SSH; runs until Ctrl+C
mynah --probe                  # detect GPU + print the recommended backend/model, then exit
mynah --permissions            # (macOS) print Microphone / Input Monitoring / Accessibility status
mynah --list-devices           # list microphones, then exit
mynah --write-config [--force] # write a commented config.toml to the app-data dir
mynah --purge-runtime          # remove engine packs + config + logs (keeps the model cache)
mynah --purge-ui               # open the per-model delete checklist for the shared model cache
```

## Flags

| Flag | Meaning |
|---|---|
| `-m`, `--model NAME` | Model: `large-v3` (default), `large-v3-turbo`, `medium`, `small`, … |
| `-l`, `--language CODE` | Pin a language (e.g. `en`, `uk`, `pl`, `ru`); `auto` to auto-detect (default). |
| `--backend {auto,vulkan,cuda,metal,cpu}` | Engine pack to run. `auto` = best installed (Vulkan on PC GPUs, Metal on Apple Silicon, else CPU). |
| `--device {auto,cuda,cpu}` | Compute device for the language-ID gate. |
| `--multilingual` / `--no-multilingual` | Split mixed-language clips (default: on). |
| `--wakeword` / `--no-wakeword` | Hands-free wake-word listening mode (default: off). |
| `--wake-phrase "…"` | Set the wake phrase (e.g. `"hey mynah"`). |
| `--hotkey "f9"` | Push-to-talk key/combo (comma-separate for several). Defaults: `f9` on Windows, `cmd+shift+space` on macOS. |
| `--method {paste,type}` | Insert by clipboard paste (default) or simulated typing. |
| `--no-sound` | Disable start/stop sound cues. |
| `--no-tray`, `--headless` | Run as a console app without the tray (runs until Ctrl+C). |
| `--engine NAME` | ASR engine: `auto` \| `whispercpp` (legacy `faster-whisper` accepted). |
| `--config PATH` | Use a specific config file. |
| `--version` | Print the version. |

For the full `config.toml` schema, see [configuration](/mynah/using-mynah/configuration/).
