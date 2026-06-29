"""whisper.cpp backend via a resident whisper-server (prebuilt CUDA binary).

Chosen as the single cross-vendor engine after the bake-off (whisper.cpp CUDA
matched faster-whisper on latency *and* accuracy). The model stays resident in VRAM by
running ``whisper-server`` as a child process; each utterance is POSTed to ``/inference``.

Notes:
- **Greedy decoding** (no ``-bs``): beam search in server mode is pathologically slow
  with flash-attn (a cold beam call took ~33 s in testing; greedy warm calls are ~0.7 s).
- This is the integration used to validate whisper.cpp live; the proper component
  manager (binary/model download, GGML catalog) replaces the hard-coded paths later.
"""
from __future__ import annotations

import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

import numpy as np

from .base import Transcriber

# whisper.cpp emits these for non-speech; treat as empty. Restricted to known tags so we
# never blank a real utterance that happens to be fully parenthesised.
_NOISE_RE = re.compile(
    r"^\s*[\[(]\s*(blank_?audio|silence|music|inaudible|applause|laughter|sound|noise)\s*[\])]\s*$",
    re.I,
)


def _wav_bytes(audio: np.ndarray, samplerate: int) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _multipart(fields: dict, wav: bytes) -> tuple[bytes, str]:
    boundary = "----mynah" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        out += (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode()
    out += (f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="a.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n").encode()
    out += wav + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _clean(text: str) -> str:
    keep = [ln for ln in (l.strip() for l in text.splitlines())
            if ln and not _NOISE_RE.match(ln)]
    return " ".join(keep).strip()


class WhisperCppServer(Transcriber):
    """whisper.cpp kept resident behind a local whisper-server child process.

    Backend-agnostic: drives **any** whisper-server build — CUDA (NVIDIA), **Vulkan**
    (AMD/Intel, and validated at CUDA parity on the RTX 2080), or CPU. The
    acceleration is whatever ``binary_dir`` was built with; :pyattr:`description` reports it
    from the ggml backend DLL present.
    """

    def __init__(self, model_path, binary_dir, host: str = "127.0.0.1",
                 port: int = 0, samplerate: int = 16000) -> None:  # port 0 => pick free
        self.model_path = Path(model_path)
        self.binary_dir = Path(binary_dir)
        self.host = host
        self.port = int(port)
        self.samplerate = samplerate
        self._proc: subprocess.Popen | None = None
        self._base = f"http://{host}:{self.port}"

    def load(self) -> None:
        exe_name = "whisper-server.exe" if os.name == "nt" else "whisper-server"
        exe = self.binary_dir / exe_name
        if not exe.exists():
            raise FileNotFoundError(f"{exe_name} not found in {self.binary_dir}")
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"GGML model not found: {self.model_path} — download "
                f"{self.model_path.name} (e.g. from huggingface.co/ggerganov/whisper.cpp) "
                f"into {self.model_path.parent}, or pick an installed model.")
        if not self.port:  # grab a free port so model swaps never collide
            with socket.socket() as s:
                s.bind((self.host, 0))
                self.port = s.getsockname()[1]
            self._base = f"http://{self.host}:{self.port}"
        cmd = [str(exe), "-m", str(self.model_path), "-l", "auto",
               "--host", self.host, "--port", str(self.port)]
        self._proc = subprocess.Popen(
            cmd, cwd=str(self.binary_dir),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # CREATE_NO_WINDOW: don't pop a console window for the server child (the packaged
            # app is windowed). 0 on non-Windows.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._wait_ready()
        # Warm the CUDA graph so the first real utterance isn't the slow one.
        try:
            self.transcribe(np.zeros(self.samplerate, dtype=np.float32))
        except Exception:
            pass

    def _wait_ready(self, timeout: float = 120.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError(
                    f"whisper-server exited early (code {self._proc.returncode})")
            try:
                urllib.request.urlopen(self._base + "/", timeout=2)
                return
            except urllib.error.HTTPError:
                return  # any HTTP response means it's listening
            except Exception:
                time.sleep(0.4)
        raise TimeoutError("whisper-server did not become ready")

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> str:
        if audio is None or len(audio) == 0:
            return ""
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        wav = _wav_bytes(audio, self.samplerate)
        fields = {"temperature": "0", "response_format": "json",
                  "language": language or "auto"}
        body, ctype = _multipart(fields, wav)
        req = urllib.request.Request(self._base + "/inference", data=body,
                                     headers={"Content-Type": ctype})
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", "replace")
        try:
            return _clean(json.loads(raw).get("text", ""))
        except Exception:
            return _clean(raw)

    def unload(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=10)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None

    def _accel(self) -> str:
        """Acceleration of the build in binary_dir, from its ggml backend shared lib.

        Each whisper.cpp build ships its GPU backend as a separate ggml-<name> library
        (``.dll`` on Windows, ``.dylib`` on macOS, ``.so`` on Linux). Detect by stem so this
        works across CUDA / Vulkan / Metal / CPU regardless of platform."""
        def _has(stem: str) -> bool:
            for ext in (".dll", ".dylib", ".so"):
                if (self.binary_dir / f"{stem}{ext}").is_file():
                    return True
            # macOS dylibs may be versioned (libggml-metal.dylib); glob to be safe.
            return bool(list(self.binary_dir.glob(f"{stem}*")) or
                        list(self.binary_dir.glob(f"lib{stem}*")))

        if _has("ggml-cuda"):
            return "CUDA"
        if _has("ggml-vulkan"):
            return "Vulkan"
        if _has("ggml-metal"):
            return "Metal"
        return "CPU"

    @property
    def description(self) -> str:
        state = "running" if self._proc and self._proc.poll() is None else "stopped"
        return (f"whisper.cpp {self._accel()} server :{self.port} "
                f"[{self.model_path.name}] ({state})")
