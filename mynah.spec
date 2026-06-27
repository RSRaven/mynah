# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Mynah — onedir, windowed (no console).

Build:  pyinstaller --noconfirm mynah.spec   ->   dist/Mynah/Mynah.exe

The base build is deliberately small: app + light deps only. It bundles **no** GPU runtime and
**no** model — those are fetched on first run by the component/model managers into
%LOCALAPPDATA%\\mynah\\engines and the shared Hugging Face cache. faster-whisper / CTranslate2
are gone (single-engine), so nothing CUDA-related is collected here.
"""

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

datas = [
    ("mynah/assets/*.png", "mynah/assets"),
    ("mynah/assets/*.ico", "mynah/assets"),
    ("mynah/manifest.json", "mynah"),       # pinned component manifest
]
binaries = []
hiddenimports = [
    # input + tray backends (PyInstaller's hooks usually catch these; pinned to be safe)
    "pynput.keyboard._win32", "pynput.mouse._win32",
    "pystray._win32",
]

# sounddevice ships the PortAudio DLL under _sounddevice_data/ — pull it in.
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
    icon="mynah/assets/mynah.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Mynah",
)
