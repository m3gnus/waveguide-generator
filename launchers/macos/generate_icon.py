#!/usr/bin/env python3
"""Generate the committed macOS launcher icon using only the standard library."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zlib


HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = (
    HERE / "Waveguide Generator.app" / "Contents" / "Resources" / "WaveguideGenerator.icns"
)
ICON_SIZES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}
ICNS_ELEMENTS = {
    b"icp4": "icon_16x16.png",
    b"icp5": "icon_32x32.png",
    b"icp6": "icon_32x32@2x.png",
    b"ic07": "icon_128x128.png",
    b"ic08": "icon_256x256.png",
    b"ic09": "icon_512x512.png",
    b"ic10": "icon_512x512@2x.png",
}


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def _inside_rounded_square(x: float, y: float) -> bool:
    nearest_x = min(max(x, 0.16), 0.84)
    nearest_y = min(max(y, 0.16), 0.84)
    return math.hypot(x - nearest_x, y - nearest_y) <= 0.16


def _segment_distance(
    x: float, y: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    dx, dy = x2 - x1, y2 - y1
    length_squared = dx * dx + dy * dy
    if not length_squared:
        return math.hypot(x - x1, y - y1)
    amount = min(1.0, max(0.0, ((x - x1) * dx + (y - y1) * dy) / length_squared))
    return math.hypot(x - (x1 + amount * dx), y - (y1 + amount * dy))


def _pixel(x: float, y: float) -> tuple[int, int, int, int]:
    if not _inside_rounded_square(x, y):
        return (0, 0, 0, 0)

    glow = max(0.0, 1.0 - math.hypot(x - 0.72, y - 0.25) / 0.8)
    red = int(18 + 16 * glow)
    green = int(48 + 45 * glow)
    blue = int(78 + 62 * glow)
    color = (red, green, blue, 255)

    # Three acoustic wavefronts radiating from the throat.
    radius = math.hypot(x - 0.34, y - 0.5)
    if x >= 0.34 and any(abs(radius - ring) < 0.014 for ring in (0.16, 0.27, 0.38)):
        color = (67, 208, 207, 255)

    # A clean white horn cross-section: throat, flaring walls, and mouth.
    line = min(
        _segment_distance(x, y, 0.25, 0.46, 0.37, 0.46),
        _segment_distance(x, y, 0.37, 0.46, 0.69, 0.30),
        _segment_distance(x, y, 0.25, 0.54, 0.37, 0.54),
        _segment_distance(x, y, 0.37, 0.54, 0.69, 0.70),
    )
    mouth = abs(math.hypot((x - 0.62) / 0.16, (y - 0.5) / 0.22) - 1.0)
    if line < 0.018 or (x >= 0.62 and mouth < 0.075):
        color = (248, 251, 255, 255)
    return color


def write_png(path: Path, size: int) -> None:
    rows = bytearray()
    samples = 2 if size < 256 else 1
    for row in range(size):
        rows.append(0)
        for column in range(size):
            totals = [0, 0, 0, 0]
            for sample_y in range(samples):
                for sample_x in range(samples):
                    x = (column + (sample_x + 0.5) / samples) / size
                    y = (row + (sample_y + 0.5) / samples) / size
                    for channel, value in enumerate(_pixel(x, y)):
                        totals[channel] += value
            divisor = samples * samples
            rows.extend(value // divisor for value in totals)
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
    png += _chunk(b"IDAT", zlib.compress(bytes(rows), level=9)) + _chunk(b"IEND", b"")
    path.write_bytes(png)


def _pack_icns(output: Path, iconset: Path) -> None:
    elements = bytearray()
    for kind, filename in ICNS_ELEMENTS.items():
        png = (iconset / filename).read_bytes()
        elements.extend(kind)
        elements.extend(struct.pack(">I", len(png) + 8))
        elements.extend(png)
    output.write_bytes(b"icns" + struct.pack(">I", len(elements) + 8) + elements)


def build(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wg2-icon-") as temporary:
        iconset = Path(temporary) / "WaveguideGenerator.iconset"
        iconset.mkdir()
        for name, size in ICON_SIZES.items():
            write_png(iconset / name, size)
        # Recent iconutil builds reject even an iconset they just unpacked. ICNS
        # itself is a small tagged container, so pack its standard PNG elements
        # deterministically, then ask iconutil to validate/unpack the result when
        # it is available. This retains a no-dependency build on every platform.
        _pack_icns(output, iconset)
        iconutil = shutil.which("iconutil")
        if iconutil is not None:
            validation = Path(temporary) / "validated.iconset"
            subprocess.run(
                (iconutil, "-c", "iconset", "-o", str(validation), str(output)),
                check=True,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="verify the committed icon")
    args = parser.parse_args()
    if not args.check:
        build(args.output)
        print(f"Generated {args.output}")
        return 0
    with tempfile.TemporaryDirectory(prefix="wg2-icon-check-") as temporary:
        generated = Path(temporary) / "WaveguideGenerator.icns"
        build(generated)
        if not args.output.is_file() or generated.read_bytes() != args.output.read_bytes():
            print(f"{args.output} is stale; rerun {Path(__file__).relative_to(HERE.parents[1])}")
            return 1
    print(f"Verified {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
