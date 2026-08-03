# V1 inputs completeness audit

**Verdict: FAIL.** V2 registers all 110 claimed legacy parameter names, but it does not provide complete editable parity. The largest gaps are solve/polar submission, Freeform editing, expression entry, export management, result controls, and viewer preferences.

Audit was read-only; nothing was modified.

Sources: [v1 inventory](</Users/magnus/Code/hornlab-workspace/Waveguide Generator/src/ui/parameterInventory.js:1>), [v1 schema](</Users/magnus/Code/hornlab-workspace/Waveguide Generator/src/config/schema.js:1>), [v1 parameter UI](</Users/magnus/Code/hornlab-workspace/Waveguide Generator/src/ui/paramPanel.js:501>), [v1 settings](</Users/magnus/Code/hornlab-workspace/Waveguide Generator/src/ui/settings/modal.js:550>), [v1 polar UI](</Users/magnus/Code/hornlab-workspace/Waveguide Generator/src/ui/simulation/polarSettings.js:1>), [v2 registry](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/design/parameterRegistry.ts:73), [v2 panel](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/design/ParamPanel.tsx:50), [v2 solve submission](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/frontend/src/jobs/actions.ts:54), [v2 request schema](/Users/magnus/Code/hornlab-workspace/waveguide-generator-v2/server/jobs/models.py:18).

Counting rules:

- Family-specific profile keys are separately counted; shared keys are counted once, matching the claimed 110-entry inventory.
- Repeated Freeform points/stations are counted once under their parent parameter.
- Separate selectors, checkboxes, transient editor choices, file inputs, result controls, and viewport controls are separate rows.
- Pure commands such as Run, Delete, Reset, and manual Export are excluded unless they set a persistent/view/design value.
- `†` means v1 accepts ATH expressions but v2 exposes only a numeric field. These 43 rows are present at the row level but do **not** have full value-domain parity.

## The gap table — centralized design parameters

### Family/profile inputs

