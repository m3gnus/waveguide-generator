I have comprehensive evidence. Writing the report.

# FREEFORM audit — mesher `7baf134..45d91ad`, WG `3f7301c..1bd0dca`

**Verification baseline.** Both suites are green on my run: mesher `535 passed, 23 skipped` (227 s, via the WG venv), WG `584 passed, 0 failed`. I re-verified the prior review's C1–C4 and C6 as fixed: absolute corner radii are now validated across their full active span (`freeform.py:1042-1085`), intermediate rounded rectangles now drive per-ring sampling regardless of the mouth shape (`profile_sampling.py:719-721`, `any(...)` over all stations), interior anchors are clamped to the current length on every state write (`freeformParams.js:105-110` invoked from `state.js:75,137,170` and `params.js:224`), explicit CircSym now compares whole profile blocks including angles and scales (`axisymmetry.py:101-107`), and both nested FREEFORM contract models carry `extra="forbid"` (`contracts/__init__.py:391,412`). **C5 is not fixed** and is the subject of §2(b).

---

## 1. What's left / where it works poorly

### F1 — A FREEFORM design cannot be saved to a file at all. *(highest impact)*

State lives only in `localStorage` and the 50-deep undo ring. Every file-producing path is closed:

- `.mwg`/config export hard-rejects: `src/modules/export/index.js:463-472` throws `"FREEFORM config export is not supported: the .mwg/ATH parameter config format has no FREEFORM representation…"`. `src/export/mwgConfig.js:53` has exactly one type branch (`R-OSSE` vs everything-else-is-OSSE); the `else` would emit `Coverage.Angle = undefined` / `Throat.Diameter = NaN` for FREEFORM state, which is why the guard exists.
- Import is symmetric: `src/config/index.js` has no `Freeform.*` block handling; type detection keys off `Coverage.Angle`/`Length`/`Term.n` only, so a hypothetical file yields `type: null` → `"Could not find OSSE or R-OSSE block in config file."`
- STL (`export/index.js:360`) and profile-CSV (`:407`) are rejected by `assertLocalGeometryFormula` (`:36-44`).
- Only STEP survives — it is ungated on the frontend (`runStepExportTask`, `export/index.js:265-356`, no formula guard) and admitted by `routes_mesh.py:127-133`. But STEP is *geometry*, not parameters: you cannot reopen it as an editable design.

So: hand-tune 20 anchors, clear site data or move machines, and the work is gone. This is worse than any pre-existing family. **Fixed looks like:** carve FREEFORM out of the `export/index.js:463` rejection and add a `Freeform.ProfileH/V` + `Freeform.CrossSections` serializer/parser pair. Watch the two verified traps: `coerceConfigParams` (`src/geometry/params.js:70-78`) `String()`s every value, flattening `interiorH: [[40,60,12,1]]` into `"40,60,12,1"`, and the shared mesh/source key normalization is duplicated inside the OSSE and R-OSSE branches of `src/config/index.js:56-64,265`.

### F2 — The absolute corner radius is silently capped by the *throat* radius, making realistic rounded-rect mouths inexpressible from the UI. *(highest impact)*

The C1 fix over-corrected. `_validate_station_corner_radii` (`freeform.py:1059-1085`) requires `cornerRadiusMm ≤ min(a,b)` at every sampled `t` where the station carries nonzero blend weight. For the plan's own canonical owner schedule `[circle@0, rr@0.4, rr@1.0]`, station index 1 has nonzero weight in *both* spans, so its active z range is the entire horn — and the minimum of `min(a,b)` over the whole horn is the throat radius.

Measured, on the mesher's own `_owner_config` (throat 12.7, mouth 160×110):

```
cornerRatio 0.12  → effective corner 5.89 mm @t=0.4, 9.21 @t=0.7, 13.20 @t=1.0   (builds)
cornerRadiusMm=12.7 → OK
cornerRadiusMm=13.2 → REJECT "…exceeds the maximum allowed value 12.7 mm over its
                      active z range [0, 120] mm"
```

