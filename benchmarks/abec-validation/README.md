# ABEC3 cross-validation, ASRO R-OSSE horn

Everything needed to re-check our solver against ABEC3 without re-solving or
re-running ABEC. `python compare_abec.py` prints the table in
[`docs/windows-baseline.md`](../../docs/windows-baseline.md).

| file | what it is |
|---|---|
| `abec-asro68-full-nosym-2026-07-31.txt` | ABEC3 3.6.0 b7, full mesh, **corrected** project |
| `abec-asro2-quarter-sym-2026-07-31.txt` | ABEC3 3.6.0 b7, quarter mesh + `Sym=xy`, as ATH intends |
| `abec-asro68-full-nosym-solving.txt` | the corrected solver script (one line differs from the shipped one) |
| `ours-asro68-full-2026-07-31.npz` | our full-domain solve, normalised dB |
| `ours-asro2-quarter-2026-07-31.npz` | our quarter solve, `yz+xz` native symmetry |
| `compare_abec.py` | parser + comparison |
| `ath-fine1-config.txt` | the 4.4x-refined ATH config (11650 quarter triangles) |
| `ours-fine1-quarter-2026-07-31.npz` | our solve on that refined mesh |

## This is *not* the `HORNLAB_VALIDATION_ARTIFACTS` set

`hornlab-bempp-bem` has two tests (`test_reference_asro68.py` and
`test_wg_solver_parity.py`) that read `HORNLAB_VALIDATION_ARTIFACTS` looking for
`hornlab_postfix_asro68.npz` and `abec_baseline_asro68.npz` — the **May 2026**
validation NPZs pinned to `commit 27902f5` and to
`docs/waveguide-generator/_public/research/260517-bem-vs-ath-validation.md`,
expecting 0.03 dB. Those files are not on this machine, so both tests skip.

Do **not** point that env var here and do not rename these files to match.
This is a newer, independently generated dataset; giving it those names silently
substitutes a different baseline into tests written for the old one. The two
sets do not agree: measured today, ABEC-vs-ours at 100 Hz on the pinned key
angles is 0.052 dB against the shipped reference and 0.061 dB against the
corrected one — both above the 0.05 dB those tests assert.

That gap is **not** a solver regression. Our full-domain solve reproduces the
pinned anchor in `test_asro68_100hz_hplane_matches_pinned_origin_smoke`
(`[0.0, -0.24731088, -1.49123359, -2.26645803]`) to 4 decimal places, so our
side is unchanged. The difference must therefore sit in the historical
`abec_baseline_asro68.npz`, which is worth locating before anyone edits those
thresholds.

Our `.npz` files carry `spl_db[frequency, plane, angle]`, `frequency_hz`,
`angles_deg` and `planes`, on the shared grid: 40 log frequencies 100 Hz -
20 kHz, planes `horizontal, vertical, diagonal`, 37 angles 0 - 180 deg at 5 deg.

The meshes are deliberately *not* copied here. Both solvers read the external
ATH exports directly, byte for byte — `asro2.msh` md5 `1c74051f…` and
`250917asro68.msh` md5 `a66d124f…`. Regenerating or re-exporting either would
break the comparison silently, so the paths stay external and hashed.

## Why the shipped reference was re-run

The reference project as shipped pairs `Sym=xy` with the **full** mesh. ABEC's
manual is explicit that `Sym=` means "I have given you only part of the model,
mirror it", so ABEC solved four superimposed copies of a bi-symmetric horn.
ATH is not at fault — it emits `Sym=xy` only for `Mesh.Quadrants = 1` and no
`Sym` clause at all for `1234`; the shipped `solving.txt` is a stale leftover
from an earlier quarter-mesh run.

Measured cost of that misconfiguration, main-lobe rms, same mesh, one line
changed: 0.021 dB below 1 kHz, 0.052 dB at 1 - 4 kHz, 0.111 dB at 4 - 11 kHz,
0.636 dB above. Small, because the images landed back on the same surface and
on-axis normalisation divides out the level error — but correcting it tightens
ABEC's own full-vs-quarter agreement by 2 - 4x.

## Read main-lobe statistics, not worst-of-all-angles

Both sides are normalised on axis. The worst angle on such a polar is almost
always a deep rear null, where a fraction of a dB of absolute pressure appears
as tens of dB relative and neither code is converged — ABEC disagrees with
*itself* by up to 33 dB back there. `compare_abec.py` gates on ABEC level
> -20 dB for that reason, and prints the ungated figures alongside so the
difference is visible.
