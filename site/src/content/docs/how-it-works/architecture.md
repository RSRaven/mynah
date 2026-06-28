---
title: Architecture & pipeline
description: One loop, one runtime — hotkey to text, entirely on your machine.
sidebar:
  order: 1
---

```
[hotkey / wake word] → record mic → whisper.cpp (resident on GPU) → insert text at cursor
```

Mynah records 16 kHz mono audio while the hotkey is held (or after the wake word), runs it
through a resident **whisper.cpp** model, and pastes the result at your cursor. The model stays
loaded between dictations for low latency.

The same `whisper.dll` also powers the in-process **language detector** and the **voice-activity
splitter** used for multilingual mode and the wake word — there's no second runtime.

## What's used

- **Engine:** `whisper.cpp` (one engine for every platform; GGML model format).
- **GPU backend:** **Vulkan** by default on any GPU (NVIDIA / AMD / Intel). **CUDA** is an
  optional NVIDIA-only speed pack; **CPU** is the universal fallback. On Apple Silicon, Metal/MLX
  (planned). See [GPU backends](/mynah/how-it-works/why-whisper-cpp-and-vulkan/).
- **Model:** `large-v3` by default (selectable). Stays resident in VRAM.
- **Insertion:** clipboard paste by default (restores your previous clipboard), or simulated
  typing.

Read on for [why whisper.cpp + Vulkan](/mynah/how-it-works/why-whisper-cpp-and-vulkan/),
[how the wake word works](/mynah/how-it-works/wake-word/), and the [privacy model](/mynah/how-it-works/privacy/).
