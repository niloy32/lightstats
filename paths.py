"""Path helpers that work in both dev and frozen (PyInstaller) builds.

PyInstaller's --onefile bootstrapper extracts the bundled payload to a
temp dir at `sys._MEIPASS` on each run — but that dir is deleted when
the process exits. User data (config, database, logs) must live
elsewhere, or it'll vanish silently between launches.

We keep things portable: user data sits next to the executable. That way
`LightStats.exe` can be dropped on a USB stick with its config/history
alongside, and nothing touches AppData or registry for storage.

- `app_dir()` → where writable user data goes.
  - Frozen: parent of `sys.executable` (the `LightStats.exe` folder).
  - Dev: the repo root (parent of this file).

- `resource_dir()` → where read-only bundled assets live.
  - Frozen: `sys._MEIPASS` (PyInstaller's extraction dir).
  - Dev: same as `app_dir()`.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).parent.resolve()
    return Path(__file__).parent.resolve()


def resource_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass).resolve()
    return app_dir()