The equivalent-in-mm form of the shipped reference design is rejected. On a 140×140 mouth, a 30 mm corner is refused; on the same design a mouth-only station (index 2 of 3) is capped at 42.3 mm while the mid station is capped at 12.7 mm — yet the "hold" contract requires the two to be *identical*. The only workaround is inserting a dummy `ellipse` station at mid-length to shorten the active span; it is undocumented, non-obvious, and the error text gives the number without the cause or remedy.

This is aggravated by the fact that **the WG UI only ever writes `cornerRadiusMm`** — `paramPanel.js:913-916` seeds `cornerRadiusMm: 10` on shape change and the `cornerRatio` form is read-only legacy (`paramPanel.js:973-985`). Every mesher build test, by contrast, uses `cornerRatio`. The two forms are not interchangeable and the UI has only the broken one.

**Fixed looks like:** validate the mm radius against `min(a,b)` weighted by the station's blend weight (a station at weight ~0 near the throat imposes no constraint), or validate only over the span between the station and its immediate neighbours, or accept clamping with a reported `effectiveCornerRadiusMm` range instead of rejecting. Any of these; the current rule is the one behaviour that cannot express a real horn.

### F3 — The convexity guard rejects ordinary designs with an unactionable message.

Measured sweep, circle→rounded-rect at the mouth, 120 mm long, 140×100 mouth:

```
corner  2,3,4 mm  → REJECT "crossSections span 0..1 produces a non-convex outline
                    near t=0.75; adjust its shape, aspect, or corner setting"
corner  5..12.7   → OK
corner 15+        → REJECT (F2 throat cap)
```

The usable window is **5–12.7 mm**, and neither endpoint is discoverable. A user asking for a crisp 3 mm rectangular mouth — the whole point of the feature — is told the outline is "non-convex near t=0.75", a position they did not choose, about a shape they cannot see (§3a). Note `cornerRatio: 0.05` fails the same way while `0.25` passes, so the guard is a genuine geometric limit of the smootherstep blend of a circle into a tight-cornered rectangle, not a validation artifact. **Fixed looks like:** report the minimum feasible corner radius in the message (it is cheap to bisect), and point at the blend span visually rather than by `t`.

### F4 — No "Convert current design to FREEFORM". *(highest impact for adoption)*

Definitively absent — no implementation, no stub, in `src/` or `server/`. Without it the on-ramp is: build a 20-anchor horn by clicking "Add point" twenty times. This is the single item most responsible for the feature feeling unusable, and it is much cheaper than the plan assumed (see §3b).

### F5 — The editor's S-curve display and the mesher's diagnostic describe different curves.

`src/geometry/freeformCurve.js:192-282` finite-differences a ~96-point resampled polyline; `freeform.py:1116-1148` uses exact spline derivatives over 4001 samples. I swept 3570 two-interior-anchor profiles:

- **11 badge flips** — JS shows nothing, Python reports a 1.02–1.15° drop. All sit exactly on the 1.0° suppression threshold, as predicted.
- **One 14.5° / 75% disagreement** from *span merging*, not noise. Profile `[[0,12.7],[40,70],[120,140]]`, throat 68°, mouth 60°:
  - JS: one span, `z 0 → 74.66 mm`, **33.70°**
  - Python: two spans, `z 0 → 39.19 mm` @ **19.20°** and `z 40 → 75.08 mm` @ **14.52°**

  The anchor at z=40 is a knot where the exact curvature sign flips; the 96-point resample smooths across it. So under `inflectionPolicy: reject` the badge says "S-curve in H: 33.7 deg" and the rejection says `z=0.000..39.189 mm with a 19.20 deg tangent-angle drop`. Two different numbers, two different ranges, one curve.
- Baseline case from the prior review reproduces: JS `4.551°`, Python `5.021°` on the same span (10% error).

There is **no parity test**: `tests/freeform-curve.test.js` has 7 tests, all self-consistency, none against the mesher.

### F6 — Every diagnostic the mesher computes is thrown away before the user sees it.

