---
title: Wake word
description: How hands-free listening works — and why it never runs Whisper to listen.
sidebar:
  order: 3
---

The wake word is an optional, hands-free way to start dictating. It never runs the full model
just to listen.

## How it works

- A tiny, **VAD-gated** spotter listens on the **CPU**. It only wakes on detected speech, so it
  isn't running a heavy model continuously.
- When it hears the wake phrase, it starts a normal dictation — and only that dictation runs
  `large-v3` on the GPU.
- After you stop speaking (a trailing pause longer than the **stop delay**), it transcribes and
  inserts the text, exactly like push-to-talk.

## Why not always-on Whisper

Running `large-v3` continuously to listen would waste the GPU and your battery, and keep the mic
permanently busy on the heavy model. The spotter-then-dictate design keeps the expensive model
idle until you actually speak to it.


## Privacy

The mic is read continuously **on your machine only** while listening mode is on — audio is never
uploaded. Push-to-talk stays the primary trigger; the wake word is an add-on you opt into. See the
[privacy model](/mynah/how-it-works/privacy/).

Tuning lives under `[wakeword]` — `phrase`, `sensitivity`, `silence_ms` (stop delay), and
`max_seconds`. See [configuration](/mynah/using-mynah/configuration/).
