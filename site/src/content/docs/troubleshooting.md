---
title: Troubleshooting
description: Common issues and fixes.
---

- **Wrong microphone:** run `mynah --list-devices`, then set `input_device` in `[audio]`.
- **Hotkey does nothing:** another app may grab it — change it in Settings or with `--hotkey`. For
  push-to-talk, hold the key the whole time you speak.
- **Wake word too eager / not triggering:** adjust **Sensitivity**; raise **Stop delay** if it
  cuts you off mid-phrase.
- **No GPU / wrong backend:** **Settings → Backend** overrides detection (Auto / Vulkan / NVIDIA
  CUDA / CPU). CPU always works as a fallback (pick a smaller model like `small`).
- **Paste doesn't land in some terminals:** a few use Ctrl+Shift+V — set `method = "type"` in
  `[insertion]` to simulate keystrokes instead.
- **First transcription is slow (~2 s), later ones ~1 s:** normal GPU warm-up; the model stays
  resident afterwards.
- **SmartScreen warning on first run:** the app is unsigned — click **More info → Run anyway**.

Still stuck? [Open an issue](https://github.com/RSRaven/mynah/issues).