- `mesher_adapter.py:722-728` attaches the full `build_freeform_geometry(...).report()` to `metadata["freeform"]` on every viewport response (I confirmed `meta_freeform=True` on live builds). `useCases.js:193` carries it through as `metadata`. `scene.js:181` then does `toRenderMesh(viewportMesh, variant)`, which returns only `{vertices, indices, normals, preparedParams}`. **Zero frontend consumers of `metadata.freeform` exist.**
- `freeformProfileDeviationMm` — the OCC-vs-analytic wall deviation that §2.2 of the plan calls the "honesty requirement" — is computed in `mesher.py:104-132`, asserted `< 0.25` in `test_freeform_builds.py:104,117`, and never leaves the mesher. It is not in the viewport metadata (the viewport path never runs the OCC fit) and not in any WG surface.
- `maxNormalDeviationMm` (5.5 mm H / 6.2 mm V on my baseline — a large, design-relevant number) is likewise computed and dropped.

### F7 — Anchor entry is one row at a time; the promised paste/import never shipped.

`createPointsControl` (`paramPanel.js:631-841`) builds a numeric table with an "Add point" button. Plan §4.2 specified a textarea accepting 2-column `z r` in mm and the 3-column semicolon profile-CSV, with the cm→mm trap handled. None of it exists. Relatedly `src/export/profiles.js:15,37` still applies the undocumented `scale = 0.1` (mm→cm) with no units header, so even a round trip through the app's own CSV would be silently 10× off.

### F8 — Any invalid intermediate edit blanks the 3-D viewport and prints raw mesher text.

`scene.js:207-217`: FREEFORM is server-only, so on a 4xx the code calls `clearViewportMesh(app)` and `reportMeshBuildFailure`, which pushes `error.backendDetail` verbatim into the param panel strip (`paramPanel.js:1153`). Combined with F2/F3 — where nudging a mouth radius can cross the convexity boundary — the loop reads: drag, release, 3-D disappears, `FREEFORM crossSections span 0..1 produces a non-convex outline near t=0.75`. **Fixed looks like:** keep the last valid mesh dimmed, and attach errors to the offending anchor/station rather than a text strip.

### F9 — The endpoint tangent handles are not truthful CAD handles.

`tangentKnob` (`freeformProfileEditor.js:511`) hardcodes `armLength = max(8, min(length, radiusMax) * 0.18)` — independent of `throatTangentScaleH/V` and `mouthTangentScaleH/V`, which are four real schema fields (`schema.js:391-397, 401-407, 434-440, 444-450`) editable only as sliders elsewhere in the panel. Dragging the knob writes angle only (`onPointerMove`, `:877-888`). Interior anchors *do* get angle+strength from one handle (`:859-876`) — but only for the currently selected anchor (`drawTangentHandles:627-641`), so the shape of the curve between two anchors cannot be inspected without clicking each one.

Also: every handle carries `tabindex: 0` but the editor registers no `keydown`/`keyup` listener anywhere. Handles are focusable and inoperable — a keyboard/AT dead end.

### F10 — `inflectionPolicy` exposes a user-visible option that does nothing.

`schema.js:466-480` offers three choices: *Warn on S-curves* / *Enforce one-way* / *Free*. Only `reject` has behaviour (`freeform.py:1251-1253`). `allow` and `warn` produce byte-identical geometry and identical `report()` output; they differ only in the memo key (`freeform.py:43`), as `test_freeform_core.py:619` asserts. The mesher's own rejection text advertises both: *"change inflectionPolicy to 'warn' or 'allow'"* (`freeform.py:1160-1161`).

### F11 — Export failures are toasts, not affordances.

The four export menu items (`index.html:231-242`) are plain buttons with no `disabled` and no `title`; `events.js:232-241` dispatches unconditionally. A FREEFORM user clicks "Export STL", waits, and gets a toast telling them to "use a backend-backed export instead" — which names nothing they can click. STEP, the one export that *does* work, is not distinguished in any way.

### F12 — Zero documentation, anywhere.

`grep -rni freeform docs/ README.md examples/` in the mesher returns nothing. `docs/config-schema.md:20` still lists the accepted formulas as OSSE/R-OSSE/ROSSE/ICW/LOOKUP; there is no FREEFORM key table, so `profileH`, `profileV`, `crossSections`, `overshootPolicy`, `inflectionPolicy`, `cornerRatio`, `cornerRadiusMm` are undocumented. `docs/geometry-contract.md` has per-family sections for OSSE/R-OSSE/ICW and none for FREEFORM — the station/blend semantics and per-ring angle grid exist only in the plan. No `examples/*.toml`. The WG side is the same.

