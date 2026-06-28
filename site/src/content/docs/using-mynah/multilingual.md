---
title: Multilingual dictation
description: Mix languages in a single clip — each part transcribed in its own language.
sidebar:
  order: 2
---

A single Whisper pass commits to one language per clip. With **Multilingual** on (the default), a
cheap language check runs first; single-language clips take the fast path, and only clips that
actually mix languages are split and transcribed **each part in its own language**.

Toggle it in Settings, with `--no-multilingual`, or `multilingual = false` under `[language]`.

- Single-language clips stay fast — the extra check is cheap.
- Mixed-language clips take a little longer, because each segment is transcribed separately.
- Pin one language instead with `--language en` (or `uk`, `pl`, `ru`, …), or set `mode = "fixed"`
  under `[language]`.

This is the same LID-gate + per-segment design described in
[how it works](/mynah/how-it-works/architecture/) — there's no second engine.
