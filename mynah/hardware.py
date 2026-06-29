"""Hardware probe: classify the GPU and recommend an ASR backend.

Runs in pure Python at startup — deliberately **no** ``torch`` / ``ctranslate2`` imports
(CTranslate2's CUDA detection needs the very cuBLAS/cuDNN libs we may not have downloaded
yet). Detection order, cheapest/most-reliable first:

- **NVIDIA** -> NVML (``nvml.dll``, shipped by the GPU driver) for name + VRAM; the driver
  API ``nvcuda.dll`` is the presence signal.
- **AMD / Intel / NVIDIA via Vulkan** -> load ``vulkan-1.dll`` (the loader ships with GPU
  drivers) and enumerate physical devices for vendor IDs (0x1002 AMD, 0x8086 Intel,
  0x10DE NVIDIA), device names, and VRAM (largest DEVICE_LOCAL heap).
- **WMI** ``Win32_VideoController`` -> coarse fallback if the loaders are missing.
- else -> **CPU**.

The probe returns a :class:`GpuInfo`; :func:`recommend_backend` maps it to a backend the
config understands. **Vulkan is the default GPU backend for every vendor**;
``cuda`` is an optional NVIDIA-only upgrade offered at setup (see
:func:`cuda_is_optional`), and ``cpu`` is the floor. Onboarding shows the recommendation but
always lets the user override (the "pick what you have" path). The macOS (Metal/MLX) branch
is added in Phase 4.
"""

from __future__ import annotations

import ctypes
import struct
import sys
from dataclasses import dataclass, field

# PCI vendor IDs (same numbers Vulkan and PCI report).
VENDOR_NVIDIA = 0x10DE
VENDOR_AMD = 0x1002
VENDOR_INTEL = 0x8086
VENDOR_NAMES = {
    VENDOR_NVIDIA: "nvidia",
    VENDOR_AMD: "amd",
    VENDOR_INTEL: "intel",
    0x1010: "imgtec",
    0x13B5: "arm",
    0x5143: "qualcomm",
}

# Backends understood by the engine selector (see transcriber.build_transcriber).
BACKEND_VULKAN = "vulkan"  # whisper.cpp Vulkan — DEFAULT GPU backend (NVIDIA/AMD/Intel)
BACKEND_CUDA = "cuda"      # whisper.cpp CUDA — optional NVIDIA-only speed upgrade
BACKEND_METAL = "metal"    # whisper.cpp Metal — DEFAULT GPU backend on Apple Silicon
BACKEND_CPU = "cpu"


@dataclass
class GpuInfo:
    """Best-effort description of the primary GPU."""

    vendor: str | None = None          # "nvidia" | "amd" | "intel" | "unknown" | None
    name: str = ""
    vram_mb: int = 0                   # 0 = unknown
    source: str = "none"               # nvml | vulkan | wmi | none
    devices: list[dict] = field(default_factory=list)  # all detected, for multi-GPU UX

    @property
    def has_gpu(self) -> bool:
        return self.vendor in ("nvidia", "amd", "intel", "apple")


# --------------------------------------------------------------------------- NVIDIA / NVML

