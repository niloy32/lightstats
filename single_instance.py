"""Single-instance guard via a named Win32 mutex.

Double-clicking the .exe should not produce two overlays. We grab a
named mutex on startup; if it's already held, there's already an instance
running — we try to surface its window, then exit.

The mutex name lives in the Local\\ namespace so it's scoped to the
current logon session. Two users on the same machine each get their own
instance, which is what you'd expect.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_MUTEX_NAME = r"Local\com.lightstats.desktop.singleinstance"
_WINDOW_TITLE = "LightStats"

_ERROR_ALREADY_EXISTS = 183

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

_CreateMutexW = _kernel32.CreateMutexW
_CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_CreateMutexW.restype = wintypes.HANDLE

_FindWindowW = _user32.FindWindowW
_FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_FindWindowW.restype = wintypes.HWND

_ShowWindow = _user32.ShowWindow
_ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_ShowWindow.restype = wintypes.BOOL

_SetForegroundWindow = _user32.SetForegroundWindow
_SetForegroundWindow.argtypes = [wintypes.HWND]
_SetForegroundWindow.restype = wintypes.BOOL

_SW_RESTORE = 9


# The handle is module-global so the mutex stays alive for the whole
# process lifetime. If it were garbage-collected the lock would release
# early and a second instance could sneak in.
_held_handle: int | None = None


def acquire() -> bool:
    """Try to grab the single-instance mutex.

    Returns True if we're the first instance (caller should continue),
    False if another instance is already running (caller should exit).
    """
    global _held_handle
    if _held_handle is not None:
        return True  # already acquired earlier in this process

    handle = _CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        # Mutex creation itself failed (unusual). Don't block startup — the
        # worst case is two instances, which is still better than crashing.
        return True

    err = ctypes.get_last_error()
    if err == _ERROR_ALREADY_EXISTS:
        # Another process owns the mutex — we're the second instance.
        # Don't close our handle: keep it harmless. The OS will drop it
        # when our process exits.
        _poke_existing_window()
        return False

    _held_handle = handle
    return True


def _poke_existing_window() -> None:
    """Best-effort: find the running LightStats window and bring it forward."""
    hwnd = _FindWindowW(None, _WINDOW_TITLE)
    if hwnd:
        try:
            _ShowWindow(hwnd, _SW_RESTORE)
            _SetForegroundWindow(hwnd)
        except Exception:
            pass
