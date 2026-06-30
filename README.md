# Mynah

**Local, GPU-accelerated push-to-talk voice typing for Windows and macOS** — like SuperWhisper /
MacWhisper, but **free, open-source, and fully offline**. Hold a hotkey (or say a wake word),
talk, and the transcribed text is inserted at your cursor — in the terminal, your editor, the
browser, anywhere. No cloud, no account, no per-word cost. Audio never leaves your machine.

- **Activation:** a global push-to-talk hotkey, a tap-to-toggle hotkey, or an optional
  hands-free **wake word** (say *"hey mynah"*).
- **Accurate & multilingual:** Whisper `large-v3` by default, auto-detects the language, and
  can split clips that mix languages.
- **Fast:** the model stays resident on the GPU, so a short utterance is transcribed in well
  under a second.
- **Private:** 100% on-device. No telemetry.
- **Tray app:** runs in the system tray; everything is configurable in a Settings window.

---

## Install (Windows)

Grab the latest build from the [**Releases**](https://github.com/RSRaven/mynah/releases)
page:

- **`Mynah-Setup-X.Y.Z.exe`** — installer (per-user, no admin prompt). Creates Start-menu
  and desktop shortcuts and an optional "run at login".
- **`Mynah-X.Y.Z-portable.zip`** — unzip and run `Mynah.exe`, no install.

> The app is unsigned, so Windows SmartScreen may warn on first run — click **More info →
> Run anyway**.

**First run** opens a short setup screen: Mynah detects your hardware, then downloads the
speech model with a progress bar. The GPU engine (Vulkan + a CPU fallback) ships inside the
app, so only the model is fetched. After that it lives in the tray and starts quietly. The
optional NVIDIA CUDA upgrade is the one engine pulled on demand, if you choose it.

## Install (macOS — Apple Silicon)

Grab **`Mynah-X.Y.Z-macos-arm64.dmg`** from the
[**Releases**](https://github.com/RSRaven/mynah/releases) page, double-click it, and **drag
`Mynah` into Applications**. Apple Silicon (M1/M2/M3/…) only for now. (A `.zip` of the same app
is also published — it just unzips in place, so you move `Mynah.app` to `/Applications` yourself.)

> The app is **unsigned** (notarization is deferred), so Gatekeeper blocks it on first launch.
> Either **right-click the app → Open** (then confirm once), or run:
> ```bash
> xattr -dr com.apple.quarantine /Applications/Mynah.app
> ```

Mynah is a **menu-bar** app (no Dock icon). The **Metal** engine ships inside the `.app`, so on
first run it downloads only the speech model, then sits in the menu bar. macOS will ask for
three permissions the core loop needs — grant each in **System Settings → Privacy & Security**:

- **Microphone** — to hear you (prompts automatically on first capture).
- **Input Monitoring** — to detect the push-to-talk hotkey.
- **Accessibility** — to paste the transcribed text (Cmd+V).

Without these the app *silently* does nothing. Run `mynah --permissions` (from a source
install) to print the current grant status and the exact panes to open.

### Run from source (any OS with Python 3.10+)

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows; use source .venv/bin/activate elsewhere
pip install -e .
mynah
```

You also need a `whisper.cpp` build (whisper-server + the whisper shared lib) and a GGML model.
On a normal install the app downloads these for you; to point at your own, set
`MYNAH_WHISPERCPP_DIR` (the build dir) and `MYNAH_WHISPERCPP_MODEL` (a `ggml-*.bin`).

On **macOS** the tray/menu-bar + hotkey backends need pyobjc, and the Settings window needs
Tk:

```bash
brew install python-tk@3.12
pip install -e . pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ApplicationServices
```

### Build the macOS app locally

Produces the same `Mynah.app` the Releases page ships. Needs Python 3.12, CMake, and the Xcode
command-line tools (for the Metal whisper.cpp build).

```bash
brew install cmake python@3.12 python-tk@3.12
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e . pyinstaller pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ApplicationServices

# 1) Build + pack the Metal engine pack (Mynah-hosted; no upstream Metal asset exists)
bash scripts/build_wcpp_metal.sh
python scripts/pack_metal.py \
  --bin scripts/_artifacts/wcpp-src/build-metal/bin \
  --out dist/whispercpp-metal-arm64.zip

# 2) Build the .app and ad-hoc sign it (CI ships this — unsigned distribution)
pyinstaller --noconfirm mynah.spec        # -> dist/Mynah.app
codesign -s - --deep --force dist/Mynah.app
```

`Mynah.app` still fetches the engine pack + model on first run, so the build stays small. It runs
as a **menu-bar agent** (`LSUIElement`, no Dock icon); grant Microphone / Input Monitoring /
Accessibility on first run (see the [macOS install steps](#install-macos--apple-silicon) above).

> **Iterating on the app? Use a stable self-signed certificate.** macOS ties privacy grants
> (Mic / Input Monitoring / Accessibility) to the app's **code identity**. Ad-hoc signing
> (`codesign -s -`) produces a *new* identity on every build, so each rebuild forces you to
> re-grant. Sign with a stable self-signed cert instead and you grant **once**:
>
> ```bash
> # one-time: create a self-signed code-signing cert and trust it
> #   Keychain Access → Certificate Assistant → Create a Certificate…
> #     Name: "Mynah Dev Signing"  ·  Identity Type: Self Signed Root
> #     Certificate Type: Code Signing  ·  (then set it to "Always Trust" for code signing)
> # after each build, sign with it (same identity every time):
> codesign -s "Mynah Dev Signing" --deep --force dist/Mynah.app
> ```
>
> This is a **local dev convenience only** — it is not committed and CI does not use it. The
> published release is ad-hoc signed (unsigned distribution), so end users grant once per
> install (their copy is never rebuilt, so the grant sticks).

### Build the Windows app locally

Produces the same portable build (and installer) the Releases page ships. Needs Python 3.10+
(and, for the installer, [Inno Setup 6](https://jrsoftware.org/isdl.php)).

```powershell
pip install -e . pyinstaller
pyinstaller mynah.spec                      # -> dist\Mynah\Mynah.exe (portable build)
iscc /DMyAppVersion=0.4.0 installer.iss       # optional -> Mynah-Setup-0.4.0.exe
```

`Mynah.exe` still fetches the engine pack + model on first run, so the build stays small.
(The Vulkan engine pack itself is built by CI from `scripts\build_wcpp_vulkan.bat`, which needs
Visual Studio 2022 Build Tools + the Vulkan SDK — you don't need that just to build the app.)

---

## Using it

After setup the model loads and stays resident. Then:

Default hotkeys differ by OS (Windows uses the free F-key row; macOS uses Space chords because
the F-row there needs Fn and common chords collide with app shortcuts). Change them anytime in
Settings.

| Action | Windows | macOS |
|---|---|---|
| **Push-to-talk** | **Hold `F9`** | **Hold `Cmd+Shift+Space`** |
| **Toggle** (tap on/off) | **Tap `F10`** | **Tap `Ctrl+Shift+Space`** |

| Action | How |
|---|---|
| **Push-to-talk** | Hold the key, speak, release — text is pasted at the cursor. |
| **Toggle** | Tap to start, tap again to stop (hands-free, no holding). |
| **Wake word** | Turn on *Listening mode* (below), say **"hey mynah"**, pause, then dictate. |
| **Settings / model / language / hotkeys** | Left-click the tray icon (or right-click → **Settings…**). |
| **Quit** | Right-click the tray icon → **Quit**. |

The tray icon colour reflects state: **blue** idle · **red** recording · **amber**
transcribing · **purple** loading.

### Wake-word "listening mode" (optional)

Hands-free dictation without touching a key. Enable it in **Settings → Listening mode (wake
word)**, on the CLI with `--wakeword`, or with `enabled = true` under `[wakeword]`. Then **say
the phrase, pause, and dictate**:

- Default phrase is **"hey mynah"**. A carrier word ("hey …") is recognised most reliably; a
  bare word is mis-heard more often. Change it in Settings or with `--wake-phrase "…"`.
- **Sensitivity** controls how easily it triggers (it also self-calibrates to your mic).
  **Stop delay** is how long a pause ends a phrase — raise it if it cuts you off.
- While it's recording your dictation, **your push-to-talk / toggle hotkey stops it early** and
  types what you said.
- Push-to-talk stays the primary trigger; the wake word is an add-on. The mic is read
  continuously **on your machine only** while it's on.

It never runs the full model just to listen: a tiny model gates on detected speech, and only
your actual dictation runs `large-v3`.

### Multilingual dictation (on by default)

A single Whisper pass commits to one language per clip. With **Multilingual** on, a cheap
language check runs first; single-language clips take the fast path, and only clips that
actually mix languages are split and transcribed **each part in its own language**. Toggle it
in Settings, with `--no-multilingual`, or `multilingual = false` under `[language]`.

---

## Command-line reference

`mynah` runs the tray app. Flags override the config file for that run.

```
mynah                          # start the tray app
mynah --no-tray                # console-only (no tray), e.g. over SSH
mynah --probe                  # detect GPU + print the recommended backend/model, then exit
mynah --list-devices           # list microphones, then exit
mynah --write-config [--force] # write a commented config.toml to %APPDATA%\mynah
```

| Flag | Meaning |
|---|---|
| `-m`, `--model NAME` | Model: `large-v3` (default), `large-v3-turbo`, `medium`, `small`, … |
| `-l`, `--language CODE` | Pin a language (e.g. `en`, `uk`, `pl`, `ru`); `auto` to auto-detect (default). |
| `--backend {auto,vulkan,cuda,cpu}` | Engine pack to run. `auto` = best installed (Vulkan on any GPU, else CPU). |
| `--device {auto,cuda,cpu}` | Compute device for the language-ID gate. |
| `--multilingual` / `--no-multilingual` | Split mixed-language clips (default: on). |
| `--wakeword` / `--no-wakeword` | Hands-free wake-word listening mode (default: off). |
| `--wake-phrase "…"` | Set the wake phrase (e.g. `"hey mynah"`). |
| `--hotkey "f9"` | Push-to-talk key/combo (comma-separate for several, e.g. `"f9,ctrl+space"`). |
| `--method {paste,type}` | Insert by clipboard paste (default) or simulated typing. |
| `--no-sound` | Disable start/stop sound cues. |
| `--config PATH` | Use a specific config file. |
| `--version` | Print the version. |

---

## Configuration

Settings live in `%APPDATA%\mynah\config.toml` (created on first save); see
[`config.example.toml`](./config.example.toml) for every option with comments. Command-line
flags override the file; the Settings window and tray write changes back to it. Key sections:

- `[model]` — `name` (the model), `device`.
- `[hardware]` — `backend` = `auto | vulkan | cuda | cpu`.
- `[language]` — `mode` (`auto`/`fixed`), `fixed`, `multilingual`.
- `[hotkey]` — `push_to_talk`, `toggle`, optional `wakeword` toggle.
- `[insertion]` — `method` (`paste`/`type`), `restore_clipboard`.
- `[audio]` — `sample_rate`, `input_device`.
- `[ux]` — `sound_cues`, cue device/files, `min_clip_ms`.
- `[wakeword]` — `enabled`, `phrase`, `sensitivity`, `silence_ms` (stop delay), `max_seconds`.

Engine packs live in `%LOCALAPPDATA%\mynah\engines\`; models live in the shared Hugging Face
cache (`~/.cache/huggingface/hub`), so they're reused across apps. Logs go to
`%APPDATA%\mynah\mynah.log`.

---

## How it works

```
[hotkey / wake word] → record mic → whisper.cpp (resident on GPU) → insert text at cursor
```

Mynah records 16 kHz mono audio while the hotkey is held (or after the wake word), runs it
through a resident **whisper.cpp** model, and pastes the result at your cursor. The model stays
loaded between dictations for low latency. The same `whisper.dll` also powers the in-process
language detector and the voice-activity splitter used for multilingual mode and the wake word
— there's no second runtime.

### What's used

- **Engine:** `whisper.cpp` (one engine for every platform; GGML model format).
- **GPU backend:** **Vulkan** by default on any GPU (NVIDIA / AMD / Intel). **CUDA** is an
  optional NVIDIA-only speed pack; **CPU** is the universal fallback. On Apple Silicon,
  Metal/MLX (planned).
- **Model:** `large-v3` by default (selectable). Stays resident in VRAM.
- **Insertion:** clipboard paste by default (restores your previous clipboard), or simulated
  typing.

### Comparison

**ASR engine** — Mynah ships a single engine; the alternative was measured at parity:

| Engine | Backends | Extra GPU download | Notes |
|---|---|---|---|
| **whisper.cpp** (used) | CPU · Vulkan · CUDA · Metal | none for Vulkan/CPU | one codebase + one model format for every platform |
| faster-whisper (CTranslate2) | CPU · CUDA | ~1.3 GB (cuBLAS/cuDNN) on GPU | NVIDIA-only on GPU; comparable speed & accuracy |

**GPU backend** (RTX 2080, `large-v3`, warm — short dictation clip):

| Backend | Works on | Extra download | Speed | Accuracy (WER) |
|---|---|---|---|---|
| **Vulkan** (default, PC) | NVIDIA / AMD / Intel | none — engine ~74 MB; loader ships with the driver | sub-second | 0.012 |
| **Metal** (default, Mac) | Apple Silicon | none — engine ~4 MB; uses the OS Metal stack | sub-second | 0.012 |
| **CUDA** (optional) | NVIDIA only | ~1.3 GB (cuBLAS + cuDNN) | sub-second (≈ Vulkan) | 0.012 |
| **CPU** (fallback) | any machine | none | several seconds (use a smaller model) | ≈ 0.012 |

Vulkan reaches CUDA-level speed and identical accuracy on this hardware, with no extra
download — which is why it's the default. CUDA remains available for cards where it's faster.

---

## Troubleshooting

- **Wrong microphone:** `mynah --list-devices`, then set `input_device` in `[audio]`.
- **Hotkey does nothing:** another app may grab it — change it in Settings or with `--hotkey`.
  For push-to-talk, hold the key the whole time you speak.
- **Wake word too eager / not triggering:** adjust **Sensitivity**; raise **Stop delay** if it
  cuts you off mid-phrase.
- **No GPU / wrong backend:** **Settings → Backend** overrides detection (Auto / Vulkan /
  NVIDIA CUDA / CPU on PC; Auto / Metal / CPU on Mac). CPU always works as a fallback (pick a
  smaller model like `small`).
- **Paste doesn't land in some terminals:** a few use Ctrl+Shift+V — set `method = "type"` in
  `[insertion]` to simulate keystrokes instead.
- **First transcription is slow (~2 s), later ones ~1 s:** normal GPU warm-up; the model stays
  resident afterwards.
- **(macOS) Nothing happens when you hold the hotkey or it won't paste:** grant **Input
  Monitoring** + **Accessibility** in System Settings → Privacy & Security (and **Microphone**
  for capture). `mynah --permissions` prints what's missing. After a rebuild, re-grant if the
  app's signature changed.
- **(macOS) "Mynah.app is damaged / can't be opened":** it's just unsigned — right-click →
  **Open**, or `xattr -dr com.apple.quarantine /Applications/Mynah.app`.

---

## Privacy & License

Mynah is **local-only** — audio is processed on your device and never uploaded; there is no
telemetry and no account. Free and open-source under the **[MIT License](./LICENSE)**.