def _probe_nvml() -> GpuInfo | None:
    """NVIDIA via NVML (driver-shipped). Returns None if no NVIDIA GPU / NVML missing."""
    # nvcuda.dll is the cheap presence check (driver API, always there with an NVIDIA GPU).
    try:
        ctypes.WinDLL("nvcuda.dll")
    except OSError:
        return None

    for libname in ("nvml.dll",):
        try:
            nvml = ctypes.WinDLL(libname)
        except OSError:
            continue
        try:
            if nvml.nvmlInit_v2() != 0:
                continue
        except Exception:
            continue
        try:
            count = ctypes.c_uint(0)
            if nvml.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0 or count.value == 0:
                continue
            devices: list[dict] = []
            for i in range(count.value):
                handle = ctypes.c_void_p()
                if nvml.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(handle)) != 0:
                    continue
                name_buf = ctypes.create_string_buffer(96)
                try:
                    nvml.nvmlDeviceGetName(handle, name_buf, ctypes.c_uint(96))
                    name = name_buf.value.decode("utf-8", "replace")
                except Exception:
                    name = "NVIDIA GPU"
                vram_mb = 0
                # nvmlMemory_t = { total u64, free u64, used u64 }
                mem = (ctypes.c_ulonglong * 3)()
                try:
                    if nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem)) == 0:
                        vram_mb = int(mem[0] // (1024 * 1024))
                except Exception:
                    pass
                devices.append({"vendor": "nvidia", "name": name, "vram_mb": vram_mb})
            if not devices:
                continue
            top = max(devices, key=lambda d: d["vram_mb"])
            return GpuInfo(vendor="nvidia", name=top["name"], vram_mb=top["vram_mb"],
                           source="nvml", devices=devices)
        finally:
            try:
                nvml.nvmlShutdown()
            except Exception:
                pass
    return None


# --------------------------------------------------------------------------------- Vulkan

# VkPhysicalDeviceProperties: fixed-offset fields we care about (before the giant `limits`).
#   u32 apiVersion(0) u32 driverVersion(4) u32 vendorID(8) u32 deviceID(12)
#   u32 deviceType(16) char deviceName[256](20)
_VK_PROPS_BUF = 1024
_VK_DEVICE_LOCAL_BIT = 0x1


def _probe_vulkan() -> GpuInfo | None:
    """Enumerate Vulkan physical devices for vendor/name/VRAM. None if the loader/devices
    are unavailable."""
    try:
        vk = ctypes.CDLL("vulkan-1.dll")
    except OSError:
        return None

    VkResult = ctypes.c_int32
    vk.vkCreateInstance.restype = VkResult
    vk.vkEnumeratePhysicalDevices.restype = VkResult
    vk.vkGetPhysicalDeviceProperties.restype = None
    vk.vkGetPhysicalDeviceMemoryProperties.restype = None
    vk.vkDestroyInstance.restype = None

    # ctypes.Structure handles the native (C ABI) field alignment/padding for us; doing
    # this by hand with struct.pack is brittle (and `P` isn't valid in standard-size mode).
    class _VkApplicationInfo(ctypes.Structure):
        _fields_ = [
            ("sType", ctypes.c_uint32),                 # = 0 (APPLICATION_INFO)
            ("pNext", ctypes.c_void_p),
            ("pApplicationName", ctypes.c_char_p),
            ("applicationVersion", ctypes.c_uint32),
            ("pEngineName", ctypes.c_char_p),
            ("engineVersion", ctypes.c_uint32),
            ("apiVersion", ctypes.c_uint32),
        ]

    class _VkInstanceCreateInfo(ctypes.Structure):
        _fields_ = [
            ("sType", ctypes.c_uint32),                 # = 1 (INSTANCE_CREATE_INFO)
            ("pNext", ctypes.c_void_p),
            ("flags", ctypes.c_uint32),
            ("pApplicationInfo", ctypes.c_void_p),
            ("enabledLayerCount", ctypes.c_uint32),
            ("ppEnabledLayerNames", ctypes.c_void_p),
            ("enabledExtensionCount", ctypes.c_uint32),
            ("ppEnabledExtensionNames", ctypes.c_void_p),
        ]

    app = _VkApplicationInfo(sType=0, apiVersion=(1 << 22))  # VK_API_VERSION_1_0
    ci = _VkInstanceCreateInfo(
        sType=1, pApplicationInfo=ctypes.cast(ctypes.byref(app), ctypes.c_void_p))

    instance = ctypes.c_void_p()
    if vk.vkCreateInstance(ctypes.byref(ci), None, ctypes.byref(instance)) != 0 \
            or not instance.value:
        return None
    try:
        n = ctypes.c_uint32(0)
        if vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(n), None) != 0 or n.value == 0:
            return None
        handles = (ctypes.c_void_p * n.value)()
        if vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(n), handles) != 0:
            return None

        devices: list[dict] = []
        for h in handles[: n.value]:
            props = ctypes.create_string_buffer(_VK_PROPS_BUF)
            vk.vkGetPhysicalDeviceProperties(ctypes.c_void_p(h), props)
            raw = props.raw
            vendor_id = struct.unpack_from("=I", raw, 8)[0]
            dev_type = struct.unpack_from("=I", raw, 16)[0]
            name = raw[20:20 + 256].split(b"\x00", 1)[0].decode("utf-8", "replace")

            vram_mb = _vk_vram_mb(vk, h)
            devices.append({
                "vendor": VENDOR_NAMES.get(vendor_id, "unknown"),
                "vendor_id": vendor_id, "name": name,
                "vram_mb": vram_mb, "device_type": dev_type,  # 2 = DISCRETE_GPU
            })
        if not devices:
            return None
        # Prefer a discrete GPU, then the most VRAM.
        top = max(devices, key=lambda d: (d["device_type"] == 2, d["vram_mb"]))
        return GpuInfo(vendor=top["vendor"], name=top["name"], vram_mb=top["vram_mb"],
                       source="vulkan", devices=devices)
    finally:
        try:
            vk.vkDestroyInstance(instance, None)
        except Exception:
            pass


