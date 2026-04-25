"""User settings persisted to config.json next to main.py.

Only controls which widgets are visible on the overlay (plus a few
general prefs). Ping targets, intervals, retention live in code — if
users want to tune those, they edit main.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from typing import Dict

from paths import app_dir

CONFIG_PATH = app_dir() / "config.json"


# Widget keys must match what the overlay renders and the metric names
# the system worker writes. Keep them stable — config files in the wild
# reference them by string.
DEFAULT_WIDGETS: Dict[str, bool] = {
    "ping": True,
    "network": True,
    "cpu": True,
    "memory": True,
    "gpu": True,
    "disk_io": False,
    "uptime": True,
    "adapter": True,
}


DEFAULT_FONT_SIZE = 11
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 22


@dataclass
class Config:
    widgets: Dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_WIDGETS))
    font_size: int = DEFAULT_FONT_SIZE

    # 0/0 means "no saved size — let the overlay auto-fit on first launch".
    # Populated when the user resizes via the bottom-right grip or after the
    # first adjustSize(). Persisted so the app reopens at the same size.
    window_width: int = 0
    window_height: int = 0

    def widget_enabled(self, key: str) -> bool:
        return self.widgets.get(key, DEFAULT_WIDGETS.get(key, False))

    def clamped_font_size(self) -> int:
        v = int(self.font_size) if self.font_size else DEFAULT_FONT_SIZE
        return max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, v))

    def saved_size(self) -> tuple[int, int] | None:
        if self.window_width > 0 and self.window_height > 0:
            return (self.window_width, self.window_height)
        return None


def load() -> Config:
    """Read config.json; return defaults on any problem."""
    if not CONFIG_PATH.exists():
        return Config()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return Config()
    widgets = dict(DEFAULT_WIDGETS)
    # Merge only known keys so a renamed/removed widget won't leak.
    for k, v in (raw.get("widgets") or {}).items():
        if k in DEFAULT_WIDGETS and isinstance(v, bool):
            widgets[k] = v

    font_size = raw.get("font_size", DEFAULT_FONT_SIZE)
    try:
        font_size = int(font_size)
    except (TypeError, ValueError):
        font_size = DEFAULT_FONT_SIZE

    try:
        window_width = int(raw.get("window_width", 0) or 0)
        window_height = int(raw.get("window_height", 0) or 0)
    except (TypeError, ValueError):
        window_width = window_height = 0

    return Config(
        widgets=widgets,
        font_size=font_size,
        window_width=window_width,
        window_height=window_height,
    )


def save(cfg: Config) -> None:
    """Write atomically — a crashed write shouldn't corrupt config."""
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
