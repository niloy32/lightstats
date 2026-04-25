"""Settings dialog — toggle which widgets show on the overlay."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import autostart
import config
from icon_loader import app_icon


# (key, label, hint) — hint is a one-liner shown under the checkbox.
WIDGET_OPTIONS: list[tuple[str, str, str]] = [
    ("ping", "Ping servers", "Per-server ping, jitter, loss"),
    ("network", "Network speed", "Download / upload throughput"),
    ("adapter", "Network adapter", "Active adapter name + link speed"),
    ("cpu", "CPU usage", "Overall %"),
    ("memory", "Memory usage", "Used / total GB and %"),
    ("gpu", "GPU usage", "Utilization % (NVIDIA adds VRAM + temp)"),
    ("disk_io", "Disk I/O", "Read / write throughput"),
    ("uptime", "Uptime", "Time since last boot"),
]


class SettingsDialog(QDialog):
    """Small modal dialog. On Accept, writes config.json and calls `on_apply`."""

    def __init__(
        self,
        parent: QWidget | None,
        cfg: config.Config,
        on_apply: Callable[[config.Config], None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("LightStats — settings")
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(360)
        self._on_apply = on_apply
        self._cfg = cfg

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        intro = QLabel("Pick which widgets appear on the overlay.")
        intro.setStyleSheet("color: #888;")
        root.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self._checks: dict[str, QCheckBox] = {}
        for key, label, hint in WIDGET_OPTIONS:
            chk = QCheckBox(label)
            chk.setChecked(cfg.widget_enabled(key))
            hint_lbl = QLabel(hint)
            hint_lbl.setStyleSheet("color: #888; font-size: 11px;")
            row = QVBoxLayout()
            row.setSpacing(0)
            row.addWidget(chk)
            row.addWidget(hint_lbl)
            container = QWidget()
            container.setLayout(row)
            form.addRow(container)
            self._checks[key] = chk

        root.addLayout(form)

        # --- Font size row --------------------------------------------------
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #444;")
        root.addWidget(sep)

        font_row = QHBoxLayout()
        font_label = QLabel("Font size")
        font_label.setMinimumWidth(80)
        font_row.addWidget(font_label)

        self._font_slider = QSlider(Qt.Orientation.Horizontal)
        self._font_slider.setRange(config.MIN_FONT_SIZE, config.MAX_FONT_SIZE)
        self._font_slider.setValue(cfg.clamped_font_size())
        self._font_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._font_slider.setTickInterval(2)
        font_row.addWidget(self._font_slider, 1)

        self._font_spin = QSpinBox()
        self._font_spin.setRange(config.MIN_FONT_SIZE, config.MAX_FONT_SIZE)
        self._font_spin.setValue(cfg.clamped_font_size())
        self._font_spin.setSuffix(" pt")
        font_row.addWidget(self._font_spin)

        # Keep slider + spinbox in sync and push live updates so the user
        # can see the overlay resize while dragging.
        self._font_slider.valueChanged.connect(self._on_font_changed)
        self._font_spin.valueChanged.connect(self._on_font_changed)

        root.addLayout(font_row)

        # --- Start with Windows --------------------------------------------
        # Only surface this in frozen builds — registering a dev-mode venv
        # path in the Run key tends to break as soon as the working dir moves.
        self._autostart_chk: QCheckBox | None = None
        if autostart.supported():
            self._autostart_chk = QCheckBox("Start LightStats when I sign in to Windows")
            self._autostart_chk.setChecked(autostart.is_enabled())
            hint = QLabel("Adds a per-user Run entry in the registry.")
            hint.setStyleSheet("color: #888; font-size: 11px;")
            root.addWidget(self._autostart_chk)
            root.addWidget(hint)

        root.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self._cancel)
        root.addWidget(buttons)

        # Remember original config values so Cancel can restore them after
        # live-preview edits.
        self._original_font_size = cfg.clamped_font_size()
        self._original_widgets = dict(cfg.widgets)

    def _on_font_changed(self, v: int) -> None:
        # Keep slider + spinbox synchronized without fighting each other.
        if self.sender() is self._font_slider and self._font_spin.value() != v:
            self._font_spin.blockSignals(True)
            self._font_spin.setValue(v)
            self._font_spin.blockSignals(False)
        elif self.sender() is self._font_spin and self._font_slider.value() != v:
            self._font_slider.blockSignals(True)
            self._font_slider.setValue(v)
            self._font_slider.blockSignals(False)
        # Live preview — push the new size to the overlay immediately.
        self._cfg.font_size = v
        self._on_apply(self._cfg)

    def _accept(self) -> None:
        for key, chk in self._checks.items():
            self._cfg.widgets[key] = chk.isChecked()
        self._cfg.font_size = int(self._font_spin.value())
        try:
            config.save(self._cfg)
        except Exception:
            # Settings still apply in-memory; disk persistence is best effort.
            pass
        # Autostart toggle is persisted in the registry, not config.json —
        # it has to round-trip through Windows regardless of whether we
        # successfully wrote config.
        if self._autostart_chk is not None:
            autostart.set_enabled(self._autostart_chk.isChecked())
        self._on_apply(self._cfg)
        self.accept()

    def _cancel(self) -> None:
        # Undo live-preview font changes.
        self._cfg.font_size = self._original_font_size
        self._cfg.widgets = dict(self._original_widgets)
        self._on_apply(self._cfg)
        self.reject()
