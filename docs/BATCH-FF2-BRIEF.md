# FREEFORM: one axial coordinate — WG v2 batch W2

Working dir: this repo, on branch `freeform-simplify` (already checked out, W1 already committed).

**Do not run any git command.** Leave all changes uncommitted in the working tree.
**Do not touch `pins.json`, `server/requirements-pins.txt`, or anything in the mesher.**
The mesher is deliberately untouched by this batch — see below.

## Why

FREEFORM currently carries **two axial coordinate systems**. Meridian anchors live in absolute
`z` millimetres; cross-section stations live in normalized `t ∈ [0, 1]`. Because of that,
`length` is not a scale — it is a destructive edit.

`freeform.length` is wired to `profile_h.points.$last.z` (parameterRegistry.ts:128), so changing
it moves only the mouth point. Interior anchors keep their absolute z. Shorten a 120 mm design
with an interior anchor at z = 70 down to 60 mm and the points become
`[0, 25, 70, 60]` — no longer strictly increasing, so the mesher rejects the whole design.
(V1 had the same root cause with a worse symptom: it silently *deleted* the anchor.)
Stations, being in `t`, rescale cleanly and are unaffected. Same model, two coordinates, one of
which breaks on a routine edit.

## The shape of the fix

The **design document** — the thing the user edits and saves — gets one axial coordinate: `t`.
Anchors move to `t`, joining the stations that are already there, and `length` becomes a real
top-level scalar in millimetres.

The **mesher wire format does not change.** `z` in millimetres is the correct coordinate at that
layer, because that is where the geometry actually lives; `translate.py` converts
`z = t * length * scale` on the way out. So this batch touches v2 only, and the mesher pin stays
where it is.

The **editor keeps showing millimetres.** A waveguide designer thinks in mm, not in fractions —
the table column stays "z mm" and simply converts on read and write. `t` is the storage
representation, not the interaction.

After this, `length` and `scale` are both pure multipliers and no edit to either can destroy or
invalidate an anchor.

## What to change

### 1. `server/design/schema.py`

- `FreeformPoint`: replace `z: Expr` with `t: Expr`. Keep `r` and `angle_deg`.
- `FreeformConfig`: add `length: Expr` (required — every FREEFORM design has one). There is no
  name collision; `DesignCommon` has `throat_ext_length` and `slot_length`, not `length`.
- Add a `points` validator on `FreeformProfile`, in the style of the existing
  `_valid_cross_section_domain`: at least 2 points; every `t` scalar and within `[0, 1]`;
  strictly increasing; `points[0].t == 0`; `points[-1].t == 1`. Error messages in the same voice
  as the station ones.
- Add a validator that `length` is a scalar and `> 0`.

### 2. `server/design/migrate.py`

Add migration `005_freeform_normalized_axis` at the end of `MIGRATIONS`, in the existing style.

`applies_if`: a FREEFORM payload where any `profile_h`/`profile_v` point carries `z` (object
form) — or, for the list row form `[z, r, ...]`, where the payload has no top-level `length`.

`transform`:
- take `length` from `profile_h`'s last point `z`
- set the payload's top-level `length` to it
- rewrite every point in **both** profiles as `t = z / length`, dropping `z`
- clamp nothing and drop nothing — every anchor must survive

Normalize both profiles by **profile_h's** length, not each profile's own. The two profiles are
required to share a length; if a payload violates that, `profile_v`'s last `t` will not be 1 and
the schema validator from item 1 will say so, which is the right outcome. Silently normalizing
each profile separately would paper over a real inconsistency.

If `length` is missing, non-scalar, or `<= 0`, leave the payload alone and let validation report
it — a migration must not invent geometry.

### 3. `server/design/textcfg.py`

**`.mwg` point rows stay in millimetres.** It is a human-facing text format and `Freeform.Length`
is already a separate scalar key in it, so existing files keep working unchanged and no file
needs rewriting. textcfg converts.

- Reader (`_freeform_payload`, ~line 405-425): it already synthesizes the endpoints from
  `Freeform.Length` and `Freeform.ThroatRadius`, so the length is in hand. Emit points as
  `t` instead of `z`: throat `t = 0`, mouth `t = 1`, interior `t = z / length`. Put `length` on
  the payload. Keep the existing validation that interior z values are inside the span, phrased
  in mm since that is what the file contains.
- Writer (`_serialize_freeform`, ~line 610-640): `length` now comes from `config.length` rather
  than `profile_h.points[-1].z`. Point rows are written back in mm as `t * length`. Skip the
  throat and mouth rows exactly as today (they are carried by `Freeform.ThroatRadius` /
  `MouthRadius`).
- Update the header comment to note the rows are in mm.