| V1 location · label · key | V2 status | V2 mapping / finding |
|---|---|---|
| ParamPanel · Model Type · `type` | **RENAMED(to Family)** | `design.formula`; editable family selector |
| R-OSSE · Scale · `scale` | **PRESENT-editable** | Profile → `scale` |
| R-OSSE · Mouth Radius (R) · `R`† | **PRESENT-editable†** | Profile → `R`; expression entry lost |
| R-OSSE · Mouth Coverage Angle (a) · `a`† | **PRESENT-editable†** | Profile → `a`; expression entry lost |
| R-OSSE · Throat Coverage Angle (a0) · `a0`† | **PRESENT-editable†** | Profile → `a0` |
| R-OSSE · Throat Radius (r0) · `r0`† | **PRESENT-editable†** | Profile → `r0` |
| R-OSSE · Throat Rounding (k) · `k`† | **PRESENT-editable†** | Profile → `k` |
| R-OSSE · Apex Shift (m) · `m`† | **PRESENT-editable†** | Profile → `m` |
| R-OSSE · Bending (b) · `b`† | **PRESENT-editable†** | Profile → `b` |
| R-OSSE · Apex Radius (r) · `r`† | **PRESENT-editable†** | Profile → `r` |
| R-OSSE · Shape Factor (q) · `q`† | **PRESENT-editable†** | Profile → `q` |
| R-OSSE · Truncation Limit (tmax) · `tmax`† | **PRESENT-editable†** | Profile → `tmax` |
| OSSE · Scale · `scale` | **PRESENT-editable** | Profile → `scale` |
| OSSE · Horn Length (L) · `L`† | **PRESENT-editable†** | Profile → `L` |
| OSSE · Mouth Coverage Angle (a) · `a`† | **PRESENT-disabled(active guide mode)** | `a`; v1 remains editable, v2 disables it when guide mode is 1/2 |
| OSSE · Throat Coverage Angle (a0) · `a0`† | **PRESENT-editable†** | Profile → `a0` |
| OSSE · Throat Radius (r0) · `r0`† | **PRESENT-editable†** | Profile → `r0` |
| OSSE · Flare Constant (k) · `k`† | **PRESENT-editable†** | Profile → `k` |
| OSSE · Termination Shape (s) · `s`† | **PRESENT-editable†** | Profile → `s` |
| OSSE · Termination Curvature (n) · `n`† | **PRESENT-editable†** | Profile → `n` |
| OSSE · Termination Smoothness (q) · `q`† | **PRESENT-editable†** | Profile → `q` |
| OSSE · Shape Factor (h) · `h`† | **PRESENT-editable†** | Profile → `h` |
| ICW · Scale · `scale` | **PRESENT-editable** | Profile → `scale` |
| ICW · Throat Radius (r0) · `r0` | **PRESENT-editable** | Profile → `r0` |
| ICW · Throat Half-Angle (a0) · `a0` | **PRESENT-editable** | Profile → `a0` |
| ICW · Horn Length (L) · `L` | **PRESENT-editable** | Profile → `L`; v2 incorrectly leaves it visible during rollback |
| ICW · Mouth Radius (R) · `R` | **PRESENT-disabled(coverage hold enabled)** | `R`; v2 treats it as emergent |
| ICW · Coverage Angle · `coverage_angle` | **PRESENT-disabled(rollback)** | Geometry extras → `coverage_angle`; v1 hides it during rollback |
| ICW · Coverage Hold Start · `hold_start` | **PRESENT-editable** | Geometry extras → `hold_start`; v2 hides it when coverage is zero |
| ICW · Coverage Hold End · `hold_end` | **PRESENT-editable** | Geometry extras → `hold_end`; same visibility difference |
| ICW · Curvature Coefficients · `n_coeff` | **PRESENT-editable** | Profile → `n_coeff` |
| ICW · Termination · `termination` | **PRESENT-editable** | Geometry extras → `termination` |
| ICW · Rollback Curl Angle · `theta1_deg` | **PRESENT-editable** | Visible only for rollback, matching v1 |
| ICW · Rollback Depth · `depth` | **PRESENT-editable** | Visible only for rollback, matching v1 |
| FREEFORM · Scale · `scale` | **PRESENT-editable** | Profile → `scale` |
| FREEFORM · Length · `length` | **PRESENT-editable** | `profile_h.points[1].z` mirrored to V; **wrong endpoint when imported profiles have interior points** |
| FREEFORM · Throat Radius · `throatRadius` | **PRESENT-editable** | `profile_h/v.points[0].r` |
| FREEFORM · Throat Angle · `throatAngle` | **PRESENT-editable** | `profile_h/v.throat_angle_deg` |
| FREEFORM · Horizontal Mouth Radius · `mouthRadiusH` | **PRESENT-editable** | `profile_h.points[1].r`; same fixed-index import bug |
| FREEFORM · Horizontal Mouth Angle · `mouthAngleH` | **PRESENT-editable** | `profile_h.mouth_angle_deg` |
| FREEFORM · Horizontal interior points · `interiorH` | **PRESENT-disabled(read-only table)** | Profile → `profile_h.points`; server schema exists |
| FREEFORM · Horizontal Throat Tangent Scale · `throatTangentScaleH` | **PRESENT-editable** | `profile_h.throat_tangent_scale` |
| FREEFORM · Horizontal Mouth Tangent Scale · `mouthTangentScaleH` | **PRESENT-editable** | `profile_h.mouth_tangent_scale` |
| FREEFORM · Vertical Mouth Radius · `mouthRadiusV` | **PRESENT-editable** | `profile_v.points[1].r`; same fixed-index import bug |
| FREEFORM · Vertical Mouth Angle · `mouthAngleV` | **PRESENT-editable** | `profile_v.mouth_angle_deg` |
| FREEFORM · Vertical interior points · `interiorV` | **PRESENT-disabled(read-only table)** | Profile → `profile_v.points`; server schema exists |
| FREEFORM · Vertical Throat Tangent Scale · `throatTangentScaleV` | **PRESENT-editable** | `profile_v.throat_tangent_scale` |
| FREEFORM · Vertical Mouth Tangent Scale · `mouthTangentScaleV` | **PRESENT-editable** | `profile_v.mouth_tangent_scale` |
| FREEFORM · Cross-section Stations · `crossSections` | **PRESENT-disabled(read-only table)** | Profile → `cross_sections`; server schema exists |
| FREEFORM · Spline Overshoot · `overshootPolicy` | **PRESENT-editable** | `overshoot_policy` |
| FREEFORM · Curve Direction · `inflectionPolicy` | **PRESENT-editable** | `inflection_policy` |

