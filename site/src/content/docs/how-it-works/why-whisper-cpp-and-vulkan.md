---
title: Why whisper.cpp + Vulkan
description: The engine and GPU-backend choices, with the benchmark numbers.
sidebar:
  order: 2
---

Mynah ships one engine across every platform and defaults to a GPU backend that needs no extra
download. Both choices were benchmarked, not guessed.

## One engine: whisper.cpp

The alternative (faster-whisper / CTranslate2) was measured at parity — so we kept the simpler
one. whisper.cpp gives one codebase and one model format for every platform.

| Engine | Backends | Extra GPU download | Notes |
|---|---|---|---|
| **whisper.cpp** (used) | CPU · Vulkan · CUDA · Metal | none for Vulkan/CPU | one codebase + one model format for every platform |
| faster-whisper (CTranslate2) | CPU · CUDA | ~1.3 GB (cuBLAS/cuDNN) on GPU | NVIDIA-only on GPU; comparable speed & accuracy |

faster-whisper is kept only as the automatic fallback.

## GPU backend: Vulkan by default

Vulkan reaches CUDA-level speed and identical accuracy on this hardware, with no extra download
— which is why it's the default. CUDA remains available for cards where it's faster.

Measured on an RTX 2080, `large-v3`, warm — a short dictation clip:

| Backend | Works on | Extra download | Speed | Accuracy (WER) |
|---|---|---|---|---|
| **Vulkan** (default) | NVIDIA / AMD / Intel | none — engine ~74 MB; loader ships with the driver | sub-second | 0.012 |
| **CUDA** (optional) | NVIDIA only | ~1.3 GB (cuBLAS + cuDNN) | sub-second (≈ Vulkan) | 0.012 |
| **CPU** (fallback) | any machine | none | several seconds (use a smaller model) | ≈ 0.012 |

## Why large-v3

`large-v3` (int8_float16, ~3 GB) won on this RTX 2080 for auto-detect accuracy across EN/UA/PL/RU.
It's selectable — `large-v3-turbo` is the lighter alternative, `medium` the low-VRAM fallback, and
`small` works on CPU-only machines. The model stays resident in VRAM between dictations.
