"""Repeatable check: WG's full-3D Metal infinite-baffle solve against ABEC's G8.

Manual script, not part of the test suite. Run it with WG's pinned environment
from the repository root (about 20 s on Metal, plus a few seconds of meshing):

    .venv/bin/python docs/validation/abec-g8-circsym-ib/compare_3d.py

It builds G8 through WG's own library (text config -> mesher -> Metal
adapter; the observation arc is set directly, so the request layer is not
exercised), at infinite-baffle aperture scale 1.0 and an 8 mm
mouth cap, solves real-k on ABEC's own 100 frequencies with a quarter model, and
compares with ``Results/Spectrum_ABEC.txt``. Unlike ``compare.py`` it uses WG's
mesh of the ATH parameters, not ABEC's ``nodes.txt`` -- the point is to check
what a user's design produces -- so it does not need the meridian trick, and it
imports nothing from ``compare.py`` (which needs the axisymmetric engine).

Exit status is 0 when every assertion holds, 1 otherwise. ``--mouth`` and
``--no-assert`` exist for sensitivity runs (a 4.3 mm mouth is ABEC's own cap
and takes about two minutes); the assertions are calibrated for the default.

DRIVE. WG solves unit acceleration; ABEC's project drives the throat dome with
``Acceleration = 100`` (``observation.txt``). Pressure is linear in the drive, so
ours is multiplied by 100 before any absolute comparison. Pattern comparisons
are normalised on axis and independent of it. The throat impedance, which is
drive independent, is compared as well and shows the drive is right.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

# ABEC constants (its manual defaults; ``compare.py`` sets the same on the solver).
DRIVE_ACCELERATION = 100.0  # observation.txt: DrvType=Acceleration; Value=100
RHO = 1.205
C_ABEC = 343.32

ANGLES = np.linspace(0.0, 90.0, 19)  # PolarRange 0-90, 19 points
FIRST_HALF_ANGLE_STEP = 5.0

# The ATH parameters of ``g8_osse_circsym_ib.cfg``, plus the 3-D mesh items ATH
# ignores for CircSym. Quarter model, infinite baffle.
G8_CFG = """
Throat.Diameter = 25.4
Throat.Angle = 10
Throat.Profile = 1
Coverage.Angle = 45
Length = 120
OS.k = 1
Term.s = 0.6
Term.n = 4
Term.q = 0.98
Mesh.AngularSegments = 40
Mesh.LengthSegments = 20
Mesh.ThroatResolution = 6
Mesh.MouthResolution = {mouth}
Mesh.RearResolution = 40
Mesh.WallThickness = 5
Mesh.Quadrants = 1
Mesh.ApertureResolutionScale = 1.0
ABEC.SimType = 1
ABEC.f1 = 200
ABEC.f2 = 20000
ABEC.NumFrequencies = 100
"""

# Thresholds (the STUDY-E findings, real-k, 8 mm mouth, quarter model).
LOW_BAND_HZ = 4000.0
ABS_RMS_LIMIT_DB = 0.3
PATTERN_RMS_LIMIT_DB = 0.2
HALF_ANGLE_LIMIT_DEG = 2.0
HALF_ANGLE_UP_TO_HZ = 12000.0
HALF_ANGLE_MIN_HZ = 2047.0
HALF_ANGLE_MIN_WINDOW_HZ = (1200.0, 3500.0)
IMPEDANCE_MEDIAN_LIMIT = 0.03


# Copied from ``compare.py`` (which imports metal-bem's axisymmetric engine at the
# top and so cannot be imported here). Keep the two parsers in step.
def load_abec():
    """Parse Spectrum_ABEC.txt into ``caption -> (frequencies, complex (F, A))``.

    Rows are ``frequency, (re, im) x A``. This export writes ``.`` decimals; the
    older ASRO reference files write ``,``, so both are accepted.
    """
    text = (HERE / "Results" / "Spectrum_ABEC.txt").read_text(errors="replace")
    out = {}
    caption = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("Graph_Caption="):
            caption = line.split("=", 1)[1].strip().strip('"')
        elif line == "Data":
            freqs, rows = [], []
            i += 1
            while i < len(lines) and lines[i].strip() != "Data_End":
                parts = lines[i].replace(",", ".").split()
                if len(parts) >= 3:
                    values = [float(v) for v in parts]
                    freqs.append(values[0])
                    pairs = values[1:]
                    rows.append(
                        [
                            complex(pairs[k], pairs[k + 1])
                            for k in range(0, len(pairs) - 1, 2)
                        ]
                    )
                i += 1
            if caption is not None and rows:
                out[caption] = (np.asarray(freqs), np.asarray(rows, dtype=complex))
        i += 1
    return out


def db(x):
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-300))


def half_angle(row):
    """First -6 dB crossing of a normalised pattern, linear between the 5 degree samples."""
    level = db(row) - db(row[0])
    below = np.nonzero(level <= -6.0)[0]
    if below.size == 0:
        return np.nan
    k = int(below[0])
    if k == 0:
        return 0.0
    t = (-6.0 - level[k - 1]) / (level[k] - level[k - 1])
    return float(ANGLES[k - 1] + t * FIRST_HALF_ANGLE_STEP)


def solve_ours(frequencies, mouth_mm):
    """Solve G8 through WG's library. Returns complex pressure (F, A), Zin, stats, seconds."""
    from server.design import textcfg
    from server.jobs.models import PolarConfig
    from server.mesh.builder import build_solver_mesh
    from server.solver import metal as wg_metal
    from server.solver.context import SolverContext

    captured: dict = {}
    original = wg_metal.build_solver_response

    def capture(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    wg_metal.build_solver_response = capture
    try:
        design = textcfg.parse(G8_CFG.format(mouth=mouth_mm)).design
        mesh = asyncio.run(build_solver_mesh(design, {}))
        polar = PolarConfig(
            angle_range=(0.0, 90.0, 19),
            distance=2.0,
            norm_angle=0.0,
            enabled_axes=["horizontal"],
            field_plane=False,
        ).model_dump(mode="json")
        context = SolverContext(
            design=design,
            frequency_range=(float(frequencies[0]), float(frequencies[-1])),
            num_frequencies=len(frequencies),
            frequencies_hz=tuple(float(f) for f in frequencies),
            quadrants=1,
            sim_type=1,
            polar_config=polar,
        )
        started = time.time()
        out = wg_metal.solve_metal_from_msh_text(
            mesh["msh_text"], context, mesh_metadata=mesh["metadata"], mesh_stats=mesh["stats"]
        )
        seconds = time.time() - started
    finally:
        wg_metal.build_solver_response = original
    result = captured["result"]
    plane = list(result.observation_planes).index("horizontal")
    meta = out["metadata"]["metal"]
    return {
        "p": np.asarray(result.pressure_complex)[:, plane, :],
        "z": np.asarray(result.impedance),
        "freqs": np.asarray(result.frequencies_hz),
        "triangles": int(mesh["stats"]["triangle_count"]),
        "aperture_triangles": int(mesh["stats"]["dense_solver_aperture_triangle_count"]),
        "aperture_scale": mesh["metadata"]["apertureMeshResolutionScale"],
        "formulation": (meta["formulation"], meta["complex_k_shift"]),
        "seconds": seconds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mouth", type=float, default=8.0, help="Mesh.MouthResolution in mm (default 8)")
    parser.add_argument("--no-assert", action="store_true", help="print the numbers, always exit 0")
    args = parser.parse_args()

    abec = load_abec()
    freqs, p_abs = abec["PM_SPL_ABS"]
    _, p_norm = abec["PM_SPL"]
    _, z_abec = abec["RadImp"]
    assert len(freqs) == 100 and p_abs.shape == (100, 19), (len(freqs), p_abs.shape)

    import hornlab_metal_bem

    ours = solve_ours(freqs, args.mouth)
    print(f"hornlab_metal_bem: {hornlab_metal_bem.__file__}")
    print(
        f"mesh: {ours['triangles']} triangles, {ours['aperture_triangles']} on the aperture, "
        f"aperture scale {ours['aperture_scale']}, mouth {args.mouth:g} mm; "
        f"formulation {ours['formulation'][0]} (complex_k shift {ours['formulation'][1]}); "
        f"solve {ours['seconds']:.1f} s"
    )
    assert np.allclose(ours["freqs"], freqs, rtol=1e-9), "solved frequencies differ from ABEC's"

    # The drive: unit acceleration solved, ABEC ran 100.
    p = ours["p"] * DRIVE_ACCELERATION
    ours_abs, abec_abs = db(p), db(p_abs)
    ours_norm, abec_norm = db(ours["p"] / ours["p"][:, :1]), db(p_norm)
    f = freqs

    # Throat radiation impedance, drive independent: p/(-i w u) with u the unit velocity.
    z_ours = np.conj(ours["z"] * (-1j) * 2.0 * np.pi * f) / (RHO * C_ABEC)
    z_rel = np.abs(z_ours - z_abec[:, 0]) / np.abs(z_abec[:, 0])

    low = f < LOW_BAND_HZ
    rms = lambda a: float(np.sqrt(np.mean(a**2)))  # noqa: E731
    abs_rms = rms((ours_abs - abec_abs)[low])
    pattern_rms = rms((ours_norm - abec_norm)[low])
    on_axis_offset = float(np.median(ours_abs[:, 0] - abec_abs[:, 0]))

    half_ours = np.array([half_angle(row) for row in ours["p"]])
    half_abec = np.array([half_angle(row) for row in p_norm])
    band = f <= HALF_ANGLE_UP_TO_HZ
    both = band & np.isfinite(half_ours) & np.isfinite(half_abec)
    half_diff = np.abs(half_ours - half_abec)[both]
    half_diff_max = float(half_diff.max()) if half_diff.size else float("nan")
    lo, hi = HALF_ANGLE_MIN_WINDOW_HZ
    window = (f >= lo) & (f <= hi)
    min_ours = float(f[window][np.nanargmin(half_ours[window])]) if np.isfinite(half_ours[window]).any() else float("nan")
    min_abec = float(f[window][np.nanargmin(half_abec[window])]) if np.isfinite(half_abec[window]).any() else float("nan")
    grid_step = float(f[1] / f[0])

    print(f"\nbelow {LOW_BAND_HZ:g} Hz, all 19 angles ({int(low.sum())} frequencies)")
    print(f"  absolute SPL rms  {abs_rms:.3f} dB   (limit {ABS_RMS_LIMIT_DB})   median on-axis offset {on_axis_offset:+.3f} dB")
    print(f"  pattern rms       {pattern_rms:.3f} dB   (limit {PATTERN_RMS_LIMIT_DB})")
    print(f"-6 dB half-angle up to {HALF_ANGLE_UP_TO_HZ / 1000:g} kHz ({int(both.sum())} frequencies)")
    print(f"  max |ours - ABEC| {half_diff_max:.2f} deg   (limit {HALF_ANGLE_LIMIT_DEG})   rms {rms(half_diff) if half_diff.size else float('nan'):.2f} deg")
    print(f"half-angle minimum in {lo:g}-{hi:g} Hz: ours {min_ours:.0f} Hz, ABEC {min_abec:.0f} Hz (expected {HALF_ANGLE_MIN_HZ:.0f}, one grid step = x{grid_step:.4f})")
    print(f"throat impedance vs ABEC RadImp: median relative error {np.median(z_rel):.4f}, max {z_rel.max():.4f}")
    for lo_hz, hi_hz in ((200, 1000), (1000, 4000), (4000, 11000), (11000, 20001)):
        m = (f >= lo_hz) & (f < hi_hz)
        print(
            f"  {lo_hz:5d}-{hi_hz:5d} Hz  abs rms {rms((ours_abs - abec_abs)[m]):.2f}  "
            f"pattern rms {rms((ours_norm - abec_norm)[m]):.2f}  (max abs {np.abs(ours_abs - abec_abs)[m].max():.2f})"
        )

    within_step = lambda x: np.isfinite(x) and abs(np.log(x / HALF_ANGLE_MIN_HZ)) <= np.log(grid_step) * 1.0001  # noqa: E731
    checks = [
        ("real-k formulation, no shift", ours["formulation"] == ("standard", 0.0)),
        (f"absolute rms < {ABS_RMS_LIMIT_DB} dB below {LOW_BAND_HZ:g} Hz", abs_rms < ABS_RMS_LIMIT_DB),
        (f"pattern rms < {PATTERN_RMS_LIMIT_DB} dB below {LOW_BAND_HZ:g} Hz", pattern_rms < PATTERN_RMS_LIMIT_DB),
        (f"-6 dB half-angle within {HALF_ANGLE_LIMIT_DEG} deg up to {HALF_ANGLE_UP_TO_HZ / 1000:g} kHz", bool(half_diff.size and half_diff_max <= HALF_ANGLE_LIMIT_DEG)),
        (f"half-angle minimum at {HALF_ANGLE_MIN_HZ:.0f} Hz +- one grid step", within_step(min_ours) and within_step(min_abec)),
        (f"throat impedance median error < {IMPEDANCE_MEDIAN_LIMIT}", bool(np.median(z_rel) < IMPEDANCE_MEDIAN_LIMIT)),
    ]
    print()
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    failed = [name for name, ok in checks if not ok]
    print("\nG8 3-D check:", "all assertions hold" if not failed else f"{len(failed)} failed")
    return 0 if args.no_assert or not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
