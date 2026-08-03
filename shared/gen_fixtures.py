#!/usr/bin/env python3
"""Generate the shared FRAME-SPEC v1.1 conformance corpus."""

from __future__ import annotations

import json
import struct
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.protocol.frame import DEFAULT_MAX_FRAME_BYTES, MAGIC, encode  # noqa: E402

OUT = Path(__file__).resolve().parent / "frame-fixtures"
GOOD = OUT / "good"
BAD = OUT / "malformed"


def fidelity(*, limited: bool = False) -> dict[str, Any]:
    return {
        "maxChordErrorMmRequested": 0.05,
        "maxNormalStepDegRequested": 3.0,
        "minSilhouetteSegmentsRequested": 4,
        "maxChordErrorMmAchieved": 0.04 if not limited else 0.08,
        "maxNormalStepDegAchieved": 2.5,
        "minSilhouetteSegmentsAchieved": 4,
        "vertexCapLimited": limited,
    }


def preview_header(surfaces: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    header: dict[str, Any] = {
        "kind": "preview",
        "epoch": 3,
        "seq": 412,
        "designRevision": 57,
        "lod": "fine",
        "evalMs": 2.1,
        "geometryApi": "hornlab.preview/1",
        "units": "mm",
        "coordinateFrame": "mesher-xyz",
        "fidelity": fidelity(),
        "surfaces": surfaces,
    }
    header.update(extra)
    return header


def surface(role: str, positions: str, indices: str, normals: str, **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "role": role,
        "positions": positions,
        "indices": indices,
        "normals": normals,
        "shading": "smooth",
        "normalMethod": "analytic-parametric",
    }
    value.update(extra)
    return value


def minimal_preview() -> bytes:
    arrays = OrderedDict(
        positions=np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0]], dtype=np.float32),
        indices=np.array([0, 1, 2], dtype=np.uint32),
        normals=np.array([[0, 0, 1], [0, 0, 1], [0, 0, 1]], dtype=np.float32),
    )
    return encode(
        preview_header(
            [surface("source_cap", "positions", "indices", "normals", shading="flat", normalMethod="exact-planar")]
        ),
        arrays,
    )


