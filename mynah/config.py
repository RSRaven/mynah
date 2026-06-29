"""Configuration: in-code defaults + optional TOML override in the app-data dir.

Defaults are defined in-code below. The user file (``%APPDATA%\\mynah\\config.toml`` on
Windows) only needs to contain the keys it wants to override; it's deep-merged over the
defaults. We read TOML with the stdlib `tomllib` (3.11+) or `tomli` (3.10).
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

from .platform_layer import config_path

# Default push-to-talk / toggle hotkeys, per OS. On Windows the F9/F10 row keys are free and
# easy. On macOS the top-row F-keys default to media keys (need Fn), and common chords like
# ctrl+space collide with app shortcuts (e.g. VS Code suggestions / input-source switch) —
# so the mac defaults are conflict-free Space chords that don't need Fn.
if sys.platform == "darwin":
    _DEFAULT_PTT = "cmd+shift+space"     # hold to dictate
    _DEFAULT_TOGGLE = "ctrl+shift+space"  # tap on/off
else:
    _DEFAULT_PTT = "f9"
    _DEFAULT_TOGGLE = "f10"

DEFAULTS: dict = {
    "model": {
        "engine": "auto",          # auto | whispercpp (single engine; legacy faster-whisper/cuda/cpu accepted)
        "name": "large-v3",        # validated default on the RTX 2080; medium | large-v3-turbo | small | ...
        "compute_type": "int8_float16",  # legacy faster-whisper knob — ignored by whisper.cpp
        "device": "auto",          # auto | cuda | cpu (also picks GPU vs CPU for the LID gate)
        "vad": True,               # legacy faster-whisper knob — ignored by whisper.cpp
        "beam_size": 5,            # legacy faster-whisper knob — whisper.cpp server uses greedy
    },
    "language": {
        "mode": "auto",            # auto | fixed
        "fixed": "en",             # used when mode = fixed
        "multilingual": True,      # on: split mixed-language clips. off: one language per clip
    },
    "hardware": {
        # Which whisper.cpp engine pack to run. auto = best installed (default GPU = Vulkan for
        # every vendor; CPU floor). Override to pin vulkan | cuda | cpu.
        "backend": "auto",         # auto | vulkan | cuda | metal | cpu
    },
    "hotkey": {
        "push_to_talk": _DEFAULT_PTT,   # hold to record, release to transcribe (walkie-talkie)
        "toggle": _DEFAULT_TOGGLE,      # tap to start, tap again to stop (hands-free switch)
        "multilingual": "",     # optional: tap to toggle multilingual mode ("" = disabled)
        "wakeword": "",         # optional: tap to toggle wake-word listening mode ("" = disabled)
    },
    "insertion": {
        "method": "paste",         # paste | type
        "restore_clipboard": True,
    },
    "audio": {
        "sample_rate": 16000,
        "input_device": "default",
    },
    "ux": {
        "sound_cues": True,
        "cue_device": "default",   # output for start/stop cues; "default" or index/name
        "cue_start_file": "",      # optional .wav override (else per-OS default/synth)
        "cue_stop_file": "",       # optional .wav override (else per-OS default/synth)
        "min_clip_ms": 300,        # ignore accidental taps
    },
    "wakeword": {                  # optional hands-free "listening mode", off by default
        "enabled": False,
        "phrase": "hey mynah",       # say this to start dictating; a carrier word ("hey") is most reliable
        "sensitivity": 0.5,        # 0..1 — higher = easier to trigger (and looser phrase match)
        "silence_ms": 1500,        # "stop delay": end an utterance after this much trailing
                                   # silence. 1.5s tolerates natural thinking pauses; raise toward
                                   # 2500 if it still cuts you off, lower for a snappier finish.
        "max_seconds": 120,        # safety cap on one hands-free dictation; it normally ends on
                                   # silence (stop delay) well before this — not a fixed timer

    },
}

# Written verbatim by `--write-config`; kept in sync with DEFAULTS above.
DEFAULT_CONFIG_TEMPLATE = """\
# Mynah configuration. Delete a key to fall back to its built-in default.

[model]
engine = "auto"                 # auto | whispercpp — single engine; legacy faster-whisper/cuda/cpu accepted
name = "large-v3"               # whisper.cpp GGML model: large-v3 | large-v3-turbo | medium | small | ...
device = "auto"                 # auto | cuda | cpu (also selects GPU vs CPU for the multilingual LID gate)
# whisper.cpp is the only ASR engine. The GPU backend is set by *which build* you run — the
# default is **Vulkan** for every GPU (NVIDIA/AMD/Intel; no cuBLAS/cuDNN download). CUDA is an
# optional NVIDIA-only speed upgrade (a different whisper-server build); a CPU build covers
# machines with no usable GPU. First run downloads the right pack + model for you.
# Engine packs live in:   %LOCALAPPDATA%/mynah/engines/whispercpp-{vulkan,cuda,cpu}
# Models live in the shared Hugging Face cache (~/.cache/huggingface/hub), reused across apps.
# To use your own build/model instead, set MYNAH_WHISPERCPP_DIR / MYNAH_WHISPERCPP_MODEL,
# or the pins below:
# whispercpp_dir = ""
# whispercpp_model = ""
# compute_type / vad / beam_size are legacy faster-whisper knobs and are ignored by whisper.cpp.

[hardware]
# Which engine pack to run. auto = best installed (default GPU backend = Vulkan on PC for
# every vendor, Metal on Apple Silicon; CPU fallback). Pin vulkan | cuda | metal | cpu to
# override detection.
backend = "auto"                # auto | vulkan | cuda | metal | cpu

