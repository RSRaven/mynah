---
title: Troubleshooting
description: Common issues and fixes.
---

- **Wrong microphone:** run `mynah --list-devices`, then set `input_device` in `[audio]`.
- **Hotkey does nothing:** another app may grab it — change it in Settings or with `--hotkey`. For
  push-to-talk, hold the key the whole time you speak. On macOS see the permissions note below.
- **Wake word too eager / not triggering:** adjust **Sensitivity**; raise **Stop delay** if it
  cuts you off mid-phrase (default 1.5 s; try 2.5 s).
- **No GPU / wrong backend:** **Settings → Backend** overrides detection (Auto / Vulkan / NVIDIA
  CUDA / CPU on PC; Auto / Metal / CPU on Mac). CPU always works as a fallback (pick a smaller
  model like `small`).
- **Paste doesn't land in some terminals:** a few use Ctrl+Shift+V — set `method = "type"` in
  `[insertion]` to simulate keystrokes instead.
- **First transcription is slow (~2 s), later ones ~1 s:** normal GPU warm-up; the model stays
  resident afterwards.
- **SmartScreen warning on first run (Windows):** the app is unsigned — click **More info → Run
  anyway**.

## macOS

- **Nothing happens on the hotkey, or text won't paste:** the bundled app needs three grants in
  **System Settings → Privacy & Security** — **Input Monitoring** (to see the hotkey),
  **Accessibility** (to paste), and **Microphone** (to hear you). Enable **Mynah** in each, then
  **quit and relaunch from Finder** (macOS applies grants on a fresh launch). `mynah --permissions`
  (source checkout) prints what's missing. A common tell: you hear a system "funk" beep on the
  hotkey but nothing records — that means the key isn't reaching Mynah (Input Monitoring).
- **"Mynah.app is damaged / can't be opened":** it's just unsigned — right-click → **Open**, or
  `xattr -dr com.apple.quarantine /Applications/Mynah.app`.
- **Hotkeys stopped working after I rebuilt the app:** ad-hoc rebuilds change the app's code
  identity, so macOS drops the grants. Re-enable Mynah in the panes above, or (for iterative dev)
  sign with a stable self-signed cert — see [Build the macOS app](/mynah/get-started/build-macos/).
- **No bird icon / "where's the window?":** Mynah is a **menu-bar app** with no Dock icon — look
  in the menu bar (top-right). The Settings window opens from the menu-bar icon.

Still stuck? [Open an issue](https://github.com/RSRaven/mynah/issues).
