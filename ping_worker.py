"""Background ping worker.

Uses the system `ping` command via subprocess (no admin required on Windows).
Maintains a rolling window per server to compute avg, jitter, and loss %.
"""

from __future__ import annotations

import platform
import re
import socket
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Deque, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

import db

IS_WINDOWS = platform.system().lower() == "windows"

# Hide the console window that subprocess would otherwise pop up on Windows.
if IS_WINDOWS:
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _CREATE_NO_WINDOW = 0x08000000
else:
    _STARTUPINFO = None
    _CREATE_NO_WINDOW = 0


# Regex patterns to parse a single ping reply's RTT.
# Windows: "Reply from 8.8.8.8: bytes=32 time=12ms TTL=117"
# Windows (sub-ms): "time<1ms"
# Unix: "64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=12.3 ms"
_RTT_RE = re.compile(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)


@dataclass
class ServerStats:
    """Rolling stats for one target. Window defaults to 30 samples."""

    name: str
    host: str
    window: int = 30
    samples: Deque[Optional[float]] = field(default_factory=deque)
    last_rtt: Optional[float] = None

    def record(self, rtt_ms: Optional[float]) -> None:
        self.samples.append(rtt_ms)
        while len(self.samples) > self.window:
            self.samples.popleft()
        self.last_rtt = rtt_ms

    @property
    def received(self) -> list[float]:
        return [s for s in self.samples if s is not None]

    @property
    def avg_ms(self) -> Optional[float]:
        r = self.received
        return sum(r) / len(r) if r else None

    @property
    def min_ms(self) -> Optional[float]:
        r = self.received
        return min(r) if r else None

    @property
    def max_ms(self) -> Optional[float]:
        r = self.received
        return max(r) if r else None

    @property
    def jitter_ms(self) -> Optional[float]:
        """Mean absolute difference between consecutive successful samples."""
        r = self.received
        if len(r) < 2:
            return None
        diffs = [abs(r[i] - r[i - 1]) for i in range(1, len(r))]
        return sum(diffs) / len(diffs)

    @property
    def loss_pct(self) -> float:
        if not self.samples:
            return 0.0
        lost = sum(1 for s in self.samples if s is None)
        return 100.0 * lost / len(self.samples)

    @property
    def online(self) -> bool:
        """Considered online if at least one of the last 3 samples succeeded."""
        tail = list(self.samples)[-3:]
        return any(s is not None for s in tail)


def detect_default_gateway() -> Optional[str]:
    """Best-effort default gateway detection.

    Returns None if detection fails — caller should fall back gracefully.
    """
    try:
        if IS_WINDOWS:
            # `route print 0.0.0.0` lists default routes; parse the gateway col.
            out = subprocess.run(
                ["route", "print", "0.0.0.0"],
                capture_output=True,
                text=True,
                timeout=3,
                startupinfo=_STARTUPINFO,
                creationflags=_CREATE_NO_WINDOW,
            ).stdout
            for line in out.splitlines():
                parts = line.split()
                # Row looks like: 0.0.0.0  0.0.0.0  192.168.1.1  192.168.1.42  25
                if len(parts) >= 3 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                    gw = parts[2]
                    if _is_ipv4(gw) and gw != "0.0.0.0":
                        return gw
        else:
            # Linux/macOS: parse `ip route` / `route -n get default`
            try:
                out = subprocess.run(
                    ["ip", "route", "show", "default"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                ).stdout
                m = re.search(r"default\s+via\s+(\S+)", out)
                if m:
                    return m.group(1)
            except FileNotFoundError:
                pass
            try:
                out = subprocess.run(
                    ["route", "-n", "get", "default"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                ).stdout
                m = re.search(r"gateway:\s*(\S+)", out)
                if m:
                    return m.group(1)
            except FileNotFoundError:
                pass
    except Exception:
        return None
    return None


def _is_ipv4(s: str) -> bool:
    try:
        socket.inet_aton(s)
        return s.count(".") == 3
    except OSError:
        return False


def ping_once(host: str, timeout_ms: int = 1500) -> Optional[float]:
    """Send one ICMP echo. Returns RTT in ms, or None on timeout/failure."""
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    else:
        # -W is seconds on Linux (integer), seconds (float) on macOS.
        secs = max(1, int(round(timeout_ms / 1000)))
        cmd = ["ping", "-c", "1", "-W", str(secs), host]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=(timeout_ms / 1000) + 2,
            startupinfo=_STARTUPINFO,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    if result.returncode != 0:
        return None

    m = _RTT_RE.search(result.stdout)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


class PingWorker(QObject):
    """Runs in its own QThread. Emits `updated` after each round of pings."""

    updated = pyqtSignal(list)  # list[ServerStats]

    def __init__(
        self,
        servers: list[tuple[str, str]],
        interval_ms: int = 1000,
        window: int = 30,
        persist: bool = True,
    ) -> None:
        super().__init__()
        self.interval_ms = interval_ms
        self.stats = [ServerStats(name=n, host=h, window=window) for n, h in servers]
        self._stop = False
        self._persist = persist
        # Parallelism cap: more than ~8 simultaneous ping subprocesses gives
        # diminishing returns and bothers some firewalls. Most users have ≤3
        # servers anyway.
        self._max_parallel = min(max(1, len(self.stats)), 8)

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # called via QThread.started
        from PyQt6.QtCore import QThread as _QT

        import errors as _errors
        log = _errors.get_logger()

        # SQLite connections are per-thread, so open it here inside run().
        conn = None
        if self._persist:
            try:
                conn = db.connect()
            except Exception as e:
                log.exception("Ping worker: db.connect failed, persistence off: %s", e)
                conn = None

        # Parallel ping pool: each tick fires every host concurrently so a
        # slow gateway doesn't delay the round (and the per-tick UI update).
        pool = ThreadPoolExecutor(
            max_workers=self._max_parallel,
            thread_name_prefix="ping",
        )

        def _safe_ping(host: str) -> Optional[float]:
            try:
                return ping_once(host)
            except Exception as e:
                log.warning("ping_once(%s) raised: %s", host, e)
                return None

        try:
            while not self._stop:
                try:
                    ts = time.time()
                    # Submit all pings in one shot; collect results in order.
                    futures = [pool.submit(_safe_ping, s.host) for s in self.stats]
                    rows = []
                    for s, fut in zip(self.stats, futures):
                        if self._stop:
                            break
                        try:
                            # Cap at slightly over the per-ping timeout so a
                            # hung subprocess can't stall the loop forever.
                            rtt = fut.result(timeout=4.0)
                        except Exception as e:
                            log.warning("ping future for %s failed: %s", s.host, e)
                            rtt = None
                        s.record(rtt)
                        rows.append((ts, s.name, s.host, rtt))

                    if conn is not None and rows:
                        try:
                            db.insert_round(conn, rows)
                        except Exception as e:
                            log.warning("db.insert_round failed: %s", e)

                    # Emit a snapshot (the dataclasses are mutable but we
                    # only read them on the UI thread between emissions).
                    self.updated.emit(list(self.stats))
                except Exception as e:
                    # Last-ditch guard so a bug in one iteration doesn't
                    # kill the worker entirely. Log + keep going.
                    log.exception("Ping loop iteration failed: %s", e)

                # Sleep in small chunks so stop() is responsive.
                slept = 0
                while slept < self.interval_ms and not self._stop:
                    _QT.msleep(50)
                    slept += 50
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
