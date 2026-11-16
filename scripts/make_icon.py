"""Render the JARVIS app icon as PNG and multi-size ICO, using only the standard library.

    python scripts/make_icon.py

Writes src/jarvis/web/icon.png (256 px) and src/jarvis/web/icon.ico (16-256 px).
The drawing matches icon.svg: a navy tile, a dashed outer ring, a bright arc and a glowing
core. Each pixel is supersampled 4x4 so the edges stay smooth at every size.
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "jarvis" / "web"
BLUE = (63, 166, 255)
HIGHLIGHT = (159, 211, 255)
WHITE = (234, 246, 255)
TILE = (4, 12, 26)


def _sample(x: float, y: float) -> tuple[float, float, float, float]:
    """Colour (r, g, b, a in 0..1) at a point of the 64x64 design space."""
    # Rounded tile with a radius of 14.
    qx, qy = max(abs(x - 32) - 18, 0), max(abs(y - 32) - 18, 0)
    if math.hypot(qx, qy) > 14:
        return 0, 0, 0, 0
    r, g, b = (c / 255 for c in TILE)
    dx, dy = x - 32, y - 32
    dist = math.hypot(dx, dy)
    angle = (math.degrees(math.atan2(dy, dx)) + 360) % 360

    def blend(color, alpha):
        nonlocal r, g, b
        cr, cg, cb = (c / 255 for c in color)
        r, g, b = r + (cr - r) * alpha, g + (cg - g) * alpha, b + (cb - b) * alpha

    blend(BLUE, max(0.0, 1 - dist / 30) * 0.28)                 # ambient glow
    if abs(dist - 23) < 0.8 and int(angle / 7.5) % 2 == 0:      # dashed outer ring
        blend(BLUE, 0.55)
    if abs(dist - 17.5) < 1.5 and int((angle + 20) / 90) % 4 != 3 and (angle + 20) % 90 < 76:
        blend(HIGHLIGHT, 0.95)                                  # three bright arc segments
    if abs(dist - 12) < 0.6:                                    # thin inner ring
        blend(BLUE, 0.6)
    if dist < 10:                                               # glowing core
        blend(BLUE, 0.9 * (1 - dist / 10) ** 0.6)
    if dist < 4.6:
        blend(WHITE, min(1.0, (4.6 - dist) / 1.2))
    return r, g, b, 1.0


def render(size: int) -> bytes:
    """RGBA pixels, row by row, for a square icon of the given size."""
    grid = 4
    rows = bytearray()
    for py in range(size):
        rows.append(0)  # PNG filter type "none"
        for px in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(grid):
                for sx in range(grid):
                    x = (px + (sx + 0.5) / grid) * 64 / size
                    y = (py + (sy + 0.5) / grid) * 64 / size
                    r, g, b, a = _sample(x, y)
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            n = grid * grid
            alpha = acc[3] / n
            if alpha:
                rows += bytes(round(255 * acc[i] / acc[3]) for i in range(3))
            else:
                rows += b"\0\0\0"
            rows.append(round(255 * alpha))
    return bytes(rows)


def png(size: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data +
                struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) +
            chunk(b"IDAT", zlib.compress(render(size), 9)) + chunk(b"IEND", b""))


def ico(images: dict[int, bytes]) -> bytes:
    """ICO container with PNG-compressed entries (supported since Windows Vista)."""
    count = len(images)
    offset = 6 + 16 * count
    directory, blobs = b"", b""
    for size, data in sorted(images.items()):
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    return struct.pack("<HHH", 0, 1, count) + directory + blobs


def main() -> None:
    images = {size: png(size) for size in (16, 24, 32, 48, 64, 128, 256)}
    (OUT / "icon.png").write_bytes(images[256])
    (OUT / "icon.ico").write_bytes(ico(images))
    print(f"icon.png und icon.ico geschrieben nach {OUT}")


if __name__ == "__main__":
    main()
