# ICW advanced inputs and FREEFORM circle stations

These controls follow the pinned HornLab mesher at `232dff4`, especially
`docs/config-schema.md`, `profile_formulas.build_icw_curve`,
`icw.solver.ICWTargets`, and `freeform._normalise_stations`.

Geometry → Profile ends with a collapsed **Advanced ICW** group, shown only
for ICW. Blank optional values are omitted from serialized designs and leave
the mesher's default intact. Scalar expressions must evaluate to finite,
azimuth-independent numbers. Geometric feasibility remains the mesher's check.

| Key | Meaning / units | Unset behavior and bounds |
| --- | --- | --- |
| `r_aperture` | Rollback radius at the foremost 90° wall-angle crossing, mm | No target; must be positive; used only when `R` is absent. Clear Mouth radius to use it. |
| `x_aperture` | Rollback axial coordinate of that crossing from the throat, mm | No target. |
| `x_setback` | Aperture axial coordinate minus tip axial coordinate, mm | No target; positive places the tip behind the aperture. |
| `kappa0` | Signed throat curvature, 1/mm | Defaults to 0 (locally conical). No fixed numerical bound. |
| `kappa_abs_max` | Cap on sampled absolute curvature, 1/mm | No cap; when set, strictly positive. |
| `dkappa_ds_abs_max` | Cap on sampled absolute curvature derivative with respect to normalized arc length σ | No cap; when set, strictly positive. The implementation differentiates κ(σ), without dividing by total arc length S, despite the key's `ds` name. UI units are `1/mm per σ`; the mesher docstring also labels this `1/mm² per unit sigma`. |
| `theta_max_deg` | Rollback cap on sampled wall angle, degrees | No cap; must be in (90, 180] (90 itself is rejected). Accepted but inert on flat baffle. This does not set the terminal angle. |
| `pin_mouth_radius` | Add `R` as a hard target during flat-baffle coverage hold | Defaults to false. No effect without coverage hold or on rollback. |
| `icw_S` | Total arc length of a direct coefficient curve, mm | No default; required and strictly positive in direct mode. Ignored in target/seed modes. |
| `icw_seed` | Nested OSSE/R-OSSE mesher parameter dictionary | No seed. Preserved; read-only presence indicator. |
| `icw_coeffs` | Direct curvature spline coefficient list | No coefficients. Preserved; read-only count indicator. |

Rollback needs at least one axial target (`depth`, `x_aperture`, `x_setback`)
and a radial target (`R`, falling back to `r_aperture`). Multiple axial targets
are simultaneous constraints, not alternatives that erase each other. `R`
always takes precedence over `r_aperture`. With a flat-baffle coverage hold,
`R` is normally an emergent output; pinning it adds a constraint and can make a
previously feasible request fail. Curvature caps guide the solve and are checked
afterwards; they never silently relax geometry targets.

The mesher selects seed mode before direct coefficient mode before target mode.
Seed mode fits the nested seed (using `n_coeff` when supplied). Direct mode uses
`icw_coeffs`, `icw_S`, `r0` and `a0`. Target controls are disabled when imported
seed/direct inputs select another mode; the imported values remain stored.

There are **no ATH keys** for the advanced ICW inputs. JSON retains them in the
ICW object. WG text documents retain them in the existing `WG.Geometry` JSON
`Payload`, under `icw`, for any WG-stamped ICW save, including a design that
carries an ATH-import stamp from before a family switch. ATH ignores the whole
`WG.Geometry` block, so ATH reads such a file without these values. This is a WG
extension, not an ATH profile block mapping. Existing documents with all advanced
inputs absent retain identical serialization.

Scale: the Scale setting divides every curvature (`kappa0`, `kappa_abs_max`,
`dkappa_ds_abs_max`, and each numeric `icw_coeffs` entry, all 1/mm) and multiplies
every length (`r0`, `L`, `R`, `r_aperture`, `x_aperture`, `x_setback`, `icw_S`).
A string coefficient becomes `(expr) / scale`. An `icw_seed` holds unscaled nested
OSSE/R-OSSE lengths, so Scale other than 1 with a seed is refused; use the
**Clear imported seed / coefficients** action or set Scale to 1.
Coverage angle and hold window are sent only on a flat baffle; the mesher refuses
them on a rollback.

Validation: the schema refuses what the mesher refuses: non-finite numbers inside
`icw_seed`, a seed `type` other than OSSE or R-OSSE (a missing type means OSSE),
an empty `icw_coeffs`, boolean or non-finite coefficients, string coefficients that
are not finite constants, and `icw_coeffs` without `icw_S` when no seed is present.
`r_aperture` must be positive.

FREEFORM station `shape = "circle"` is accepted at any station, including the
throat, and its spelling survives native import/export and migrations. It is
an alias of the exponent-2 ellipse outline: the H/V splines still supply its
local semi-axes, so unequal radii produce an ellipse. Circle takes neither an
exponent nor a corner radius. Switching a row to Circle removes those values;
the editor shows no parameter input. The throat selector offers only Circle
and Ellipse. Position/count/order rules remain unchanged.

Catalog conditions `present` and `absent` mean non-null and null/missing,
respectively; `any` means at least one child condition holds. These describe
optional imported payloads and the same exclusions as the editor.
