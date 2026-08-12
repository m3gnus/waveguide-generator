#!/usr/bin/env python3
"""Generate the committed macOS launcher icon using only the standard library.

The mark is two curves the application itself produces, drawn in the interface
palette: the R-OSSE flare of the reference design as a solid cream mass running
off the left, right and bottom borders, and that design's measured on-axis
response riding along its edge in the accent.

Both are sampled at uniform x below, so a pixel finds its segment by index
rather than by searching -- the whole 1024 px master is one pass with no
per-pixel scan over a couple of hundred segments. To regenerate the tables,
resample compute_rosse_profile_points and the run's spl_on_axis at these same
counts; do not hand-edit them.
"""

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


# The interface palette: Console's panel gradient, its ink, and the accent.
_GROUND_LIGHT = (40, 37, 35)
_GROUND_DARK = (21, 19, 18)
_CREAM = (236, 232, 224)
_EMBER = (224, 103, 63)

# Stroke half-width and the mark's coordinate system, both in the 64-unit
# space the SVG favicon uses, so the two stay comparable by eye.
_VIEWBOX = 64.0
_RESPONSE_HALF_WIDTH = 1.8

FLARE_Y = (
    64.000, 63.765, 63.523, 63.274, 63.018, 62.755, 62.486, 62.211,
    61.930, 61.642, 61.349, 61.050, 60.746, 60.437, 60.124, 59.805,
    59.482, 59.153, 58.820, 58.483, 58.141, 57.794, 57.444, 57.089,
    56.730, 56.366, 55.999, 55.627, 55.251, 54.871, 54.487, 54.098,
    53.705, 53.308, 52.907, 52.501, 52.091, 51.677, 51.258, 50.835,
    50.407, 49.974, 49.537, 49.095, 48.648, 48.196, 47.740, 47.277,
    46.810, 46.337, 45.859, 45.375, 44.886, 44.390, 43.889, 43.381,
    42.867, 42.345, 41.817, 41.283, 40.741, 40.191, 39.634, 39.069,
    38.496, 37.914, 37.323, 36.723, 36.113, 35.493, 34.863, 34.221,
    33.568, 32.904, 32.226, 31.534, 30.829, 30.108, 29.370, 28.616,
    27.841, 27.048, 26.231, 25.391, 24.523, 23.625, 22.694, 21.724,
    20.709, 19.642, 18.511, 17.300, 15.985, 14.525, 12.844, 10.738,
    6.000,
)

RESPONSE_Y = (
    39.000, 38.732, 38.421, 37.839, 37.221, 36.509, 35.738, 34.880,
    33.945, 32.944, 31.871, 30.764, 29.660, 28.557, 27.629, 26.738,
    26.146, 25.625, 25.356, 25.147, 25.048, 24.948, 24.847, 24.683,
    24.483, 24.208, 23.910, 23.595, 23.281, 23.042, 22.824, 22.703,
    22.606, 22.560, 22.522, 22.493, 22.467, 22.442, 22.407, 22.369,
    22.299, 22.222, 22.095, 21.962, 21.790, 21.618, 21.447, 21.289,
    21.153, 21.064, 21.016, 21.042, 21.105, 21.210, 21.327, 21.472,
    21.620, 21.784, 21.969, 22.234, 22.533, 22.898, 23.276, 23.670,
    24.061, 24.450, 24.875, 25.312, 25.775, 26.241, 26.742, 27.244,
    27.757, 28.260, 28.736, 29.236, 29.771, 30.211, 30.578, 31.048,
    31.563, 32.008, 32.437, 32.928, 33.402, 33.719, 34.070, 34.530,
    35.018, 35.550, 36.126, 36.743, 37.159, 37.464, 37.673, 37.854,
    37.859, 37.854, 37.963, 38.052, 38.056, 38.138, 38.387, 38.533,
    38.557, 38.609, 38.680, 38.549, 38.343, 38.278, 38.236,
)


def _rounded_square_distance(x: float, y: float) -> float:
    """Signed distance to the tile: negative inside, in normalised units."""
    nearest_x = min(max(x, 0.16), 0.84)
    nearest_y = min(max(y, 0.16), 0.84)
    return math.hypot(x - nearest_x, y - nearest_y) - 0.16


def _curve_y(table: tuple[float, ...], x: float) -> float:
    """Height of a uniform-x polyline at x, in viewBox units."""
    position = min(max(x, 0.0), 1.0) * (len(table) - 1)
    lower = min(int(position), len(table) - 2)
    weight = position - lower
    return table[lower] + (table[lower + 1] - table[lower]) * weight


def _segment_distance(
    x: float, y: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    dx, dy = x2 - x1, y2 - y1
    length_squared = dx * dx + dy * dy
    if not length_squared:
        return math.hypot(x - x1, y - y1)
    amount = min(1.0, max(0.0, ((x - x1) * dx + (y - y1) * dy) / length_squared))
    return math.hypot(x - (x1 + amount * dx), y - (y1 + amount * dy))


def _response_distance(x: float, y: float) -> float:
    """Distance to the response polyline, in viewBox units.

    Only the segments either side of x can be nearest, because the polyline is
    sampled at uniform x and its slope is bounded; checking a small window
    keeps this O(1) instead of a scan over every segment for every pixel.
    """
    last = len(RESPONSE_Y) - 1
    step = _VIEWBOX / last
    index = int(min(max(x, 0.0), 1.0) * last)
    nearest = float("inf")
    for j in range(max(0, index - 2), min(last, index + 3)):
        nearest = min(nearest, _segment_distance(
            x * _VIEWBOX, y * _VIEWBOX,
            j * step, RESPONSE_Y[j], (j + 1) * step, RESPONSE_Y[j + 1],
        ))
    return nearest


def _coverage(distance: float, feather: float) -> float:
    """Linear coverage across one pixel: 1 well inside, 0 well outside."""
    return min(1.0, max(0.0, 0.5 - distance / feather))


def _mix(under: tuple[float, float, float], over: tuple[int, int, int], amount: float):
    return tuple(u + (o - u) * amount for u, o in zip(under, over))


def _pixel(x: float, y: float, feather: float) -> tuple[int, int, int, int]:
    tile = _coverage(_rounded_square_distance(x, y), feather)
    if tile <= 0.0:
        return (0, 0, 0, 0)

    # The panel gradient, lit from the top-left corner down, like the favicon's.
    lift = min(1.0, max(0.0, 1.0 - (x + y) / 2.0))
    color = tuple(dark + (light - dark) * lift for dark, light in zip(_GROUND_DARK, _GROUND_LIGHT))

    # The flare, filled below its edge and cropped by the tile on three sides.
    edge = _curve_y(FLARE_Y, x) / _VIEWBOX
    color = _mix(color, _CREAM, _coverage(edge - y, feather))

    # The measured response, riding along that edge.
    span = (_response_distance(x, y) - _RESPONSE_HALF_WIDTH) / _VIEWBOX
    color = _mix(color, _EMBER, _coverage(span, feather))

    return (round(color[0]), round(color[1]), round(color[2]), round(255 * tile))


def write_png(path: Path, size: int) -> None:
    """Rasterise the mark at one size.

    Edges are antialiased from the distance functions themselves rather than by
    supersampling: a diagonal mass edge and a curved stroke both need coverage
    at every size, and 2x2 samples only ever gave five levels of it.
    """
    rows = bytearray()
    feather = 1.0 / size
    for row in range(size):
        rows.append(0)
        y = (row + 0.5) / size
        for column in range(size):
            rows.extend(_pixel((column + 0.5) / size, y, feather))
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