### F13 — Peripheral enumerations left stale.

- `config_parser.py:134-151` correctly rejects FREEFORM from ATH text ingest, but the message names only ICW as the dict-only family — a text-config user gets a message that doesn't mention their formula.
- `cli.py:63` still says *"Build an OSSE or R-OSSE waveguide mesh"* (cosmetic; FREEFORM configs do build through the CLI).
- `experimental/cabinet.py:47-56` has its own `_normalize_formula` over `{OSSE, R-OSSE, LOOKUP}` and rejects FREEFORM. Planned, undocumented.
- `profile_sampling.py:815` gates gcurve on `formula in {"R-OSSE", "ICW"}`; FREEFORM is covered indirectly by the line-698 validator, which is a load-bearing ordering dependency with no comment.

### F14 — Optimizer: nothing. Phase 3 untouched.

`grep -ril freeform` returns 0 files in `hornlab-optimizer` and `hornlab-sim` (the 14 hits in `hornlab-research` are unrelated pre-existing prose). Expected per §6, but worth stating: the request's stated motivation — "maximum degrees of freedom to finely optimize" — is not yet served at all.

### F15 — Test blind spots.

Coverage is genuinely good (37 core + 11 grid + 6 build + 11 server tests, including enclosure/IB/freestanding watertight builds, the OCC deviation stat, the wall-curvature guard, and the outer-offset normal-flip guard). Missing:

- **JS↔Python curve/inflection parity** — the gap that lets F5 exist.
- **Convexity rejection** — `freeform.py:1262-1273` has no test; `grep -rn "non-convex" tests/` is empty. Only the passing case is asserted. Given F3, this is the guard most in need of characterization tests.
- **CircSym actual build** — only `circsym_rejection_reasons` is exercised (`test_freeform_builds.py:172`); `build_meridian` is never called with a FREEFORM config, so the throat-angle branch at `config_builder.py:1796-1801` is untested.
- Outer-offset *self-intersection* branches (`freeform.py:977-996`); `wallThickness > 0 && encDepth > 0` (guard-skip path); FREEFORM via `viewport.build_viewport_geometry_from_config` in-mesher; Phase 1c sharp corners not started.

### F16 — Performance is fine; two small notes.

Measured, steady-state, through `build_viewport_geometry`:

| case | latency |
|---|---|
| ellipse-only, 64×60 | 9.2 ms |
| rounded-rect ×2, 64×60 | 14.0 ms |
| rounded-rect ×2, 180×120 | 30.6 ms |
| `build_freeform_geometry`, 64 anchors/plane | 18.3 ms |
| `build_freeform_geometry`, 32 rounded-rect stations | 28.2 ms |

Nothing here is a bottleneck. Two caveats: the lazy `scipy` import costs **~295 ms on the first FREEFORM request per server process** (my first cold call was 299 ms vs 4 ms warm), which reads as a hang on the very first edit; and the editor's `onPointerMove` (`freeformProfileEditor.js:832-891`) calls `draw()` → full `clearNode` + node rebuild + both plane curves + `computeInflectionSpans` on every pointer event, unthrottled by rAF. Neither is urgent. The editor commits only on `pointerup`, so there is exactly one viewport round trip per drag — that design choice is correct and should be kept.

---

## 2. Assessment of the four proposed simplifications

### (a) Consolidate the ~4 frontend interior-point normalizers — **DO NOW · effort M**

**They are not merely duplicated; they disagree.** Four implementations of the same normalization, with four different out-of-range policies and two different caps:

| site | sorts | z out of range | cap |
|---|---|---|---|
| `freeformParams.js:18` + `:49` | yes | **clamps** to `[1, length−1]`, dedups collisions | none |
| `paramPanel.js:157` | yes | **ignores** | `.slice(0, 62)` |
| `freeformProfileEditor.js:75` | yes | **ignores** | `.slice(0, 62)` |
| `waveguidePayload.js:75-106` | yes | **throws** | none |

