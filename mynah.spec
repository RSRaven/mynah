# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Mynah — onedir, windowed (no console).

Build:
  Windows:  pyinstaller --noconfirm mynah.spec   ->   dist/Mynah/Mynah.exe
  macOS:    pyinstaller --noconfirm mynah.spec   ->   dist/Mynah.app  (menu-bar agent)

The base build is deliberately small: app + light deps only. It bundles **no** GPU runtime and
**no** model — those are fetched on first run by the component/model managers into the per-OS
runtime dir (``%LOCALAPPDATA%\\mynah\\engines`` / ``~/Library/Application Support/mynah``) and
the shared Hugging Face cache. faster-whisper / CTranslate2 are gone (single-engine), so nothing
CUDA-related is collected here.
"""

import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

IS_MACOS = sys.platform == "darwin"

datas = [
    ("mynah/assets/*.png", "mynah/assets"),
    ("mynah/assets/*.ico", "mynah/assets"),
    ("mynah/manifest.json", "mynah"),       # pinned component manifest
]
binaries = []

# Per-OS input + tray backends. pynput/pystray pick a backend module by platform at import
# time; PyInstaller's static analysis misses the one it isn't running on, so pin it explicitly.
if IS_MACOS:
    hiddenimports = [
        "pynput.keyboard._darwin", "pynput.mouse._darwin",
        "pystray._darwin",
    ]
    # pystray's + pynput's darwin backends are built on pyobjc (Quartz / AppKit / Foundation),
    # and our permissions helper uses ApplicationServices. Pull the whole pyobjc surface so no
    # framework binding is missing at runtime. HIServices is critical: pynput does
    # `import HIServices; HIServices.AXIsProcessTrusted()` to check the Accessibility grant, and
    # that symbol is a lazily-bound pyobjc constant whose metadata must be collected or the
    # hotkey listener dies with `KeyError: 'AXIsProcessTrusted'`.
    for pkg in ("objc", "Quartz", "AppKit", "Foundation", "ApplicationServices",
                "CoreFoundation", "HIServices", "PyObjCTools"):
        try:
            d, b, h = collect_all(pkg)
            datas += d
            binaries += b
            hiddenimports += h
        except Exception:
            pass
else:
    hiddenimports = [
        "pynput.keyboard._win32", "pynput.mouse._win32",
        "pystray._win32",
    ]

# sounddevice ships the PortAudio library under _sounddevice_data/ — pull it in.
datas += collect_data_files("sounddevice")
binaries += collect_dynamic_libs("sounddevice")

# huggingface_hub (model manager) + its HTTP stack — bundle code/data/certs.
for pkg in ("huggingface_hub", "requests", "certifi"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h


a = Analysis(
    ["run_mynah.py"],          # repo-root launcher (absolute import; PyInstaller-safe)
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Single-engine now: make sure no heavyweight ASR/ML stack sneaks in via a stray import.
        "faster_whisper", "ctranslate2", "torch", "tensorflow", "onnxruntime",
        "scipy", "matplotlib", "pandas",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Mynah",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                       # windowed — no console (logs go to mynah.log)
    icon=("mynah/assets/mynah.icns" if IS_MACOS else "mynah/assets/mynah.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Mynah",
)

if IS_MACOS:
    # Ship a proper .app: a menu-bar agent (LSUIElement → no Dock icon), with the privacy usage
    # string macOS requires before it will even show the Microphone prompt. Code-signing /
    # notarization is deferred (ship unsigned + documented Gatekeeper step); CI ad-hoc signs the
    # bundle so TCC grants stay stable across rebuilds (permissions bind to the code identity).
    app = BUNDLE(
        coll,
        name="Mynah.app",
        icon="mynah/assets/mynah.icns",
        bundle_identifier="com.mynah.mynah",
        info_plist={
            "CFBundleName": "Mynah",
            "CFBundleDisplayName": "Mynah",
            "CFBundleShortVersionString": "0.4.2",
            "CFBundleVersion": "0.4.2",
            "LSUIElement": True,            # menu-bar agent, no Dock icon / app switcher entry
            "LSMinimumSystemVersion": "12.0",
            "NSMicrophoneUsageDescription":
                "Mynah transcribes your speech locally while you hold the dictation hotkey. "
                "Audio never leaves your Mac.",
            "NSHighResolutionCapable": True,
        },
    )
