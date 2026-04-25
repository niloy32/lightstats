"""Windows "Start with login" toggle via HKCU\\...\\Run.

We use the per-user Run key (HKEY_CURRENT_USER) rather than HKLM because:
- no admin prompt required
- user-scoped config keeps isolated per account
- removing the registry value is all that's needed to disable

Only meaningful for frozen builds — a dev-mode source checkout pointing at
`run.bat` tends to break when the user's working directory shifts, so we
refuse to register autostart in that case.
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

from paths import is_frozen

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "LightStats"


def _exe_path() -> str | None:
    """The command to register. Quoted path to the frozen .exe.

    Returns None when running from source — we don't want to autostart a
    venv + .bat combo that might not survive moves.
    """
    if not is_frozen():
        return None
    return f'"{Path(sys.executable).resolve()}"'


def supported() -> bool:
    """Whether autostart is a meaningful option in the current build."""
    return _exe_path() is not None


def is_enabled() -> bool:
    """True if our Run entry exists and points at this build's exe."""
    expected = _exe_path()
    if expected is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            val, _ = winreg.QueryValueEx(key, _VALUE_NAME)
        return isinstance(val, str) and val.strip().lower() == expected.strip().lower()
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_enabled(enable: bool) -> bool:
    """Add or remove our Run entry. Returns True on success."""
    expected = _exe_path()
    if expected is None:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enable:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, expected)
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
