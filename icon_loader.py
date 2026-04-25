"""App icon loader.

Looks for user-supplied icons in `icon/` or `icons/`:

1. If any `.ico` file is present, use the first one (multi-res internally).
2. Otherwise, gather *all* `.png` files from the folder and pack them into
   a single multi-resolution QIcon — Qt automatically picks the sharpest
   pixmap for each context (taskbar, Task Manager, Alt-Tab, dialogs).
3. Otherwise try a `.svg` (Qt renders it at any size if the SVG plugin
   ships with the Qt install).
4. Falls back to a procedurally generated teal-dot icon so the app
   always has something to show.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap

from paths import app_dir, resource_dir


def _icon_dirs() -> tuple[Path, ...]:
    """Folders to search, in priority order.

    User-supplied icons next to the executable win over icons bundled
    inside a frozen build — so a distributed .exe can still be re-skinned
    by dropping a new `icon/` folder next to it.
    """
    candidates: list[Path] = []
    for base in (app_dir(), resource_dir()):
        for name in ("icon", "icons"):
            p = base / name
            # Deduplicate: in dev mode app_dir() == resource_dir().
            if p not in candidates:
                candidates.append(p)
    return tuple(candidates)


def _build_fallback_icon() -> QIcon:
    """Procedurally render a multi-resolution teal-dot icon."""
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            margin = max(1, size // 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(30, 36, 48))
            p.drawEllipse(margin, margin, size - 2 * margin, size - 2 * margin)
            inner = max(2, size // 4)
            p.setBrush(QColor(76, 201, 240))
            p.drawEllipse(
                (size - inner) // 2,
                (size - inner) // 2,
                inner,
                inner,
            )
        finally:
            p.end()
        icon.addPixmap(pm)
    return icon


def _collect_pngs(folder: Path) -> QIcon:
    """Load every PNG in `folder` into a single multi-res QIcon.

    This handles the common pattern where icons are exported as a set of
    size-suffixed files like `myapp-16.png`, `myapp-32.png`, `myapp-256.png`.
    QIcon picks the best pixmap automatically at render time based on each
    widget's requested size and its logicalDpi.
    """
    icon = QIcon()
    added = 0
    # Sort so smaller sizes are added first (not required by Qt, but makes
    # debugging prints predictable).
    for path in sorted(folder.glob("*.png")):
        pm = QPixmap(str(path))
        if pm.isNull():
            continue
        icon.addPixmap(pm)
        added += 1
    return icon if added > 0 else QIcon()


def _try_single_file(folder: Path, extensions: tuple[str, ...]) -> Optional[QIcon]:
    """Return the first readable file in `folder` matching an extension."""
    for ext in extensions:
        for path in sorted(folder.glob(f"*{ext}")):
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return None


_cached: Optional[QIcon] = None


def app_icon() -> QIcon:
    """Return the app icon, preferring user files then falling back to a dot."""
    global _cached
    if _cached is not None:
        return _cached

    for folder in _icon_dirs():
        if not folder.is_dir():
            continue

        # Prefer .ico — Windows stores all resolutions in one file.
        ico = _try_single_file(folder, (".ico",))
        if ico is not None:
            _cached = ico
            return ico

        # Pack every PNG in the folder into one multi-res icon.
        png_icon = _collect_pngs(folder)
        if not png_icon.isNull() and png_icon.availableSizes():
            _cached = png_icon
            return png_icon

        # Last try: an SVG (Qt renders it at request-time).
        svg = _try_single_file(folder, (".svg",))
        if svg is not None:
            _cached = svg
            return svg

    _cached = _build_fallback_icon()
    return _cached
