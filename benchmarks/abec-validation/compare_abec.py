"""Compare our polars against an ABEC3 `Spectrum_ABEC.txt` on the shared grid.

Both sides are normalised on axis, so only pattern *shape* is compared; absolute
level and phase carry no information here. Report main-lobe statistics, not
worst-of-all-angles: on an on-axis-normalised polar the worst angle is almost
always a deep rear null, where a fraction of a dB of absolute pressure shows up
as tens of dB relative and neither code is converged.

    python compare_abec.py                     # stored corrected references
    python compare_abec.py A.txt ours.npz      # any other pair

`Spectrum_ABEC.txt` gotchas: decimal commas, and the block order is
RadImp, D, H, V -- D first, not H. Identify blocks by `Graph_Caption`, never by
position.
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PLANES = ("PM_SPL_H", "PM_SPL_V", "PM_SPL_D")  # our array's plane order
NPZ_PLANE_ALIASES = {
    "horizontal": "PM_SPL_H",
    "vertical": "PM_SPL_V",
    "diagonal": "PM_SPL_D",
}
BANDS = ((0, 1000), (1000, 4000), (4000, 11000), (11000, 20001))
MAIN_LOBE_DB = -20.0  # gate: ABEC's own normalised level


def read_abec_spectrum(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Parse an ABEC3 spectrum export into (frequency_hz, spl_db[f, plane, angle])."""
    blocks: dict[str, list[list[float]]] = {}
    caption = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "Graph_Caption" in line:
                caption = line.split("=")[-1].strip().strip("\"'")
                blocks[caption] = []
            elif caption and line.strip() and line.strip()[0].isdigit():
                blocks[caption].append(
                    [float(v.replace(",", ".")) for v in line.replace(";", " ").split()]
                )

    missing = [p for p in PLANES if p not in blocks]
    if missing:
        raise ValueError(f"{path}: missing polar blocks {missing}")

    rows = {p: np.asarray(blocks[p], dtype=np.float64) for p in PLANES}
    freq = rows[PLANES[0]][:, 0]
    for p in PLANES[1:]:
        if not np.array_equal(rows[p][:, 0], freq):
            raise ValueError(f"{path}: plane {p} has a different frequency axis")

    spl = np.stack(
        [20 * np.log10(np.abs(r[:, 1::2] + 1j * r[:, 2::2]) + 1e-300) for r in
         (rows[p] for p in PLANES)],
        axis=1,
    )
    return freq, spl


def read_ours(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Load our stored polars, normalising on axis if the file holds complex pressure."""
    with np.load(path, allow_pickle=False) as data:
        freq = data["frequency_hz"]
        if "spl_db" in data:
            polars = data["spl_db"]
        else:
            pressure = data["p"]
            polars = 20 * np.log10(np.abs(pressure / pressure[:, :, 0:1]) + 1e-300)

        if "planes" in data:
            raw_planes = np.asarray(data["planes"]).reshape(-1)
            plane_names = []
            for value in raw_planes:
                name = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                plane_names.append(NPZ_PLANE_ALIASES.get(name.lower(), name))
            if polars.ndim < 2 or len(plane_names) != polars.shape[1]:
                raise ValueError(
                    f"{path}: planes metadata count {len(plane_names)} does not match "
                    f"polar plane axis {polars.shape[1] if polars.ndim >= 2 else 'missing'}"
                )
            if len(set(plane_names)) != len(PLANES) or set(plane_names) != set(PLANES):
                raise ValueError(
                    f"{path}: planes metadata must be a permutation of {list(PLANES)}, "
                    f"got {plane_names}"
                )
            polars = polars[:, [plane_names.index(plane) for plane in PLANES], ...]

        if "angles_deg" in data:
            angles = np.asarray(data["angles_deg"], dtype=np.float64).reshape(-1)
            angle_count = polars.shape[2] if polars.ndim >= 3 else 0
            expected = np.linspace(0.0, 180.0, angle_count)
            if angles.size != angle_count or not np.allclose(
                angles, expected, rtol=0.0, atol=1e-7
            ):
                raise ValueError(
                    f"{path}: angles_deg must match a {angle_count}-point 0..180 linspace"
                )

    return freq, polars


def report(freq: np.ndarray, ours: np.ndarray, ref: np.ndarray, label: str) -> None:
    if ours.shape != ref.shape:
        raise ValueError(f"shape mismatch: ours {ours.shape} vs reference {ref.shape}")
    diff = ours - ref
    gate = ref > MAIN_LOBE_DB
    angles = np.linspace(0, 180, ours.shape[2])
    front = angles <= 90

    print(f"\n{label}")
    print(f"  all points          rms {rms(diff):6.3f}  max {np.abs(diff).max():7.2f} dB")
    print(f"  front hemisphere    rms {rms(diff[:, :, front]):6.3f}  "
          f"max {np.abs(diff[:, :, front]).max():7.2f} dB")
    print(f"  main lobe (>{MAIN_LOBE_DB:.0f} dB) rms {rms(diff[gate]):6.3f}  "
          f"max {np.abs(diff[gate]).max():7.2f} dB")
    print("\n  main-lobe rms by band, dB")
    for lo, hi in BANDS:
        band = (freq >= lo) & (freq < hi)
        if not band.any():
            continue
        sel = gate[band]
        print(f"    {lo / 1000:5.0f} - {hi / 1000:5.0f} kHz   {rms(diff[band][sel]):6.3f}")


def rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(a)))) if a.size else float("nan")


def main(argv: list[str]) -> int:
    if len(argv) == 3:
        pairs = [(argv[1], argv[2], "supplied pair")]
    elif len(argv) == 1:
        pairs = [
            (os.path.join(HERE, "abec-asro68-full-nosym-2026-07-31.txt"),
             os.path.join(HERE, "ours-asro68-full-2026-07-31.npz"),
             "full domain: ours vs ABEC3 (Sym clause removed)"),
            (os.path.join(HERE, "abec-asro2-quarter-sym-2026-07-31.txt"),
             os.path.join(HERE, "ours-asro2-quarter-2026-07-31.npz"),
             "quarter domain: ours vs ABEC3 (Sym=xy, as ATH intends)"),
        ]
    else:
        print(__doc__)
        return 2

    for abec_path, ours_path, label in pairs:
        freq, ref = read_abec_spectrum(abec_path)
        our_freq, ours = read_ours(ours_path)
        if not np.allclose(freq, our_freq, rtol=1e-9):
            raise ValueError(f"{label}: frequency axes differ")
        report(freq, ours, ref, label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
