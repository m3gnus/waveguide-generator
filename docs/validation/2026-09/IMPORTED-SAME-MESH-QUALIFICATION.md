# Imported CAD on Metal and BEAT-CPU: same-mesh qualification

Status: a dated measurement, 2026-09-13, at `waveguide-generator` `31856070` on
Darwin arm64 (Python 3.13.1), with `hornlab-metal-bem` `e7e32d0`,
`hornlab-beat-bem` `74da18c` and `hornlab-bempp-bem` `57b1260`. It describes that
commit and those pins, not a current release status.

Imported CAD geometry used to solve on Metal only. The BEAT-CPU imported path
(`server/solver/beat_imported.py`) now takes the same verified record. This record
answers one question: **given the same mesh, source tags, anchor frame, excitation
and frequencies, do Metal and BEAT-CPU give the same complex answer, within a
tolerance derived from how accurate each one is?**

Every judged row below passes. Rows with no tolerance are reported, not judged:
discretisation errors, which define the tolerances, and two ingest findings.

Reproduce on a host with Metal and a provisioned BEAT CPU runtime:

```bash
python scripts/qualify_imported_same_mesh.py --json result.json --markdown result.md
```

The machine-readable result of this run is
[`imported-same-mesh-qualification.json`](imported-same-mesh-qualification.json).
`scripts/tests/test_qualify_imported_same_mesh.py` checks the harness itself: the
geometry, the analytic references, the error measure and the fixture. It runs
without a solver. `WG2_QUALIFY_IMPORTED=1` adds the real run to that file.

## The rotated frame against the analytic answer

This is the fixture most likely to hide a mistake, so it comes first. An
oscillating sphere and its anchor frame are rotated by 70° about (0.3, 1, 0.2)
and translated by (0.12, −0.04, 0.055) m. The solve must undo that, because
polar cuts and the DI sphere are frame-relative. BEAT additionally needs the
rigid rotation that maps the record's frame onto its own +z/+x/+y.

| Engine | Moved vs unmoved (tolerance 1e-3) | Moved vs analytic | Tolerance |
| --- | --- | --- | --- |
| Metal | 1.84e-06 | **1.76e-02** | 1.86e-02 |
| BEAT-CPU | 1.77e-06 | **1.72e-02** | 1.82e-02 |

The moved solve equals the unmoved one to single precision, so the rotation
itself adds nothing. The analytic tolerance is each engine's *own* unmoved error
at that density plus the exact-equivalence tolerance. That is why the margin
looks thin: the row asks "does moving the body change the error?", and the
answer is no. The error against the analytic answer (1.7e-2) is the
discretisation error of the 960-triangle sphere, identical to the unmoved row
below. It is not a frame error. A frame, sign or axis mistake reads between
about 1 and 2.

An off-axis cap checks that u and v map as well as the axis. Moved vs unmoved
reads 1.98e-06 on Metal and 5.91e-07 on BEAT-CPU. Its horizontal and vertical
cuts differ by 1.99, as an asymmetric source must.

## How the tolerances are derived

- **Discretisation error, from the analytic spheres.** A 0.1 m sphere is solved
  at three densities, 224, 960 and 3,968 triangles, as a pulsating sphere and as
  a sphere oscillating along its axis. Both have closed forms (e^{-iωt}, per unit
  acceleration):
  - pulsating: `ρa²/r · e^{ik(r−a)}/(1−ika)`;
  - oscillating: `−ρ cosθ · h₁(kr)/(k h₁′(ka))`.

  The error is the complex L2 error over every polar-cut and DI-sphere point,
  per frequency, and the worst frequency is reported.

  | Engine | Body | 224 tri | 960 tri | 3,968 tri |
  | --- | --- | --- | --- | --- |
  | Metal | pulsating | 5.08e-02 | 1.24e-02 | 5.61e-03 |
  | Metal | oscillating | 6.81e-02 | 1.76e-02 | 5.27e-03 |
  | BEAT-CPU | pulsating | 5.41e-02 | 1.35e-02 | 3.36e-03 |
  | BEAT-CPU | oscillating | 6.71e-02 | 1.72e-02 | 4.33e-03 |

  BEAT-CPU's error falls about 4× per 4.3× triangles, which is second order in
  element size. Metal's does too down to 960 triangles, then only 2.2× to 3,968,
  levelling near 5e-3. That is consistent with Metal's complex-wavenumber
  formulation (`complex_k`, shift 0.005) setting an accuracy floor of that size.
  It is an observation, not a proven cause.
