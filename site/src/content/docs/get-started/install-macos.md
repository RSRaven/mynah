---
title: Install (macOS)
description: Download and run Mynah on macOS (Apple Silicon) — the menu-bar app, permissions, first run.
sidebar:
  order: 2
---

Mynah runs on **Apple Silicon** (M1/M2/M3/…) with a **Metal** GPU backend. Intel Macs aren't
supported yet.

Grab **`Mynah-X.Y.Z-macos-arm64.zip`** from the
[**Releases**](https://github.com/RSRaven/mynah/releases) page, unzip it, and move
**`Mynah.app`** to `/Applications`.

## Clear Gatekeeper (unsigned app)

The app is **unsigned** (notarization is deferred), so Gatekeeper blocks it on first launch.
Either:

- **Right-click `Mynah.app` → Open**, then confirm once in the dialog, **or**
- run:
  ```bash
  xattr -dr com.apple.quarantine /Applications/Mynah.app
  ```

## Grant the three permissions

Mynah is a **menu-bar app** — it shows a small bird icon in the menu bar and has **no Dock
icon**. Its core loop needs three macOS privacy grants. Without them the app *silently* does
nothing (no error). On first run macOS prompts for some; grant all three in **System Settings →
Privacy & Security**:

| Permission | Why | When it prompts |
|---|---|---|
| **Microphone** | hear your voice | automatically, on first capture |
| **Input Monitoring** | detect the push-to-talk hotkey | usually manual — add **Mynah** yourself |
| **Accessibility** | paste the transcribed text (Cmd+V) | usually manual — add **Mynah** yourself |

If Mynah isn't listed in a pane, click **+** and pick `/Applications/Mynah.app`. After granting,
**quit Mynah** (menu-bar bird → **Quit**) and **relaunch it from Finder** — macOS applies the
grants on a fresh launch.

:::tip[Run `mynah --permissions`]
From a source checkout you can print the current grant status and the exact panes to open:
`python -m mynah --permissions`.
:::

## First run

The first launch downloads the **Metal engine pack** (~4 MB) and the speech model
(`large-v3-turbo` by default) with a progress bar, into `~/Library/Application Support/mynah/`
and the shared Hugging Face cache. After that it lives in the menu bar and starts quietly.

The bird icon colour reflects state: **blue** idle · **red** recording · **amber** transcribing
· **purple** loading.

## Hotkeys (macOS defaults)

The F-key row on a Mac defaults to media keys (needs Fn) and common chords like `Ctrl+Space`
collide with system / app shortcuts, so macOS uses Space chords:

| Action | macOS default |
|---|---|
| **Push-to-talk** (hold) | **`Cmd+Shift+Space`** |
| **Toggle** (tap on/off) | **`Ctrl+Shift+Space`** |

Change them anytime in **Settings**. Left-click the menu-bar icon (or right-click → **Settings…**)
to open it; right-click → **Quit** to exit.

Next: [activation modes](/mynah/using-mynah/activation/) · [configuration](/mynah/using-mynah/configuration/).