### Shared geometry, morph, enclosure, and guiding-curve inputs

| V1 location · label · key | V2 status | V2 mapping / finding |
|---|---|---|
| Throat Extension · Angle · `throatExtAngle`† | **PRESENT-editable†** | Geometry extras → `throat_ext_angle` |
| Throat Extension · Length · `throatExtLength`† | **PRESENT-editable†** | `throat_ext_length` |
| Throat Extension · Straight Slot Length · `slotLength`† | **PRESENT-editable†** | `slot_length`; v2 always shows it, while v1 can hide a zero slot |
| Morph · Target Shape · `morphTarget` | **PRESENT-editable** | `morph.target_shape` |
| Morph · Target Width · `morphWidth`† | **PRESENT-editable†** | Hidden in v2 when target is None; v1 keeps it visible |
| Morph · Target Height · `morphHeight`† | **PRESENT-editable†** | V2 shows only for Rectangle; v1 keeps it visible |
| Morph · Corner Radius · `morphCorner`† | **PRESENT-editable†** | V2 shows only for Rectangle |
| Morph · Morph Rate · `morphRate`† | **PRESENT-editable†** | Hidden when target is None |
| Morph · Fixed Part · `morphFixed`† | **PRESENT-editable†** | Hidden when target is None |
| Morph · Allow Shrinkage · `morphAllowShrinkage` | **PRESENT-editable** | Hidden when target is None |
| Wall & Enclosure · Wall Thickness · `wallThickness` | **PRESENT-disabled(unless freestanding and no enclosure)** | Mesh & Sampling → `mesh.wall_thickness`; v1 permits preconfiguration |
| Wall & Enclosure · Enclosure Depth · `encDepth` | **PRESENT-editable** | Enclosure → `enclosure.depth` |
| Wall & Enclosure · Edge Radius · `encEdge` | **PRESENT-editable** | `enclosure.edge_radius` |
| Wall & Enclosure · Edge Finish · `encEdgeType` | **PRESENT-editable** | `enclosure.edge_type` |
| Wall & Enclosure · Left Margin · `encSpaceL` | **PRESENT-disabled(depth=0)** | `enclosure.space_l`; v1 permits editing at depth zero |
| Wall & Enclosure · Top Margin · `encSpaceT` | **PRESENT-disabled(depth=0)** | `enclosure.space_t` |
| Wall & Enclosure · Right Margin · `encSpaceR` | **PRESENT-disabled(depth=0)** | `enclosure.space_r` |
| Wall & Enclosure · Bottom Margin · `encSpaceB` | **PRESENT-disabled(depth=0)** | `enclosure.space_b` |
| Guiding Curve · Throat Profile · `throatProfile` | **PRESENT-editable** | `throat_profile` |
| Guiding Curve · Profile Rotation · `rot`† | **PRESENT-editable†** | `rotation` |
| Guiding Curve · Mode · `gcurveType` | **PRESENT-editable** | `guiding_curve.curve_type` |
| Guiding Curve · Distance · `gcurveDist`† | **PRESENT-editable†** | Hidden for explicit mode; v1 keeps it visible |
| Guiding Curve · Width · `gcurveWidth`† | **PRESENT-editable†** | Hidden for explicit mode |
| Guiding Curve · Aspect Ratio · `gcurveAspectRatio`† | **PRESENT-editable†** | Hidden for explicit mode |
| Guiding Curve · Superellipse Exponent · `gcurveSeN`† | **PRESENT-editable†** | Visible only in superellipse mode |
| Guiding Curve · Superformula Tuple · `gcurveSf`† | **PRESENT-editable†** | V2 numeric field cannot represent v1 comma tuple |
| Guiding Curve · Superformula a · `gcurveSfA`† | **PRESENT-editable†** | `guiding_curve.sf_a` |
| Guiding Curve · Superformula b · `gcurveSfB`† | **PRESENT-editable†** | `guiding_curve.sf_b` |
| Guiding Curve · Superformula m1 · `gcurveSfM1`† | **PRESENT-editable†** | `guiding_curve.sf_m1` |
| Guiding Curve · Superformula m2 · `gcurveSfM2`† | **PRESENT-editable†** | `guiding_curve.sf_m2` |
| Guiding Curve · Superformula n1 · `gcurveSfN1`† | **PRESENT-editable†** | `guiding_curve.sf_n1` |
| Guiding Curve · Superformula n2 · `gcurveSfN2`† | **PRESENT-editable†** | `guiding_curve.sf_n2` |
| Guiding Curve · Superformula n3 · `gcurveSfN3`† | **PRESENT-editable†** | `guiding_curve.sf_n3` |
| Guiding Curve · Rotation · `gcurveRot`† | **PRESENT-editable†** | Hidden for explicit mode |
| Guiding Curve · Circular Arc Terminal Angle · `circArcTermAngle`† | **PRESENT-editable†** | Visible only for circular-arc throat profile |
| Guiding Curve · Circular Arc Radius Override · `circArcRadius`† | **PRESENT-editable†** | Same visibility restriction |

