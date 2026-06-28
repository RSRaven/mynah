---
title: Configuration
description: The config.toml schema — every section, with the defaults.
sidebar:
  order: 3
---

Settings live in `%APPDATA%\mynah\config.toml` (created on first save). Command-line flags
override the file; the Settings window and tray write changes back to it. Any key you omit falls
back to its built-in default.

Write a fully-commented starter file with `mynah --write-config`.

## Sections

- `[model]` — `name` (the model), `device`.
- `[hardware]` — `backend` = `auto | vulkan | cuda | cpu`.
- `[language]` — `mode` (`auto`/`fixed`), `fixed`, `multilingual`.
- `[hotkey]` — `push_to_talk`, `toggle`, optional `wakeword` toggle.
- `[insertion]` — `method` (`paste`/`type`), `restore_clipboard`.
- `[audio]` — `sample_rate`, `input_device`.
- `[ux]` — `sound_cues`, cue device/files, `min_clip_ms`.
- `[wakeword]` — `enabled`, `phrase`, `sensitivity`, `silence_ms` (stop delay), `max_seconds`.

## Example

```toml
[model]
engine = "auto"                 # auto | whispercpp
name = "large-v3"               # large-v3 | large-v3-turbo | medium | small | ...
device = "auto"                 # auto | cuda | cpu (also the multilingual LID gate)

[language]
mode = "auto"                   # auto | fixed
fixed = "en"                    # used when mode = fixed
multilingual = true             # split mixed-language clips

[hotkey]
push_to_talk = "f9"             # hold to record, release to transcribe
toggle = "f10"                  # tap once to start, tap again to stop
multilingual = ""               # optional: tap to toggle multilingual ("" = disabled)
wakeword = ""                   # optional: tap to toggle listening mode ("" = disabled)

[insertion]
method = "paste"                # paste | type
restore_clipboard = true

[audio]
sample_rate = 16000
input_device = "default"        # "default" or a device index/name (see --list-devices)

[ux]
sound_cues = true
cue_device = "default"
min_clip_ms = 300               # ignore accidental taps shorter than this

[wakeword]
enabled = false
phrase = "hey mynah"            # a carrier word ("hey …") is the most reliable
sensitivity = 0.5               # 0..1 — higher triggers more easily
silence_ms = 900                # "stop delay": end a phrase after this much trailing silence
max_seconds = 30                # cap a single hands-free dictation
```

## File locations

- Config: `%APPDATA%\mynah\config.toml`
- Logs: `%APPDATA%\mynah\mynah.log`
- Engine packs: `%LOCALAPPDATA%\mynah\engines\`
- Models: the shared Hugging Face cache (`~/.cache/huggingface/hub`), reused across apps.

On macOS the app-data dir is `~/Library/Application Support/mynah/`.
