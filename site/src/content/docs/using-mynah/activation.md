---
title: Activation modes
description: Push-to-talk, toggle, and the optional hands-free wake word.
sidebar:
  order: 1
---

After setup the model loads and stays resident. Then there are three ways to dictate.

Default hotkeys differ by OS — Windows uses the free F-key row; macOS uses Space chords because
the F-row there needs Fn and chords like `Ctrl+Space` collide with app shortcuts:

| Action | Windows | macOS |
|---|---|---|
| **Push-to-talk** (hold, speak, release) | **`F9`** | **`Cmd+Shift+Space`** |
| **Toggle** (tap to start, tap to stop) | **`F10`** | **`Ctrl+Shift+Space`** |
| **Wake word** | turn on *Listening mode*, say **"hey mynah"**, pause, then dictate | same |

Open **Settings** (left-click the tray / menu-bar icon, or right-click → **Settings…**) to change
the model, language, and hotkeys. Right-click → **Quit** to exit.

## Wake-word "listening mode" (optional)

Hands-free dictation without touching a key. Enable it in **Settings → Listening mode (wake
word)**, on the CLI with `--wakeword`, or with `enabled = true` under `[wakeword]`. Then **say the
phrase, pause, and dictate**:

- Default phrase is **"hey mynah"**. A carrier word ("hey …") is recognised most reliably; a bare
  word is mis-heard more often. Change it in Settings or with `--wake-phrase "…"`.
- **Sensitivity** controls how easily it triggers (it also self-calibrates to your mic). **Stop
  delay** is how long a pause ends a phrase (default **1.5 s**) — raise it toward 2.5 s if it cuts
  you off, lower it for a snappier finish.
- After the phrase matches, the start cue plays and the mic is briefly muted so the cue isn't
  captured as the start of your dictation — so **listening begins right after the cue** (or
  immediately, if sound cues are off). Then speak.
- While it's recording your dictation, your **push-to-talk / toggle hotkey stops it early** and
  types what you said.
- Push-to-talk stays the primary trigger; the wake word is an add-on. The mic is read continuously
  **on your machine only** while it's on.

It never runs the full model just to listen: a tiny model gates on detected speech, and only your
actual dictation runs `large-v3`. See [how the wake word works](/mynah/how-it-works/wake-word/).
