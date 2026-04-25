"""Pack a set of PNGs into a Vista+-style `.ico`.

Used by `build.py` to generate the icon PyInstaller embeds in the .exe.
Windows Vista and later accept raw PNG bytes inside ICO entries at any
size, which sidesteps having to encode BMP + AND-mask for small sizes.
"""

from __future__ import annotations

import struct
from pathlib import Path


def _png_dims(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


def build_ico_from_pngs(pngs: list[Path]) -> bytes:
    """Return ICO file bytes containing one PNG-compressed entry per source.

    Duplicates at the same (width, height) are deduped, keeping the smaller
    file. Returns `b""` if none of the inputs are readable PNGs.
    """
    by_dim: dict[tuple[int, int], bytes] = {}
    for p in pngs:
        try:
            data = p.read_bytes()
            w, h = _png_dims(data)
        except (OSError, ValueError):
            continue
        if w <= 0 or h <= 0 or w > 256 or h > 256:
            continue
        key = (w, h)
        if key not in by_dim or len(data) < len(by_dim[key]):
            by_dim[key] = data
    if not by_dim:
        return b""

    entries = sorted(by_dim.items(), key=lambda kv: kv[0][0])
    count = len(entries)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + count * 16
    directory = bytearray()
    blob = bytearray()
    for (w, h), data in entries:
        # ICONDIR uses 0 to mean 256 — raw 256 would overflow the byte field.
        bw = 0 if w >= 256 else w
        bh = 0 if h >= 256 else h
        directory += struct.pack(
            "<BBBBHHII", bw, bh, 0, 0, 1, 32, len(data), offset
        )
        blob += data
        offset += len(data)
    return header + bytes(directory) + bytes(blob)
