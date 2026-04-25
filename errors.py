"""Crash logging + global exception handling.

Goals:
- Any unhandled exception (main thread or worker thread) lands in `crash.log`
  next to main.py, with a timestamp and full traceback.
- If Qt has started, also show a message box so the user knows *something*
  went wrong even if they launched via pythonw (no console).
- Catch import-time failures before Qt exists (message box falls back to
  a log-only path in that case).

Kept as a single small module so `main.py` can `import errors; errors.install()`
before anything else happens.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

from paths import app_dir

LOG_PATH = app_dir() / "crash.log"

_installed = False
_logger: Optional[logging.Logger] = None


def install(log_path: Path = LOG_PATH) -> logging.Logger:
    """Set up file logging and global exception hooks. Idempotent."""
    global _installed, _logger
    if _installed and _logger is not None:
        return _logger

    logger = logging.getLogger("internet_status")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Rotate so crash.log can't grow unbounded if something is spamming.
    handler = logging.handlers.RotatingFileHandler(
        str(log_path), maxBytes=512_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)

    # Also echo to stderr when run from a console. Harmless under pythonw
    # (stderr is silently discarded there).
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(stream)

    def _excepthook(exc_type, exc, tb):
        logger.error(
            "Unhandled exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc, tb)),
        )
        _maybe_show_dialog(exc_type, exc, tb)

    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        logger.error(
            "Unhandled exception in thread %s:\n%s",
            args.thread.name if args.thread else "?",
            "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook

    _installed = True
    _logger = logger
    logger.info("Logging initialized. Log file: %s", log_path)
    return logger


def _maybe_show_dialog(exc_type, exc, tb) -> None:
    """Show a Qt message box if Qt is up. Silently skip otherwise."""
    try:
        # Import lazily — this module must be usable before PyQt6 imports.
        from PyQt6.QtWidgets import QApplication, QMessageBox
    except Exception:
        return
    app = QApplication.instance()
    if app is None:
        return
    try:
        text = "".join(traceback.format_exception_only(exc_type, exc)).strip()
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("LightStats — crash")
        box.setText(text or "An unexpected error occurred.")
        box.setInformativeText(f"See {LOG_PATH.name} for details.")
        box.setDetailedText(detail)
        box.exec()
    except Exception:
        # A failing dialog must not hide the original crash.
        pass


def get_logger() -> logging.Logger:
    """Return the module logger; install() it first if not yet set up."""
    if _logger is None:
        return install()
    return _logger
