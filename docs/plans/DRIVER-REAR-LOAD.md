# Per-channel rear loads for LEM-coupled BEM drive channels

Status: proposed, 2026-08-27. Nothing implemented. Written after the driver
review that produced [PR #38](https://github.com/m3gnus/waveguide-generator/pull/38),
whose first commit guards a bug class this plan would delete outright.

A drive channel's rear load is today one optional number — `rear_volume_l`, a
lossless sealed compliance — except on the passive-cardioid path, where it is a
job-level singleton with a real chamber and port. This plan makes the rear load
a per-channel model, so sealed-with-losses, vented and cardioid stop being three
unrelated mechanisms.

## 1. What the coupling actually is today

WG's driver coupling is a **one-port** coupling. `_apply_channel_driver`
(`server/solver/metal.py`) takes one source patch's area-weighted surface
pressure, turns it into an acoustic self-impedance
(`driver_lem.self_impedance_from_surface_average`), and hands that to
`hornlab_sim`'s `coupled_direct_radiator_response` as the entire acoustic load.
`DriverSpec.rear_volume_l` adds `1/(s·Cab)` in series with it and nothing else.

The passive-cardioid path is the one place the coupling is genuinely
**two-port**. `_coupled_cardioid_result` pulls four terms out of the radiation
matrix — `z_mm`, `z_mf_from_port`, `z_port_from_mf`, and the port-to-port
termination load — and solves the diaphragm and the port against each other
with the mutual terms included. That is the shape every ported rear load needs,
and it already exists.

Three things stop it being reusable:

1. **It is a job-level singleton.** `passive_cardioid_rear_volume_l` and its
   siblings live on `ImportedGeometrySource`, not on a channel. One job gets
   one chamber.
2. **The binding is by magic name.** `_passive_cardioid_apertures` finds the
   port by trying `PORT_APERTURE_NAME_GROUPS` (`PASSIVE_CARDIOID`, `PORT_EXIT`,
   `MID_PORT_EXIT_LEFT/RIGHT`, …) and the diaphragm by looking for exactly one
   source whose canonical role is `MF`. Nothing in the request says which
   channel vents through which patch, because with one chamber it never had to.
3. **It is lossless.** See §2 — this is the part that was mis-scoped in the
   review that preceded this plan, and it is the main cost.

## 2. Correction: the loss machinery is on the other solver family

The review this plan follows claimed the chamber-loss physics already existed
and only needed wiring. That is **wrong**, and the error is worth recording
because it changes the size of the work.

`hornlab_sim` has two families of chamber code:

| | radiation loading | chamber losses | port model |
|---|---|---|---|
| `bandpass.Chamber` / `bandpass.Port` | lumped, LEM-only | `fill_loss`, isothermal `Cab_for` thermal correction | `Port` with viscothermal Kirchhoff-Benade Q, end corrections, `n_parallel` |
| `radiation_impedance.terminated_chamber_port_branch` | **BEM termination** | none | bare acoustic mass + one scalar series resistance |
| `driver_coupling.coupled_direct_radiator_response` | **BEM termination** | none | n/a (sealed only) |

`grep` for `Chamber(` shows it is constructed only by `bandpass.simulate`,
`bass_reflex`, `cli/tmm.py` and `hornresp/validate.py` — every one of them a
pure-LEM path. **WG calls neither.** The two helpers WG does call have zero
loss terms: `terminated_chamber_port_branch` computes `y_chamber = jωC` and
stops.

So the rich model is on the family that cannot see the BEM, and the
BEM-terminated family is the poorer one. R1 is therefore not "wire up existing
fields" — it needs `hornlab-sim` work to bring the loss terms onto the
BEM-terminated path. That is a pinned-module change with its own release and
pin bump, and it should land first.

## 3. Principles

- **The rear load belongs to a channel, not to a job.** A three-way with a
  vented LF, a sealed MF and a horn-loaded HF is the ordinary case, not an
  exotic one.
- **A port is geometry, not a lumped piston.** The whole point of coupling LEM
  to BEM rather than running LEM alone is that the port exit is a meshed
  aperture whose output radiates through the exterior solution, with its mutual
  coupling to the diaphragm included. A vented rear load therefore *requires* a
  port patch in the model; it is not a number you can type without one.
- **Say which patch, do not guess it.** The binding from channel to port patch
  becomes explicit in the request. Name-matching stays only as the migration
  for models already tagged.
- **Unset stays exactly as it solves today.** Every existing request keeps its
  result, bit for bit.

## 4. Wire

`DriverSpec.rear_volume_l: float | None` becomes
`DriveChannel.rear_load: RearLoad | None`, a discriminated union.

```json
{ "type": "sealed", "volume_l": 12.0, "fill_loss_pa_s_m3": 0.0, "leak_ql": null }
{ "type": "vented", "volume_l": 40.0, "fill_loss_pa_s_m3": 0.0, "leak_ql": null,
  "port_source_ids": ["port-exit-l", "port-exit-r"],
  "port_length_mm": 120.0, "port_area_m2": null,
  "port_area_source": "bem_aperture",
  "interior_end_correction_mm": 0.0,
  "series_resistance_pa_s_m3": 0.0,
  "invert": false }
```

Notes on the shape:

- It moves from `DriverSpec` to `DriveChannel`. The rear load describes the
  installation, not the driver — the frontend already draws that line with
  `DRIVER_INSTALLATION_KEYS`, and moving it makes a picked driver's base values
  and the installation cleanly separable.
- `port_source_ids` is the explicit binding that replaces name-matching. It
  names patches in the same ingest record `source_ids` already names.
- `port_area_source: "bem_aperture"` reuses the existing rule that the model
  area must equal the meshed area, and the existing validator can be lifted
  almost verbatim from `validate_passive_cardioid`.
- `"cardioid"` is **not** a fourth variant. A passive cardioid is
  `{"type": "vented", "invert": true}` — the existing `rear_sign = -1`. That it
  falls out as a special case rather than a peer is the main evidence the
  generalization is the right one.

### Compatibility

- `rear_volume_l` is accepted for one more minor version and mapped to
  `{"type": "sealed", "volume_l": …}`, with both present a 422.
- `passive_cardioid_*` on `ImportedGeometrySource` likewise maps onto the MF
  channel's `rear_load`, resolving the port patches through
  `PORT_APERTURE_NAME_GROUPS` exactly as `_passive_cardioid_apertures` does now.
  That keeps every tagged model and every stored solve profile working.
- Both mappings happen in a model validator, so the solver sees only the new
  shape and there is one code path below the wire.
- Additive to OpenAPI apart from the two deprecations; the deprecated fields
  keep their schema until removed.

## 5. Solver

`_apply_channel_driver` gains the branch `_coupled_cardioid_result` already
implements, generalized over which channel it applies to:

- **sealed** — today's path plus loss terms, still one-port. No port patch, no
  radiation matrix, no extra BEM cost.
- **vented** — needs the two-port terms, so it needs a radiation-impedance
  campaign over `{diaphragm patch} ∪ port_source_ids`. That is exactly
  `_run_passive_cardioid_campaign`, parameterized by aperture set instead of
  reading the job-level singleton.

The campaign is the expensive part and it is per rear load, so a job with two
vented channels pays for two campaigns. Worth measuring before assuming it is
acceptable; `_cardioid_frequency_grid` already caps campaign frequencies, and
the same cap applies per channel.

Once every rear load is per-channel, `_cardioid_rear_volume_refusal` (PR #38)
and the singleton's whole validator block are deleted, not extended — the
contradiction they guard becomes unrepresentable.

## 6. hornlab-sim prerequisite

Land this first, in `hornlab-sim`, and bump the pin.

1. Give `terminated_chamber_port_branch` and
   `coupled_direct_radiator_response` a chamber admittance that can carry
   losses: `Y = s·C_eff + 1/R_fill + 1/Z_leak`, where `C_eff` is
   `Chamber.Cab_for`'s thermal-corrected compliance. The cleanest form is to
   let both accept a `bandpass.Chamber` and reuse `_load_and_port_impedance`
   with the port's lumped radiation replaced by the BEM termination — one
   chamber implementation instead of two.
2. Keep the BEM-termination rule from `terminated_chamber_port_branch`'s
   docstring intact: the terminated aperture must not also carry LEM-side
   external radiation loading, or the port radiates twice.
3. Defaults must reproduce the current lossless result exactly, so the pin bump
   alone changes no WG output. Assert that in a test.

## 7. Slices

1. **hornlab-sim losses on the BEM-terminated path** (§6), with a
   bit-for-bit no-op test at default arguments. Pin bump.
2. **`rear_load` wire + migration** (§4), solver still sealed-only. No
   behaviour change; existing requests map onto the new shape and produce
   identical results. This is the slice that proves the migration.
3. **Sealed losses end to end** — wire to solver to rail. First slice with a
   visible result change, and the cheapest useful one: it fixes "Qtc is always
   the ideal-box value" without touching the campaign.
4. **Vented rear loads** — campaign parameterized by aperture set, two-port
   branch per channel. Measure campaign cost with two vented channels.
5. **Delete the singleton** — remove `passive_cardioid_*` and the PR #38
   refusal once the migration has shipped for a version.

Slices 1–3 are worth doing even if 4 never happens: they carry the losses,
which is the part that changes numbers for the ordinary sealed case.

## 8. Out of scope, deliberately

- **A lumped compression-driver front chamber.** In a LEM-coupled-BEM tool the
  front cavity and phase plug should be *meshed*, which the import path already
  allows. A lumped front chamber would double-count whatever is in the mesh.
  The real gap there is that nothing warns when `count × Sd` disagrees with the
  meshed patch area — a silent SPL scale error, and a separate small fix.
- **Thermal and power compression.** `server/solver/driver_limits.py` is
  explicitly small-signal and says so in its docstring. Adding thermal makes it
  a different kind of tool.
- **BP4/BP6 topologies.** Bandpass front chambers belong in `hornlab-sim`'s own
  `bandpass` path. In WG the front side is geometry.

## 9. Open questions for Magnus

1. Is a second radiation campaign per vented channel acceptable, or should a
   job be capped at one vented rear load until it is measured?
2. Should `rear_load` also reach the **parametric** path? Drivers are
   CAD-import-only today, so a parametric waveguide has no absolute SPL at all.
   That is a product decision, not a physics one, and it is larger than this
   plan.
3. `leak_ql` as a Q, or as an explicit leak resistance? Q is what enclosure
   literature quotes; a resistance is what the network wants and what
   `fill_loss` already is.
