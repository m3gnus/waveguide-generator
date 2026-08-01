# Cross-backend directivity reference, ASRO2 quarter mesh

One stored answer that both solve backends must reproduce.
`server/tests/test_cross_backend_asro2_parity.py` solves with **one** backend per
run — whichever the host would actually use — and compares against it.

Be precise about what a single run proves. On Apple Silicon that is Metal against
a Metal-generated reference: a golden regression test, not a cross-backend
comparison. It becomes cross-backend only when the other backend runs it, which
on one machine means forcing it:

```bash
WG_PARITY_BACKEND=bempp   # or 'metal'
```

Until this repo has a non-Mac runner, nothing re-checks the Bempp side
automatically. Run it by hand after changing either backend's numerics.

| | |
|---|---|
| mesh | ATH export `asro2/ABEC_FreeStanding/asro2.msh`, md5 `1c74051f05cee2f66bfe73897b3e6421`, 2275 triangles |
| grid | 40 log frequencies 100 Hz – 20 kHz, planes `horizontal, vertical, diagonal`, 37 angles 0–180° at 5° |
| solve | quarter mesh, `native_symmetry_plane="yz+xz"`, throat origin, 2 m, driver = physical group 2 (`SD1D1001`) |
| stored | `spl_db[frequency, plane, angle]`, on-axis normalised |

The mesh is **not** copied here. It is read from `ATH_REFERENCE_ROOT` and its md5
is asserted before anything is compared, because re-exporting or re-meshing it
would change the comparison silently. Without that env var the test skips.

## Provenance

Generated 2026-08-01 on macOS arm64 by `hornlab-metal-bem` (2.8 s for the
sweep). `hornlab-bempp-bem` on the same machine, same file, numba assembly
backend, reproduces it to **0.0004 dB main-lobe rms** — see the measured figures
in the test's docstring. Either backend could have produced this file; Metal did
because it is the default wherever it is available.

## This is not the `HORNLAB_VALIDATION_ARTIFACTS` set

`hornlab-bempp-bem` has tests reading `HORNLAB_VALIDATION_ARTIFACTS` for
`hornlab_postfix_asro68.npz` and `abec_baseline_asro68.npz` — the May 2026
validation NPZs pinned to a different commit and a different threshold. Do not
point that variable here, and do not rename this file to match theirs. Giving
fresh data an old dataset's name silently substitutes a different baseline into
tests written for the old one.

## What this does and does not check

Checks: directivity **pattern shape**, on the one geometry — a bare open shell,
quarter symmetry, normal-velocity source, free-standing.

Does not check: absolute SPL, impedance, enclosures, infinite baffle, axial
source, complex-k, or circsym. Both sides are on-axis normalised, so any error
common to all angles at a frequency divides out. Those are separate gates worth
having, not things this file quietly covers.
