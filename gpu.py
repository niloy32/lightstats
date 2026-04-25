"""GPU telemetry — NVIDIA via NVML, everything else via Windows PDH counters.

Strategy:
1. Try `pynvml` first. If present + a GPU initializes, report util% and VRAM.
2. Otherwise (AMD / Intel / NVML missing), fall back to Windows
   Performance Data Helper (PDH) reading the `\\GPU Engine(*)\\Utilization
   Percentage` counters — same source Task Manager uses. Aggregates the
   top-N engines per adapter so multi-engine GPUs don't overreport.

The PDH path is a self-contained `ctypes` wrapper; no extra deps required.
VRAM on AMD is not exposed consistently by PDH, so `vram_pct` is None there.
"""

from __future__ import annotations

import ctypes
import platform
import sys
import threading
from ctypes import POINTER, byref, wintypes
from dataclasses import dataclass
from typing import Optional

IS_WINDOWS = platform.system() == "Windows"


@dataclass
class GpuSample:
    util_pct: Optional[float] = None          # 0..100
    vram_used_gb: Optional[float] = None
    vram_total_gb: Optional[float] = None
    vram_pct: Optional[float] = None
    temp_c: Optional[float] = None
    name: Optional[str] = None                # best-effort adapter label
    source: str = "none"                      # "nvml" | "pdh" | "none"


# ------------------------------------------------------------------------------
# NVIDIA via pynvml
# ------------------------------------------------------------------------------

_nvml_ready = False
_nvml_error: Optional[str] = None


def _nvml_init() -> bool:
    global _nvml_ready, _nvml_error
    if _nvml_ready:
        return True
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        # Probe that at least one device exists — if not, treat as unavailable.
        if pynvml.nvmlDeviceGetCount() < 1:
            _nvml_error = "nvml: no devices"
            return False
        _nvml_ready = True
        return True
    except Exception as e:
        _nvml_error = f"nvml: {e!r}"
        return False


def _sample_nvml() -> Optional[GpuSample]:
    try:
        import pynvml  # type: ignore
        # Use device 0 (primary). Aggregating multiple GPUs is rare for the
        # typical desktop setup this app targets.
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(h).gpu
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        try:
            temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
        except Exception:
            temp = None
        try:
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode(errors="replace")
        except Exception:
            name = None
        used_gb = mem.used / (1024**3)
        total_gb = mem.total / (1024**3)
        return GpuSample(
            util_pct=float(util),
            vram_used_gb=used_gb,
            vram_total_gb=total_gb,
            vram_pct=(100.0 * used_gb / total_gb) if total_gb else None,
            temp_c=float(temp) if temp is not None else None,
            name=name,
            source="nvml",
        )
    except Exception:
        return None


# ------------------------------------------------------------------------------
# Windows PDH ctypes fallback (covers AMD / Intel / any GPU)
# ------------------------------------------------------------------------------

# PDH status codes we care about.
ERROR_SUCCESS = 0
PDH_MORE_DATA = 0x800007D2

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000  # don't clamp to 100, we'll do it ourselves

PDH_HQUERY = ctypes.c_void_p
PDH_HCOUNTER = ctypes.c_void_p


class _PDH_FMT_COUNTERVALUE_DOUBLE(ctypes.Structure):
    # Matches the native PDH_FMT_COUNTERVALUE union when requesting
    # PDH_FMT_DOUBLE. Padding keeps the 8-byte alignment of `doubleValue`.
    _fields_ = [
        ("CStatus", wintypes.DWORD),
        ("_pad", wintypes.DWORD),
        ("doubleValue", ctypes.c_double),
    ]


class _PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [
        ("szName", wintypes.LPWSTR),
        ("FmtValue", _PDH_FMT_COUNTERVALUE_DOUBLE),
    ]


