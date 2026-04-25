"""System tray icon. Click to toggle overlay; right-click for menu."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from icon_loader import app_icon
from ping_worker import ServerStats


def _make_icon(color: QColor) -> QIcon:
    """Create a simple colored-dot icon at tray size."""
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(4, 4, 24, 24)
    p.end()
    return QIcon(pm)


class TrayIcon(QSystemTrayIcon):
    def __init__(self, overlay, app: QApplication) -> None:
        super().__init__()
        self._overlay = overlay
        self._app = app

        self._icons = {
            "ok": _make_icon(QColor(120, 220, 140)),
            "warn": _make_icon(QColor(240, 200, 100)),
            "bad": _make_icon(QColor(230, 90, 90)),
            "idle": _make_icon(QColor(140, 140, 140)),
        }
        self.setIcon(self._icons["idle"])
        self.setToolTip("LightStats — starting…")

        menu = QMenu()
        self._show_action = QAction("Hide overlay")
        self._show_action.triggered.connect(self._toggle_overlay)
        menu.addAction(self._show_action)
        menu.addSeparator()
        quit_action = QAction("Quit")
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)
        self.setContextMenu(menu)

        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Left-click / double-click toggles the overlay.
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_overlay()

    def _toggle_overlay(self) -> None:
        if self._overlay.isVisible():
            self._overlay.hide()
            self._show_action.setText("Show overlay")
        else:
            self._overlay.show()
            self._overlay.raise_()
            self._show_action.setText("Hide overlay")

    def _quit(self) -> None:
        self._app.quit()

    def apply_stats(self, stats: list[ServerStats]) -> None:
        any_online = any(s.online for s in stats)
        losses = [s.loss_pct for s in stats] or [0.0]
        avg_loss = sum(losses) / len(losses)
        avg_pings = [s.avg_ms for s in stats if s.avg_ms is not None]
        avg_ping: Optional[float] = (
            sum(avg_pings) / len(avg_pings) if avg_pings else None
        )

        if not any_online:
            self.setIcon(self._icons["bad"])
            self.setToolTip("LightStats — offline")
            return
        if avg_loss >= 5 or (avg_ping is not None and avg_ping >= 150):
            self.setIcon(self._icons["warn"])
            tip = f"LightStats — degraded · {avg_ping:.0f} ms · {avg_loss:.0f}% loss"
            self.setToolTip(tip)
            return
        self.setIcon(self._icons["ok"])
        ping_txt = f"{avg_ping:.0f} ms" if avg_ping is not None else "—"
        self.setToolTip(f"LightStats — online · {ping_txt} · {avg_loss:.0f}% loss")
