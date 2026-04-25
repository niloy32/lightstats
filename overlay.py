"""Frameless, always-on-top, draggable, resizable overlay window.

Layout:
- Header row: status dot + summary + gear button + chart button
- Ping section (optional): column headers + per-server rows
- System section (optional widgets): network speed, adapter, CPU, memory,
  GPU, disk I/O, uptime — any subset, based on `config.Config`.

Widgets are rebuilt whenever `set_config()` is called so settings changes
apply immediately.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPaintEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

import config
from icon_loader import app_icon
from ping_worker import ServerStats
from system_worker import SystemSample


# ---- formatting helpers -----------------------------------------------------


def _fmt_ms(v: Optional[float]) -> str:
    return f"{v:.0f}" if v is not None else "—"


def _fmt_bytes_rate(bps: Optional[float]) -> str:
    """Format a bytes/sec rate as B/s, KB/s, MB/s, GB/s."""
    if bps is None:
        return "—"
    v = float(bps)
    units = ("B/s", "KB/s", "MB/s", "GB/s", "TB/s")
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    if v >= 100:
        return f"{v:.0f} {units[i]}"
    if v >= 10:
        return f"{v:.1f} {units[i]}"
    return f"{v:.2f} {units[i]}"


def _fmt_uptime(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


def _fmt_link_mbps(mbps: Optional[float]) -> str:
    if mbps is None or mbps <= 0:
        return ""
    if mbps >= 1000:
        return f"{mbps/1000:.1f} Gbps"
    return f"{mbps:.0f} Mbps"


# ---- colouring --------------------------------------------------------------


def _color_for_ping(v: Optional[float]) -> QColor:
    if v is None:
        return QColor(230, 90, 90)
    if v < 40:
        return QColor(120, 220, 140)
    if v < 100:
        return QColor(240, 200, 100)
    return QColor(230, 140, 90)


def _color_for_loss(pct: float) -> QColor:
    if pct <= 0.5:
        return QColor(120, 220, 140)
    if pct < 5:
        return QColor(240, 200, 100)
    return QColor(230, 90, 90)


def _color_for_pct(pct: Optional[float]) -> QColor:
    """Green < 60, amber < 85, red otherwise."""
    if pct is None:
        return QColor(200, 200, 200)
    if pct < 60:
        return QColor(120, 220, 140)
    if pct < 85:
        return QColor(240, 200, 100)
    return QColor(230, 90, 90)


# ---- per-server ping row ----------------------------------------------------


class _ServerRow(QWidget):
    """One line per server: name, ping, jitter, loss."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.name_lbl = QLabel()
        self.name_lbl.setMinimumWidth(70)
        self.ping_lbl = QLabel()
        self.ping_lbl.setMinimumWidth(58)
        self.ping_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.jitter_lbl = QLabel()
        self.jitter_lbl.setMinimumWidth(52)
        self.jitter_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.loss_lbl = QLabel()
        self.loss_lbl.setMinimumWidth(48)
        self.loss_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        for lbl in (self.name_lbl, self.ping_lbl, self.jitter_lbl, self.loss_lbl):
            lbl.setStyleSheet("color: #e6e6e6;")

        row.addWidget(self.name_lbl)
        row.addWidget(self.ping_lbl)
        row.addWidget(self.jitter_lbl)
        row.addWidget(self.loss_lbl)

    def update_from(self, s: ServerStats) -> None:
        self.name_lbl.setText(s.name)
        ping = s.last_rtt
        self.ping_lbl.setText(f"{_fmt_ms(ping)} ms")
        self.ping_lbl.setStyleSheet(f"color: {_color_for_ping(ping).name()};")
        self.jitter_lbl.setText(f"±{_fmt_ms(s.jitter_ms)}")
        self.jitter_lbl.setStyleSheet("color: #bfbfbf;")
        self.loss_lbl.setText(f"{s.loss_pct:.0f}%")
        self.loss_lbl.setStyleSheet(f"color: {_color_for_loss(s.loss_pct).name()};")


# ---- generic key/value row (CPU, memory, etc.) ------------------------------


