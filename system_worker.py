"""System metrics worker (CPU, memory, network, GPU, disk, uptime, adapter).

Runs on its own QThread, polls every `interval_ms`, emits a `SystemSample`
on each tick, and (optionally) persists everything to the `metrics` table.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from typing import Optional

import psutil
from PyQt6.QtCore import QObject, QThread, pyqtSignal

import db
import gpu


# Canonical metric names written to the DB. Chart-window dropdown uses these.
METRIC_NAMES = [
    "cpu_pct",
    "mem_pct",
    "net_down_bps",      # bytes/sec aggregate
    "net_up_bps",
    "gpu_pct",
    "gpu_vram_pct",
    "disk_read_bps",
    "disk_write_bps",
]


@dataclass
class SystemSample:
    """Snapshot of every known metric. `None` means not available."""

    cpu_pct: Optional[float] = None
    mem_pct: Optional[float] = None
    mem_used_gb: Optional[float] = None
    mem_total_gb: Optional[float] = None

    net_down_bps: Optional[float] = None   # bytes/sec
    net_up_bps: Optional[float] = None
    net_adapter: Optional[str] = None      # best active interface
    net_link_mbps: Optional[float] = None  # nominal link speed

    gpu: gpu.GpuSample = field(default_factory=gpu.GpuSample)

    disk_read_bps: Optional[float] = None
    disk_write_bps: Optional[float] = None

    uptime_seconds: Optional[float] = None


# --- helpers -----------------------------------------------------------------


def _pick_active_interface() -> Optional[str]:
    """Pick the most plausible active, non-loopback interface.

    Priority: the interface that has the most bytes flowing + is up + has a
    non-link-local IPv4. Falls back to any up interface.
    """
    try:
        stats = psutil.net_if_stats()
        counters = psutil.net_io_counters(pernic=True)
        addrs = psutil.net_if_addrs()
    except Exception:
        return None

    best = None
    best_bytes = -1
    for name, st in stats.items():
        if not st.isup:
            continue
        if name.lower().startswith(("loopback", "lo")):
            continue
        has_ipv4 = any(
            a.family == socket.AF_INET and not a.address.startswith("169.254.")
            for a in addrs.get(name, [])
        )
        if not has_ipv4:
            continue
        c = counters.get(name)
        if not c:
            continue
        total = c.bytes_sent + c.bytes_recv
        if total > best_bytes:
            best_bytes = total
            best = (name, st)
    return best[0] if best else None


def _adapter_info(name: Optional[str]) -> tuple[Optional[str], Optional[float]]:
    """Return (display_name, link_speed_mbps) for the given interface."""
    if not name:
        return None, None
    try:
        st = psutil.net_if_stats().get(name)
    except Exception:
        return name, None
    if not st:
        return name, None
    speed = float(st.speed) if st.speed and st.speed > 0 else None
    return name, speed


# --- worker ------------------------------------------------------------------


class SystemWorker(QObject):
    updated = pyqtSignal(object)  # SystemSample

    def __init__(self, interval_ms: int = 1000, persist: bool = True) -> None:
        super().__init__()
        self.interval_ms = interval_ms
        self._stop = False
        self._persist = persist

        # Prime psutil's CPU counter so the first reading isn't 0.
        psutil.cpu_percent(interval=None)

        self._last_net: Optional[tuple[float, int, int]] = None  # (ts, recv, sent)
        self._last_disk: Optional[tuple[float, int, int]] = None
        # Cache for active-interface lookup. Each lookup makes 3 psutil calls
        # and walks every NIC; refreshing once every few seconds is plenty
        # for a label that just shows the user "you're on Wi-Fi."
        self._iface_cache: Optional[tuple[float, Optional[str], Optional[float]]] = None
        self._iface_ttl_s = 5.0

    def stop(self) -> None:
        self._stop = True

    def _poll(self) -> SystemSample:
        s = SystemSample()
        now = time.time()

        # CPU + memory
        try:
            s.cpu_pct = float(psutil.cpu_percent(interval=None))
        except Exception:
            pass
        try:
            vm = psutil.virtual_memory()
            s.mem_pct = float(vm.percent)
            s.mem_used_gb = (vm.total - vm.available) / (1024**3)
            s.mem_total_gb = vm.total / (1024**3)
        except Exception:
            pass

        # Network: aggregate all non-loopback interfaces for throughput;
        # pick a single "active" one for the adapter label.
        try:
            nio = psutil.net_io_counters(pernic=False)
            if self._last_net is not None:
                dt = now - self._last_net[0]
                if dt > 0:
                    s.net_down_bps = max(0.0, (nio.bytes_recv - self._last_net[1]) / dt)
                    s.net_up_bps = max(0.0, (nio.bytes_sent - self._last_net[2]) / dt)
            self._last_net = (now, nio.bytes_recv, nio.bytes_sent)
        except Exception:
            pass

        # Cache the adapter lookup — psutil.net_if_* are surprisingly slow
        # on Windows when many virtual adapters are present.
        if (
            self._iface_cache is None
            or now - self._iface_cache[0] > self._iface_ttl_s
        ):
            active = _pick_active_interface()
            adapter_name, adapter_speed = _adapter_info(active)
            self._iface_cache = (now, adapter_name, adapter_speed)
        _, s.net_adapter, s.net_link_mbps = self._iface_cache

        # Disk I/O
        try:
            dio = psutil.disk_io_counters()
            if dio is not None:
                if self._last_disk is not None:
                    dt = now - self._last_disk[0]
                    if dt > 0:
                        s.disk_read_bps = max(0.0, (dio.read_bytes - self._last_disk[1]) / dt)
                        s.disk_write_bps = max(0.0, (dio.write_bytes - self._last_disk[2]) / dt)
                self._last_disk = (now, dio.read_bytes, dio.write_bytes)
        except Exception:
            pass

        # GPU (best-effort — may be GpuSample with all Nones)
        try:
            s.gpu = gpu.sample()
        except Exception:
            s.gpu = gpu.GpuSample()

        # Uptime
        try:
            s.uptime_seconds = now - psutil.boot_time()
        except Exception:
            pass

        return s

    def _rows_for_db(self, s: SystemSample, ts: float) -> list[tuple[float, str, Optional[float]]]:
        """Flatten a sample into metric rows. Only emits values we have."""
        rows: list[tuple[float, str, Optional[float]]] = []
        pairs: list[tuple[str, Optional[float]]] = [
            ("cpu_pct", s.cpu_pct),
            ("mem_pct", s.mem_pct),
            ("net_down_bps", s.net_down_bps),
            ("net_up_bps", s.net_up_bps),
            ("gpu_pct", s.gpu.util_pct),
            ("gpu_vram_pct", s.gpu.vram_pct),
            ("disk_read_bps", s.disk_read_bps),
            ("disk_write_bps", s.disk_write_bps),
        ]
        for name, val in pairs:
            if val is None:
                continue
            rows.append((ts, name, float(val)))
        return rows

    def run(self) -> None:
        import errors as _errors
        log = _errors.get_logger()

        conn = None
        if self._persist:
            try:
                conn = db.connect()
            except Exception as e:
                log.exception("System worker: db.connect failed: %s", e)
                conn = None

        try:
            while not self._stop:
                try:
                    sample = self._poll()
                    if conn is not None:
                        rows = self._rows_for_db(sample, time.time())
                        if rows:
                            try:
                                db.insert_metrics(conn, rows)
                            except Exception as e:
                                log.warning("insert_metrics failed: %s", e)
                    self.updated.emit(sample)
                except Exception as e:
                    log.exception("System poll iteration failed: %s", e)

                slept = 0
                while slept < self.interval_ms and not self._stop:
                    QThread.msleep(50)
                    slept += 50
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
