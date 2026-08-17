"""Draws icon.png and logo.png.

They are generated rather than drawn by hand so that a change is a diff in this
file instead of a binary blob nobody can review. No image library is used: the
shapes are simple, PNG is not hard to write, and adding Pillow to the build for
two pictures is not worth it.

    python tools/generate_images.py

Home Assistant wants icon.png square at 128x128 and logo.png around 250x100.

The picture is a meter dial with a needle, and three bars for the three phases.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "smartmeter"

BACKGROUND = (18, 42, 66, 255)  # deep blue, sits well on both HA themes
DIAL = (242, 193, 78, 255)  # amber
PHASE = (94, 178, 232, 255)  # light blue
CLEAR = (0, 0, 0, 0)

#: Samples per axis when rasterising. 4 is enough to hide the stair-steps at
#: these sizes and keeps the script fast enough to run on every change.
SUPERSAMPLE = 4

Colour = tuple[int, int, int, int]


# ------------------------------------------------------------------ PNG output


def write_png(path: Path, pixels: list[list[Colour]]) -> None:
    height, width = len(pixels), len(pixels[0])
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter type "none"
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8 bit RGBA
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    print(f"{path.relative_to(ROOT)}: {width}x{height}")


# --------------------------------------------------------------------- shapes


def over(top: Colour, bottom: Colour) -> Colour:
    """Alpha composite one colour onto another."""
    ta = top[3] / 255
    if ta >= 1:
        return top
    ba = bottom[3] / 255
    out_a = ta + ba * (1 - ta)
    if out_a == 0:
        return CLEAR
    return (
        *(round((top[i] * ta + bottom[i] * ba * (1 - ta)) / out_a) for i in range(3)),
        round(out_a * 255),
    )


def rounded_rect(x: float, y: float, box: tuple[float, float, float, float], radius: float) -> bool:
    left, top, right, bottom = box
    if not (left <= x <= right and top <= y <= bottom):
        return False
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    return math.hypot(x - cx, y - cy) <= radius


def ring(x: float, y: float, cx: float, cy: float, radius: float, width: float) -> bool:
    return abs(math.hypot(x - cx, y - cy) - radius) <= width / 2


def needle(
    x: float, y: float, cx: float, cy: float, angle: float, length: float, width: float
) -> bool:
    dx, dy = x - cx, y - cy
    along = dx * math.cos(angle) + dy * math.sin(angle)
    across = -dx * math.sin(angle) + dy * math.cos(angle)
    return 0 <= along <= length and abs(across) <= width / 2


def render(width: int, height: int, shader) -> list[list[Colour]]:
    step = 1 / SUPERSAMPLE
    offset = step / 2
    rows = []
    for py in range(height):
        row = []
        for px in range(width):
            r = g = b = a = 0
            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    sample = shader(px + sx * step + offset, py + sy * step + offset)
                    r += sample[0] * sample[3]
                    g += sample[1] * sample[3]
                    b += sample[2] * sample[3]
                    a += sample[3]
            count = SUPERSAMPLE**2
            row.append((round(r / a), round(g / a), round(b / a), round(a / count)) if a else CLEAR)
        rows.append(row)
    return rows


# ------------------------------------------------------------------- pictures


def dial_shader(cx: float, cy: float, radius: float):
    """A meter dial: an open ring with a needle pointing up and to the right."""
    thickness = radius * 0.18
    gap = math.radians(50)  # opening at the bottom, like a gauge

    def shade(x: float, y: float) -> Colour:
        angle = math.atan2(y - cy, x - cx)
        in_gap = abs(angle - math.pi / 2) < gap / 2
        if ring(x, y, cx, cy, radius, thickness) and not in_gap:
            return DIAL
        if needle(x, y, cx, cy, math.radians(-52), radius * 0.78, thickness * 0.85):
            return DIAL
        if math.hypot(x - cx, y - cy) <= thickness * 0.8:
            return DIAL
        return CLEAR

    return shade


def make_icon() -> None:
    size = 128
    dial = dial_shader(size / 2, size / 2 + 2, 40)

    def shade(x: float, y: float) -> Colour:
        background = BACKGROUND if rounded_rect(x, y, (0, 0, size, size), 26) else CLEAR
        return over(dial(x, y), background)

    write_png(OUT / "icon.png", render(size, size, shade))


def make_logo() -> None:
    width, height = 250, 100
    dial = dial_shader(52, 50, 33)
    # Three bars for the three phases, rising left to right.
    bars = [(112, 62, 20), (150, 44, 38), (188, 26, 56)]

    def shade(x: float, y: float) -> Colour:
        pixel = dial(x, y)
        if pixel[3]:
            return pixel
        for left, top, tall in bars:
            if rounded_rect(x, y, (left, top, left + 22, top + tall), 8):
                return PHASE
        return CLEAR

    write_png(OUT / "logo.png", render(width, height, shade))


if __name__ == "__main__":
    make_icon()
    make_logo()
