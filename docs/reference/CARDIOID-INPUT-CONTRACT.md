# Passive-cardioid campaign — input contract

Established by the F0 spike, 2026-08-13, against reference run
`hornlab-research/runs/fusion360/260704-124242-260627_-_PartyMEH_v10`.
Every number below is measured, not quoted.

## What "agrees" means, quantitatively

| Stage | Comparison | Result |
|---|---|---|
| Post-processing (chamber/port branch) | `exit_to_input_volume_velocity_ratio`, `input_impedance` vs shipped `transfer` | **max abs delta 0.0** — bit-exact, 160/160 points |
| BEM matrix | `solver_impedance_matrix` (160, 2, 2) vs shipped npz | **max relative delta 1.461660e-04** |

The matrix residual is **conditioning-shaped**: it grows with frequency
(7.2e-7 at 50 Hz, ~3e-5 by 4.6 kHz, 1.5e-4 worst). A future A/B showing a
frequency-flat residual, or one worse at low frequency, is a different failure
and should not be waved through because it is "the same order as 1.46e-4".

Cost: **19.6 s for the full 160-point campaign, 0.12 s/frequency** (2 apertures,
symmetry-reduced mesh, add-in `.venv`). An earlier extrapolation from the d070
smoke test suggested hours; it was wrong by ~700x because that run used a much
larger mesh on the OpenCL CPU environment. **Measure before letting a cost
number shape a design.**

## Wire fields

Six fields, mapping 1:1 onto the add-in's CLI flags:

| Wire field | CLI flag | Reference value | Notes |
|---|---|---|---|
| chamber volume | `--passive-cardioid-rear-volume-l` | 6.0 L = 0.006 m3 | **A VOLUME, not a compliance.** `chamber_compliance_m3_per_pa` in the summary is *derived*: V/(rho c^2) = 4.235458725511545e-08 against the recorded ...546e-08, identical to the last ulp. |
| port length | `--passive-cardioid-port-length-mm` | 25.0 mm | |
| port area | `--passive-cardioid-port-area-cm2` | 500 cm2 = 0.05 m2 | **See below — this is two fields, not one.** |
| foam resistance | `--passive-cardioid-foam-resistance-pa-s-m3` | 10000.0 | |
| invert | `--passive-cardioid-invert-port` | True -> `rear_drive_sign: -1.0` | |
| coupled | `--passive-cardioid-coupled` | flag | |

### Port area is two fields plus a provenance flag

- `model_port_area_m2` = 0.05 — user-supplied, and **the one the physics uses**.
- `bem_port_area_m2` = 0.009471859930646809 — geometric, measured from the mesh.
  Equals the `PORT_EXIT` entry of the matrix npz's `aperture_area_m2` exactly.
- `port_area_source` = `"user"` — the discriminator.

**Conflating them is a ~40% error**, measured: substituting the geometric area
gives relative deltas of 3.965e-01 on the volume-velocity ratio and 3.891e-01 on
the input impedance. Large enough to be badly wrong, small enough to look like a
plausible curve. Never resolve "port area" from a single field.

## Frequency grids

**Reconcile grids explicitly, never by length.** On the reference run the matrix
grid and the consumer grid are element-for-element identical (both 160 points,
50.00-20000.00 Hz, `np.array_equal` True), so a length-only check passes while
proving nothing — and the next run that narrows one grid diverges silently. Both
arrays being length 160 is a coincidence of this run, not a guarantee.

**The validity cap is conditional in practice.** `_radiation_matrix_freq_max_hz`
returns `min(freq_max_hz, source_freq_max[name] for the matrix's apertures)`. It
only narrows when `source_freq_max` is populated for those apertures; on the
reference run it was not, and the full 20 kHz passed through despite
`--source-aperture-valid-hz PORT_EXIT:2188.27` being on the command line. F1 must
handle both the narrowed and un-narrowed case.

The cap is retained on **physics** grounds — it bounds the range where the
aperture model is valid. It is not a cost control; at 0.12 s/frequency there is
nothing to control. A reduced-grid-plus-interpolation option was considered and
**dropped**.