Migration is invoked at four call sites (`state.js:75,137,170`, `params.js:224`). The clamp-vs-ignore-vs-throw split is exactly the class of bug C3 was — it was fixed by making one of the four authoritative on write, which papers over the divergence rather than removing it.

**Design:** one `src/config/freeformModel.js` exporting `normalizeAnchor`, `normalizeAnchorList(list, {length})`, `normalizeStations`, `toWirePayload(params)`, `fromWirePayload`. One out-of-range policy (clamp-on-write, throw-on-serialize is defensible if the clamp is guaranteed upstream — but pick one and state it). All four sites import it; `paramPanel.js`'s `normalizeInteriorPoints`/`normalizeStations` and the editor's `normalizedInterior` delete outright; `waveguidePayload.js:74-160` shrinks to the camel→snake mapping.
**Deletes:** three normalizer bodies (~90 lines), the duplicated sort/filter/cap logic, and — once `.mwg` round-trip lands (F1) — the branch-only `profileH/profileV` migration in `freeformParams.js:75-116` can go too.
**Risk:** low. Only risk is dropping developers' pre-W3 `localStorage` designs, which is acceptable on an unmerged branch.
**Rationale:** this is the one simplification that removes latent correctness divergence rather than just lines, and it is a prerequisite for the `.mwg` work in F1.

### (b) Backend-authoritative curve/diagnostic data — **DO NOW · effort M**

The premise checks out on both ends: the server already ships `metadata.freeform` on every viewport response (`mesher_adapter.py:722-728`), and the client already has it in hand at `useCases.js:193` before discarding it at `scene.js:181`. And the divergence is not cosmetic — §1/F5 shows badge flips at the policy threshold and one **33.70° vs 19.20°+14.52°** span-merging disagreement where the badge and the rejection message describe different geometry.

**Design:** add `curveSamples: {H: [[z,r],…], V: […]}` to the report alongside the existing `inflectionSpans`/`maxNormalDeviationMm`/`anchorTangents` (`freeform.py:175-208` — one `plane.spline(u)` call, cheap). Thread `metadata` through `toRenderMesh`/`applyVariantIfCurrent` in `scene.js:181-190` into `paramPanel.freeformEditor.setAuthoritative(metadata.freeform)`. The editor keeps `buildFreeformDisplayCurve` strictly as an optimistic in-drag preview (rendered dashed / at reduced opacity), and swaps to the authoritative samples + spans on every landed response. Delete `computeInflectionSpans` (`freeformCurve.js:186-282`, ~96 lines) entirely — nothing else uses it.
**Deletes:** the JS inflection implementation, the finite-difference derivative helper, and the whole class of "which number is right" bugs. Also gives you F6's deviation readouts for free.
**Risk:** medium-low. Highlighting lags by one round trip (~15 ms server + network) during a drag; that is exactly the behaviour you want, and the dashed-preview convention makes it legible. One design decision below in §3.
**Rationale:** it deletes a mirrored numerical implementation that is measurably wrong, using a data channel that already exists end-to-end.

### (c) Collapse the mesher's triple FREEFORM gating — **DO LATER (partial) · effort S→M**

The three sites are `_validate_formula_features` FREEFORM branch (`config_builder.py:453-507`), the post-`common` `_validate_freeform_config(common)` (`config_builder.py:1103-1107`), and the `build_point_grid`-path call (`profile_sampling.py:698`).

Sites 2 and 3 are literally the same function, differing only in call site and exception type (`ConfigError` vs `ValueError`) — that pair is genuinely redundant on the config path and should stay only because site 3 is the *sole* gate for direct `build_point_grid(params)` callers. That is defensible and cheap; leave it.

**Site 1 is not the pure subset the review assumed.** Two verified behavioural deltas:

- `rot`/`h`/`throatExtLength`/`throatExtAngle`/`slotLength`: site 1 uses `_param_is_nonzero` (`config_builder.py:739-750`), which treats any non-float-coercible expression as active. Site 2 does `eval_param(..., 0.0, 0.0)` — **phi = 0 only** (`freeform.py:1330-1332`). An expression that is zero at phi=0 and nonzero elsewhere is caught today and would be missed after a naive collapse.
- gcurve: site 1's `_gcurve_could_be_active` fires on bare gcurve *keys* with no resolvable type/width; site 2 requires `gcurveType ∈ {1,2}` **and** `gcurveWidth > 0`.

Site 1 also runs against raw config sections with full alias resolution (`morph_target`/`morphTarget`, `cross_section_exponent`/`exponent`, sourced from `cross` **or** `profile` **or** top-level `config`), whereas site 2 reads only `common["profileSystem"]["crossSection"]`. Collapsing requires first proving that `common` carries everything site 1 can see.

**Verdict rationale:** the cleanup is real but it is a *behaviour-preservation* exercise disguised as a deletion, and it buys the user nothing. Do it when the alias surface is next touched — and when you do, port the phi-sweep and the key-presence gcurve check into the canonical validator first, then delete site 1, with a test per delta.

### (d) Drop the unpublished `cornerRatio` and `inflectionPolicy='allow'` aliases — **SPLIT**

**`inflectionPolicy: 'allow'` → DO NOW · effort S.** It is a no-op alias for `warn` (`freeform.py:1228-1233`; `test_freeform_core.py:619` asserts the identity), it costs a memo-cache dimension, and — contrary to "unpublished" — it is **user-visible** as the "Free" option in `schema.js:476` and is recommended in the mesher's own rejection text (`freeform.py:1160-1161`). Reduce to `warn | reject`, relabel the dropdown to two options, fix the error string, drop the test. Risk: only configs created on this branch.

**`cornerRatio` → SKIP as stated; revisit only after F2 is fixed · effort S when unblocked.** The review's premise — that `cornerRadiusMm` is a strict replacement — is false today. Measured: the plan's own canonical owner design uses `cornerRatio: 0.12`, which produces 5.89 → 13.20 mm along the horn; its mm equivalent (13.2) is **rejected** because `_validate_station_corner_radii` caps station 1 at the throat radius, 12.7 mm. Every build test in `test_freeform_builds.py` uses the ratio form. So `cornerRatio` is currently the *only* representation that can express a proportionally-widening corner on a 2- or 3-station schedule — i.e. the only one that can express a real horn — while the UI writes only the broken form.

Deleting it now would break the reference design and leave the user with no working path. **Sequence it:** fix the active-span cap (F2), confirm the owner schedule builds with `cornerRadiusMm: 13.2`, migrate the mesher build tests to mm, *then* delete the ratio branch (`freeform.py:733-750, 784-798`), the mixed-corner branches, and the legacy-ratio UI at `paramPanel.js:973-985`. As one change it is small; as sequenced it is correct.

---

## 3. Assessment of the three endorsed UX items

### (a) Scrubbable cross-section inset showing the true blended outline — **effort M**

Today `drawStations` (`freeformProfileEditor.js:410-430`) draws a vertical line plus a text label — `"round rect"` — and that is the entire visual account of the feature. The circle→rect→hold concept, the convexity limit in F3, and the corner-radius cap in F2 are all invisible.

**Sketch.** New component `src/ui/freeformCrossSectionInset.js`, mounted by `paramPanel.js:412-417` beside the existing editor in the `core-profile` section, sharing the editor's `params` clone and its `state:updated` subscription. A second SVG (square viewBox, front view, one quadrant mirrored ×4) plus a `t` scrubber whose track is horizontally aligned with the profile editor's z axis (`transforms.x`), so the scrub cursor is a single vertical line drawn across *both* panels. Render the outline by sampling `cross_section_radius(phi, t)`.

Where the radii come from is the one decision. Two options, and I would pick the second:
1. Port the station blend to JS (`_station_radius` + `_smootherstep` + `_resolve_active_station_blend`) — ~80 lines, instant scrubbing, and a *fifth* mirrored numerical implementation, directly against the grain of §2(b).
2. **Derive it from the grid the client already has.** The viewport response carries `grid.inner_points` shaped `(n_phi, n_length+1, 3)` plus `angle_list` and `slice_map` (I confirmed all three on live responses). Scrubbing = picking the nearest axial ring and drawing its `(x,y)` — exact, zero new math, and it shows the *sampled* outline, which is what actually gets meshed and solved. Interpolate between rings for smooth scrubbing.