### Mesh, source, and simulation inputs

| V1 location · label · key | V2 status | V2 mapping / finding |
|---|---|---|
| Viewport Mesh · Surface Angular Samples · `angularSegments` | **PRESENT-editable** | Mesh & Sampling → `mesh.angular_segments` |
| Viewport Mesh · Surface Length Samples · `lengthSegments` | **PRESENT-editable** | `mesh.length_segments` |
| Viewport Mesh · Surface Corner Samples · `cornerSegments` | **PRESENT-editable** | `mesh.corner_segments` |
| Viewport Mesh · Throat Slice Samples · `throatSegments` | **PRESENT-editable** | `mesh.throat_segments` |
| Viewport Mesh · Preview Slice Bias · `throatSliceDensity` | **PRESENT-editable** | `mesh.throat_slice_density` |
| Frequency Sweep · Sweep Start · `freqStart` | **PRESENT-editable** | Simulation → `simulation.f1` |
| Frequency Sweep · Sweep End · `freqEnd` | **PRESENT-editable** | `simulation.f2` |
| Frequency Sweep · Frequency Samples · `numFreqs` | **PRESENT-editable** | `simulation.num_frequencies` |
| Source · Source Surface · `sourceShape` | **PRESENT-editable** | Source → `source.shape` |
| Source · Source Radius · `sourceRadius` | **PRESENT-editable** | `source.radius`; v2 adds an explicit mm unit |
| Source · Source Curvature · `sourceCurv` | **PRESENT-editable** | `source.curvature`; v2 hides it for flat-disc source, unlike v1 |
| Source · Source Velocity · `sourceVelocity` | **RENAMED(to source.velocity_convention)** | V1 enum Normal/Axial. Registry incorrectly assigns legacy key `sourceVelocity` to new numeric amplitude `source.velocity` |
| Solve Mesh · Simulation Type · `simType` | **PRESENT-editable** | `simulation.sim_type` |
| Solve Mesh · Solver Mode · `solverMode` | **PRESENT-editable** | `simulation.solver_mode`; sent in design, but frontend still forces `options.engine="dryrun"` |
| Solve Mesh · Throat Resolution · `throatResolution` | **PRESENT-editable** | `mesh.throat_resolution` |
| Solve Mesh · Mouth Resolution · `mouthResolution` | **PRESENT-editable** | `mesh.mouth_resolution` |
| Solve Mesh · Rear Resolution · `rearResolution` | **PRESENT-disabled(wall thickness=0)** | `mesh.rear_resolution`; v1 permits preconfiguration |
| Solve Mesh · Aperture Resolution Scale · `apertureResolutionScale` | **PRESENT-editable** | `mesh.aperture_resolution_scale` |
| Solve Mesh · Hard Triangle Limit · `maxTriangles` | **PRESENT-editable** | `mesh.max_triangles` |
| Solve Mesh · Large Mesh Approval · `allowLargeMesh` | **PRESENT-editable** | `mesh.allow_large_mesh` |
| Solve Mesh · Export Vertical Offset · `verticalOffset` | **PRESENT-editable** | `mesh.vertical_offset` |
| Solve Mesh · Quadrants · `quadrants` | **PRESENT-editable** | Custom quadrant control → `mesh.quadrants` |
| Solve Mesh · Front Baffle Resolution · `encFrontResolution` | **PRESENT-disabled(depth=0)** | `enclosure.front_resolution`; v1 accepts a four-value expression, v2 only one number |
| Solve Mesh · Rear Baffle Resolution · `encBackResolution` | **PRESENT-disabled(depth=0)** | `enclosure.back_resolution`; same tuple/scalar loss |