def _vk_vram_mb(vk, handle) -> int:
    """Largest DEVICE_LOCAL heap, in MB. 0 if it can't be read."""
    # VkPhysicalDeviceMemoryProperties:
    #   u32 memoryTypeCount; VkMemoryType[32] (8B each); u32 memoryHeapCount;
    #   <pad to 8>; VkMemoryHeap[16] { u64 size; u32 flags; <pad 4> }  (16B each)
    buf = ctypes.create_string_buffer(1024)
    try:
        vk.vkGetPhysicalDeviceMemoryProperties(ctypes.c_void_p(handle), buf)
    except Exception:
        return 0
    raw = buf.raw
    heap_count = struct.unpack_from("=I", raw, 4 + 32 * 8)[0]
    heaps_off = 264  # 4 + 256 + 4 + 4(pad) -> 8-aligned start of memoryHeaps
    best = 0
    for i in range(min(heap_count, 16)):
        off = heaps_off + i * 16
        size, flags = struct.unpack_from("=QI", raw, off)
        if flags & _VK_DEVICE_LOCAL_BIT:
            best = max(best, size)
    return int(best // (1024 * 1024))


# ------------------------------------------------------------------------------------ WMI

def _probe_wmi() -> GpuInfo | None:
    """Coarse fallback via WMI Win32_VideoController (vendor from the PnPDeviceID VEN_)."""
    import subprocess

    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | "
             "Select-Object Name,AdapterRAM,PNPDeviceID | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no flashing console
        ).stdout.strip()
    except Exception:
        return None
    if not out:
        return None
    import json

    try:
        data = json.loads(out)
    except Exception:
        return None
    if isinstance(data, dict):
        data = [data]

    devices: list[dict] = []
    for d in data:
        pnp = (d.get("PNPDeviceID") or "").upper()
        vendor = "unknown"
        if "VEN_10DE" in pnp:
            vendor = "nvidia"
        elif "VEN_1002" in pnp:
            vendor = "amd"
        elif "VEN_8086" in pnp:
            vendor = "intel"
        ram = d.get("AdapterRAM") or 0
        # WMI AdapterRAM is a signed 32-bit field — caps/wraps above 4 GB; treat as a hint.
        vram_mb = int(ram // (1024 * 1024)) if isinstance(ram, int) and ram > 0 else 0
        devices.append({"vendor": vendor, "name": d.get("Name") or "GPU", "vram_mb": vram_mb})
    if not devices:
        return None
    order = {"nvidia": 3, "amd": 2, "intel": 1}
    top = max(devices, key=lambda d: (order.get(d["vendor"], 0), d["vram_mb"]))
    return GpuInfo(vendor=top["vendor"], name=top["name"], vram_mb=top["vram_mb"],
                   source="wmi", devices=devices)


# ------------------------------------------------------------------------ macOS (Apple GPU)

def _probe_apple() -> GpuInfo | None:
    """Apple Silicon via ``sysctl``. The GPU is the integrated Apple GPU (Metal); memory is
    **unified** (CPU+GPU share it), so ``hw.memsize`` is the right ceiling for model sizing.
    Returns None on Intel Macs (no Apple GPU → fall through to CPU)."""
    import subprocess

    def _sysctl(key: str) -> str:
        try:
            return subprocess.run(["sysctl", "-n", key], capture_output=True, text=True,
                                  timeout=5).stdout.strip()
        except Exception:
            return ""

    # arm64 only — Apple Silicon. Intel Macs have no Metal-class Apple GPU we target.
    if _sysctl("hw.optional.arm64") != "1" and "Apple" not in _sysctl("machdep.cpu.brand_string"):
        return None
    chip = _sysctl("machdep.cpu.brand_string") or "Apple Silicon"
    vram_mb = 0
    memsize = _sysctl("hw.memsize")
    if memsize.isdigit():
        vram_mb = int(int(memsize) // (1024 * 1024))  # unified memory, in MB
    return GpuInfo(vendor="apple", name=chip, vram_mb=vram_mb, source="sysctl",
                   devices=[{"vendor": "apple", "name": chip, "vram_mb": vram_mb}])


# ----------------------------------------------------------------------------------- main

def probe_gpu() -> GpuInfo:
    """Detect the primary GPU. Never raises — returns a CPU GpuInfo if nothing is found."""
    if sys.platform == "darwin":
        try:
            info = _probe_apple()
        except Exception:
            info = None
        if info and info.has_gpu:
            return info
    elif sys.platform == "win32":
        for fn in (_probe_nvml, _probe_vulkan, _probe_wmi):
            try:
                info = fn()
            except Exception:
                info = None
            if info and info.has_gpu:
                return info
    # Linux GPU branches land in a later phase; CPU is the universal floor.
    return GpuInfo(vendor=None, name="CPU", vram_mb=0, source="none")


def recommend_model(vram_mb: int, has_gpu: bool) -> str:
    """Size the model to the hardware (auto mode). VRAM 0 = unknown -> conservative."""
    if not has_gpu:
        return "small"
    if vram_mb == 0:
        return "medium"      # GPU present but VRAM unknown: safe middle ground
    if vram_mb >= 7000:
        return "large-v3"    # ~3 GB at int8/f16 fits comfortably (RTX 2080 = 8 GB)
    if vram_mb >= 4500:
        return "large-v3-turbo"
    if vram_mb >= 3000:
        return "medium"
    return "small"


def cuda_is_optional(info: GpuInfo) -> bool:
    """True if the CUDA pack is worth *offering* as an optional upgrade — i.e. an NVIDIA
    GPU. Vulkan is the default everywhere; CUDA is a setup-time opt-in
    on NVIDIA for users who want the last few % of speed (and accept the ~1.3 GB download).
    Never on Apple (Metal is the only GPU backend there)."""
    return info.vendor == "nvidia"


def recommend_backend(info: GpuInfo | None = None) -> tuple[str, str, str]:
    """Return ``(backend, model, reason)`` for the detected hardware.

    backend ∈ {``vulkan``, ``metal``, ``cpu``}. **Vulkan is the default GPU backend for every
    PC vendor** (NVIDIA, AMD, Intel); **Metal is the default on Apple Silicon** (unified
    memory). One whisper.cpp build per backend, no cuBLAS/cuDNN download. No GPU -> CPU. On
    NVIDIA the optional CUDA pack can still be installed at setup (see
    :func:`cuda_is_optional`); the caller may also override the backend in config / onboarding.
    """
    info = info or probe_gpu()
    model = recommend_model(info.vram_mb, info.has_gpu)
    if info.vendor == "apple":
        mem = f"{info.vram_mb} MB unified" if info.vram_mb else "memory unknown"
        return (BACKEND_METAL, model,
                f"Apple Silicon ({info.name}, {mem}) -> whisper.cpp Metal")
    vram = f"{info.vram_mb} MB" if info.vram_mb else "VRAM unknown"
    if info.has_gpu:
        extra = "  (CUDA pack optional for max speed)" if cuda_is_optional(info) else ""
        return (BACKEND_VULKAN, model,
                f"{info.vendor.upper()} GPU ({info.name}, {vram}) -> whisper.cpp Vulkan{extra}")
    return (BACKEND_CPU, model, "No supported GPU detected -> CPU")


if __name__ == "__main__":  # `python -m mynah.hardware` — quick manual probe
    g = probe_gpu()
    backend, model, reason = recommend_backend(g)
    print(f"GPU      : {g.vendor or 'none'}  ({g.name})")
    print(f"VRAM     : {g.vram_mb or '?'} MB   [source: {g.source}]")
    if len(g.devices) > 1:
        for d in g.devices:
            print(f"  - {d.get('vendor'):8} {d.get('name')}  {d.get('vram_mb', 0)} MB")
    print(f"backend  : {backend}")
    print(f"model    : {model}")
    print(f"reason   : {reason}")