[language]
mode = "auto"                   # auto | fixed
fixed = "en"                    # used when mode = fixed
# Multilingual dictation: on (default) = detect mixed-language clips, split them, and
# transcribe each part in its own language (single-language clips stay fast; mixed clips
# take longer). Set false for one language per clip. See README.
multilingual = true

[hotkey]
# Two independent triggers (use one or both). Each is a single key or combo,
# e.g. "f9", "cmd+shift+space", "ctrl+alt+d". Set to "" to disable that one.
# (macOS defaults to Space chords; the F-key row there needs Fn and collides with media keys.)
push_to_talk = "{ptt}"   # hold to record, release to transcribe (walkie-talkie style)
toggle = "{toggle}"        # tap once to start, tap again to stop (hands-free switch)
multilingual = ""     # optional: tap to toggle multilingual mode on/off ("" = disabled)
wakeword = ""         # optional: tap to toggle wake-word listening mode on/off ("" = disabled)

[insertion]
method = "paste"                # paste | type
restore_clipboard = true

[audio]
sample_rate = 16000
input_device = "default"        # "default" or a device index/name (see --list-devices)

[ux]
sound_cues = true
cue_device = "default"          # output device for cues; "default" or index/name
# Cue sounds: Windows uses built-in notification .wav files; macOS/Linux fall back to
# synthesized tones. Point these at any .wav to override (recommended on non-Windows).
cue_start_file = ""
cue_stop_file = ""
min_clip_ms = 300               # ignore accidental taps shorter than this

[wakeword]
# Hands-free "listening mode", OFF by default. When enabled, say the wake phrase
# (then pause) to start dictation without touching a hotkey — a tiny, VAD-gated spotter
# listens on the CPU; the full model only runs on your dictation. Push-to-talk stays the
# primary trigger. The mic is read continuously *locally* while this is on (never uploaded).
enabled = false
phrase = "hey mynah"             # a carrier word ("hey …") is the most reliable; bare words mis-hear
sensitivity = 0.5              # 0..1 — higher triggers more easily (and matches more loosely)
silence_ms = 1500             # "stop delay": finish a phrase after this much trailing silence (≈500–3000; raise if cut off)
max_seconds = 120             # safety cap on one hands-free dictation (it ends on silence first)
"""


def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: str | Path | None = None) -> dict:
    """Return defaults deep-merged with the user's TOML file, if present."""
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path) if path else config_path()
    if p.is_file():
        with open(p, "rb") as f:
            user = tomllib.load(f)
        _deep_merge(cfg, user)
    return cfg


def _rendered_default_template() -> str:
    """The config template with the per-OS default hotkeys filled in (see DEFAULTS)."""
    return (DEFAULT_CONFIG_TEMPLATE
            .replace("{ptt}", _DEFAULT_PTT)
            .replace("{toggle}", _DEFAULT_TOGGLE))


def write_default_config(path: str | Path | None = None, force: bool = False) -> Path:
    """Write the commented default config; returns the path. Won't clobber unless forced."""
    p = Path(path) if path else config_path()
    if p.exists() and not force:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_rendered_default_template(), encoding="utf-8")
    return p


def _format_toml_value(value) -> str:
    if isinstance(value, bool):  # before int — bool is an int subclass
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):  # arrays of scalars (e.g. a multi-combo hotkey)
        return "[" + ", ".join(_format_toml_value(v) for v in value) + "]"
    raise TypeError(f"Can't persist {type(value).__name__} to TOML: {value!r}")


def update_config_values(updates: dict, path: str | Path | None = None) -> Path:
    """Persist a few scalar settings to the user's TOML, preserving its comments.

    `updates` is ``{section: {key: value}}`` for simple scalars only (str/bool/number)
    — used by the tray for model, language, and the sound toggle. Existing
    ``key = value`` lines are rewritten in place (keeping any inline comment); missing
    keys/sections are appended. If the file doesn't exist yet, the commented default
    is written first so the standard keys are already there to edit.
    """
    p = Path(path) if path else config_path()
    if not p.is_file():
        write_default_config(p)

    lines = p.read_text(encoding="utf-8").splitlines()
    pending = {(sec, key): val for sec, kv in updates.items() for key, val in kv.items()}
    section_re = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*$")

    current: str | None = None
    for i, line in enumerate(lines):
        m = section_re.match(line)
        if m:
            current = m.group("name").strip()
            continue
        if current is None:
            continue
        for (sec, key), val in list(pending.items()):
            if sec != current:
                continue
            km = re.match(rf"^(?P<indent>\s*){re.escape(key)}\s*=\s*(?P<rest>.*)$", line)
            if not km:
                continue
            rest = km.group("rest")
            hash_idx = rest.find("#")  # safe: our scalar values never contain '#'
            if hash_idx == -1:
                trailing = ""
            else:  # keep the inline comment (and the whitespace run before it)
                j = hash_idx
                while j > 0 and rest[j - 1] in " \t":
                    j -= 1
                trailing = rest[j:]
            lines[i] = f"{km.group('indent')}{key} = {_format_toml_value(val)}{trailing}"
            del pending[(sec, key)]

    # Anything not found in place: append (after its section header, or as a new section).
    for (sec, key), val in pending.items():
        entry = f"{key} = {_format_toml_value(val)}"
        header_idx = next(
            (i for i, ln in enumerate(lines)
             if (mm := section_re.match(ln)) and mm.group("name").strip() == sec),
            None,
        )
        if header_idx is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend([f"[{sec}]", entry])
        else:
            lines.insert(header_idx + 1, entry)

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
