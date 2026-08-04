# FREEFORM simplification — WG v2 batch W1

Working dir: this repo, on branch `freeform-simplify` (already checked out).

**Do not run any git command.** Leave all changes uncommitted in the working tree.
**Do not touch `pins.json` or `server/requirements-pins.txt`** — the mesher pin bump is handled
separately, and the currently installed mesher is still the old pin. That is expected: nothing
in this batch requires the new mesher to be installed.

## Why

The FREEFORM meridian is semantically a function r(z), but it is implemented in the mesher as a
2D chord-length-parameterised curve whose derivatives are then overwritten by
`automatic_speed * user_scale * (cos θ, sin θ)`. That override destroys the monotonicity and
no-overshoot guarantees the underlying PCHIP construction provides, and the mesher then bolts on
fold/overshoot validation to police the damage.

The mesher is being changed (batch M1, in progress) to **solve the tangent speed instead of
accepting it**. That deletes four user-facing controls and one policy select that are artifacts
of the parameterisation rather than of the design task. This batch makes v2 follow the new
contract.

Concretely, on the current mesher: `throatTangentScale = 3.0` — the top of its own documented
range, and the top of the slider in `parameterRegistry.ts` — hard-fails with
`profileH segment 0 folds backward` on an ordinary 3-anchor profile. And an interior anchor angle
of `-5°` is rejected for an overshoot of 0.105 mm because the tolerance is a floating-point
epsilon rather than a physical one. Both failure modes disappear once the speed is solved.

## The new mesher contract

Removed from the mesher's FREEFORM input:

- `profile_{h,v}.throat_tangent_scale`
- `profile_{h,v}.mouth_tangent_scale`
- the per-anchor `strength` (the 4th element of a point row) — rows are now 2 or 3 elements
- `overshoot_policy` — the tolerance is now physical and the solver backs off automatically, so
  there is no policy left to choose

Unchanged: `inflection_policy` stays (S-curve intent is a real design control), and so do
`throat_angle_deg`, `mouth_angle_deg`, the per-anchor `angle_deg`, and everything about
cross-section stations except the item below.

Also: the mesher now treats the station shape `circle` as a plain alias for `ellipse`, accepted
at any index and normalised to `ellipse` on ingest. At t = 0 the throat radius is shared between
the planes, so a == b and the two shapes are identically the same curve — `circle` was a
redundant, positionally-restricted alias.

## What to change

### 1. `server/design/schema.py`

- `FreeformPoint`: drop the `strength` field and the `_strength_requires_angle` validator.
- `FreeformProfile`: drop `throat_tangent_scale` and `mouth_tangent_scale`.
- `FreeformConfig`: drop `overshoot_policy`.
- `CrossSectionStation.shape`: drop `"circle"` from the Literal, leaving
  `"ellipse" | "superellipse" | "rounded_rectangle"`.
- `_valid_cross_section_domain`: the first station must now be `0 ellipse`, not `0 circle`.

These models are `StrictModel`, so removing a field turns a stale payload into a validation
error — which is exactly why the migration in item 2 must run first. It already does:
`DesignConfig._migrate_legacy_payload` applies migrations before validation.

### 2. `server/design/migrate.py`

Add one new migration at the end of the `MIGRATIONS` tuple — follow the existing shape exactly
(`applies_if` / `transform` / `note`, with the transform mutating the payload in place):

- strip `strength` from every `profile_h.points` / `profile_v.points` row (both the object form
  and any list form the payload may carry)
- strip `throat_tangent_scale` / `mouth_tangent_scale` from both profiles
- strip `overshoot_policy` from the config
- rewrite any `cross_sections[*].shape == "circle"` to `"ellipse"`

`applies_if` must return True only when at least one of those is actually present, so the
migration note does not appear on payloads that do not need it. Write the note in the same voice
as the existing ones.

This single migration covers both the JSON API path and the `.mwg` text path, since textcfg's
output is validated through `DesignConfig`.

### 3. `server/design/textcfg.py`

`.mwg` is a text format, and saved files may contain the removed keys — they must still open.

