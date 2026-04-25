"""Chart window — plot any tracked metric over a selectable time range.

Modes:
- "Ping" (default) — one curve per server, Y = RTT ms, gaps on loss.
- "Network down / up" — bytes/sec, auto-scaled axis label.
- "CPU %", "Memory %", "GPU %", "GPU VRAM %" — single curve, 0–100.
- "Disk read / write" — bytes/sec.

Auto-refresh via QTimer. The window owns its own SQLite connection because
sqlite3 connections are per-thread; it's closed in `closeEvent`.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import db
from icon_loader import app_icon


# Time ranges in seconds. None = "all history".
RANGES: list[tuple[str, Optional[int]]] = [
    ("5 min", 5 * 60),
    ("15 min", 15 * 60),
    ("1 hr", 60 * 60),
    ("6 hr", 6 * 60 * 60),
    ("24 hr", 24 * 60 * 60),
    ("All", None),
]

# Distinct, colourblind-friendly palette cycled per line.
PALETTE = [
    "#4cc9f0",  # cyan
    "#f0c864",  # amber
    "#78dc8c",  # green
    "#e65a5a",  # red
    "#b48ef0",  # purple
    "#ff9b6a",  # orange
]


# (display_label, mode_key). Mode keys: "ping" is special (multi-series);
# everything else is a metrics.name key.
CHART_MODES: list[tuple[str, str]] = [
    ("Ping (all servers)", "ping"),
    ("Network — download", "net_down_bps"),
    ("Network — upload", "net_up_bps"),
    ("CPU %", "cpu_pct"),
    ("Memory %", "mem_pct"),
    ("GPU %", "gpu_pct"),
    ("GPU VRAM %", "gpu_vram_pct"),
    ("Disk read", "disk_read_bps"),
    ("Disk write", "disk_write_bps"),
]


# ---- helpers ---------------------------------------------------------------


def _fmt_bytes_rate(bps: float) -> str:
    v = float(bps)
    units = ("B/s", "KB/s", "MB/s", "GB/s")
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    if v >= 100:
        return f"{v:.0f} {units[i]}"
    if v >= 10:
        return f"{v:.1f} {units[i]}"
    return f"{v:.2f} {units[i]}"


class TimeAxis(pg.AxisItem):
    """Format unix timestamps as HH:MM[:SS]."""

    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            try:
                lt = time.localtime(v)
                if spacing >= 3600:
                    out.append(time.strftime("%H:%M", lt))
                else:
                    out.append(time.strftime("%H:%M:%S", lt))
            except (ValueError, OSError):
                out.append("")
        return out


class BytesAxis(pg.AxisItem):
    """Format a linear bytes/sec axis with KB/MB/GB suffixes."""

    def tickStrings(self, values, scale, spacing):
        return [_fmt_bytes_rate(v) if v >= 0 else "" for v in values]


# ---- window ----------------------------------------------------------------


class ChartWindow(QWidget):
    def __init__(self, default_servers: Optional[list[str]] = None) -> None:
        super().__init__()
        self.setWindowTitle("LightStats — history")
        self.setWindowIcon(app_icon())
        self.resize(880, 500)

        self._conn = db.connect()
        self._range_seconds: Optional[int] = 15 * 60  # default 15 min
        self._mode_key: str = "ping"

        # Ping-mode state.
        self._server_checks: dict[str, QCheckBox] = {}
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._color_for: dict[str, str] = {}
        self._known_servers: list[str] = list(default_servers or [])

        # Single-series mode state.
        self._single_curve: Optional[pg.PlotDataItem] = None

        self._build_ui()
        self._refresh_server_checks()
        self._reload()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._reload)
        self._timer.start(3000)

    # --- UI -----------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Row 1: metric selector + range buttons + auto-refresh + stats.
        top = QHBoxLayout()
        top.setSpacing(6)

        top.addWidget(QLabel("Metric:"))
        self._mode_combo = QComboBox()
        for label, key in CHART_MODES:
            self._mode_combo.addItem(label, key)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_change)
        top.addWidget(self._mode_combo)

        top.addSpacing(12)
        top.addWidget(QLabel("Range:"))
        self._range_group = QButtonGroup(self)
        self._range_group.setExclusive(True)
        for label, secs in RANGES:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, s=secs: self._set_range(s))
            if secs == self._range_seconds:
                btn.setChecked(True)
            self._range_group.addButton(btn)
            top.addWidget(btn)

        top.addSpacing(16)
        self._auto_chk = QCheckBox("Auto-refresh")
        self._auto_chk.setChecked(True)
        self._auto_chk.toggled.connect(self._on_auto_toggle)
        top.addWidget(self._auto_chk)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._reload)
        top.addWidget(refresh_btn)

        top.addStretch(1)
        self._stats_lbl = QLabel("")
        self._stats_lbl.setStyleSheet("color: #888;")
        top.addWidget(self._stats_lbl)
        root.addLayout(top)

        # Row 2: server toggles — only shown in ping mode.
        self._servers_bar_wrap = QWidget()
        self._servers_bar = QHBoxLayout(self._servers_bar_wrap)
        self._servers_bar.setContentsMargins(0, 0, 0, 0)
        self._servers_bar.setSpacing(12)
        self._servers_bar.addWidget(QLabel("Servers:"))
        root.addWidget(self._servers_bar_wrap)

        # Plot.
        pg.setConfigOptions(antialias=True, background=(20, 22, 28), foreground="#ddd")
        self._plot = pg.PlotWidget(
            axisItems={"bottom": TimeAxis(orientation="bottom")},
        )
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._plot.addLegend(offset=(10, 10))
        self._plot.getPlotItem().getViewBox().setDefaultPadding(0.02)
        root.addWidget(self._plot, 1)

        self._apply_axis_for_mode()

    def _refresh_server_checks(self) -> None:
        known = set(self._known_servers) | set(db.distinct_servers(self._conn))
        for name in sorted(known):
            if name in self._server_checks:
                continue
            color = PALETTE[len(self._server_checks) % len(PALETTE)]
            self._color_for[name] = color
            chk = QCheckBox(name)
            chk.setChecked(True)
            chk.setStyleSheet(f"color: {color}; font-weight: 600;")
            chk.toggled.connect(self._reload)
            self._server_checks[name] = chk
            self._servers_bar.addWidget(chk)

    def _apply_axis_for_mode(self) -> None:
        """Set Y-axis label/formatter based on the current mode."""
        plot_item = self._plot.getPlotItem()

        if self._mode_key == "ping":
            plot_item.setAxisItems({"left": pg.AxisItem(orientation="left")})
            plot_item.setLabel("left", "RTT", units="ms")
        elif self._mode_key in ("net_down_bps", "net_up_bps", "disk_read_bps", "disk_write_bps"):
            plot_item.setAxisItems({"left": BytesAxis(orientation="left")})
            plot_item.setLabel("left", "throughput")
        else:
            plot_item.setAxisItems({"left": pg.AxisItem(orientation="left")})
            plot_item.setLabel("left", "%")

        # Re-apply bottom time axis in case setAxisItems replaced everything.
        plot_item.setAxisItems({"bottom": TimeAxis(orientation="bottom")})

    # --- event handlers -----------------------------------------------------

    def _on_mode_change(self, _idx: int) -> None:
        self._mode_key = self._mode_combo.currentData()
        # Tear down existing curves — they belong to the previous mode.
        for c in list(self._curves.values()):
            self._plot.removeItem(c)
        self._curves.clear()
        if self._single_curve is not None:
            self._plot.removeItem(self._single_curve)
            self._single_curve = None

        # Show/hide the per-server toggle bar.
        self._servers_bar_wrap.setVisible(self._mode_key == "ping")

        self._apply_axis_for_mode()
        self._reload()

    def _set_range(self, seconds: Optional[int]) -> None:
        self._range_seconds = seconds
        self._reload()

    def _on_auto_toggle(self, on: bool) -> None:
        if on:
            self._timer.start(3000)
        else:
            self._timer.stop()

    # --- data reload --------------------------------------------------------

    def _compute_since(self, fallback_ts: Optional[float]) -> float:
        now = time.time()
        if self._range_seconds is None:
            return fallback_ts if fallback_ts is not None else now - 60
        return now - self._range_seconds

    def _reload(self) -> None:
        if self._mode_key == "ping":
            self._reload_ping()
        else:
            self._reload_metric(self._mode_key)

    def _reload_ping(self) -> None:
        self._refresh_server_checks()
        earliest = db.earliest_ts(self._conn)
        since = self._compute_since(earliest)
        now = time.time()
        rows = db.fetch_range(self._conn, since_ts=since)

        by_server: dict[str, list[tuple[float, Optional[float]]]] = defaultdict(list)
        for ts, server, rtt in rows:
            by_server[server].append((ts, rtt))

        total = len(rows)
        lost = sum(1 for _, _, r in rows if r is None)
        got = [r for _, _, r in rows if r is not None]
        loss_pct = (100.0 * lost / total) if total else 0.0
        avg = sum(got) / len(got) if got else None
        if avg is None:
            self._stats_lbl.setText(f"{total} samples · loss {loss_pct:.1f}%")
        else:
            self._stats_lbl.setText(
                f"{total} samples · avg {avg:.0f} ms · loss {loss_pct:.1f}%"
            )

        for name, chk in self._server_checks.items():
            samples = by_server.get(name, [])
            if not chk.isChecked() or not samples:
                if name in self._curves:
                    self._curves[name].setData([], [])
                continue
            xs = np.fromiter((s[0] for s in samples), dtype=float, count=len(samples))
            ys = np.fromiter(
                (s[1] if s[1] is not None else np.nan for s in samples),
                dtype=float, count=len(samples),
            )
            curve = self._curves.get(name)
            color = self._color_for.get(name, "#cccccc")
            if curve is None:
                curve = self._plot.plot(
                    xs, ys, pen=pg.mkPen(QColor(color), width=1.6),
                    name=name, connect="finite",
                )
                self._curves[name] = curve
            else:
                curve.setData(xs, ys, connect="finite")

        self._apply_x_range(since, now, rows and (rows[0][0], rows[-1][0]))

    def _reload_metric(self, name: str) -> None:
        earliest = db.earliest_metric_ts(self._conn, name)
        since = self._compute_since(earliest)
        now = time.time()
        rows = db.fetch_metric(self._conn, name, since_ts=since)

        if not rows:
            self._stats_lbl.setText(f"0 samples")
            if self._single_curve is not None:
                self._single_curve.setData([], [])
            return

        xs = np.fromiter((r[0] for r in rows), dtype=float, count=len(rows))
        ys = np.fromiter(
            (r[1] if r[1] is not None else np.nan for r in rows),
            dtype=float, count=len(rows),
        )

        color = PALETTE[0]  # cyan; single-series charts use first palette colour
        if self._single_curve is None:
            self._single_curve = self._plot.plot(
                xs, ys, pen=pg.mkPen(QColor(color), width=1.6),
                name=self._mode_combo.currentText(),
                connect="finite",
            )
        else:
            self._single_curve.setData(xs, ys, connect="finite")

        # Stats line tailored to the unit.
        valid = ys[~np.isnan(ys)]
        if valid.size == 0:
            summary = f"{len(rows)} samples"
        elif name.endswith("_bps"):
            summary = (
                f"{len(rows)} samples · avg {_fmt_bytes_rate(float(valid.mean()))}"
                f" · peak {_fmt_bytes_rate(float(valid.max()))}"
            )
        else:
            summary = (
                f"{len(rows)} samples · avg {float(valid.mean()):.1f}"
                f" · peak {float(valid.max()):.1f}"
            )
        self._stats_lbl.setText(summary)

        self._apply_x_range(since, now, (rows[0][0], rows[-1][0]))

    def _apply_x_range(
        self,
        since: float,
        now: float,
        data_span: Optional[tuple[float, float]],
    ) -> None:
        if self._range_seconds is not None:
            self._plot.setXRange(since, now, padding=0.02)
        else:
            if data_span:
                self._plot.setXRange(data_span[0], data_span[1], padding=0.02)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        try:
            self._conn.close()
        except Exception:
            pass
        super().closeEvent(event)
