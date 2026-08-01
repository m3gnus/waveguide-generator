# Freeform H/V spline profiles — implementation plan

*2026-08-01. Status: PLAN — nothing implemented. Local doc, not yet committed/pushed.*
*Grounded against mesher `60301db` / WG `6ac96f1`. Review provenance in §9: three
Claude verifier passes + two codex passes (review + design research), all
findings re-verified before folding in. Rev 3: cross-section **shape stations**
and the **2-anchor tangent-spline** representation added per owner direction.*

## 0. The request

A Waveguide Generator user asked for the ability to design waveguides from **a
series of points (or splines) for the horizontal and vertical profiles**, rather
than only parametric formulas (OSSE / R-OSSE / ICW):

> "define arbitrary 2D profiles for the H and V axes, providing maximum degrees
> of freedom to finely optimize the geometry, which seems like a necessary step
> for dome tweeters and midranges. The intermediate angles would then be
> interpolated using azimuthal functions (e.g., forcing a circular cross-section
> at the throat, transitioning to a superellipse or custom function at the
> mouth, possibly with an intermediate blending function)."

They reference the BIGMEH-thread post
([post-8330236](https://www.diyaudio.com/community/threads/bigmeh-design-exploration.440373/post-8330236))
where the same idea was executed *manually*: a Fusion 360 loft from a circular
throat to a rectangular mouth with hand-tweaked guiding splines.

**Owner requirements (2026-08-01), which drive the Rev 3 design:**

1. Separate H and V profiles; the H and V cross-section views must follow the
   drawn profiles *exactly*.
2. Front view: circle at the throat → user-chosen shape at the mouth
   (ellipse / rectangle / rounded rectangle / superellipse), **plus at least
   one user-positioned intermediate shape station** — e.g. loft from circle to
   a rectangle reached at 40% of the length, then hold rectangular to the
   mouth. Every station position and shape user-adjustable.
3. Profiles must work with as few as **2 spline points** (throat + mouth), or
   more, with points in between.

## 1. Ground truth: what exists today

**The grid pipeline is already formula-agnostic and per-phi.** Every profile
family funnels into one representation: a sampled point grid
`inner_points (n_phi, n_length+1, 3)` built by `build_point_grid`
(`hornlab_mesher/profile_sampling.py:577-746`). `_raw_radial_grid`
(`profile_sampling.py:505-574`) re-evaluates the full 2-D meridian **per
azimuth** and stores per-phi `z` and `r` — non-monotonic z works end-to-end
(R-OSSE rollback proves it). Caveat for this feature: the per-phi loop fixes
one phi for an entire meridian; FREEFORM's per-ring angle grids (§2.2) need
their own dispatch path, not just another `elif` (§3.4).

**A point-based profile already exists but is axisymmetric and unexposed.**
The experimental `LOOKUP` formula (`_lookup_curve`,
`profile_sampling.py:475-502`) accepts a dense `[[z_mm, r_mm], ...]` polyline
(strictly increasing z, **caller owns the PCHIP fit** — the mesher only
`np.interp`s) and threads it through the identical grid pipeline. The WG
server rejects it everywhere (`server/solver/mesher_adapter.py:53-61`,
`server/api/routes_mesh.py:59,127,187`); there is no UI. Known holes if
exposed as-is: `profile_points` (`profile_formulas.py:668-687`) has **no
LOOKUP branch** — it falls into the R-OSSE `else` with default coefficients,
so the CircSym resolution budget (`_profile_arc_length_mm`,
`config_builder.py:1432-1440`) would be silently computed from a phantom
default meridian; and sparse pasted points produce faceted cone-segment
geometry unless someone densifies first.

**ICW contributed spline machinery a freeform family can reuse (with small
adapters, not verbatim):**

- `icw/seed.py:fit_from_points` / `fit_error` — polyline → curvature-spline
  fit with mm-deviation reporting (rollback-capable; needs ≥4 points, it is
  an approximation fit — not suitable as the primary representation, see
  §2.3).
- `icw/checks.py`: `meridian_self_intersects(x, r)` (checks.py:124) is
  directly polyline-compatible. `shell_offset_report` (checks.py:197) is
  **surface-of-revolution math** (circumferential curvature = `cos(theta)/r`,
  meridian-only offset test) — valid for FREEFORM only in its axisymmetric
  special case; the general surface needs true 2-D principal curvatures
  (§2.5). `feature_scale_ok` (checks.py:411) is B-spline-specific (reads
  knots/degree) — replace with an anchor-spacing guard.
- The DIRECT-mode input-validation template (`profile_formulas.py:506-546`)
  and the lossless LRU curve memo (`profile_formulas.py:327-414`).
- ICW lazily imports scipy (`profile_formulas.py:469`) — scipy in the fit path
  is fine as long as it stays off module import. The WG venv ships scipy
  1.17.1 with `CubicHermiteSpline` / `PchipInterpolator`.

**The morph machinery contributes the outline primitives** (verified reuse
table in §2.2): `_rounded_rect_radius(phi, half_width, half_height,
corner_radius)` (`profile_morph.py:124-148`) is a pure outline evaluator that
returns exactly `half_width` at phi=0 and `half_height` at phi=90° — including
the sharp-rectangle case at corner radius 0; `_rounded_rect_quadrant_angles`
(`profile_morph.py:368-423`) computes the ATH corner-arc azimuth structure
from one outline; the acoustic corner-arc subdivision channel
(`config_builder.py:2063+`) solves "fixed arc intervals don't shrink with
angularSegments".

**Azimuthal variation today is "one meridian family × modulation", never two
independent profiles.** Idioms: per-phi parameter expressions (`eval_param`,
`profile_common.py:201-209`); multiplicative superellipse scale ring
(`_superellipse_scale`, `profile_sampling.py:357-363` — dormant, WG hardcodes
exponent 2.0/aspect 1.0 at `mesher_adapter.py:301-304`); morph
(`profile_morph.py:229-273`); OSSE-only guiding curve
(`profile_morph.py:18-112`). **What does not exist anywhere: two or more
independently drawn profiles with azimuthal interpolation between them.**

### Prior art (ATH 4.8.2 + 231015 binary, local archive)

ATH reaches non-round shapes only indirectly: `f(p)` expressions on formula
parameters, `GCurve` (superellipse/superformula only), `Morph.TargetShape`
(rectangle/circle only, reached only *at* the mouth). Its only point-drawn
inputs are the axisymmetric **source contour** (Appendix B) and the
**enclosure plan** (Appendix C). There is **no import path for a point/spline
horn-wall profile**, and no mid-length shape stations — both are genuinely
novel. (One unknown: `profile_2g` / "2-profile geometry" strings in the 2023
binary, undocumented; worth a quick probe before announcing novelty
publicly.)

## 2. Design

New formula family **`FREEFORM`** (working name; `LOFT` also fits). Two
independent parts: the **profile splines** (what the H and V cuts look like,
§2.3) and the **cross-section shape schedule** (what the front view looks
like along the length, §2.2). They compose because every supported
cross-section outline passes exactly through the two profile radii.

### 2.1 Geometry model — overview

The user supplies, per plane (H = phi 0, V = phi 90°):

- **anchors**: `[[z_mm, r_mm], ...]`, 2-64 points, shared throat and mouth z
  across planes, equal throat radius (circle at the throat within 1e-6 mm);
- **end tangents**: throat tangent angle (default: the `a0` key / driver exit
  angle) and mouth tangent angle, each with an optional tangent-scale factor
  (§2.3);

plus a **cross-section station list** (§2.2). At every axial position t the
profiles give the semi-axes `a(t) = r_H(t)`, `b(t) = r_V(t)`; the active
station blend gives the outline shape; the radius surface is

```
r(phi, t) = (1 − w(u)) · ρ_k(phi; a(t), b(t)) + w(u) · ρ_k+1(phi; a(t), b(t))
```

where `ρ_k` is station k's normalized outline evaluated with the *local*
semi-axes and `w` is the C2 smootherstep `6u^5 − 15u^4 + 10u^3` over the
station span `u = (t − t_k)/(t_k+1 − t_k)`. Zero first *and* second
derivatives at both span ends make station joins C1+ with no rate parameter
at all (this supersedes Rev 2's `n(t)` exponent schedule and its
`n_mouth`/`blend_fixed`/`blend_rate` keys entirely).

Every outline family satisfies `ρ(0) = a` and `ρ(π/2) = b` **exactly**, so
every blend does too — the drawn H and V profiles are honored at every
station, every shape, every blend position. Shared z across planes means the
mouth ring is planar at max z, satisfying the enclosure contract
(`builders/enclosure.py:120`) and the IB planar frontmost-ring contract
(`builders/point_grid_dispatch.py:63-85`) with no special cases.

### 2.2 Cross-section shape stations

**Schema** (station list; first station locked):

```
crossSections: [
  { t: 0.0, shape: "circle" },                          // locked: throat is circular
  { t: 0.4, shape: "rounded_rectangle", cornerRatio: 0.12 },
  { t: 1.0, shape: "rounded_rectangle", cornerRatio: 0.12 },
]
```

- Shapes: `ellipse` (superellipse n=2), `superellipse` (`exponent` n ∈
  [2, 16]), `rounded_rectangle` (`cornerRatio` f ∈ (0, 1]; corner radius
  `c = f · min(a(t), b(t))`), `rectangle` (= `cornerRatio` 0 — **deferred to
  a fast-follow**, see "sharp corners" below). The throat station is the
  circle (serialized as ellipse; exactly circular because the throat radii
  are equal; UI labels it "Circle", locked).
- **Hold semantics**: two consecutive stations with the same descriptor blend
  identical outlines → the shape holds over that span. The owner's example is
  exactly the schema above: circle → rounded rectangle reached at t = 0.4 →
  held to the mouth. No extra `hold_until` key needed.
- Count 2-32; `t` strictly increasing, first = 0, last = 1; every station t
  merged into the axial sampling grid (§3.4).
- Default: `[{t:0, circle}, {t:1, ellipse}]` — pure profile-driven horn.

**Verified reuse of the morph machinery:**

| Component | Verdict |
|---|---|
| `_rounded_rect_radius` (`profile_morph.py:124`) | direct reuse — exact axis intercepts, handles c=0 |
| `_superellipse_scale` (`profile_sampling.py:357`) | reusable via base radius b + aspect a/b |
| `_morph_factor` (`profile_morph.py:229`) | do **not** reuse (single fixed target, non-C1 terminal); keep only the clamp idiom |
| `_rounded_rect_quadrant_angles` (`profile_morph.py:368`) | reuse the arc math **per ring**, not one global list |
| corner-arc subdivision channel (`config_builder.py:2063+`) | generalize and reuse |
| `_corner_arc_edge_mask` (`config_builder.py:1874`) | replace with a per-ring (2-D) mask |

**Per-ring angle grids (the key structural change).** Rounded-rectangle
tangency azimuths are `atan2(b−c, a)` / `atan2(b, a−c)` — they depend on the
local semi-axes, so as `a(t)`, `b(t)` grow at different rates the corner
azimuths *move along the horn*. A single mouth-derived angle list (morph's
approach — valid there because morph has exactly one target outline) would
let interior-station corners drift between pinned azimuths and alias.
FREEFORM therefore computes the structural angle list **per axial ring**
(constant row count and ordering, phi=0/90° rows always pinned so symmetry
snapping still works), and `build_point_grid`'s radial→XYZ conversion uses
`phi_grid[i, j]` instead of a fixed per-row phi
(`profile_sampling.py:673-700` must change). The point-grid/geometry contract
already stores arbitrary XYZ per node (`geometry.py:127`), so downstream
consumers are unaffected; the corner-arc **edge mask** becomes per-ring.

**Sampling / sagitta treatment (per shape, not global).** The angular
sagitta guard's disable condition keys on `morphTarget/gcurveType`
(`config_builder.py:2028-2036`) — FREEFORM with rectangle-class stations
would otherwise carry genuine near-vertices with the guard *enabled*, the
exact failure the disable exists to prevent. Correct treatment:

- ellipse / superellipse / rounded rectangle (c > 0): sagitta guard stays
  **active** (smooth outlines);
- rounded-rectangle corner arcs: route chord *and* sagitta error into the
  generalized arc-subdivision factor (`max(chordRatio, sqrt(sagittaRatio))`);
- sharp rectangle (c = 0): needs vertex pinning per ring, sagitta masked only
  on vertex-adjacent triplets, a crease mask, and OCC patch splitting at
  crease rows so the corner stays a CAD crease — **this is why c = 0 is a
  fast-follow, not Phase 1**. (BEM also meshes sharp corners badly; 3-D
  printed horns have a physical corner radius anyway. Phase 1 enforces
  `cornerRatio ≥ 0.02` and the fast-follow lifts it.)

**Convexity check.** A convex blend of radial functions is star-shaped but
not automatically convex; degenerate combinations (extreme aspect + tiny
corner ratio mid-blend) can go non-convex. Ingest runs a dense
polygon-convexity check per sampled ring and rejects with a pointer at the
offending station span. (A support-function blend would guarantee convexity
but discards the reusable outline machinery — not worth it.)

**"Exact" scoping (honesty requirement).** The analytic grid honors H/V and
the station outlines to machine precision, but the acoustic surface is an
OCC *approximating* B-spline fit through the control grid
(`config_builder.py:2008-2015`, `builders/_occ.py:122`) — the final
CAD/BEM surface is exact only where patch boundaries pin it. Phase 1:
report max OCC deviation against the analytic H/V curves in the build stats
(and test it); the fast-follow adds patch splitting at the phi=0/90° rows and
crease rows so the drawn profiles become true patch boundaries.

### 2.3 Profile splines — parametric cubic Hermite, 2+ anchors

**Representation** (codex-researched, alternatives ranked below): one
vector-valued **parametric cubic Hermite** curve per plane,
`P(u) = (z(u), r(u))` via `scipy.interpolate.CubicHermiteSpline`:

1. parameterize anchors by normalized cumulative chord length;
2. interior derivatives from PCHIP's monotone slope rules (no fabricated
   oscillation between anchors);
3. end derivatives replaced by the user's tangent handles:
   `P'(0) = s0·(cos θ_throat, sin θ_throat)`,
   `P'(1) = s1·(cos θ_mouth, sin θ_mouth)` — angles measured from the +z
   axis (ICW's convention, `icw/core.py:223`), scales `s` = automatic chord
   speed × user `tangentScale ∈ (0, 3]`;
4. evaluate on shared-z stations by inverting the monotone `z(u)`.

Why this beats the alternatives:

- **2 anchors are genuinely useful**: throat + mouth + two tangent angles
  span the classic flare families (a plain PCHIP through 2 points is a
  straight cone — useless; that killed PCHIP-only as the representation).
- **Parametric, not scalar r(z)**: an exact 90° mouth tangent (vertical
  baffle landing) is representable; scalar r(z) would need `tan(90°)`.
- **Anchors lie ON the curve** (unlike Bézier control polygons or ICW's
  curvature spline, which approximates and needs ≥4 points,
  `icw/seed.py:65`) — "the curve passes through my points" UX, and clean
  fixed-length optimizer gene vectors.
- **CAD-familiar UI for free**: each endpoint tangent is mathematically a
  cubic-Bézier handle at `P ± Δu·P'/3` — the Phase-2 editor renders drag
  handles exactly like Fusion; numeric angle fields mirror them.
- Ranked alternatives: full piecewise Bézier (best interaction, 2 extra
  handles per segment → gene/validation blow-up), scalar r(z) Hermite (no
  vertical mouth tangent), ICW curvature B-spline (fair but
  non-interpolating), plain PCHIP (cone at 2 points). The cubic-Bézier z-map
  (`_ath_default_zmap`, `profile_sampling.py:242-272`) is precedent for the
  inversion step.

**Config keys** (per plane, nested block):

```
profileH: { points: [[z,r],...], throatAngleDeg, mouthAngleDeg,
            throatTangentScale: 1.0, mouthTangentScale: 1.0 }
profileV: { ... }
```

`throatAngleDeg` defaults from `a0` (driver exit angle — this also feeds
`source_auto_angle_deg`, §2.4, making the throat tangent an explicit user
parameter instead of a derived one); `mouthAngleDeg` defaults to the last
chord direction.

**Validation**: 2-64 anchors; finite, `r > 0`, strictly increasing anchor z;
shared H/V start/end z; equal throat radii (1e-6 mm); endpoint angles ∈
[−90°, 90°] in Phase 1 (beyond 90° = rollback, Phase 4); per-segment analytic
derivative checks `z'(u) ≥ 0` (zero only at the endpoints — permits the 90°
mouth tangent while keeping z monotone) and `r(u) > 0`; between-anchor
overshoot rejected by default with an explicit `overshootPolicy: "allow"`
escape (waists are legitimate but must be opted into); warn when H and V
throat angles differ materially (the source cap is spherical, §2.4).

### 2.4 Throat & source-cap contract — and the dome question

The source cap assumes a near-circular planar throat ring (mean-radius
sphere, `builders/point_grid_sources.py:381-414`) with curvature from `a0` at
phi=0, silently defaulting to 15.5° (`config_builder.py:1686,2302`).
FREEFORM:

- enforces the circular throat (equal first-anchor radii + locked circle
  station);
- **primary dome control**: `sourceRadius`/`sourceCurv` already thread
  end-to-end (contract `server/contracts/__init__.py:488-490` → adapter
  `mesher_adapter.py:336-338` → `_source_cap_radius` priority,
  `point_grid_sources.py:385-394`) — set the cap radius to the *physical dome
  radius*. Expose these next to the profile fields in the Phase-1 UI.
  **Caveat (verified)**: the builder silently raises the cap radius to
  ≥ `throat_radius × 1.001` (`point_grid_sources.py:400-403`) — validate
  `sourceRadius ≥ throat radius` at ingest (scale-aware) and surface
  requested-vs-effective radius with an explicit warning otherwise;
- `source_auto_angle_deg` comes straight from `profileH.throatAngleDeg`
  (§2.3) — no tangent derivation needed anymore.

The full point-drawn **source contour** (ATH Appendix B; rejected today at
`mesher_adapter.py:125-131`) stays a separate feature — but it is arguably
the other half of "necessary for dome tweeters", so it is listed in Phase 4
with a note to reassess pulling it forward once FREEFORM lands.

### 2.5 Validation & guards (ingest-time)

Per plane, on the dense evaluated meridian: `meridian_self_intersects`; an
anchor-spacing guard (min z-spacing vs local mesh resolution — replaces the
B-spline-specific `feature_scale_ok`); the §2.3 derivative checks. Per ring:
the §2.2 convexity check. **Curve-deviation report with a defined metric**:
the Hermite curve interpolates anchors exactly, so the meaningful readout is
the max normal distance between the curve and the piecewise-linear anchor
polyline per segment (how much the smooth curve bellies away from the drawn
polyline) — surfaced through mesher metadata → viewport response
(`build_viewport_geometry` emits only fixed fields today,
`mesher_adapter.py:650-656`) → UI (§4.1 item 6, §4.2).

**Wall-offset regularity (the ICW check does not transfer):**
`shell_offset_report` is surface-of-revolution math (`icw/checks.py:197-215`)
— wrong principal curvatures for a blended non-revolution surface, and the
max-curvature azimuth shifts off 45° for unequal axes. Phase 1 instead:
(a) compute both principal curvatures of the blended surface `S(z, phi)` on
the dense grid by finite-difference second fundamental form, across all
azimuths, and apply the `|t·kappa_i| < margin` regularity precheck there;
(b) validate the *actual generated* outer offset grid (`_outer_offset_shell`,
`profile_sampling.py:419-472`) for normal flips and self-intersection;
(c) keep `shell_offset_report` only for the axisymmetric special case.
Enclosure mode inherits the existing hard checks
(`_reject_front_baffle_wall_intersections`, roundover clamps) unchanged.

Failure policy: raise `ConfigError`/`ValueError` with actionable text (ICW
DIRECT-mode template); never build a bad grid and let gmsh discover it.

### 2.6 Interaction with morph / gcurve / cross-section (value-based exclusion)

`FREEFORM` rejects **active** azimuthal shapers: `morphTarget` resolving to
1/2, gcurve active (reuse `_gcurve_could_be_active`,
`config_builder.py:387-393`), cross-section exponent/aspect overrides, `rot`,
`h`-bulge, throat extension/slot keys. The check must be **value-based, not
key-presence-based**: WG always sends `morph_target: 0`
(`waveguidePayload.js:139`), so the presence-based
`_validate_formula_specific_keys` template (`config_builder.py:265-326`)
copied verbatim would reject every WG payload. Rationale: the station system
(§2.2) *is* the morph generalization — it does everything morph does
(including rounded-rect mouths) plus mid-length stations, so composing the
two would double-shape. This exclusion is now permanent, not provisional.

### 2.7 What the model deliberately cannot express (named limits)

- **Sharp rectangle corners in Phase 1** (`cornerRatio ≥ 0.02`); exact
  creases are the named fast-follow (§2.2 sharp-corner treatment).
- **No diagonal/corner control beyond the corner ratio**; mouth aspect is
  fixed by `r_H(1)/r_V(1)`.
- **No azimuth-dependent axial depth/stationing, and no axial twist.** The
  grid stores a separate z curve per phi and OSSE evaluates lengths per
  azimuth (`osse_length_config(params, p)`, `profile_formulas.py:180-186`) —
  OSSE designs with `p`-expressions on lengths have phi-dependent z stations
  that shared-z FREEFORM cannot reproduce. (Twist stations are a candidate
  future feature, §8.5.)
- **Full mirror symmetry about both planes is a deliberate Phase-1 scope
  cut**, intrinsic to the outline families. Asymmetric top/bottom profiles
  (relevant to MEH mid-port layouts) are a cheap Phase-4 extension: distinct
  `r_V_top`/`r_V_bottom` half-plane profiles stay continuous across phi=0/π,
  at the cost of xz-mirror quadrant reduction — the client symmetry resolver
  already handles half-symmetry outcomes
  (`src/modules/design/symmetry.js:145-176`).
- Rollback (non-monotone z / tangent angles beyond 90°) excluded in Phase 1
  (§6 Phase 4).

### 2.8 Where the fit lives (decision)

**The mesher owns the spline evaluation** (anchors + tangents in config →
Hermite construction + dense sample inside the mesher, lazy scipy,
LRU-memoized), unlike LOOKUP where the caller owns it. Rationale: three
callers (WG UI, optimizer server-free path, hand-written configs) must
produce identical geometry from the same control data; duplicating the
construction invites parity drift. LOOKUP stays as-is for
archived/generated configs. Config stores *anchors + tangent angles +
stations* — compact, human-editable, diff-able.

## 3. Implementation — mesher (hornlab-waveguide-mesher)

New module `hornlab_mesher/freeform.py` (spline construction + station
outlines + blending + validation, ~350 lines) plus registration touch-points
(ICW commit `382a5c0` is the template):

1. `_normalise_formula` ×2: `profile_common.py:297-305`,
   `config_builder.py:254-262` — add `"FREEFORM"`.
2. Key/feature gating per §2.6 — presence-based rejection for foreign profile
   *coefficients*, **value-based** rejection for morph/gcurve/cross-section
   activity.
3. Param threading branch in `build_geometry_params`
   (`config_builder.py:894-993`). Keys: `profileH`/`profileV` blocks (§2.3),
   `crossSections` station list (§2.2), optional `a0` (feeds the throat-angle
   default), `overshootPolicy`.
4. **Grid construction — a dispatch, not an `elif`.** The current per-phi
   loop fixes one phi per meridian (`profile_sampling.py:505-574`); FREEFORM
   needs per-ring angle grids (§2.2), so `_raw_radial_grid` dispatches at the
   top to `_freeform_raw_radial_grid`: memoized splines → merged axial map →
   per-ring `phi_grid` + feature masks → station blend per node.
   `build_point_grid`'s radial→XYZ conversion must consume `phi_grid[i, j]`
   (`profile_sampling.py:673-700`). Sampling rules:
   - the axial map **merges every H/V anchor z and station t** (the acoustic
     loop only measures already-sampled chords and forces the ATH z-map when
     no custom map exists, `config_builder.py:2025-2026`; a feature between
     stations is invisible to refinement) plus a fidelity test comparing
     midpoint radii against direct spline evaluation;
   - ring resolution grading should use the slice map, not ring index
     (`config_builder.py:2058` grades by index even for nonuniform maps);
   - angular refinement: sagitta guard stays active for smooth outlines;
     corner arcs route `max(chordRatio, sqrt(sagittaRatio))` into a
     generalized per-ring arc-subdivision channel (§2.2). The global
     `angularSegments` retargeting (`config_builder.py:2180-2183`) still
     applies — a small-corner-ratio mouth is an angular-budget cost; test a
     `cornerRatio = 0.02` large mouth converges within the caps.
5. `profile_points` dispatch (`profile_formulas.py:668-687`) — return the
   H-plane meridian. Callers: the CircSym arc-length budget
   (`config_builder.py:1436`), ICW seeding (`icw/seed.py:238,255`), and the
   legacy loft builders (`builders/osse_waveguide.py:22`,
   `builders/rosse_waveguide.py:42`). (The missing **LOOKUP** branch here is
   a live bug for Phase 0 — LOOKUP currently falls through to R-OSSE
   defaults; fix both.)
6. CircSym: **no changes needed for correctness** —
   `circsym_rejection_reasons` (`config_builder.py:1561-1583`) delegates to
   `build_meridian`, `_axisymmetric_rejection_reasons`
   (`config_builder.py:1270-1306`) never keys on formula, and the
   `_radial_profile_from_grid` span check catches H≠V at runtime with a
   usable reason. Optional polish: a cheap `profileH≠profileV` /
   non-ellipse-station early reason for a better auto-mode log line; test
   that an axisymmetric FREEFORM actually takes the CircSym path.
7. OCC surface: Phase 1 reports max OCC-vs-analytic H/V deviation in build
   stats (§2.2 "exact" scoping); the multi-patch helper
   (`builders/point_grid_surfaces.py:426`) is the extension point for the
   fast-follow's axis/crease patch splitting.
8. `config_parser.py` (ATH text ingest): Phase 1 **rejects** FREEFORM from
   the text format, extending the `:146-151` message (which today names only
   ICW) to name FREEFORM and point at the dict/contract path. Parsing
   `Freeform.*` blocks in the mesher's text parser is deferred until the WG
   `.mwg` block syntax (§4.2) has settled — one format, defined once.
9. **One shared FREEFORM validator, called from both entry points**:
   `build_geometry_params` *and* `build_point_grid` — the low-level grid
   builder duplicates its own formula gates (the gcurve rejection at
   `profile_sampling.py:581-585` names only R-OSSE/ICW) and is called by
   paths that bypass the config builder.
10. Peripheral surfaces that enumerate formulas: the experimental cabinet
    bridge (`experimental/cabinet.py:47,68`) — document FREEFORM-unsupported
    until Phase 3; update the CLI help text that advertises only OSSE/R-OSSE
    (`cli.py:60`).
11. Docs: `docs/config-schema.md` (new family table),
    `docs/geometry-contract.md` (station/blend semantics, per-ring angle
    grid, throat/mouth contract).

Tests (mesher):
- Golden math: H==V + all-ellipse stations reproduces the axisymmetric
  radius exactly; phi=0/90° honor the drawn profiles at every station shape
  and blend position; rounded-rect outline matches `_rounded_rect_radius`
  closed form; smootherstep hold spans are bit-constant.
- 2-anchor curves: throat+mouth+tangents reproduce expected flare shapes
  (golden values); 90° mouth tangent lands vertically.
- **Round-trip acceptance**: sample a morph-free monotone OSSE (tritonia)
  from its *built grid*, feed as FREEFORM anchors, assert grid agreement
  within tolerance; then (manual, once) solve both and compare polars.
- Quadrant-vs-full grid equality (mirror-symmetry invariant) — now also
  exercising per-ring angle grids.
- The owner's example: circle → rounded rect at t=0.4 → hold; assert the
  cross-section at t=0.4 and t=0.7 matches the rect outline exactly.
- `cornerRatio = 0.02` large-mouth acoustic-topology build converges within
  the attempt/segment caps.
- Corner-azimuth/surface curvature wall-offset guard fires on a
  tight-corner + thick-wall case; convexity rejection case.
- Enclosure + IB + freestanding e2e builds, watertight/open-edge checks.
- Ingest validation: every rejection path (anchor count/monotonicity/
  overshoot, station ordering/params, throat mismatch, tangent ranges),
  curve-deviation reporting, OCC deviation stat.

## 4. Implementation — WG server + frontend

### 4.1 Server

1. `server/contracts/__init__.py:389-498` — add **nested** FREEFORM models
   (`profileH`/`profileV` blocks, `crossSections` list) to
   `WaveguideParamsRequest`. **Hazard (verified)**: the model has no
   `model_config`, so pydantic-v2 `extra="ignore"` applies. A *stale deployed
   server* fails loudly — the three mesh routes 422 immediately on their own
   allowlists (`routes_mesh.py:59,127,187`); `/api/solve` fails later, at job
   time, when `_normalize_formula` raises. The **silent** hazard is
   per-field: forgetting one new key in the contract silently drops it and
   meshes with the default — wrong geometry, no error. Cover with a
   payload-threading round-trip test asserting every new key survives
   `WaveguideParamsRequest(**payload).model_dump()`.
2. Route allowlists ×3: `server/api/routes_mesh.py:59,127,187`; also add the
   missing formula gate on `/api/solve`
   (`services/simulation_validation.py:56-99`) for a clean 422 instead of a
   job-time error.
3. `_normalize_formula` (`mesher_adapter.py:53-61`) + a FREEFORM branch in
   `waveguide_payload_to_mesher_config` (`:186-263`) forwarding only FREEFORM
   keys.
4. Explicit-CircSym validator `server/solver/axisymmetry.py:94-108` — reject
   explicit `solver_mode='circsym'` for non-axisymmetric FREEFORM (auto mode
   is already safe via the mesher probe at `:38-55`).
5. Auto-quadrants: `src/modules/design/symmetry.js:145-176` — FREEFORM is
   mirror-symmetric by construction, but **do not return early**: the
   resolver reduces symmetry *after* the angular scan for asymmetric
   enclosure spacing/resolution and non-zero `verticalOffset`
   (`symmetry.js:163-172`). Seed `xSymmetric = zSymmetric = true` for
   FREEFORM, skip only the angular-expression scan, and fall through to the
   enclosure/offset checks unchanged.
6. Viewport response: add the §2.5 curve-deviation and OCC-deviation metrics
   to `build_viewport_geometry` metadata (`mesher_adapter.py:650-656` emits
   only fixed fields today) so the frontend can display them.
7. Server tests: formula gates incl. `/api/solve`, payload threading
   round-trip (item 1), viewport route incl. the new metadata fields,
   back-compat (old configs revalidate; new keys Optional/defaulted).

STEP export (`build_inner_surface_step`, `mesher_adapter.py:520-565`) and the
viewport route are point-grid-based — both work once the allowlists admit the
family. **No JS geometry port**: follow the ICW precedent, add FREEFORM to
`SERVER_ONLY_FORMULAS` (`src/modules/geometry/useCases.js:22`); the viewport
renders via `POST /api/mesh/viewport` (Hermite evaluation + outline blending
is far cheaper than the ~1 s ICW homotopy the route already tolerates).
`geometry-parity.test.js` needs no new JS cases; add snake/camel aliases to
`scripts/eval_profiles.py:38-64` only if blend-math parity coverage is wanted.

### 4.2 Frontend, Phase 1 (functional, not fancy)

File list = the ICW *arc*, *not just* the first commit: `abaa54f` (10 files)
**unioned with the `2c24d0a` follow-up**, which is where the integration
fixes landed. Concretely:

- `schema.js` (new `FREEFORM` group + `FORMULA_FIELD_ALLOWLIST.FREEFORM = []`;
  needs two new field types: a `points` table and a `stationList`),
  `parameterInventory.js` (`whenTypes`; **hide the MORPH group** for
  FREEFORM), `paramPanel.js:381` (selector), `waveguidePayload.js`,
  `useCases.js` (SERVER_ONLY).
- `defaults.js` — **mandatory** `morphTarget = 0` override (ICW pattern,
  `defaults.js:25-30`): the shared MORPH default is `morphTarget = 1`
  (`schema.js:473-481`), so without the override a fresh FREEFORM design is
  rejected by §2.6 out of the box.
- `src/geometry/params.js` — **scale policy**: `SCALE_LENGTH_KEYS`
  (`params.js:21-36`) scales only scalar mm keys; anchor arrays pass through
  untouched. Scale the `profileH`/`profileV` anchor arrays in
  `prepareGeometryParams` (tangent *angles* are scale-invariant; ICW hit this
  exact class — `2c24d0a` had to add `depth`).
- `src/modules/export/index.js` + `tests/export-module.test.js` — the
  server-only rejection (`assertLocalGeometryFormula`, `:36-45`; config
  export hard-reject `:464-472`, STL `:360`, CSV `:407`, added in `2c24d0a`)
  **blocks the planned `.mwg` round-trip** unless FREEFORM is carved out of
  the config-export rejection. Carve out config export; keep STL/CSV rejected
  in Phase 1.
- `src/export/mwgConfig.js` — a FREEFORM serializer branch
  (`Freeform.ProfileH/V` + `Freeform.CrossSections` blocks, syntax modeled on
  ATH Appendix B/C point lists) — plus `src/config/index.js` import. Three
  verified traps: (a) **double emission** — the parser stashes unknown blocks
  into `result.blocks` → `params._blocks` → re-exported verbatim
  (`mwgConfig.js:198-214`); the importer must consume-and-delete `Freeform.*`
  from `parsed.blocks` (or add to the export skip-list next to
  `Mesh.Enclosure`); (b) **value coercion** — `coerceConfigParams`
  stringifies every value (`src/geometry/params.js:65-69`), which would
  flatten anchor arrays into comma strings; the import path must
  preserve/validate nested arrays (add `params.js` to the import touch
  list); (c) **shared-key duplication** — the parser's mesh/source/simulation
  key normalization is duplicated *inside* the OSSE and R-OSSE family
  branches (`src/config/index.js:56-64,265`), so a FREEFORM branch must
  either get its own copy or the shared mapping gets refactored out first.
  Round-trip test: structural array equality + all shared fields.
- *Not* needed: `scene.js` — the viewport cache key derives from
  `Object.keys(PARAM_SCHEMA[type])` (`scene.js:45-55`) automatically;
  `SUPPORTED_MODEL_TYPES` also derives from the schema (`state.js:15-17`).

Point/station entry, Phase 1:
- **anchors**: a `points` textarea/table field (paste `z r` pairs). Accepted
  formats, specified up front: 2-col whitespace/comma `z r` in **mm**, and
  the 3-col semicolon profile-CSV export format — noting the **unit trap**:
  the existing exporter multiplies by 0.1 (cm output,
  `src/export/profiles.js:12-23`), so the importer must convert cm→mm for
  that format (and new exports should gain a units header). Define the
  plane-selection/column-mapping rule explicitly, and test reimported
  dimensions. External imports (BLab exports, traced/measured horns) out of
  scope beyond these two formats.
- **tangents**: numeric angle + scale fields per plane end (the visual
  handles come in Phase 2).
- **stations**: an editable list control — position slider (throat/mouth
  rows locked at 0/1), shape dropdown, per-shape parameter field. The
  owner's circle→rect@40%→hold case must be constructible in this plain UI.
- **"Convert current design to FREEFORM"** — the on-ramp. **Sample the built
  point grid** (phi=0 and phi=90° rows of `inner_points` from
  `build_point_grid`), *not* `profile_points`: the raw formula meridian
  misses morph/gcurve/h-bulge/cross-section shaping, and R-OSSE (the WG
  default) has non-monotone z that FREEFORM rejects. Verified plumbing
  requirements:
  - a **dedicated grid request / conversion use case**:
    `prepareBackendViewportMesh` discards the raw grid after tessellation
    (`useCases.js:180-189`);
  - **un-scale before storing**: prepared params are scaled before payload
    creation (`params.js:231-249`) — divide grid coordinates by the active
    `scale` before storing anchors, or they get scaled twice. Test with
    `scale ≠ 1`;
  - **decimate**: the smooth viewport grid has 81 axial stations
    (`tessellation.js:11-14`), over the ≤64-anchor contract — tolerance-based
    decimation, and fit end tangents from the sampled end slopes;
  - a morphed source design also implies a station list: seed
    `crossSections` from the source's morph settings (rect/rounded-rect at
    t=1 with the morph's corner radius) so the converted design matches;
  - for rollback designs, trim at the max-z station with an explicit message
    ("rollback lip dropped — see Phase 4").
  Document which design classes convert losslessly: scalar-parameter
  morph-free monotone OSSE/ICW yes; designs with `p`-expressions on lengths
  no (phi-dependent z stations, §2.7); morph mouths now convert *well*
  (station list) but blend position differs (morph's power blend vs
  smootherstep) — approximate, reported; R-OSSE rollback truncated.
  Conversion tests for each class.
- `sourceRadius`/`sourceCurv` surfaced next to the profile fields (§2.4).

### 4.3 Frontend, Phase 2 — interactive spline editor

Nothing exists to build on (results charts are backend PNGs; the only
interactive canvases are Three.js), so this is a new self-contained
SVG/canvas component: draggable anchors, **endpoint tangent handles rendered
as Bézier handles** (§2.3 equivalence), H/V tabs or overlay, mm grid, anchor
insert/delete, numeric mirroring of every handle, live curve +
curve-deviation readout. **Station scrubber**: a draggable t-slider that
shows the blended cross-section outline at that position in a small
front-view inset — this is how the user *sees* "rectangle reached at 40%".
Anchors/stations live in `params`, so undo (50-deep) and localStorage
persistence come free via `state.js`; edits throttle-trigger the existing
backend viewport rebuild (`scene.js:162-230` handles supersession/cooldown).
Overlays worth having early: ghost of the previous parametric design;
wall-thickness offset curve; enclosure box outline. Pure frontend; ships
independently after Phase 1.

## 5. Optimizer integration (Phase 3)

The request's stated motivation is "maximum degrees of freedom to finely
optimize". Contract (verified in hornlab-optimizer):

- **Genes** (fixed-dimension vector, anchor z positions fixed per run —
  the gene space rejects dimension mismatches,
  `bem_optimizer/genes/space.py:92`):
  `H.r[1..N−1], V.r[1..M−1], H.throatAngle, H.mouthAngle, V.throatAngle,
  V.mouthAngle` (+ optional tangent scales; + optional continuous station
  genes: positions, superellipse exponent, corner ratio). Station *shape
  enums stay configuration*, not genes. Throat radii and end z stay locked.
  6-8 anchors/plane ⇒ 12-20 genes — consistent with the 6-13-gene cluster of
  successful runs. Do **not** reuse LOOKUP's sort-and-repair decode
  (`optimization/lookup.py:224`) for movable z anchors — it can swap
  semantic anchors mid-run.
- **Builder**: register an experiment builder calling
  `hornlab_mesher.config_builder.build_from_config` directly (ICW-cabinet
  pattern, `run_ab_cabinet_full.py:120-151`) or the WG server-free
  `build_waveguide_mesh` path (m_12 pattern). The builder contract
  (`optimization/experiment.py:5-12`) is profile-agnostic;
  `WaveguideParams.__post_init__` (`waveguide_geometry/params.py:402-423`)
  and `MeshGenerationService` routing (`mesh/generator.py:117-226`) must
  learn the family for the dashboard path.
- **Guards**: reuse §2.5 metrics through `candidate_metadata`
  (`_icw_geometry_metrics` pattern) as floored soft guard terms; raise for
  infeasible genomes (penalty path); NaN handling is already correct
  end-to-end.
- **Hard-won lessons** (tritonia LOOKUP smoke,
  `experiments/tritonia_spline_lookup/RESULTS.md:35-40`): a point-profile
  search *lost to its own OSSE seed* without seed preservation. Before any
  serious run: warm-start from an OSSE-sampled seed (`cma.py:788-807` +
  `es.inject`), restore **gen-0 elitism** in `ExperimentSpec` (still
  missing — standing caveat from the icw-phase2a merge), and prefer narrow
  per-gene bounds around the seed (m_12 refinement pattern).

## 6. Phasing & effort

| Phase | Deliverable | Size | Depends on |
|---|---|---|---|
| **0** (optional — see note) | Expose LOOKUP end-to-end. True cost is *not* server-only: `profile_points` LOOKUP branch (CircSym budget bug, §3.5) + a densification owner + allowlists/contract/textarea. | ~1 session | — |
| **1a** | Mesher tranche: FREEFORM module (Hermite splines + station outlines + smootherstep blend + per-ring angle grids + §2.5 validation) + sampling fidelity + OCC-deviation stat + tests. | 2-2.5 sessions | — |
| **1b** | Integration tranche: server plumbing incl. `/api/solve` gate; UI (anchor table + tangent fields + station list + formats/units), convert-from-built-grid use case (un-scale + decimate + station seeding), `.mwg` round-trip (parser arrays + shared-key refactor + export-module carve-out), scale/defaults fixes, deviation display; round-trip acceptance vs OSSE. | 1.5 sessions | 1a |
| **1c** (fast-follow) | Sharp rectangle (`cornerRatio = 0`): crease masks, per-ring vertex pinning, sagitta masking on vertex triplets, OCC patch splitting at axis/crease rows (`point_grid_surfaces.py:426` extension point). | ~1 session | 1a |
| **2** | Interactive 2-D spline editor: anchors + Bézier-style tangent handles + station scrubber + overlays, live preview. | 1.5-2 sessions, frontend-only | 1 |
| **3** | Optimizer: FREEFORM builder + guards + seeded CMA experiment; fix gen-0 elitism first. | 1-2 sessions | 1 |
| **4** (later, each its own decision) | Asymmetric half-plane profiles (top≠bottom — cheap, §2.7); rollback (parametric per-plane curves, tangents beyond 90°); N guide profiles at intermediate azimuths / per-phi Fourier (the ICW seam); axial twist stations (§8.5); dome **source contour** import (ATH Appendix B parity — reassess priority once FREEFORM lands, §2.4). | — | 1 |

Phase-0 recommendation: **fold into Phase 1** — once the mesher owns the
spline evaluation, exposing LOOKUP separately buys little.

Phase 1 acceptance: tritonia OSSE → convert (built-grid sampling) → solve
reproduces the OSSE polars within tolerance; the owner's
circle→rounded-rect@40%→hold design builds in all three modes
(freestanding/enclosure/IB), solves under quadrant symmetry, exports STEP,
and its t=0.4/t=0.7 cross-sections match the outline exactly; a 2-anchor
design with tangent handles builds and solves; `cornerRatio = 0.02`
converges; conversion tests pass for morph-OSSE (station-seeded) and
R-OSSE-rollback (truncation messaging).

## 7. Release checklist (the parts that bite)

- **Pin bump** (mesher changes): push mesher → `npm run deps:bump-pins` →
  hand-update `README.md:74-77`, `docs/PROJECT_DOCUMENTATION.md:373-376`,
  `server/README.md:140-143`, `tests/docs-parity.test.js:9-11`,
  `server/solver/deps.py:20-35`,
  `server/tests/test_dependency_runtime.py:156-158` → `npm test` +
  `npm run test:server`.
- **Parallel Windows session**: three WG PRs open as of 2026-08-01 — #5
  (ABEC3 comparison), #6 (mesh ceiling 50k — touches the *same
  `WaveguideParamsRequest` block* Phase 1 edits; rebase the contract change
  after it lands), #7 (the install.sh/macOS backport). Coordinate before
  pushing anything.
- Stale-server behavior (verified): a deployed server without the feature
  fails **loudly** — immediate 422 on the three mesh routes, job-time
  `_normalize_formula` error on `/api/solve`; the silent hazard is an
  individual field missing from the contract (per-field `extra="ignore"`
  drop) — covered by the §4.1 round-trip test.
- Docs alongside code: `docs/config-schema.md`, `docs/geometry-contract.md`
  (mesher); `docs/modules/geometry.md`, `PROJECT_DOCUMENTATION.md` feature
  list (WG).

## 8. Open decisions (owner's call)

1. **Name**: `FREEFORM` vs `LOFT` vs `SPLINE` (user-facing label "Freeform
   (H/V splines)"?).
2. Fold Phase 0 into Phase 1 (recommended) or ship LOOKUP exposure first?
3. Phase-1 corner-ratio floor 0.02 acceptable, with sharp corners as the 1c
   fast-follow? (Recommendation: yes — BEM meshes sharp corners poorly and
   printed horns have a physical radius anyway.)
4. Reply to the requester now with direction + rough timeline? Draft:
   *"Yes — planned. The approach: pick spline points for the H and V
   profiles (as few as two, with tangent handles at throat and mouth, or
   convert an existing design into editable points); the front-view shape is
   a list of stations — circle at the throat, then your choice of
   ellipse/superellipse/rounded rectangle at any positions along the length,
   e.g. reach a rectangle at 40% and hold it to the mouth — and your drawn H
   and V curves are honored exactly in their planes throughout. The same
   BEM/viewport/STEP pipeline consumes it. First version keeps a small
   minimum corner radius on rectangles (sharp creases follow), and an
   interactive spline editor plus CMA optimization over the control points
   come after the core lands."*

### 8.5 Candidate future features (beyond Phase 4)

- **Cross-section area / equivalent-radius plot + optional monotonic-area
  guard** — the acoustically meaningful quantity; a shape change can create
  an area contraction even while both H/V radii grow. Cheap to compute from
  the grid, high design value (codex suggestion, endorsed).
- **Axial twist/rotation stations** (rotated rectangles, installation
  constraints) — composes with the per-ring angle grid naturally.
- **H/V linking modes** (fixed aspect, fixed area, mirrored tangents, shared
  normalized profile) — one-click symmetric editing in the Phase-2 editor.
- **Multi-resolution optimization** — optimize a coarse anchor set, insert
  anchors where curvature/acoustic sensitivity warrants, re-optimize
  (pairs with the §5 warm-start machinery).
- **Inverse design** — fit FREEFORM anchors to a *target coverage curve*
  (the ICW `build_linear_constraints`/`feasible_subspace` idiom applied to
  the new family): "give me the profile that holds 80° to 8 kHz".
- **Per-azimuth profile export** (CSV/DXF of any phi cut) for CAM/CAD
  verification of the blended surface.
- **Crease-vs-fillet policy** surfaced explicitly (sharp CAD crease vs
  manufacturable fillet propagated consistently to viewport, BEM, STEP).

## 9. Review provenance

Drafted from six parallel subsystem ground-truth reports (mesher
profiles/morph, ICW machinery, WG frontend, WG server, optimizer contract,
ATH prior art), then adversarially reviewed by three independent Claude
verifier passes (code-fact check, completeness-vs-request, integration
gotchas; 24 confirmed findings folded in), then a codex (`gpt-5.6-sol`)
review pass (1 P0 — `shell_offset_report` is surface-of-revolution math — +
7 P1s, all re-verified before folding; one codex claim corrected:
`profile_points` is also consumed by the legacy loft builders). Rev 3 adds
the owner's shape-station and 2-anchor requirements, designed via a second
codex research pass (station outlines + smootherstep blending + per-ring
angle grids + parametric-Hermite representation; adopted with two
modifications — station minimum is 2 not 3, and hold semantics come from
duplicate stations instead of `hold_until`) with the key reuse claims
(`_rounded_rect_radius` axis-exactness, corner-angle structure,
scipy availability) re-verified directly.