Option 2 requires keeping the raw grid on the `useCases.js:189-195` return — the same one-line change §3(b) needs. Overlay the two profile radii as crosshairs at phi=0/90° so the "H and V are honored exactly" contract is visible, and shade the polygon red when `metadata.freeform` flags the span non-convex.

**Owner decision:** does the inset show the *analytic* outline or the *sampled* one? They differ by the angular discretization, and the sampled one is more honest but will visibly facet at low `angularSegments`. My recommendation is sampled, with the analytic curve as a thin ghost.

### (b) "Convert current design to FREEFORM" — **effort M (cheaper than the plan assumed)**

Plan §4.2 lists a "dedicated grid request / conversion use case" as a verified plumbing requirement because `prepareBackendViewportMesh` discards the raw grid. **That requirement is already satisfied** — the grid is in the JSON, it is only dropped client-side. Verified on live R-OSSE and OSSE responses:

```
grid keys: angle_list, full_circle, grid_n_length, grid_n_phi, inner_points,
           morph_corner_arc_span, outer_points, quadrants, sampling_mode,
           slice_map, symmetry_planes, vertical_offset_mm
inner_points → (64, 41, 3);  angle_list[0] = 0.0 exactly, angle_list[16] = π/2 exactly
R-OSSE phi=0 row z-monotone: False   (last z = 34.88 mm — the rollback lip)
OSSE   phi=0 row z-monotone: True
```

So no new endpoint, no new server code. The work is entirely in `src/modules/geometry/useCases.js` and one new use case:

1. `useCases.js:189-195` — return `grid: payload.grid` alongside `metadata` (one line; also unblocks §3a).
2. New `src/modules/design/convertToFreeform.js`: reshape `inner_points` by `grid_n_phi`, pick rows via `angle_list` (exact 0 and π/2 always present), and take `(hypot(x,y), z)` per station.
3. **Un-scale** by `preparedParams.scale` before storing — `params.js:242-289` scales `length`/`throatRadius`/`mouthRadiusH/V`/`interiorH/V`/`cornerRadiusMm` on the way out, so storing raw grid coordinates double-scales. Test at `scale ≠ 1`.
4. **Truncate rollback**: cut at the max-z index of the phi=0 row. The WG *default* is R-OSSE, whose phi=0 row is non-monotone (34.88 mm lip, measured above), so this path is the common case, not the exotic one. Message it explicitly.
5. **Decimate** 41→≤62 interior anchors by tolerance (Douglas-Peucker on the meridian), then fit `throatAngle`/`mouthAngleH/V` from the end slopes and `mouthTangentScale*` from the end chord speeds.
6. **Seed `crossSections`** from the source's morph settings when `morphTarget ∈ {1,2}` — and here F2 bites: a morph corner radius converted to `cornerRadiusMm` will very likely exceed the throat radius and be rejected. Either fix F2 first or seed the ratio form.
7. Report before/after max deviation using `maxNormalDeviationMm` from `metadata.freeform`.

**Owner decisions:** (i) does convert replace the current design or open a copy — I would replace, since undo is 50-deep and free; (ii) is a lossy conversion (morph blend-law mismatch, rollback truncation) allowed to proceed with a warning, or must it be confirmed in a dialog first. Given F8 blanks the viewport on failure, I'd confirm before replacing.

### (c) Draggable station lines with depth in mm — **effort S**

Stations are normalized `t` everywhere the user touches them: the numeric input is `min=0 max=1 step=0.01` (`paramPanel.js:857-863`), and the editor's station line is drawn but carries no interaction (`freeformProfileEditor.js:415-422` — a bare `<line>` with `data-station-index` and no `data-handle`, so `onPointerDown:806` ignores it). Horn designers reason in mm.