## Freeform editor inputs outside the centralized keys

| V1 location · input | V2 status | Where it belongs |
|---|---|---|
| Profile editor · H-plane visibility | **MISSING** | Profile editor toolbar; frontend-only visibility state |
| Profile editor · V-plane visibility | **MISSING** | Profile editor toolbar; frontend-only visibility state |
| Cross-section inset · Depth, 0–1 step .001 | **MISSING** | Profile editor/inset; frontend-only scrub state |
| Point paste · H/V points textarea | **MISSING** | Profile editor importer; writes existing `profile_h/v.points` schema |
| Point paste · Apply CSV to both H and V | **MISSING** | Profile editor importer; frontend-only option |
| Point paste · Set imported length / keep current length | **MISSING** | Profile editor importer; writes existing endpoint-point schema |
| Switch to FREEFORM · Start blank / Convert current design | **MISSING** | Family-switch workflow; converter writes existing Freeform schema |

## Settings, solve options, and export management

| V1 location · label/key | V2 status | Where it belongs / schema |
|---|---|---|
| Viewer · Real-time Updates · `live-update` | **MISSING** | Viewport preferences; client-only `live_update` |
| Viewer/Layout · Results layout · `resultsLayout` | **MISSING** | Results/Layout preferences; client-only |
| Viewer/Layout · Split view panels · `panelMode` | **MISSING** | Results/Layout preferences; client-only panel-count setting |
| Viewer/Layout · Panel arrangement · `panelArrangement` | **MISSING** | Results/Layout preferences; client-only |
| Viewer · Rotate Speed · `rotateSpeed` | **MISSING** | Viewport preferences; client-only OrbitControls value |
| Viewer · Zoom Speed · `zoomSpeed` | **MISSING** | Viewport preferences; client-only |
| Viewer · Pan Speed · `panSpeed` | **MISSING** | Viewport preferences; client-only |
| Viewer · Enable Damping · `dampingEnabled` | **MISSING** | Viewport preferences; v2 currently fixes damping off |
| Viewer · Damping Factor · `dampingFactor` | **MISSING** | Viewport preferences; visible when damping enabled |
| Viewer · Startup Camera Mode · `startupCameraMode` | **MISSING** | Viewport preferences; perspective/orthographic camera schema needed client-side |
| Viewer · Invert Scroll Zoom · `invertWheelZoom` | **MISSING** | Viewport preferences; client-only |
| Viewer · Keyboard Pan Shortcuts · `keyboardPanEnabled` | **MISSING** | Viewport preferences; client-only |
| Appearance · Chart Theme · `chartTheme` | **MISSING** | Results preferences; new chart-theme field/registry. V2 app light/dark theme is not equivalent |
| Simulation · Mesh Validation Policy · `meshValidationMode` | **MISSING** | Solve options UI → existing `SolveOptions.mesh_validation_mode` |
| Simulation · Sweep Spacing · `frequencySpacing` | **PRESENT-disabled(job option not wired)** | Disabled registry placeholder; existing `SolveOptions.frequency_spacing`; frontend does not submit it |
| Simulation · Verbose Backend Logging · `verbose` | **MISSING** | Solve options UI → existing `SolveOptions.verbose`; runtime behavior also needs wiring |
| Simulation · Solver Backend · `solverBackend` | **MISSING** | Solve options UI → existing `SolveOptions.engine`; add v1 `auto` resolution or capability-based selection |
| Jobs · Default Task Sort · `defaultSort` | **MISSING** | Jobs preferences; client-only |
| Jobs · Minimum Rating Filter · `minRatingFilter` | **MISSING** | Jobs preferences; client-only |
| Exports · Auto-download solve mesh · `downloadSimMesh` | **MISSING** | Export preferences; use existing mesh-artifact endpoint |
| Exports · Auto-export on complete · `autoExportOnComplete` | **MISSING** | Export preferences plus completion automation/persistence |
| Export format · Parameter Config · `mwg_config` | **MISSING** | Export preferences `formats[]`; manual config export exists |
| Export format · Waveguide STEP · `step` | **MISSING** | Export preferences `formats[]`; manual STEP export exists |
| Export format · Chart Images · `png` | **MISSING** | Export preferences `formats[]`; chart-export implementation needed |
| Export format · Frequency Data · `csv` | **MISSING** | Export preferences `formats[]`; results export implementation needed |
| Export format · Full Results · `json` | **MISSING** | Export preferences `formats[]`; raw-results export wiring needed |
| Export format · Summary Text · `txt` | **MISSING** | Export preferences `formats[]`; summary exporter needed |
| Export format · Polar Directivity · `polar_csv` | **MISSING** | Export preferences `formats[]`; polar exporter needed |
| Export format · Impedance · `impedance_csv` | **MISSING** | Export preferences `formats[]`; impedance exporter needed |
| Export format · ABEC Spectrum · `vacs` | **MISSING** | Export preferences `formats[]`; VACS exporter needed |
| Export format · Waveguide STL · `stl` | **MISSING** | Export preferences `formats[]`; manual STL export exists |
| Export format · Fusion 360 curves · `fusion_csv` | **MISSING** | Export preferences `formats[]`; manual profile export is the nearest v2 capability |
| Workspace · Output Folder | **MISSING** | Workspace settings; new selected-path state and folder-picker/server contract |