## Aperture identity

Two naming conventions exist for the same physical thing:

- mesher: `MID_PORT_EXIT_LEFT = 10`, `MID_PORT_EXIT_RIGHT = 11`, named
  `mid_port_exit_left` / `mid_port_exit_right` (`hornlab_mesher/tags.py`).
- imported CAD: `PORT_EXIT`, `PORT_EXIT_L`, `PORT_EXIT_R`.

On the reference run these coincide — it is `--source PORT_EXIT:25:10`, a single
aperture at tag 10, matching the mesher's `MID_PORT_EXIT_LEFT`. **So the F0 A/B
does NOT exercise the `_L`/`_R` path.** That branch is pinned by a mapping-level
unit test in F1 instead, with no solve required.

A wrong tag mapping produces a matrix over the wrong faces that is still
reciprocal and still passive — no listed diagnostic catches it. Hence artifacts
must record face identity: per-aperture **name, area, and tag**. Names and areas
already exist upstream in the matrix npz; only the tag needs adding, and the
existing key names should be mirrored exactly rather than paralleled.

## Diagnostics

`hornlab_sim` already computes these in `RadiationMatrixDiagnostics`:
`reciprocity_max_abs`, `reciprocity_max_rel`, `passivity_min_eig`,
`passivity_min_eig_reciprocal`, `passivity_ok`, `low_ka_self_impedance`,
`low_ka_self_impedance_rel_error`. WG **records** them; it does not implement
them. That also makes WG's numbers and any reference numbers comparable by
construction, since they come from the same code.

**Thresholds must not be naive constants.** On the shipped, accepted reference
run: `reciprocity_max_rel` min 2.966e-05, median 4.190e-04, **max 1.145e-01 at
14794.8 Hz** — three orders above the median. `passivity_min_eig` min 1.062,
median 3502, `passivity_ok` 160/160. A fixed bound tight enough to be meaningful
at the median would fail this run at the top end. Any gate is frequency-aware, or
record-only with the distribution reported.

## Reproduction footnote

Manifests written before 2026-08-09 record
`~/.waveguide-generator/opencl-cpu-env/bin/python` as the interpreter. That
environment has `hornlab_metal_bem` but **not** `hornlab_sim`, so it can no longer
drive this path. The add-in's own `.venv` (added 2026-08-09) has both. A recorded
interpreter in an old manifest is a historical record, not a runnable instruction.

For a faithful A/B, do not reconstruct the solver config by hand: import the
add-in module and drive its own `parse_args` / `_build_frame` / `_build_config`
with the manifest's recorded argv. Config fidelity is then guaranteed by
construction, including quirks. (One such quirk is under separate investigation:
the solve warns `native_symmetry_plane is None` despite
`--native-symmetry-plane yz` being passed. Both paths share the builder, so the
A/B is unaffected.)

## Known-benign warning — and why it must never be filtered

The campaign emits a native-symmetry warning through this entry point:

> Mesh may be a reduced native-symmetry mesh (suspected plane 'yz') but
> native_symmetry_plane is None...

**It is spurious.** The flag reaches `SolveConfig` correctly; hornlab-sim's
`solve_aperture_matrix` preloads the mesh at `radiation_impedance.py:133` without
forwarding `native_symmetry_plane`, while the actual solve uses the preserved
config. A one-kwarg fix is landing on hornlab-sim main. **The warning will
persist here until WG's `hornlab-sim` pin moves past that fix** — recorded so
that nobody sees it vanish after a routine pin bump and wonders what changed.

**Do not suppress this warning class.** The counterfactual is load-bearing: if
`native_symmetry_plane` genuinely were `None` on a reduced mesh, the solve would
treat the half-mesh as free-standing and return materially wrong numbers, with
this warning as the only tell. Filtering the class to silence today's benign
instance would remove the only signal for a real one.

This is the concrete reason the diagnostics section says *record*, not *gate*: a
signal that is benign under one pin and load-bearing under another cannot be
encoded as a constant.