**Sketch.** In `drawStations`, widen the hit area (a transparent `<rect>` of ~10 px around the line), add `data-handle="station"` and `data-index`, and extend the existing `onPointerDown`/`onPointerMove`/`onPointerUp` switch with a `station` case that maps `transforms.z(screenX) → t = z / geometry.length`, clamps between the neighbouring stations with a small epsilon, and commits `crossSections` on pointerup. First and last stations are locked — render them dimmed and non-draggable, matching `position.disabled = isFirst || isLast`. The drag readout bubble already exists (`drawDragGuides:758-777`); add a `station` branch showing `"t 0.40 · 48.0 mm"`. In the label at `:423-428`, append the mm depth. In `paramPanel.js:846-870`, add a second read-only mm field next to the `t` input (or make it a bidirectional mm input that divides by `length` on commit — better, and only a few more lines).

This shares all the machinery §3(a) needs for its scrubber, so build them together. **Owner decision:** when `length` changes, do stations hold their `t` (current behaviour, shape schedule stretches) or their mm depth (shape schedule stays put, `t` re-derives)? The mm-first UI makes the second reading natural and it is not what the code does today.

---

## 4. Recommended execution order

1. **Fix the corner-radius active-span cap** (F2) — weight-aware or neighbour-span validation, plus the corner-window bounds in the error text. *M.* Everything about rounded rectangles is blocked behind this, including §3b's station seeding and §2d's `cornerRatio` deletion.
2. **Characterization tests for the convexity guard and the corner window** (F3, F15) — including the exact 5 mm / 12.7 mm boundaries I measured, so the fix in (1) cannot silently move them. *S.*
3. **Convert current design to FREEFORM** (§3b, F4) — the adoption on-ramp, and cheaper than planned since `grid.inner_points` already ships. Includes the `useCases.js` grid passthrough. *M.*
4. **`.mwg` round-trip + config-export carve-out** (F1) — designs become savable and shareable. Do the frontend model consolidation (§2a) as the first commit of this item, since the serializer needs one canonical shape anyway. *M–L.*
5. **Backend-authoritative curve + diagnostics; delete the JS inflection mirror** (§2b, F5, F6) — surfaces `maxNormalDeviationMm` and `freeformProfileDeviationMm` in the same pass. *M.*
6. **Draggable station lines with mm depth** (§3c, F7-adjacent) — small, high daily value, shares machinery with 7. *S.*
7. **Scrubbable cross-section inset** (§3a) — driven from the grid, reusing 3's passthrough and 6's scrubber. *M.*
8. **Anchor paste/import + CSV units header** (F7) — 2-col `z r` mm and the 3-col semicolon format, and add the units header to `src/export/profiles.js`. *S–M.*
9. **Error UX pass** (F8, F11) — keep the last valid mesh dimmed instead of blanking, attach mesher errors to the offending anchor/station, disable non-working export buttons with a reason and point FREEFORM users at STEP. *M.*
10. **Docs** (F12) — `docs/config-schema.md` FREEFORM key table, `docs/geometry-contract.md` station/blend/per-ring-angle-grid section, one `examples/freeform-*.toml`, and the WG feature list. Do it with 4 so the file format is documented as it lands. *S–M.*
11. **Drop `inflectionPolicy: 'allow'`** (§2d, F10) — two-option dropdown, fix the mesher's rejection text. *S.*
12. **Drop `cornerRatio`** (§2d) — only after 1 lands and the build tests migrate to mm. *S.*
13. **Endpoint tangent handles honour and edit tangent scale** (F9) — make the four scale sliders reachable from the handles; add keyboard handlers for the `tabindex` elements while in the file. *S–M.*
14. **Peripheral cleanup** (F13) — `config_parser.py` message names FREEFORM, `cli.py` help text, `experimental/cabinet.py` documented-unsupported, comment the `profile_sampling.py:815`/`:698` ordering dependency. *S.*
15. **Collapse the triple gate** (§2c) — port the phi-sweep and key-presence gcurve checks into the canonical validator, then delete the `_validate_formula_features` branch, with a test per behavioural delta. *M.* Do it here, not earlier: no user benefit, and it is a behaviour-preservation exercise.
16. **Phase 1c sharp corners** and **Phase 3 optimizer** (F14) — both still fully deferred; neither is blocked by anything above, but both are larger than everything above.