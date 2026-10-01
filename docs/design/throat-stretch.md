# Throat stretch

OSSE and R-OSSE have two profile controls: **Throat stretch amount (s1)**
and **Throat stretch sharpness (s2)**. Both are plain finite numbers from 0 to
10. Per-angle expressions are refused. Negative values are refused because they
can fold the profile back through the throat.

The mesher adds `s1 * degrees(atan(s2 * x))` to the profile's axial coordinate;
the radius stays the same. Larger s1 adds more length, and larger s2 makes the
transition sharper. Either coefficient at zero turns stretch off. Global Scale
applies to the stretched profile. Old designs without these controls keep their
existing geometry, serialization, and identity.

Stretch needs mesher 0.2.4. With the older pin, preview, solve, and geometry
exports refuse either nonzero coefficient with “needs mesher 0.2.4”. Saving a
valid design remains possible. The pin is identified by its capabilities, since
mesher development commits may still declare an older package version.

ATH text imports read s1 and s2 from the selected OSSE or R-OSSE block. A flat
OSSE config without a profile block reads them at top level. Top-level values
beside a profile block are ignored, matching the mesher, and the import report
names them. Invalid supplied coefficients are refused even in ignored sections.
For active stretch, an OSSE-block Rot wins over a top-level Rot; top-level Rot
is used when the block omits it. Active OSSE profile dimensions come from the
block. Inactive stretch retains WG's existing import precedence.

Active stretch also refuses the mesher's unverified combinations:

- Rotation with a throat extension or slot, and any R-OSSE rotation.
- OSSE slots.
- A guiding curve with an extension, slot, or rotation.
- R-OSSE with top-level Length.
- Per-angle expressions in extension, slot, rotation, or guiding curve controls.

R-OSSE guiding curves are refused because the mesher supports guiding curves
only for OSSE. Supported OSSE guiding curves accept the ATH GCurve and GCURVE
namespace and block forms, with the same precedence as the mesher. Preview,
solve, profile export, and STEP/CAD export share the same configuration translation.
