---
title: Build the Windows app
description: Build the portable Mynah.exe and the installer yourself, step by step.
sidebar:
  order: 5
---

This builds the exact artifacts the [Releases](https://github.com/RSRaven/mynah/releases) page
ships: a **portable `Mynah.exe`** (PyInstaller onedir) and an optional **per-user installer**
(Inno Setup). The build is deliberately small — it bundles the app and light dependencies, but
**no GPU runtime and no model**. Those are fetched on first run into
`%LOCALAPPDATA%\mynah\engines` and the shared Hugging Face cache, so the download stays small.

## Prerequisites

- **Python 3.10+** (64-bit).
- **PyInstaller** — for the portable build.
- **[Inno Setup 6](https://jrsoftware.org/isdl.php)** — only if you want the `.exe` installer
  (provides the `iscc` compiler). Skip it if you only need the portable build.

```powershell
python -m venv .venv && .venv\Scripts\activate
pip install -e . pyinstaller
```

## 1. Portable build (PyInstaller)

```powershell
pyinstaller --noconfirm mynah.spec
```

Output: **`dist\Mynah\Mynah.exe`** — a onedir build (the `.exe` plus its DLLs/data in
`dist\Mynah\`). Run it in place, or zip the whole folder to make the portable distribution:

```powershell
Compress-Archive -Path dist\Mynah\* -DestinationPath dist\Mynah-0.4.0-portable.zip
```

What [`mynah.spec`](https://github.com/RSRaven/mynah/blob/master/mynah.spec) does:

- **Windowed** build (`console=False`) — no console window; logs go to `%APPDATA%\mynah\mynah.log`.
  Use `Mynah.exe --no-tray` for console output (see the [CLI page](/mynah/using-mynah/cli/)).
- Icon is `mynah\assets\mynah.ico`; tray PNGs and `manifest.json` (the pinned component manifest)
  are bundled as data.
- Pulls in the PortAudio DLL (`sounddevice`) and the model-manager HTTP stack
  (`huggingface_hub`, `requests`, `certifi`).
- **Excludes** the heavy ML stacks (`faster_whisper`, `ctranslate2`, `torch`, …) — Mynah is
  single-engine (whisper.cpp), so nothing CUDA-related is collected. This is what keeps the build
  small.

## 2. Installer (Inno Setup, optional)

With the portable build present in `dist\Mynah\`, compile the installer and pass the version:

```powershell
iscc /DMyAppVersion=0.4.0 installer.iss
```

Output: **`dist\Mynah-Setup-0.4.0.exe`**.

What [`installer.iss`](https://github.com/RSRaven/mynah/blob/master/installer.iss) produces:

- A **per-user** install (`PrivilegesRequired=lowest`) into
  `%LOCALAPPDATA%\Programs\Mynah` — no admin prompt.
- A Start-menu shortcut, an optional **desktop shortcut**, and an optional **"run at login"**
  (a per-user `HKCU\…\Run` value; the in-app Settings toggle writes the same value).
- A clean uninstaller: it runs `Mynah.exe --purge-runtime` (engine packs + config + logs + the
  autostart key), then `--purge-ui` (a per-model checklist for the shared model cache, nothing
  checked by default).

:::note[Match the version]
Pass the same version to `iscc` that you're shipping. `OutputBaseFilename` becomes
`Mynah-Setup-<version>.exe`, and the version shows in Add/Remove Programs.
:::

## What's downloaded at first run (not bundled)

The build contains no engine pack and no model. On first launch the app detects your hardware and
downloads:

- the **Vulkan engine pack** (~74 MB) into `%LOCALAPPDATA%\mynah\engines\`, and
- the **speech model** (e.g. `large-v3`) into the shared Hugging Face cache.

This is why the installer is small and why the same `Mynah.exe` "just works" after a fresh build.

## The Vulkan engine pack (you don't need this to build the app)

The prebuilt Vulkan `whisper-server` pack the app downloads is produced separately by CI from
[`scripts\build_wcpp_vulkan.bat`](https://github.com/RSRaven/mynah/blob/master/scripts/build_wcpp_vulkan.bat),
which needs **Visual Studio 2022 Build Tools + the Vulkan SDK**. Building `Mynah.exe` does **not**
require any of that — the app fetches the pack at runtime.

## Troubleshooting the build

- **`iscc` not found:** install Inno Setup 6 and add its folder to `PATH`, or call it by full
  path (e.g. `& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=0.4.0 installer.iss`).
- **A stray ML import bloats the build:** make sure you installed into a clean virtualenv —
  the spec excludes `torch`/`ctranslate2`/etc., but only what's importable is analyzed.
- **SmartScreen on the unsigned output:** expected for a self-built, unsigned exe — see
  [Install](/mynah/get-started/install/).