- **Same-mesh tolerance: 1.5 × (worst per-frequency sum of both engines' errors
  at that density).** Two solutions that are each within e₁ and e₂ of the truth
  are within e₁ + e₂ of each other: the triangle inequality. The 1.5 covers only
  applying a sphere's envelope to another body. The results are 2.03e-01 at 224
  triangles, 5.22e-02 at 960 and 1.35e-02 at 3,968. Same-mesh sphere fixtures run
  at 960.
- **Exact equivalence: 1e-3.** This covers comparisons of one engine with
  itself:
  - linearity (two tags' bases sum to their merged tag);
  - a mirror image;
  - a reduced domain against the whole;
  - the same triangles moved.

  These differ only by single-precision assembly: measured up to 7e-5 on BEAT
  and 1e-5 on Metal. 1e-3 is about fifteen times that, and three orders below
  the smallest error a frame, sign or tag mistake produced in these fixtures.
- **Horn tolerance, from the horn's own refinement.** A real return has no
  formula, so the reference density's error is estimated from a ladder of the
  same return.
  - The ladder uses the surface deviation, which is what sizes an imported mesh.
    WG accepts 0.1 to 0.35 mm, so the ladder covers every density a user can
    request: 0.35 mm (248 triangles in the quarter), the default 0.15 mm (470)
    and 0.1 mm (628).
  - The finest is only 1.3× the reference, so the reference-to-finest distance
    understates the reference's error. Richardson extrapolation at the
    sphere-measured order 2 (error ∝ 1/N) scales it by 3.97.
  - That gives estimated reference errors of 7.95e-02 on Metal and 1.39e-02 on
    BEAT-CPU, and a same-mesh horn tolerance of 1.5 × the worst per-frequency
    sum, 1.40e-01.
  - **The order check does not hold for Metal.** Order 2 predicts the coarse
    level to sit 4.56× as far from the finest as the reference does. BEAT-CPU
    reads 8.42: it converges faster than assumed, so its estimate is
    conservative. Metal reads 2.12, which is below what *any* positive order
    predicts for these three densities (at least 3.2). Metal's horn ladder is
    not in its asymptotic range, and its Richardson estimate is indicative only.
    The same-mesh verdict does not depend on it: the two engines differ by
    1.82e-02, 7.7× inside the tolerance.

## Results

**Same mesh, Metal vs BEAT-CPU**

| Fixture | Worst | Tolerance |
| --- | --- | --- |
| Hemispheres (960 tri), normal motion, one channel | 4.80e-03 | 5.22e-02 |
| Hemispheres, normal, two channels: top / bottom / channel sum | 3.51e-03 / 3.51e-03 / 4.80e-03 | 5.22e-02 |
| Hemispheres, axial motion, one channel | 4.12e-03 | 5.22e-02 |
| Hemispheres, axial, two channels: top / bottom / channel sum | 3.24e-03 / 3.24e-03 / 4.12e-03 | 5.22e-02 |
| Rotated + translated off-axis cap, all points | 2.98e-03 | 5.22e-02 |
| Off-axis cap, source-average pressure (impedance) | 5.04e-03 | 5.22e-02 |
| Horn quarter from a real return (470 tri), normal | 1.82e-02 | 1.40e-01 |
| Horn quarter from a real return, axial | 1.82e-02 | 1.40e-01 |

Every comparison is complex, at every polar-cut and DI-sphere point, per channel
and for the channel sum. Magnitudes alone are never compared.

**Exact equivalences, each engine against itself (tolerance 1e-3)**

| Fixture | Metal | BEAT-CPU |
| --- | --- | --- |
| Two-channel sum vs one merged channel (worst of 3 densities) | 7.15e-06 | 9.92e-06 |
| Repeated HF: two bodies, two-channel sum vs one channel | 7.88e-07 | 8.99e-07 |
| Repeated HF: left mirrors right in the horizontal cut | 6.66e-07 | 8.11e-07 |
| x0 half vs whole, normal / axial | 4.85e-06 / 7.94e-06 | 4.36e-05 / 5.65e-05 |
| x0+y0 quarter vs whole, normal / axial | 7.34e-06 / 7.95e-06 | 6.98e-05 / 5.07e-05 |
| Return edited in CAD (acknowledged) vs unedited | 2.88e-07 | 0 |

**Real returns through WG's own ingest**

| Fixture | Result |
| --- | --- |
| WG's auto quarter vs the forced full domain (1,856 tri) | Metal 1.13e-02 (tolerance 2.38e-01); BEAT-CPU 1.19e-03 (tolerance 4.17e-02). The bound is twice each engine's horn error estimate, because these are two meshes of one body. |
| A y-only half, through the real plan | BEAT-CPU is refused at submission: `imported_symmetry_unsupported_by_engine`, "it cannot mirror this return's y-only half (mirrored on y = 0); it solves full, half-yz, quarter". Metal solves it, and AUTO picks Metal. |
| A linked return copied into a fresh app data dir | The fixture uses paths with spaces and non-ASCII characters (`Kopia från annan dator/Högtalare ÅÄÖ.wgreturn`, `App Data – Ärende 1`), with no design registry and no cached mesh. It is imported, prepared, solved through the job runtime, stored and reopened, on both engines. Both jobs are `complete`, the reopened results are identical, and the engine metadata names the engine that ran. Findings: `freshness` / `missing_design` (blocking, acknowledged), `stale-detection-unavailable`, `source-paint-missing` (blocking, acknowledged: the fixture tags geometry, not paint). |
| A return whose body was edited in CAD | The `freshness` / `body_modified` finding is blocking and acknowledged, and the solve matches the unedited return exactly. |

## Two ingest findings, not engine results

- **A return placed in CAD cannot be qualified yet.** This covers the same
  instance rotated and translated in the assembly. Ingest refuses it before any
  engine runs: "anchor throat face did not resolve after placement; nearest
  area-compatible face … (planar=False)".
  - WG normalises a placed anchor with gmsh's general `affineTransform`. That
    rewrites the planar throat as a B-spline, and the linked-throat gate
    requires a plane.
  - The fixture stays in the harness and runs the day ingest keeps the plane.
  - The record-level rotated fixtures above do cover the engines' frame
    handling.
- **A tagged source that faces away from the fluid inverts WG's quarter.**
  - **Fixture:** a horn whose membrane is a rounded cap. Its only planar face is
    the plug's rear, 4 mm behind the throat and facing away from the bore, and
    the linked-throat contract binds to it.
  - **Result:** the forced full domain keeps the closed body's outward winding.
    The auto quarter is re-oriented so the source normal points along +z. Every
    triangle is flipped (`flipped_global` 472 of 472), with
    `orientation_valid: true`, no warning and no finding.
  - **Consequence:** Metal and BEAT-CPU then solve an inside-out surface, and
    their quarter-vs-full errors read 1.04 and 1.00. Neither answer means
    anything.
  - **Where it arises:** in the mesher's reduced-component orientation, not in
    either engine.
  - **Real returns:** a real return tags the planar throat disc, which faces the
    bore; the add-in strips paint that spreads to the rear cap. The
    qualification fixture does the same (flat membrane), and a test holds it to
    that. WG's own `server/tests/test_cadlink_ingest_symmetry.py` builds the
    rear-cap body and asserts only `orientation_valid`.

## Interior resonances

Both engines use a formulation that is meant to be robust at the interior
resonances of a closed body: Metal complex-wavenumber, BEAT Burton–Miller. The
sphere frequencies (100–1,500 Hz, ka 0.18 to 2.7) stay below the sphere's first
interior Dirichlet resonance, 1,715 Hz (ka = π), deliberately. There the two
formulations are expected to differ, and a same-mesh tolerance derived below it
would not apply. The horn frequencies (300, 1,000 and 3,000 Hz) are far below the
first resonance of its 4 mm walls. No row here probes a resonance.

## Not covered here

- **BEMPP.** It has no imported path at this commit. On this host its assembly
  backend is numba, which is never qualified.
- **Windows and Linux.** This run is macOS. The packaged fresh-install gate on all
  three platforms is a separate change to the RC build.
- **A return placed in CAD.** Blocked at ingest, as above.

## The generated table

Unedited output of the run (`--markdown`).

| Fixture | Engine | Against | Quantity | Worst | Tolerance | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| pulsating sphere L0 (224 tri), one channel | metal | analytic | complex, all points | 5.08e-02 |  |  |
| pulsating sphere L0 (224 tri), two channels | metal | analytic | complex, all points | 5.08e-02 |  |  |
| pulsating sphere L0, two-channel sum | metal | one channel | complex, all points | 7.38e-07 | 1.00e-03 | pass |
| pulsating sphere L0 (224 tri), one channel | beat-cpu | analytic | complex, all points | 5.41e-02 |  |  |
| pulsating sphere L0 (224 tri), two channels | beat-cpu | analytic | complex, all points | 5.41e-02 |  |  |
| pulsating sphere L0, two-channel sum | beat-cpu | one channel | complex, all points | 8.43e-07 | 1.00e-03 | pass |
| oscillating sphere L0 (224 tri), one tag | metal | analytic | complex, all points | 6.81e-02 |  |  |
| oscillating sphere L0 (224 tri), one tag | beat-cpu | analytic | complex, all points | 6.71e-02 |  |  |
| pulsating sphere L1 (960 tri), one channel | metal | analytic | complex, all points | 1.24e-02 |  |  |
| pulsating sphere L1 (960 tri), two channels | metal | analytic | complex, all points | 1.24e-02 |  |  |
| pulsating sphere L1, two-channel sum | metal | one channel | complex, all points | 2.18e-06 | 1.00e-03 | pass |
| pulsating sphere L1 (960 tri), one channel | beat-cpu | analytic | complex, all points | 1.35e-02 |  |  |
| pulsating sphere L1 (960 tri), two channels | beat-cpu | analytic | complex, all points | 1.35e-02 |  |  |
| pulsating sphere L1, two-channel sum | beat-cpu | one channel | complex, all points | 3.02e-06 | 1.00e-03 | pass |
| oscillating sphere L1 (960 tri), one tag | metal | analytic | complex, all points | 1.76e-02 |  |  |
| oscillating sphere L1 (960 tri), one tag | beat-cpu | analytic | complex, all points | 1.72e-02 |  |  |
| pulsating sphere L2 (3968 tri), one channel | metal | analytic | complex, all points | 5.61e-03 |  |  |
| pulsating sphere L2 (3968 tri), two channels | metal | analytic | complex, all points | 5.61e-03 |  |  |
| pulsating sphere L2, two-channel sum | metal | one channel | complex, all points | 7.15e-06 | 1.00e-03 | pass |
| pulsating sphere L2 (3968 tri), one channel | beat-cpu | analytic | complex, all points | 3.36e-03 |  |  |
| pulsating sphere L2 (3968 tri), two channels | beat-cpu | analytic | complex, all points | 3.36e-03 |  |  |
| pulsating sphere L2, two-channel sum | beat-cpu | one channel | complex, all points | 9.92e-06 | 1.00e-03 | pass |
| oscillating sphere L2 (3968 tri), one tag | metal | analytic | complex, all points | 5.27e-03 |  |  |
| oscillating sphere L2 (3968 tri), one tag | beat-cpu | analytic | complex, all points | 4.33e-03 |  |  |
| same mesh: hemispheres, normal, one channel, both | metal | beat-cpu | complex per channel | 4.80e-03 | 5.22e-02 | pass |
| same mesh: hemispheres, normal, two channels, top | metal | beat-cpu | complex per channel | 3.51e-03 | 5.22e-02 | pass |
| same mesh: hemispheres, normal, two channels, bottom | metal | beat-cpu | complex per channel | 3.51e-03 | 5.22e-02 | pass |
| same mesh: hemispheres, normal, two channels, channel sum | metal | beat-cpu | complex channel sum | 4.80e-03 | 5.22e-02 | pass |
| same mesh: hemispheres, axial, one channel, both | metal | beat-cpu | complex per channel | 4.12e-03 | 5.22e-02 | pass |
| same mesh: hemispheres, axial, two channels, top | metal | beat-cpu | complex per channel | 3.24e-03 | 5.22e-02 | pass |
| same mesh: hemispheres, axial, two channels, bottom | metal | beat-cpu | complex per channel | 3.24e-03 | 5.22e-02 | pass |
| same mesh: hemispheres, axial, two channels, channel sum | metal | beat-cpu | complex channel sum | 4.12e-03 | 5.22e-02 | pass |
| rotated + translated oscillating sphere | metal | unmoved | complex, all points | 1.84e-06 | 1.00e-03 | pass |
| rotated + translated oscillating sphere | metal | analytic | complex, all points | 1.76e-02 | 1.86e-02 | pass |
| rotated + translated oscillating sphere | beat-cpu | unmoved | complex, all points | 1.77e-06 | 1.00e-03 | pass |
| rotated + translated oscillating sphere | beat-cpu | analytic | complex, all points | 1.72e-02 | 1.82e-02 | pass |
| rotated + translated off-axis cap | metal | unmoved | complex, all points | 1.98e-06 | 1.00e-03 | pass |
| off-axis cap: horizontal differs from vertical | metal | vertical cut | complex, polar | 1.99e+00 |  |  |
| rotated + translated off-axis cap | beat-cpu | unmoved | complex, all points | 5.91e-07 | 1.00e-03 | pass |
| off-axis cap: horizontal differs from vertical | beat-cpu | vertical cut | complex, polar | 1.99e+00 |  |  |
| rotated + translated off-axis cap | metal | beat-cpu | complex, all points | 2.98e-03 | 5.22e-02 | pass |
| off-axis cap: source-average pressure (impedance) | metal | beat-cpu | complex impedance | 5.04e-03 | 5.22e-02 | pass |
| repeated HF: two bodies, two-channel sum | metal | one channel | complex, all points | 7.88e-07 | 1.00e-03 | pass |
| repeated HF: left mirrors right in the horizontal cut | metal | mirror | complex, polar | 6.66e-07 | 1.00e-03 | pass |
| repeated HF: two bodies, two-channel sum | beat-cpu | one channel | complex, all points | 8.99e-07 | 1.00e-03 | pass |
| repeated HF: left mirrors right in the horizontal cut | beat-cpu | mirror | complex, polar | 8.11e-07 | 1.00e-03 | pass |
| x0 return vs whole, normal | metal | whole | complex, all points | 4.85e-06 | 1.00e-03 | pass |
| x0 return vs whole, normal | beat-cpu | whole | complex, all points | 4.36e-05 | 1.00e-03 | pass |
| x0 return vs whole, axial | metal | whole | complex, all points | 7.94e-06 | 1.00e-03 | pass |
| x0 return vs whole, axial | beat-cpu | whole | complex, all points | 5.65e-05 | 1.00e-03 | pass |
| x0+y0 return vs whole, normal | metal | whole | complex, all points | 7.34e-06 | 1.00e-03 | pass |
| x0+y0 return vs whole, normal | beat-cpu | whole | complex, all points | 6.98e-05 | 1.00e-03 | pass |
| x0+y0 return vs whole, axial | metal | whole | complex, all points | 7.95e-06 | 1.00e-03 | pass |
| x0+y0 return vs whole, axial | beat-cpu | whole | complex, all points | 5.07e-05 | 1.00e-03 | pass |
| horn return, coarse vs fine density | metal | fine | complex, all points | 4.24e-02 |  |  |
| horn return, reference vs fine density | metal | fine | complex, all points | 2.00e-02 |  |  |
| horn return, reference error (Richardson estimate) | metal | extrapolated | complex, all points | 7.95e-02 |  |  |
| horn return, coarse vs fine density | beat-cpu | fine | complex, all points | 2.95e-02 |  |  |
| horn return, reference vs fine density | beat-cpu | fine | complex, all points | 3.50e-03 |  |  |
| horn return, reference error (Richardson estimate) | beat-cpu | extrapolated | complex, all points | 1.39e-02 |  |  |
| same mesh: horn quarter return, normal | metal | beat-cpu | complex, all points | 1.82e-02 | 1.40e-01 | pass |
| same mesh: horn quarter return, axial | metal | beat-cpu | complex, all points | 1.82e-02 | 1.40e-01 | pass |
| horn: quarter return vs forced full domain | metal | full | complex, all points | 1.13e-02 | 2.38e-01 | pass |
| horn: quarter return vs forced full domain | beat-cpu | full | complex, all points | 1.19e-03 | 4.17e-02 | pass |
| DEFECT (ingest): source on the plug's rear -- WG's quarter vs its full domain | metal | full | complex, all points | 1.04e+00 |  |  |
| DEFECT (ingest): source on the plug's rear -- WG's quarter vs its full domain | beat-cpu | full | complex, all points | 1.00e+00 |  |  |
| horn: placed in CAD (rotated + translated) -- BLOCKED at ingest | ingest | unplaced | refusal | 1.00e+00 |  |  |
| y-only half: BEAT-CPU refused at submission with the reason | beat-cpu | plan | verdict | 0.00e+00 | 5.00e-01 | pass |
| fresh app data dir: import, prepare, solve, store, reopen | metal | end to end | job | 0.00e+00 | 5.00e-01 | pass |
| fresh app data dir: import, prepare, solve, store, reopen | beat-cpu | end to end | job | 0.00e+00 | 5.00e-01 | pass |
| return edited in CAD (acknowledged) vs the unedited return | metal | unedited | complex, all points | 2.88e-07 | 1.00e-03 | pass |
| return edited in CAD (acknowledged) vs the unedited return | beat-cpu | unedited | complex, all points | 0.00e+00 | 1.00e-03 | pass |
