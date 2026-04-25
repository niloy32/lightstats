"""Entry point.

Starts both workers (ping + system metrics), builds the overlay + tray +
(lazily) chart window + settings dialog, and wires everything through Qt
signals so the UI only touches data from the GUI thread.
"""

from __future__ import annotations

import signal
import sys
import traceback
from typing import Optional

# Install crash logging BEFORE any risky imports so dependency errors
# (missing pyqtgraph / numpy / psutil / etc.) land in crash.log instead
# of being silently swallowed by pythonw.exe.
import errors

log = errors.install()

try:
    from PyQt6.QtCore import QThread, QTimer
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

    import config
    import db
    import single_instance
    from chart_window import ChartWindow
    from icon_loader import app_icon
    from overlay import OverlayWindow
    from ping_worker import PingWorker, detect_default_gateway
    from settings_dialog import SettingsDialog
    from system_worker import SystemWorker
    from tray import TrayIcon
except Exception as _e:
    log.critical("Startup import failed: %s\n%s", _e, traceback.format_exc())
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f"LightStats failed to start:\n\n{_e}\n\nSee crash.log.",
            "LightStats — startup error",
            0x10,  # MB_ICONERROR
        )
    except Exception:
        pass
    sys.exit(1)


# Stable AppUserModelID. Windows uses this to group taskbar buttons and pick
# the taskbar icon; without it, pythonw-hosted apps get generic Python icons
# and group under "Python" in Task Manager.
APP_USER_MODEL_ID = "com.lightstats.desktop"


def _set_windows_app_id() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception as e:
        log.debug("SetCurrentProcessExplicitAppUserModelID failed: %s", e)


DEFAULT_SERVERS: list[tuple[str, str]] = [
    ("Google", "8.8.8.8"),
    ("Cloudflare", "1.1.1.1"),
]


def build_server_list() -> list[tuple[str, str]]:
    servers = list(DEFAULT_SERVERS)
    gw = detect_default_gateway()
    if gw:
        servers.insert(0, ("Gateway", gw))
    return servers


def main() -> int:
    log.info("Starting LightStats")
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Prevent a second double-click from creating a ghost overlay. If an
    # instance is already running, surface its window and bail out — the
    # mutex `acquire()` does the window-raise for us.
    if not single_instance.acquire():
        log.info("Another LightStats instance is already running; exiting.")
        return 0

    # Must happen BEFORE QApplication so Windows picks up the AppUserModelID.
    _set_windows_app_id()

    try:
        db.init_db(retention_days=7)
    except Exception as e:
        log.exception("DB init failed: %s", e)

    app = QApplication(sys.argv)
    app.setApplicationName("LightStats")
    app.setApplicationDisplayName("LightStats")
    app.setOrganizationName("LightStats")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None,
            "LightStats",
            "System tray is not available on this system.",
        )
        return 1

    cfg = config.load()

    overlay = OverlayWindow(cfg)

    screen = app.primaryScreen().availableGeometry()
    overlay.move(screen.right() - overlay.width() - 24, screen.top() + 24)
    overlay.show()

    tray = TrayIcon(overlay, app)
    tray.show()

    # --- Chart window (lazy) -------------------------------------------------
    chart_ref: dict[str, Optional[ChartWindow]] = {"ref": None}
    server_names = [name for name, _ in build_server_list()]

    def open_chart() -> None:
        w = chart_ref["ref"]
        if w is None or not w.isVisible():
            w = ChartWindow(default_servers=server_names)
            chart_ref["ref"] = w
        w.show()
        w.raise_()
        w.activateWindow()

    overlay.chart_requested.connect(open_chart)
    overlay.close_requested.connect(app.quit)

    # --- Settings dialog (lazy) ---------------------------------------------
    settings_ref: dict[str, Optional[SettingsDialog]] = {"ref": None}

    def apply_settings(new_cfg: config.Config) -> None:
        overlay.set_config(new_cfg)

    def open_settings() -> None:
        d = settings_ref["ref"]
        if d is None or not d.isVisible():
            d = SettingsDialog(None, cfg, apply_settings)
            settings_ref["ref"] = d
        d.show()
        d.raise_()
        d.activateWindow()

    overlay.settings_requested.connect(open_settings)

    # Extend tray menu with chart + settings entries.
    tray_menu = tray.contextMenu()
    chart_action = QAction("Show chart", tray_menu)
    chart_action.triggered.connect(open_chart)
    settings_action = QAction("Settings…", tray_menu)
    settings_action.triggered.connect(open_settings)
    # Insert both before the existing separator (the "Hide overlay" entry is
    # at index 0; separator is at 1).
    tray_menu.insertAction(tray_menu.actions()[1], chart_action)
    tray_menu.insertAction(chart_action, settings_action)

    # --- Ping worker thread -------------------------------------------------
    servers = build_server_list()
    ping_worker = PingWorker(
        servers=servers, interval_ms=1000, window=30, persist=True
    )
    ping_thread = QThread()
    ping_worker.moveToThread(ping_thread)
    ping_thread.started.connect(ping_worker.run)

    def _on_ping_update(stats: list) -> None:
        overlay.apply_ping_stats(stats)
        tray.apply_stats(stats)

    ping_worker.updated.connect(_on_ping_update)

    # --- System metrics worker thread ---------------------------------------
    sys_worker = SystemWorker(interval_ms=1000, persist=True)
    sys_thread = QThread()
    sys_worker.moveToThread(sys_thread)
    sys_thread.started.connect(sys_worker.run)

    def _on_sys_update(sample) -> None:
        overlay.apply_system_sample(sample)

    sys_worker.updated.connect(_on_sys_update)

    def _shutdown() -> None:
        # Persist overlay size so it reopens at the user's preferred size.
        try:
            cfg.window_width = max(0, int(overlay.width()))
            cfg.window_height = max(0, int(overlay.height()))
            config.save(cfg)
        except Exception as e:
            log.debug("Saving overlay size failed: %s", e)

        ping_worker.stop()
        sys_worker.stop()
        ping_thread.quit()
        sys_thread.quit()
        ping_thread.wait(2000)
        sys_thread.wait(2000)
        w = chart_ref["ref"]
        if w is not None:
            w.close()

    app.aboutToQuit.connect(_shutdown)

    ping_thread.start()
    sys_thread.start()

    # Keep Python's signal handler responsive under Qt's event loop.
    heartbeat = QTimer()
    heartbeat.start(250)
    heartbeat.timeout.connect(lambda: None)

    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log.exception("Fatal error in main(): %s", e)
        raise