def grid(radius0: float, radius1: float, z0: float, z1: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rings, segments = 3, 8
    positions: list[list[float]] = []
    normals: list[list[float]] = []
    for axial in range(rings):
        t = axial / (rings - 1)
        radius = radius0 + (radius1 - radius0) * t
        z = z0 + (z1 - z0) * t
        for angular in range(segments):
            phi = 2 * np.pi * angular / segments
            c, s = float(np.cos(phi)), float(np.sin(phi))
            positions.append([radius * c, radius * s, z])
            normals.append([c, s, 0.0])
    indices: list[int] = []
    for axial in range(rings - 1):
        for angular in range(segments):
            nxt = (angular + 1) % segments
            a, b = axial * segments + angular, axial * segments + nxt
            c, d = (axial + 1) * segments + angular, (axial + 1) * segments + nxt
            indices.extend((a, c, b, b, c, d))
    return (
        np.asarray(positions, dtype=np.float32),
        np.asarray(indices, dtype=np.uint32),
        np.asarray(normals, dtype=np.float32),
    )


def multi_preview() -> bytes:
    inner = grid(8.0, 30.0, 0.0, 60.0)
    outer = grid(10.0, 32.0, -1.0, 61.0)
    arrays = OrderedDict(
        hornPositions=inner[0],
        hornIndices=inner[1],
        hornNormals=inner[2],
        outerPositions=outer[0],
        outerIndices=outer[1],
        outerNormals=outer[2],
        temperature=np.linspace(20.0, 21.0, inner[0].shape[0], dtype=np.float32),
    )
    header = preview_header(
        [
            surface("horn.inner", "hornPositions", "hornIndices", "hornNormals", closedPhi=True),
            surface("horn.outer", "outerPositions", "outerIndices", "outerNormals", closedPhi=True),
        ],
        fidelity={**fidelity(), "samplingPolicy": "adaptive-v1", "perSurfaceCaps": {"horn.inner": 4096}},
    )
    return encode(header, arrays)


def curve_frame() -> bytes:
    return encode(
        {"kind": "curve", "epoch": 4, "seq": 9, "designRevision": 8, "curve": "on-axis-spl"},
        OrderedDict(
            frequencyHz=np.array([20.0, 100.0, 1000.0, 20000.0], dtype=np.float64),
            magnitudeDb=np.array([-12.5, -3.25, 1.5, -8.0], dtype=np.float32),
        ),
    )


def result_chunk_frame() -> bytes:
    return encode(
        {"kind": "result-chunk", "jobId": "job-fixture-1", "field": "balloon", "chunk": 2},
        OrderedDict(
            thetaDeg=np.array([0, 30, 60, 90], dtype=np.float32),
            splDb=np.array([[1.0, 0.5], [0.25, -0.5], [-1.0, -2.0], [-3.0, -6.0]], dtype=np.float32),
        ),
    )


def split_frame(frame: bytes) -> tuple[dict[str, Any], bytes]:
    header_length = struct.unpack_from("<I", frame, 4)[0]
    header_end = 8 + header_length
    payload_base = (header_end + 7) & ~7
    return json.loads(frame[8:header_end]), frame[payload_base:]


def rebuild(header: dict[str, Any], payload: bytes) -> bytes:
    header_bytes = json.dumps(header, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    prefix = MAGIC + struct.pack("<I", len(header_bytes)) + header_bytes
    return prefix + bytes((-len(prefix)) % 8) + payload


def alter(base: bytes, fn: Callable[[dict[str, Any], bytearray], None]) -> bytes:
    header, payload = split_frame(base)
    mutable = bytearray(payload)
    fn(header, mutable)
    return rebuild(header, bytes(mutable))


def section(header: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in header["sections"] if item["name"] == name)


def manifest_good(file: str, description: str, frame: bytes) -> dict[str, Any]:
    header, _ = split_frame(frame)
    shapes = {item["name"]: item["shape"] for item in header["sections"]}
    checks: list[dict[str, Any]] = []
    if file == "good/minimal-preview.bin":
        checks = [{"section": "positions", "index": 3, "value": 10.0}, {"section": "normals", "index": 2, "value": 1.0}]
    elif file == "good/multi-surface-preview.bin":
        checks = [{"section": "hornPositions", "index": 0, "value": 8.0}, {"section": "outerPositions", "index": 0, "value": 10.0}]
    elif file == "good/curve.bin":
        checks = [{"section": "frequencyHz", "index": 2, "value": 1000.0}, {"section": "magnitudeDb", "index": 1, "value": -3.25}]
    else:
        checks = [{"section": "thetaDeg", "index": 3, "value": 90.0}, {"section": "splDb", "index": 7, "value": -6.0}]
    return {
        "file": file,
        "description": description,
        "outcome": "ok",
        "kind": header["kind"],
        "sectionShapes": shapes,
        "spotChecks": checks,
        "byteExactReencode": True,
    }


def main() -> None:
    GOOD.mkdir(parents=True, exist_ok=True)
    BAD.mkdir(parents=True, exist_ok=True)
    good_frames = [
        ("good/minimal-preview.bin", "Minimal single planar rendered surface", minimal_preview()),
        ("good/multi-surface-preview.bin", "Horn-like inner and outer grids with normals and fidelity metadata", multi_preview()),
        ("good/curve.bin", "Frequency-response curve frame", curve_frame()),
        ("good/result-chunk.bin", "Balloon result chunk", result_chunk_frame()),
    ]
    for relative, _description, frame in good_frames:
        (OUT / relative).write_bytes(frame)

    base = good_frames[0][2]
    malformed: list[tuple[str, str, bytes]] = []

    def add(rule: str, description: str, frame: bytes) -> None:
        malformed.append((rule, description, frame))

    add("bad-magic", "Magic is not WGF1", b"NOPE" + base[4:])
    add("unsupported-version", "Header major version is unsupported", alter(base, lambda h, p: h.__setitem__("v", 2)))
    add("frame-too-large", "Frame exceeds the default 32 MiB ceiling", base + bytes(DEFAULT_MAX_FRAME_BYTES + 1 - len(base)))
    add("header-too-large", "Declared JSON header is larger than 64 KiB", MAGIC + struct.pack("<I", 65537))
    add("header-truncated", "Declared JSON header extends beyond the message", MAGIC + struct.pack("<I", 12) + b"{}")
    raw_bad_json = b"{not-json"
    add("header-json", "Header JSON is invalid", MAGIC + struct.pack("<I", len(raw_bad_json)) + raw_bad_json + bytes((-(8 + len(raw_bad_json))) % 8))
    add("sections-invalid", "Sections is missing", alter(base, lambda h, p: h.pop("sections")))
    add("section-name", "A section name is empty", alter(base, lambda h, p: h["sections"][0].__setitem__("name", "")))
    add("section-duplicate", "Two sections use the same name", alter(base, lambda h, p: h["sections"][1].__setitem__("name", "positions")))
    add("section-dtype", "A section uses an unknown dtype", alter(base, lambda h, p: h["sections"][0].__setitem__("dtype", "f16")))
    add("section-shape", "A section shape contains a negative dimension", alter(base, lambda h, p: h["sections"][0].__setitem__("shape", [-1, 3])))
    add("section-elements", "A section shape exceeds the element ceiling", alter(base, lambda h, p: h["sections"][0].__setitem__("shape", [(1 << 28) + 1])))
    add("section-length", "Byte length disagrees with dtype and shape", alter(base, lambda h, p: h["sections"][0].__setitem__("byteLength", 4)))
    add("section-range", "A section extends beyond the payload", alter(base, lambda h, p: h["sections"][0].__setitem__("byteOffset", len(p) + 8)))
    add("section-alignment", "A section offset is not 8-byte aligned", alter(base, lambda h, p: h["sections"][0].__setitem__("byteOffset", 1)))
    add("section-order", "A gap breaks contiguous header-order layout", alter(base, lambda h, p: h["sections"][1].__setitem__("byteOffset", 48)))
    add("section-overlap", "The index section overlaps the position section", alter(base, lambda h, p: h["sections"][1].__setitem__("byteOffset", 0)))
    add("preview-surfaces", "Preview omits the surfaces model", alter(base, lambda h, p: h.pop("surfaces")))
    add("surface-invalid", "A surface entry is not an object", alter(base, lambda h, p: h.__setitem__("surfaces", [7])))
    add("surface-role", "A rendered surface has no role", alter(base, lambda h, p: h["surfaces"][0].pop("role")))
    add("surface-shading", "Surface shading is not smooth or flat", alter(base, lambda h, p: h["surfaces"][0].__setitem__("shading", "auto")))
    add("surface-normal-method", "Surface normal method is not an allowed analytic/exact method", alter(base, lambda h, p: h["surfaces"][0].__setitem__("normalMethod", "triangle-averaged")))
    add("surface-closed-phi", "closedPhi is present but not boolean", alter(base, lambda h, p: h["surfaces"][0].__setitem__("closedPhi", "yes")))
    add("surface-section-missing", "Surface references a missing position section", alter(base, lambda h, p: h["surfaces"][0].__setitem__("positions", "missingPositions")))
    add("surface-position-format", "Surface positions are not f32", alter(base, lambda h, p: section(h, "positions").__setitem__("dtype", "u32")))
    add("surface-index-format", "Surface indices are not u32", alter(base, lambda h, p: section(h, "indices").__setitem__("dtype", "i32")))
    add("normals-missing", "Rendered surface omits its normal section reference", alter(base, lambda h, p: h["surfaces"][0].pop("normals")))
    add("normals-format", "Surface normals are not f32", alter(base, lambda h, p: section(h, "normals").__setitem__("dtype", "u32")))

    def short_normals(h: dict[str, Any], p: bytearray) -> None:
        meta = section(h, "normals")
        meta["shape"] = [1, 3]
        meta["byteLength"] = 12
        del p[meta["byteOffset"] + 16 :]

    add("normals-row-mismatch", "Normal and position row counts differ", alter(base, short_normals))

    def patch_float(name: str, flat_index: int, value: float) -> Callable[[dict[str, Any], bytearray], None]:
        def apply(h: dict[str, Any], p: bytearray) -> None:
            offset = section(h, name)["byteOffset"] + flat_index * 4
            struct.pack_into("<f", p, offset, value)
        return apply

    add("positions-nonfinite", "A rendered position is NaN", alter(base, patch_float("positions", 0, float("nan"))))
    add("normals-nonfinite", "A rendered normal is infinite", alter(base, patch_float("normals", 0, float("inf"))))
    add("normals-nonunit", "A rendered normal has zero length", alter(base, patch_float("normals", 2, 0.0)))
    add("fidelity-invalid", "Required achieved fidelity metadata is absent", alter(base, lambda h, p: h["fidelity"].pop("maxChordErrorMmAchieved")))

    def missed_unreported(h: dict[str, Any], p: bytearray) -> None:
        h["fidelity"]["maxChordErrorMmAchieved"] = 0.2
        h["fidelity"].pop("vertexCapLimited", None)

    add("fidelity-cap-unreported", "Missed requested tolerance does not report vertex-cap limitation", alter(base, missed_unreported))

    def bad_index(h: dict[str, Any], p: bytearray) -> None:
        struct.pack_into("<I", p, section(h, "indices")["byteOffset"], 3)

    add("indices-out-of-range", "A sampled index equals the vertex row count", alter(base, bad_index))

    fixtures = [manifest_good(path, description, frame) for path, description, frame in good_frames]
    for rule, description, frame in malformed:
        relative = f"malformed/{rule}.bin"
        (OUT / relative).write_bytes(frame)
        fixtures.append({"file": relative, "description": description, "outcome": rule})
    manifest = {"format": "FRAME-SPEC", "version": "1.1", "fixtures": fixtures}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"generated {len(good_frames)} good and {len(malformed)} malformed fixtures")


if __name__ == "__main__":
    main()