## Polar/directivity solve inputs

V1 sends these under `polar_config`. V2’s `SolveRequest` has no polar field; `SolverContext` silently uses hard-coded defaults.

| V1 label/key | V2 status | Where it belongs / schema |
|---|---|---|
| Sweep Start · `polarAngleStart` | **MISSING** | Simulation / Directivity Map → new `SolveOptions.polar_config.angle_range[0]` |
| Sweep End · `polarAngleEnd` | **MISSING** | New `polar_config.angle_range[1]` |
| Angular Step · `polarAngleStep` | **MISSING** | UI step converted to new `polar_config.angle_range[2]` count |
| Measurement Distance · `polarDistance` | **MISSING** | New `polar_config.distance` |
| Normalization Angle · `polarNormAngle` | **MISSING** | New `polar_config.norm_angle` |
| Diagonal Plane Angle · `polarDiagonalAngle` | **MISSING** | New `polar_config.inclination`; disable unless diagonal plane enabled |
| Horizontal plane · `polarEnabledAxes.horizontal` | **MISSING** | New `polar_config.enabled_axes[]` |
| Vertical plane · `polarEnabledAxes.vertical` | **MISSING** | New `polar_config.enabled_axes[]` |
| Diagonal plane · `polarEnabledAxes.diagonal` | **MISSING** | New `polar_config.enabled_axes[]`; enforce at least one plane |
| Measurement Origin · `polarObservationOrigin` | **MISSING** | New `polar_config.observation_origin` |
| 3D Balloon Sampling · `polarSphericalSampling` | **PRESENT-disabled(no request schema)** | Disabled placeholder. Its reason incorrectly says job options support it; `SolveOptions` does not |