Rounding: when converting mm → t → mm, use enough precision that a file round-trips to the same
text. Format the written value the way `_text` already formats numbers and avoid emitting
`69.99999999999999` — round the reconstructed mm value to a sensible number of decimals before
formatting.

### 4. `server/preview/translate.py`

`_profile_points` currently does `_scaled_expr(point.z, scale)`. It becomes
`t * length * scale` for the axial coordinate, with `r` scaled as today.

Use the existing `_structural_number` helper to require `t` and `length` to be scalars, raising
a clear error otherwise — FREEFORM has no formula fields, so a non-scalar here is a real problem
and must not be silently passed through as a string expression.

**The emitted mesher payload must be byte-identical to what the same design produced before this
change.** That is the single most important property in this batch.

### 5. `frontend/src/design/parameterRegistry.ts`

`freeform.length` moves from `profile_h.points.$last.z` (mirrored to `profile_v...`) to the plain
top-level path `length`, with no `mirrorPaths`. Keep its label, unit, and min/max/step.
`freeform.throatRadius` and the mouth radii keep their existing `points.0.r` / `points.$last.r`
paths.

### 6. `frontend/src/design/FreeformEditors.tsx`

The table still reads and writes millimetres; `t` is what gets stored.

- `EditablePointTable` currently derives `const length = points.at(-1)?.z ?? 120`. It must read
  `length` from the design store instead.
- The z column displays `t * length`, rounded to 4 decimals so the input does not show float
  noise. Committing a value stores `t = z / length`. Keep the existing bound — the value must
  stay strictly inside the span — expressed in mm as it is now.
- `add()` still inserts at the midpoint of the largest gap; in `t` the arithmetic is unchanged.
- `parsePointPaste` keeps accepting `z r [angle]` in millimetres (people paste mm data) and the
  compact `z_cm;r_h_cm;r_v_cm` form. Convert to `t` on import.
- `normalizedImportedPoints`: "use imported length" sets the design's `length` from the imported
  span; "keep current length" keeps it. In **both** cases every imported point maps into `[0, 1]`
  by its fraction of the imported span. Today the "keep current length" path *drops* imported
  points that fall beyond the current length — that dropping must go, since it is the same
  destructive behaviour this batch exists to remove.
- Update the `importedLength` display and the `_absent`/store plumbing as needed.

### 7. `frontend/src/stores/design.ts`

- `FreeformPoint` becomes `{ t: number; r: number; angle_deg?: number }`.
- `DesignDocument` gains `length?: number`.
- `designForFamily('FREEFORM')`: `length: 120`, points `[{ t: 0, r: 12.7 }, { t: 1, r: 140 }]`
  on both planes, everything else as it is now.

## Constraints

- Match the surrounding style. The frontend is dense and terse; the server is explicit with
  docstrings that explain *why*.
- Do not rename FREEFORM, do not redesign the editor layout, do not touch the mesher, do not
  touch the pins.
- Do not reintroduce anything removed in W1 (tangent scales, per-anchor strength,
  `overshoot_policy`, the `circle` station shape).

## Tests

Update every affected test and add coverage for the new behaviour. Relevant files:
`server/tests/test_schema.py`, `test_migrate.py`, `test_textcfg.py`,
`test_preview_ws_translate.py`, `test_preview_ws_protocol.py`, and on the frontend
`frontend/src/design/FreeformEditors.test.ts`, `parameterRegistry.test.ts`, `ParamPanel.test.tsx`,
`designStoreRoundTrip.test.ts`, `frontend/src/stores/design.test.ts`, `design.serialize.test.ts`.

New coverage that must exist:

1. **The payload is unchanged.** A legacy z-mm design migrates and then translates to exactly the
   same mesher payload it produced before — including the `scale != 1` case, where the axial
   coordinate is `t * length * scale`. Write this as a fixture-driven test over several designs,
   one of which has interior anchors, a per-anchor angle, and a non-unit scale.
2. **Length is no longer destructive.** A design with interior anchors survives a length change
   with every anchor intact and still validates — specifically the 120 mm → 60 mm case with an
   anchor at 70 mm that produces an invalid payload today.
3. A legacy payload migrates once and reports its note once; an already-normalized payload does
   not trigger the migration.
4. A `.mwg` file with mm point rows loads, and re-serializing emits the same mm values (no float
   noise, no dropped rows).
5. Schema rejects: `points[0].t != 0`, `points[-1].t != 1`, non-monotonic `t`, `t` outside
   `[0, 1]`, and `length <= 0`.
6. Paste with "keep current length" no longer drops points beyond the current length.

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

Summarise: what you changed, the verbatim pass/fail counts from all three commands, the outcome
of test 1 (payload-identical) specifically, anything you could not get green and why, and
anything you decided differently from this brief and why.
