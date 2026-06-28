---
title: Activation modes
description: Push-to-talk, toggle, and the optional hands-free wake word.
sidebar:
  order: 1
---

After setup the model loads and stays resident. Then there are three ways to dictate:

| Action | How |
|---|---|
| **Push-to-talk** | **Hold `F9`**, speak, release — text is pasted at the cursor. |
| **Toggle** | **Tap `F10`** to start, tap again to stop (hands-free, no holding). |
| **Wake word** | Turn on *Listening mode*, say **"hey mynah"**, pause, then dictate. |

Open **Settings** (left-click the tray icon, or right-click → **Settings…**) to change the
model, language, and hotkeys. Right-click → **Quit** to exit.

## Wake-word "listening mode" (optional)

Hands-free dictation without touching a key. Enable it in **Settings → Listening mode (wake
word)**, on the CLI with `--wakeword`, or with `enabled = true` under `[wakeword]`. Then **say the
phrase, pause, and dictate**:

- Default phrase is **"hey mynah"**. A carrier word ("hey …") is recognised most reliably; a bare
  word is mis-heard more often. Change it in Settings or with `--wake-phrase "…"`.
- **Sensitivity** controls how easily it triggers (it also self-calibrates to your mic). **Stop
  delay** is how long a pause ends a phrase — raise it if it cuts you off.
- While it's recording your dictation, **F9 / F10 stops it early** and types what you said.
- Push-to-talk stays the primary trigger; the wake word is an add-on. The mic is read continuously
  **on your machine only** while it's on.

It never runs the full model just to listen: a tiny model gates on detected speech, and only your
actual dictation runs `large-v3`. See [how the wake word works](/mynah/how-it-works/wake-word/).