## Results and chart controls

| V1 location · input | V2 status | Where it belongs |
|---|---|---|
| Results modal · Smoothing, 11 modes | **MISSING** | Results toolbar; client chart state/smoothing implementation |
| Results modal · Map Ref, −3/−6/−9/−12 dB | **MISSING** | Directivity card toolbar; client chart state |
| Results dock · Chart Type per panel, 10 types | **MISSING** | Dock panel configuration; client-only chart type |
| Results dock · Compare Job | **PRESENT-editable** | `Add comparison result`; currently only SPL uses the loaded overlay fully |
| Results dock resizer · Split fraction, .15–.70 | **RENAMED(to Dockview panel resize)** | Dockview layout is resizable and persisted |
| 3D Balloon · Frequency slider | **MISSING** | Results / Balloon card; balloon result renderer required |
| Forward Beam Map · Frequency slider | **MISSING** | Results / Forward Beam card; renderer required |

## Viewport controls

| V1 input | V2 status | Where it belongs / finding |
|---|---|---|
| Zoom In button | **MISSING** | Viewport toolbar; OrbitControls gestures exist, but no equivalent direct control |
| Zoom Out button | **MISSING** | Viewport toolbar |
| Focus on Model | **RENAMED(to camera preset/refit)** | Front/¾/Top presets reframe the geometry |
| Perspective/Orthographic camera toggle | **MISSING** | Viewport toolbar plus orthographic camera implementation |
| Cycle display mode: clay/solidwire/edges/wireframe/xray/zebra/curvature | **RENAMED(to seven direct mode buttons)** | All seven modes exist; `solidwire` is now `solid-wire` |

## File, naming, and job metadata inputs

| V1 location · input | V2 status | Where it belongs |
|---|---|---|
| Config file upload · `config-upload` | **PRESENT-editable** | Design File menu → Open |
| MSH file upload · `mesh-upload` | **MISSING** | File/Viewport import; new imported-mesh state plus MSH parser/preview path needed |
| Output Name · `export-prefix` | **MISSING** | Jobs/Exports; editable basename plus `SolveRequest.label` or existing post-submit metadata patch |
| Counter · `export-counter`, 1–999999 | **MISSING** | Jobs/Exports; client naming state, no server field required if label is derived |
| Job rating stars, 1–5 | **PRESENT-editable** | V2 Jobs panel and metadata API |

## Ten nontrivial unit/range/visibility checks

| Row | V1 contract | V2 result |
|---|---|---|
| R-OSSE `R` | mm; arbitrary ATH expression; no numeric min/max | mm, numeric only, .1–1000. **Expression/range mismatch** |
| OSSE `a` | deg; expression; editable in all guide modes | numeric −180–180; disabled when guide mode ≠ explicit. **Value-domain and disabled-state mismatch** |
| ICW `hold_start` | σ; .05–.90 step .01; visible for flat termination even if coverage=0 | Same range/unit, but hidden until coverage>0. **Range pass, visibility mismatch** |
| FREEFORM `length` | mm; 20–1000; controls the mouth endpoint | Same displayed range, but fixed `points[1]` path edits an interior point after importing >2 points. **Mapping failure** |
| FREEFORM `interiorH` | Up to 62 points; z 1…length−1 mm, r>0, optional angle (−90,90), strength (0,3] | Read-only table. **Editing/range semantics absent** |
| `morphHeight` | mm; expression-enabled; no numeric bounds; always rendered | numeric 0–2000; visible only for rectangle. **Expression/range/visibility mismatch** |
| `sourceVelocity` | enum 1=Normal, 2=Axial | Legacy alias points to numeric amplitude 0–100 m/s; actual enum is a differently named field. **Semantic alias mismatch** |
| `encFrontResolution` | mm; expression/tuple, default `25,25,25,25`; editable at depth 0 | scalar .01–1000; disabled at depth 0. **Shape and conditional mismatch** |
| `numFreqs` | integer 10–200 | V2 UI also 10–200; server accepts 1–401. **UI parity passes** |
| `polarDistance` | metres; min .1, step .1; sent with solve | No input/request field; server uses hard-coded 2.0 m. **Missing** |