- Reader: keep parsing `strength` (line ~373/378), `ThroatTangentScale` / `MouthTangentScale`
  (~423) and `Freeform.OvershootPolicy` (~479) into the payload so old files load; the migration
  from item 2 then strips them. Do not add a second stripping path here.
- The first-station check at ~470 must accept `circle` or `ellipse` (the migration normalises).
- Writer: stop emitting `Freeform.OvershootPolicy` (~619), `ThroatTangentScale` /
  `MouthTangentScale` (~628-629) and the point-row `strength` (~637-638). Update the header
  comment at ~612 to `"; FREEFORM point rows: z r [angleDeg]"`.
- The shape allowlist at ~341 keeps accepting `circle` on read.

### 4. `server/preview/translate.py`

Stop sending the removed keys to the mesher: the point-row `strength` (~102-103),
`throatTangentScale` / `mouthTangentScale` (~114-115), and `overshootPolicy` (~147). The mesher
rejects unknown keys, so leaving any of them in place breaks every FREEFORM preview.

### 5. `frontend/src/design/parameterRegistry.ts`

Remove these four parameter definitions and their entries in the `FREEFORM` ordering list at
line ~226:

- `freeform.throatTangentScaleH`, `freeform.mouthTangentScaleH`
- `freeform.throatTangentScaleV`, `freeform.mouthTangentScaleV`

and remove the `freeform.overshootPolicy` select. Keep `freeform.inflectionPolicy`.

### 6. `frontend/src/design/FreeformEditors.tsx`

- `EditablePointTable`: drop the `strength` column — the header cell, the input cell, the
  `strength` branch in `update()`, and the `delete target.strength` line that hangs off clearing
  the angle. Angle stays.
- `parsePointPaste`: a row is now 2 or 3 columns, not 2-4. Reject a 4-column row with a message
  saying the per-anchor strength was removed and the tangent speed is now solved automatically —
  people will paste old data. Drop the `strength` validation and the `strength` key from the
  parsed point. The `z r [angle strength]` placeholder text becomes `z r [angle]`.
- `EditableStationTable`: the first station's shape is now `ellipse`; drop the special-cased
  `<option value="circle">` that only renders at index 0. Index 0 keeps its disabled select.

### 7. `frontend/src/stores/design.ts`

Update the `FreeformPoint` and station types to match — no `strength`, no `circle` — and any
default design payload that carries the removed fields or emits a `circle` station.

## Constraints

- Match the surrounding style closely. The frontend is dense and terse with few comments; the
  server is more explicit with docstrings that explain *why*. Follow whichever file you are in.
- Do not redesign the editor layout, add disclosure sections, or rename FREEFORM. Those are
  separate decisions. This batch is strictly: follow the new contract, delete what it removes.
- Do not change `inflection_policy` anywhere.
- Do not touch `pins.json` or `server/requirements-pins.txt`.

## Tests

Update every affected test and add coverage for the new behaviour. The relevant files are
`server/tests/test_schema.py`, `test_migrate.py`, `test_textcfg.py`,
`test_preview_ws_translate.py`, `test_preview_ws_protocol.py`, and on the frontend
`frontend/src/design/FreeformEditors.test.ts`, `parameterRegistry.test.ts`,
`ParamPanel.test.tsx`, `frontend/src/stores/design.test.ts`, `design.serialize.test.ts`,
`designStoreRoundTrip.test.ts`.

New coverage that must exist:

1. A legacy payload carrying `strength`, both tangent scales, `overshoot_policy` and a `circle`
   station migrates cleanly and then validates, with the migration reporting its note once.
2. A payload with none of those does **not** trigger the migration note.
3. A `.mwg` file containing the removed keys round-trips: it loads, and re-writing it emits none
   of them.
4. `parsePointPaste` rejects a 4-column row with the "strength was removed" message and accepts
   2- and 3-column rows.
5. `translate.py` output for a FREEFORM design contains none of the removed keys.

Run and get green:

```
"../Waveguide Generator/.venv/bin/python" -m pytest server/tests -q
```
```
cd frontend && npm test
```
```
cd frontend && npx tsc --noEmit
```

## Report back

Summarise: what you changed, the verbatim pass/fail counts from all three commands above, any
test you could not get green and why, and anything you decided differently from this brief and
why.