class _PdhClient:
    """One long-lived PDH query that samples GPU engine utilization.

    PDH needs two samples to compute rate-based counters; callers should
    invoke `sample()` periodically (e.g. 1 Hz) and use the returned value.
    The very first call returns None (baseline).
    """

    GPU_ENGINE_COUNTER = r"\GPU Engine(*)\Utilization Percentage"

    def __init__(self) -> None:
        self._pdh = ctypes.WinDLL("pdh")
        self._query: Optional[int] = None
        self._counter: Optional[int] = None
        self._first = True
        self._lock = threading.Lock()

        self._setup_signatures()

        q = PDH_HQUERY()
        rc = self._pdh.PdhOpenQueryW(None, 0, byref(q))
        if rc != ERROR_SUCCESS:
            raise OSError(f"PdhOpenQueryW failed: 0x{rc:08x}")
        self._query = q.value

        c = PDH_HCOUNTER()
        rc = self._pdh.PdhAddCounterW(
            self._query, self.GPU_ENGINE_COUNTER, 0, byref(c)
        )
        if rc != ERROR_SUCCESS:
            # Counter path not available — tear down and raise so callers
            # can disable the PDH path.
            self._pdh.PdhCloseQuery(self._query)
            self._query = None
            raise OSError(f"PdhAddCounterW failed: 0x{rc:08x}")
        self._counter = c.value

    def _setup_signatures(self) -> None:
        # Make ctypes happy; prevents LP_c_void_p / int confusion on Win x64.
        self._pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, POINTER(PDH_HQUERY)]
        self._pdh.PdhOpenQueryW.restype = wintypes.DWORD
        self._pdh.PdhAddCounterW.argtypes = [
            PDH_HQUERY, wintypes.LPCWSTR, ctypes.c_size_t, POINTER(PDH_HCOUNTER),
        ]
        self._pdh.PdhAddCounterW.restype = wintypes.DWORD
        self._pdh.PdhCollectQueryData.argtypes = [PDH_HQUERY]
        self._pdh.PdhCollectQueryData.restype = wintypes.DWORD
        self._pdh.PdhGetFormattedCounterArrayW.argtypes = [
            PDH_HCOUNTER, wintypes.DWORD, POINTER(wintypes.DWORD),
            POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        self._pdh.PdhGetFormattedCounterArrayW.restype = wintypes.DWORD
        self._pdh.PdhCloseQuery.argtypes = [PDH_HQUERY]
        self._pdh.PdhCloseQuery.restype = wintypes.DWORD

    def sample(self) -> Optional[float]:
        """Return current aggregated GPU utilization %, or None if not ready."""
        with self._lock:
            if self._query is None or self._counter is None:
                return None

            rc = self._pdh.PdhCollectQueryData(self._query)
            if rc != ERROR_SUCCESS:
                return None
            if self._first:
                # First call only primes the baseline — rate counters need
                # two samples. Return None and wait for the next tick.
                self._first = False
                return None

            # Discover required buffer size.
            size = wintypes.DWORD(0)
            count = wintypes.DWORD(0)
            rc = self._pdh.PdhGetFormattedCounterArrayW(
                self._counter,
                PDH_FMT_DOUBLE,
                byref(size),
                byref(count),
                None,
            )
            if rc != PDH_MORE_DATA or size.value == 0:
                return None

            buf = (ctypes.c_byte * size.value)()
            rc = self._pdh.PdhGetFormattedCounterArrayW(
                self._counter,
                PDH_FMT_DOUBLE,
                byref(size),
                byref(count),
                buf,
            )
            if rc != ERROR_SUCCESS or count.value == 0:
                return None

            array_t = _PDH_FMT_COUNTERVALUE_ITEM_W * count.value
            items = ctypes.cast(buf, POINTER(array_t)).contents

            # Each "item" is one GPU engine instance (e.g. "pid_1234_luid_..._engtype_3D").
            # Summing naively overreports on multi-engine GPUs — instead, pick
            # the largest per-engine value as a proxy for overall GPU load.
            # This matches what Task Manager shows on the GPU graph tab.
            best = 0.0
            for i in range(count.value):
                v = items[i].FmtValue.doubleValue
                if v > best:
                    best = v
            # Clamp (PDH can return slightly >100 transiently).
            if best < 0:
                best = 0.0
            if best > 100:
                best = 100.0
            return float(best)

    def close(self) -> None:
        with self._lock:
            if self._query is not None:
                try:
                    self._pdh.PdhCloseQuery(self._query)
                except Exception:
                    pass
                self._query = None
                self._counter = None


_pdh_client: Optional[_PdhClient] = None
_pdh_attempted = False


def _pdh_sample() -> Optional[GpuSample]:
    global _pdh_client, _pdh_attempted
    if not IS_WINDOWS:
        return None
    if _pdh_client is None:
        if _pdh_attempted:
            return None
        _pdh_attempted = True
        try:
            _pdh_client = _PdhClient()
        except Exception:
            _pdh_client = None
            return None

    val = _pdh_client.sample()
    if val is None:
        return None
    return GpuSample(util_pct=val, source="pdh")


# ------------------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------------------

_chosen_source: Optional[str] = None  # "nvml" | "pdh" — locked in after first success


def sample() -> GpuSample:
    """Return current GPU sample. Falls back between NVML and PDH gracefully."""
    global _chosen_source

    if _chosen_source in (None, "nvml") and _nvml_init():
        s = _sample_nvml()
        if s is not None:
            _chosen_source = "nvml"
            return s

    if _chosen_source in (None, "pdh"):
        s = _pdh_sample()
        if s is not None:
            _chosen_source = "pdh"
            return s

    return GpuSample(source="none")


def available() -> bool:
    """Quick probe used by the settings dialog to show/hide GPU option."""
    s = sample()
    return s.source != "none" or _nvml_init() or IS_WINDOWS  # PDH path exists on Windows