## Status counts

| Status | Count |
|---|---:|
| **PRESENT-editable** | 98 |
| **PRESENT-disabled(reason)** | 16 |
| **MISSING** | 60 |
| **RENAMED(to)** | 5 |
| **NOT-APPLICABLE** | 0 |
| **Total audited rows** | **179** |

The counts do not double-count the 43 expression-domain failures. Under a strict rule that a field is “present” only if it accepts every v1 value form, those 43 daggered rows would be MISSING sub-capabilities: **103 missing, 56 editable, 15 disabled, 5 renamed**.

## Prioritized MISSING list

1. **Actual solve configuration:** Solver Backend, Mesh Validation Policy, Verbose Logging, and Sweep Spacing wiring. V2 currently always posts `engine: "dryrun"`.
2. **Polar request contract:** ten controls are missing and spherical sampling is hard-disabled. Add `SolveOptions.polar_config` and pass it into `SolverContext`.
3. **Freeform editing:** editable H/V points, stations, visibility, scrubber, paste/import workflow, and blank/convert switching.
4. **Expression editing:** restore raw ATH expression entry and round-trip behavior on 43 parameter rows.
5. **Results controls:** smoothing, map reference, chart chooser, balloon/forward-beam views and frequency controls.
6. **Export management:** auto-export, auto-download mesh, all 11 format selectors, and workspace folder selection.
7. **Viewer preferences:** orbit speeds, damping, input preferences, live-update, layout choices, and orthographic camera.
8. **Mesh/file workflow:** MSH import plus Output Name and Counter.
9. **Jobs management:** task sorting and rating filter.
10. **Chart theming:** v1’s ten chart themes; v2’s light/dark application theme is not a replacement.

## Critical non-MISSING parity defects

- V2’s 110-key registry test verifies names, not editability or semantics.
- Forty-three v1 expression-capable rows are numeric-only in v2.
- Three central Freeform parameters are permanently read-only.
- Freeform length and mouth radii use fixed point index `1`, not the last profile point.
- `sourceVelocity` is attached to the wrong semantic v2 field.
- Enclosure front/back resolution changed from four-value expressions to scalars.
- Several v2 conditionals disable or hide fields that v1 permits users to preconfigure.
- The spherical-sampling placeholder falsely claims `SolveOptions` support.
- Solve submission ignores available engine capabilities and hard-codes dry-run.

## V2 inputs not present in the v1 UI

New inputs are fine, as requested:

- Parameter search/filter.
- `length_mode` and `coverage_mode`.
- ICW-only legacy coefficients `a`, `k`, `q`, and `curl`.
- Numeric source amplitude `source.velocity`.
- Explicit `source.contours` editor; v1 schema could preserve it, but v1 had no active parameter surface.
- Z-map sampling mode and custom Z-map points.
- Design-level STL and MSH output flags.
- Application light/dark theme.
- Report-import file input.
- Viewport section cut, Show Enclosure, and Frame Stats.
- Front/¾/Top camera preset selector.
- Polar H/V result-plane selector and polar frequency slider.
- Impedance Re/Im versus magnitude/phase selector.
- More flexible individual quadrant combinations.
- Persisted arbitrary Dockview layout.
- Visible but disabled `Maximum edge guard` placeholder; it has no server schema yet.