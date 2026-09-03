"""Compare our axisymmetric CircSym engine against the ABEC3 reference solve.

Run with wg2's venv interpreter from this directory:

    ../../wg2/.venv/Scripts/python.exe compare.py

GEOMETRY POLICY -- the single most consequential choice in this harness.
The meridian handed to our solver is ABEC's own polyline from ``nodes.txt``, NOT
the analytic OS-SE curve that ATH generated it from. ABEC subdivides its
truncated-cone elements linearly, so the surface it actually solves lies on these
segments. Resampling the underlying analytic curve instead would compare two
different BODIES and charge the geometric difference to the solver -- a
confident, wrong verdict about our engine, and precisely the error class an
external reference exists to eliminate. Do not "improve" this by regenerating
the profile from the OS-SE parameters.

Three independent quantities are compared, because the normalized polar alone is
blind to the errors most worth catching:
  1. absolute polar    (PM_SPL_ABS) -- level and drive convention
  2. normalized polar  (PM_SPL)     -- pattern
  3. radiation impedance (RadImp)   -- geometry-independent, no observation frame
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

import hornlab_metal_bem as metal_bem
from hornlab_metal_bem.circsym import MeridianMesh
from hornlab_metal_bem.config import ObservationConfig, SolveConfig, VelocityMode

HERE = Path(__file__).resolve().parent

TAG_DOME, TAG_WALL, TAG_DISC = 2, 3, 4

# ABEC defaults, stated in its manual. air_density we can match through
# SolveConfig; the sound speed we cannot -- hornlab_metal_bem hardcodes
# _constants.SPEED_OF_SOUND = 343.0 with no config knob, so the shipped solver
# runs 0.093% slow against ABEC. Small, but reported rather than hidden, and
# an odd asymmetry given air_density is configurable.
RHO = 1.205
C_ABEC = 343.32
P_REF = 20e-6
ABEC_MESH_FREQUENCY = 40000.0  # solving.txt: MeshFrequency=40000
DRIVE_ACCELERATION = 100.0  # observation.txt: DrvType=Acceleration; Value=100


def load_nodes():
    """ABEC CircSym nodes are ``index <axial> <radial>`` in millimetres."""
    nodes = {}
    for line in (HERE / "nodes.txt").read_text().splitlines():
        m = re.match(r"\s*(\d+)\s+([-\d.]+)\s+([-\d.]+)\s*$", line)
        if m:
            nodes[int(m.group(1))] = (float(m.group(2)), float(m.group(3)))
    return nodes


def subdivide(pts, max_len):
    """Split each segment until no piece exceeds ``max_len``.

    This is NOT resampling: every inserted point lies exactly on the straight
    segment it came from, so the body is unchanged -- it is the same linear
    subdivision ABEC applies internally from ``MeshFrequency``. It has to be
    done here because ABEC refines its own input and our solver does not: the
    aperture interface arrives from ATH as ONE 157.6 mm element, which is 4.6
    wavelengths across at 10 kHz and destroys the radiated field while leaving
    the finely-meshed interior almost untouched.
    """
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, int(np.ceil(float(np.hypot(*(b - a))) / max_len)))
        for i in range(1, n + 1):
            out.append(a + (b - a) * (i / n))
    return np.asarray(out)


def build_meridian(nodes, max_len=None):
    """ABEC's polyline as a MeridianMesh, in metres, baffle plane at z = 0.

    ABEC orders the cycle dome(axis->rim) -> wall(throat->mouth) -> interface
    (rim->axis), which is already the orientation our solver wants: with the
    exterior on the right of the directed polyline the normals point into the
    horn cavity. Translating z by -120 mm puts the aperture disc at z = 0, the
    plane the coupled-IB path expects, and makes ``origin="mouth"`` coincide
    with ABEC's ``Offset=120mm`` polar centre.
    """
    def part(indices):
        return np.array(
            [(nodes[i][1] / 1000.0, nodes[i][0] / 1000.0 - 0.120) for i in indices]
        )

    dome = part(list(range(123, 128)) + [1])
    wall = part(list(range(1, 122)))
    disc = part([121, 122])
    if max_len is not None:
        dome, wall, disc = (subdivide(p, max_len) for p in (dome, wall, disc))
    pts = np.vstack([dome, wall[1:], disc[1:]])
    tags = np.concatenate(
        [
            np.full(len(dome) - 1, TAG_DOME),
            np.full(len(wall) - 1, TAG_WALL),
            np.full(len(disc) - 1, TAG_DISC),
        ]
    )
    return MeridianMesh.from_polyline(pts, tags)


def load_abec_spectrum():
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


def solve_ours(mesh, frequencies, angle_count):
    config = SolveConfig(
        velocity_sources={TAG_DOME: 1.0},
        velocity_mode=VelocityMode.VELOCITY,
        circsym_aperture_tag=TAG_DISC,
        air_density=RHO,
        observation=ObservationConfig(
            distance_m=2.0,
            angle_min_deg=0.0,
            angle_max_deg=90.0,
            angle_count=angle_count,
            planes=["horizontal"],
            origin="mouth",
        ),
    )
    return metal_bem.solve_circsym_frequencies(mesh, frequencies, config)


def db(x):
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-300))


def band_report(name, freqs, delta):
    """Per-band rms of an (F, A) or (F,) difference already in dB."""
    bands = [(0.0, 1000.0), (1000.0, 4000.0), (4000.0, 11000.0), (11000.0, 1e9)]
    parts = []
    for lo, hi in bands:
        sel = (freqs >= lo) & (freqs < hi)
        if not sel.any():
            continue
        d = delta[sel]
        label = "inf" if hi > 1e8 else f"{hi / 1000:g}"
        parts.append(f"{lo / 1000:g}-{label}k {np.sqrt(np.mean(d ** 2)):.3f}")
    print(
        f"  {name:<30} rms dB by band: {'  '.join(parts)}"
        f"   max |dev| {np.max(np.abs(delta)):.3f}"
    )


def half_angle(row, angles):
    """Interpolated -6 dB half-angle of one polar row, or None."""
    lev = db(row) - db(row[:1])
    below = np.nonzero(lev <= -6.0)[0]
    if below.size == 0:
        return None
    k = int(below[0])
    if k == 0:
        return 0.0
    x0, x1 = lev[k - 1], lev[k]
    t = (-6.0 - x0) / (x1 - x0)
    return float(angles[k - 1] + t * (angles[k] - angles[k - 1]))


def main():
    print("G8 -- our CircSym coupled infinite baffle vs the ABEC3 reference")
    print("GEOMETRY: ABEC's own polyline from nodes.txt, not the analytic OS-SE")
    print("curve, so any difference below is solver, not shape.")
    print()

    nodes = load_nodes()
    # ABEC refines its own input from MeshFrequency=40000 Hz; our solver does
    # not, so subdivide ABEC's segments the same way. Same body, same segments.
    max_len = C_ABEC / ABEC_MESH_FREQUENCY / 2.0
    mesh = build_meridian(nodes, max_len=max_len)
    abec = load_abec_spectrum()
    freqs, abs_polar = abec["PM_SPL_ABS"]
    _, norm_polar = abec["PM_SPL"]
    _, radimp = abec["RadImp"]
    angle_count = abs_polar.shape[1]
    angles = np.linspace(0.0, 90.0, angle_count)
    print(
        f"ABEC: {len(freqs)} frequencies {freqs[0]:.1f}-{freqs[-1]:.1f} Hz, "
        f"{angle_count} angles 0-90 deg"
    )
    print(
        f"ours: meridian {mesh.nodes.shape[0]} nodes / {mesh.segments.shape[0]} "
        f"segments, baffle plane at z=0, coupled-IB aperture tag {TAG_DISC}"
    )
    print()

    import hornlab_metal_bem.circsym as circsym_mod

    c_shipped = circsym_mod.SPEED_OF_SOUND
    c_used = c_shipped
    print(f"element cap {max_len * 1000:.2f} mm (ABEC's lambda/2 at "
          f"{ABEC_MESH_FREQUENCY / 1000:g} kHz); sound speed ours {c_shipped} vs "
          f"ABEC {C_ABEC} m/s, {100 * (C_ABEC - c_shipped) / C_ABEC:.3f}% apart")
    print()
    result = solve_ours(mesh, freqs, angle_count)
    # Empirically our impedance conjugates ABEC's (real parts agree to <0.5%,
    # imaginary parts are equal and opposite), so the two use opposite time
    # conventions. Conjugate ours into ABEC's before any phase-bearing compare.
    ours = np.conj(result.pressure_complex[:, 0, :])  # (F, A), horizontal plane

    # ABEC drives acceleration a = 100 m/s^2; we drive v_n = 1 m/s. Under the
    # shared e^{-iwt} convention v = i*a/omega, so our pressure scales by
    # i*100/(2*pi*f). Magnitude only matters for the level comparison.
    scale = 1j * DRIVE_ACCELERATION / (2.0 * math.pi * freqs)
    ours_scaled = ours * scale[:, None]

    print("1. ABSOLUTE polar -- level and drive convention")
    band_report("SPL delta (ours - ABEC)", freqs, db(ours_scaled) - db(abs_polar))
    print("   on-axis, dB SPL at 2 m:")
    for target in (200, 1000, 5000, 20000):
        j = int(np.argmin(np.abs(freqs - target)))
        a = 20 * math.log10(abs(abs_polar[j, 0]) / P_REF)
        o = 20 * math.log10(abs(ours_scaled[j, 0]) / P_REF)
        print(
            f"     {freqs[j]:8.1f} Hz   ABEC {a:7.2f}   ours {o:7.2f}"
            f"   delta {o - a:+.3f}"
        )
    print()

    print("2. NORMALIZED polar -- pattern")
    ours_norm = ours / ours[:, :1]
    band_report("pattern delta (ours - ABEC)", freqs, db(ours_norm) - db(norm_polar))
    print("   -6 dB half-angle:")
    for target in (1000, 4000, 10000, 20000):
        j = int(np.argmin(np.abs(freqs - target)))
        a = half_angle(norm_polar[j], angles)
        o = half_angle(ours_norm[j], angles)
        fa = "  none" if a is None else f"{a:6.2f}"
        fo = "  none" if o is None else f"{o:6.2f}"
        d = "" if (a is None or o is None) else f"   delta {o - a:+.2f} deg"
        print(f"     {freqs[j]:8.1f} Hz   ABEC {fa}   ours {fo}{d}")
    print()

    print("3. RADIATION IMPEDANCE -- geometry-independent, no observation frame")
    ours_z = np.conj(result.impedance) / (RHO * c_used)  # rho*c-normalized, as ABEC
    abec_z = radimp[:, 0]
    print("        f [Hz]      ABEC Re/Im           ours Re/Im          |delta|")
    for target in (200, 1000, 5000, 20000):
        j = int(np.argmin(np.abs(freqs - target)))
        print(
            f"     {freqs[j]:8.1f}   {abec_z[j].real:7.4f} {abec_z[j].imag:+7.4f}"
            f"     {ours_z[j].real:7.4f} {ours_z[j].imag:+7.4f}"
            f"     {abs(ours_z[j] - abec_z[j]):.4f}"
        )
    rel = np.abs(ours_z - abec_z) / np.maximum(np.abs(abec_z), 1e-12)
    print(
        f"   relative |Z| error: median {np.median(rel):.4f}  max {np.max(rel):.4f}"
    )
    print()

    print()

    # Convergence: halve the element cap. A result that moves here is not yet
    # converged and no agreement number from it should be quoted.
    fine = build_meridian(nodes, max_len=max_len / 2.0)
    fine_result = solve_ours(fine, freqs, angle_count)
    fine_p = np.conj(fine_result.pressure_complex[:, 0, :]) * scale[:, None]
    fine_n = np.conj(fine_result.pressure_complex[:, 0, :])
    fine_n = fine_n / fine_n[:, :1]
    print(f"4. CONVERGENCE -- element cap halved to {max_len * 500:.2f} mm "
          f"({fine.segments.shape[0]} segments)")
    band_report("SPL delta, fine mesh", freqs, db(fine_p) - db(abs_polar))
    band_report("pattern delta, fine mesh", freqs, db(fine_n) - db(norm_polar))
    fine_z = np.conj(fine_result.impedance) / (RHO * c_used)
    fine_rel = np.abs(fine_z - abec_z) / np.maximum(np.abs(abec_z), 1e-12)
    print(f"   relative |Z| error: median {np.median(fine_rel):.4f}  "
          f"max {np.max(fine_rel):.4f}")


if __name__ == "__main__":
    main()