class _KvRow(QWidget):
    """A `label:  value` row used by most system widgets."""

    def __init__(self, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.label_lbl = QLabel(label)
        self.label_lbl.setMinimumWidth(70)
        self.label_lbl.setStyleSheet("color: #bfbfbf;")

        self.value_lbl = QLabel("—")
        self.value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_lbl.setStyleSheet("color: #e6e6e6;")

        row.addWidget(self.label_lbl)
        row.addWidget(self.value_lbl, 1)

    def set_value(self, text: str, color: Optional[QColor] = None) -> None:
        self.value_lbl.setText(text)
        if color is not None:
            self.value_lbl.setStyleSheet(f"color: {color.name()};")
        else:
            self.value_lbl.setStyleSheet("color: #e6e6e6;")


# ---- main window ------------------------------------------------------------


class OverlayWindow(QWidget):
    """Compact draggable overlay. Widget visibility driven by `config.Config`."""

    chart_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self, cfg: config.Config) -> None:
        super().__init__()
        self._cfg = cfg

        # No `Qt.Tool` — we WANT a taskbar entry so the user can alt-tab,
        # minimize, and close via taskbar right-click like any other app.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("LightStats")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(260, 110)

        # Root layout gets cleared + rebuilt in _rebuild() whenever config changes.
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(12, 10, 12, 10)
        self._root.setSpacing(4)

        # Placeholders filled in _build_static() and _rebuild().
        self.status_lbl: Optional[QLabel] = None
        self.summary_lbl: Optional[QLabel] = None
        self.chart_btn: Optional[QPushButton] = None
        self.settings_btn: Optional[QPushButton] = None

        self._ping_header: Optional[QWidget] = None
        self._rows: list[_ServerRow] = []
        self._rows_layout: Optional[QVBoxLayout] = None

        # System widget rows keyed by config key.
        self._sys_rows: dict[str, _KvRow] = {}
        self._sys_layout: Optional[QVBoxLayout] = None

        # Labels that should render smaller than the base font (e.g. column
        # headers). Rebuilt every time _rebuild() runs.
        self._small_labels: list[QLabel] = []

        self._grip: Optional[QSizeGrip] = None

        # True once the user (or a restored saved size) has committed to a
        # specific size. Until then, we keep auto-fitting as new content
        # (ping rows) streams in.
        self._user_sized = False
        # True until the first auto-fit pass completes after ping rows land.
        self._auto_fit_pending = True

        self._rebuild()

        # Restore the last-used size if we have one; otherwise fit to content.
        saved = cfg.saved_size()
        if saved is not None:
            self.resize(saved[0], saved[1])
            self._user_sized = True
            self._auto_fit_pending = False
        else:
            # First-launch fit. sizeHint here only reflects header + system
            # rows — ping rows are added lazily by apply_ping_stats(), so we
            # run a second fit after the first ping round (see _auto_fit_pending).
            self.adjustSize()

        self._drag_pos: Optional[QPoint] = None

    # --- layout build / teardown -------------------------------------------

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            sub = item.layout()
            if sub is not None:
                self._clear_layout(sub)

    def _rebuild(self) -> None:
        """Tear down & recreate the layout based on current config."""
        self._clear_layout(self._root)
        self._rows = []
        self._sys_rows = {}
        self._small_labels = []

        # --- header ---------------------------------------------------------
        header = QHBoxLayout()
        header.setSpacing(6)
        self.status_lbl = QLabel("●")
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        self.status_lbl.setFont(font)
        self.status_lbl.setStyleSheet("color: #888;")
        self.summary_lbl = QLabel("starting…")
        self.summary_lbl.setStyleSheet("color: #e6e6e6; font-weight: 600;")
        header.addWidget(self.status_lbl)
        header.addWidget(self.summary_lbl, 1)

        self.settings_btn = self._make_icon_button("⚙", "Settings")
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.settings_btn)

        self.chart_btn = self._make_icon_button("📈", "Show history chart")
        self.chart_btn.clicked.connect(self.chart_requested.emit)
        header.addWidget(self.chart_btn)

        # Close button — fully quits the app (not just hide).
        close_btn = self._make_icon_button("✕", "Close LightStats")
        close_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,20);"
            " color: #e6e6e6; border: none; border-radius: 4px;"
            " font-size: 11px; }"
            "QPushButton:hover { background: #e65a5a; color: white; }"
        )
        close_btn.clicked.connect(self.close_requested.emit)
        header.addWidget(close_btn)

        self._root.addLayout(header)

        # --- ping section ---------------------------------------------------
        if self._cfg.widget_enabled("ping"):
            hdr = QHBoxLayout()
            hdr.setSpacing(10)
            for text, width, align in (
                ("server", 70, Qt.AlignmentFlag.AlignLeft),
                ("ping", 58, Qt.AlignmentFlag.AlignRight),
                ("jitter", 52, Qt.AlignmentFlag.AlignRight),
                ("loss", 48, Qt.AlignmentFlag.AlignRight),
            ):
                lbl = QLabel(text)
                lbl.setMinimumWidth(width)
                lbl.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                # No font-size in stylesheet — it'd override our QFont later.
                lbl.setStyleSheet("color: #888;")
                hdr.addWidget(lbl)
                self._small_labels.append(lbl)
            self._root.addLayout(hdr)

            self._rows_layout = QVBoxLayout()
            self._rows_layout.setSpacing(2)
            self._root.addLayout(self._rows_layout)
        else:
            self._rows_layout = None

        # Separator between ping and system widgets if both present.
        any_sys = any(
            self._cfg.widget_enabled(k)
            for k in ("network", "adapter", "cpu", "memory", "gpu", "disk_io", "uptime")
        )
        if any_sys and self._cfg.widget_enabled("ping"):
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color: #333; background: #333; max-height: 1px;")
            self._root.addWidget(sep)

        # --- system widgets ------------------------------------------------
        self._sys_layout = QVBoxLayout()
        self._sys_layout.setSpacing(2)
        self._root.addLayout(self._sys_layout)

        widget_spec: list[tuple[str, str]] = [
            ("network", "Network"),
            ("adapter", "Adapter"),
            ("cpu", "CPU"),
            ("memory", "Memory"),
            ("gpu", "GPU"),
            ("disk_io", "Disk I/O"),
            ("uptime", "Uptime"),
        ]
        for key, label in widget_spec:
            if not self._cfg.widget_enabled(key):
                continue
            row = _KvRow(label)
            self._sys_rows[key] = row
            self._sys_layout.addWidget(row)

        self._root.addStretch(1)

        # Resize grip anchored bottom-right.
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch(1)
        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(14, 14)
        grip_row.addWidget(
            self._grip, 0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
        )
        self._root.addLayout(grip_row)

        self._apply_font_size()

    def _apply_font_size(self) -> None:
        """Propagate the configured font size to every label on the overlay."""
        size = self._cfg.clamped_font_size()
        # Use QFont instead of stylesheet so we can keep per-label colors.
        base_font = QFont()
        base_font.setPointSize(size)

        # Small labels (column headers, hints) are scaled down a bit.
        small_font = QFont()
        small_font.setPointSize(max(7, size - 2))

        # Header: status dot bigger, summary bold.
        if self.status_lbl is not None:
            big = QFont(base_font)
            big.setPointSize(size + 1)
            big.setBold(True)
            self.status_lbl.setFont(big)
        if self.summary_lbl is not None:
            bold = QFont(base_font)
            bold.setBold(True)
            self.summary_lbl.setFont(bold)

        # Server rows, system rows: base font.
        for row in self._rows:
            for lbl in (row.name_lbl, row.ping_lbl, row.jitter_lbl, row.loss_lbl):
                lbl.setFont(base_font)
        for row in self._sys_rows.values():
            row.label_lbl.setFont(base_font)
            row.value_lbl.setFont(base_font)

        # Column header labels (dim text above the ping rows).
        for lbl in self._small_labels:
            lbl.setFont(small_font)

    def _make_icon_button(self, glyph: str, tooltip: str) -> QPushButton:
        b = QPushButton(glyph)
        b.setToolTip(tooltip)
        b.setFixedSize(22, 22)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,20);"
            " color: #e6e6e6; border: none; border-radius: 4px;"
            " font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,255,255,45); }"
        )
        return b

    # --- external API ------------------------------------------------------

    def set_config(self, cfg: config.Config) -> None:
        """Apply a new settings state — rebuilds the layout.

        If enabling widgets pushes the natural content size past the current
        window, grow to fit. We never auto-shrink here — the user may have
        intentionally resized larger.
        """
        self._cfg = cfg
        self._rebuild()
        hint = self.sizeHint()
        new_w = max(self.width(), hint.width())
        new_h = max(self.height(), hint.height())
        if (new_w, new_h) != (self.width(), self.height()):
            self.resize(new_w, new_h)

    # --- painting ----------------------------------------------------------

    def paintEvent(self, _: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 22, 28, 210))
        p.drawRoundedRect(self.rect(), 10, 10)

    # --- drag --------------------------------------------------------------

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._drag_pos = None

    # --- data updates ------------------------------------------------------

    def apply_ping_stats(self, stats: list[ServerStats]) -> None:
        if self._rows_layout is None:
            # Ping widget is off — still want the header status to reflect
            # connectivity, so compute summary from stats anyway.
            self._update_summary(stats)
            return

        while len(self._rows) < len(stats):
            row = _ServerRow()
            # Match the rest of the overlay's current font size.
            font = QFont()
            font.setPointSize(self._cfg.clamped_font_size())
            for lbl in (row.name_lbl, row.ping_lbl, row.jitter_lbl, row.loss_lbl):
                lbl.setFont(font)
            self._rows.append(row)
            self._rows_layout.addWidget(row)
        while len(self._rows) > len(stats):
            row = self._rows.pop()
            self._rows_layout.removeWidget(row)
            row.deleteLater()

        for row, s in zip(self._rows, stats):
            row.update_from(s)

        self._update_summary(stats)

        # First-launch only: ping rows just populated, so re-fit to include
        # them. After this, further growth only comes from settings toggles
        # (set_config) and the user's resize grip.
        if self._auto_fit_pending and not self._user_sized and self._rows:
            self._auto_fit_pending = False
            hint = self.sizeHint()
            new_w = max(self.width(), hint.width())
            new_h = max(self.height(), hint.height())
            if (new_w, new_h) != (self.width(), self.height()):
                self.resize(new_w, new_h)

    def _update_summary(self, stats: list[ServerStats]) -> None:
        if not self.status_lbl or not self.summary_lbl:
            return
        any_online = any(s.online for s in stats)
        avg_pings = [s.avg_ms for s in stats if s.avg_ms is not None]
        overall_avg = sum(avg_pings) / len(avg_pings) if avg_pings else None
        losses = [s.loss_pct for s in stats]
        overall_loss = sum(losses) / len(losses) if losses else 0.0

        if not any_online:
            self.status_lbl.setStyleSheet("color: #e65a5a;")
            self.summary_lbl.setText("offline")
        elif overall_loss >= 5 or (overall_avg is not None and overall_avg >= 150):
            self.status_lbl.setStyleSheet("color: #f0c864;")
            self.summary_lbl.setText(
                f"degraded · avg {_fmt_ms(overall_avg)} ms · loss {overall_loss:.0f}%"
            )
        else:
            self.status_lbl.setStyleSheet("color: #78dc8c;")
            self.summary_lbl.setText(
                f"online · avg {_fmt_ms(overall_avg)} ms · loss {overall_loss:.0f}%"
            )

    def apply_system_sample(self, s: SystemSample) -> None:
        """Update whichever system-widget rows are currently visible."""
        r = self._sys_rows.get("network")
        if r is not None:
            r.set_value(
                f"↓ {_fmt_bytes_rate(s.net_down_bps)}   ↑ {_fmt_bytes_rate(s.net_up_bps)}"
            )

        r = self._sys_rows.get("adapter")
        if r is not None:
            name = s.net_adapter or "—"
            link = _fmt_link_mbps(s.net_link_mbps)
            r.set_value(f"{name}" + (f" · {link}" if link else ""))

        r = self._sys_rows.get("cpu")
        if r is not None:
            r.set_value(
                f"{s.cpu_pct:.0f}%" if s.cpu_pct is not None else "—",
                _color_for_pct(s.cpu_pct),
            )

        r = self._sys_rows.get("memory")
        if r is not None:
            if s.mem_pct is not None and s.mem_used_gb is not None and s.mem_total_gb is not None:
                r.set_value(
                    f"{s.mem_used_gb:.1f} / {s.mem_total_gb:.1f} GB  ({s.mem_pct:.0f}%)",
                    _color_for_pct(s.mem_pct),
                )
            else:
                r.set_value("—")

        r = self._sys_rows.get("gpu")
        if r is not None:
            g = s.gpu
            if g.util_pct is None:
                r.set_value("—")
            else:
                parts = [f"{g.util_pct:.0f}%"]
                if g.vram_used_gb is not None and g.vram_total_gb is not None:
                    parts.append(
                        f"VRAM {g.vram_used_gb:.1f} / {g.vram_total_gb:.1f} GB"
                    )
                if g.temp_c is not None:
                    parts.append(f"{g.temp_c:.0f}°C")
                r.set_value(" · ".join(parts), _color_for_pct(g.util_pct))

        r = self._sys_rows.get("disk_io")
        if r is not None:
            r.set_value(
                f"R {_fmt_bytes_rate(s.disk_read_bps)}   W {_fmt_bytes_rate(s.disk_write_bps)}"
            )

        r = self._sys_rows.get("uptime")
        if r is not None:
            r.set_value(_fmt_uptime(s.uptime_seconds))
