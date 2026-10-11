# Native geometry formulas

Audience: users choosing Geometry panel formulas and API clients authoring WG designs.

The Geometry panel supports these constructions from the pinned mesher:

| Family | Geometry controls |
| --- | --- |
| OSSE-ADAPTER | Driver diameter, signed exit angle, adapter length, retained-body start and two tangent handles |
| OSSE-ADAPTER-CONTROLS | Three independent cubic poles, each with a Z coordinate and radius in join-local mm; the complete OSSE body follows the cubic |
| OSSE-ADAPTER-AXIAL | Authored adapter plus an absolute axial scale |
| OSSE-ROUNDOVER | Circular mouth lip radius and normal wall thickness in finished mm |
| OSSE-AXIAL | Absolute axial scale, with the ordinary scale controlling X and Y |
| OSSE-ARC | Retained-body fraction and absolute end tangent angle for a curvature-matched circular suffix |
| SOURCE-DISK | Source radius, exterior body radius, depth and uniform mesh size in finished mm |

Native horn body dimensions and adapter controls are intrinsic millimetres. Uniform
scale acts once; with absolute axial scale, that factor replaces the uniform factor
on Z. Mesh targets, lip dimensions and export placement use finished millimetres.
The independent cubic controls retain full numeric precision when saved and loaded.

These formulas support circular geometry, flat normal-driven sources and the full
mesh domain. The Geometry panel shows their applicable controls. The server validates
scalar values and coupled physical bounds against the pinned mesher before building;
unsupported compositions are refused with a field or construction explanation.
Preview and solve meshes stay centred; export vertical offset applies to native
STEP and STL geometry. The independent solve triangle and dense-memory limits still
apply, including when larger geometry exports are enabled. Certified native facets
can require more triangles than those solve limits permit.

WG design files store the native recipe in a `WG.NativeDesign` JSON payload under
the existing native geometry interpretation stamp. Design names and portable solve
settings remain ordinary `Report` and `WG.Solve` blocks. Saving and reopening preserves
native fields, passthrough data and CAD identity. No schema version migration is needed.

STEP and STL export the complete native construction. Bare native horns produce
surface STEP bodies; roundovers and standalone source bodies produce their documented
closed bodies. CAD Link / Send to CAD and profile CSV exports refuse these formulas
because their current transport cannot preserve the complete recipe. Export STEP for
manual CAD import; acoustic source tags must be assigned in the receiving CAD workflow.

BEMPP requires a closed body: native bare horns are refused during solve planning.
OSSE-ROUNDOVER and SOURCE-DISK retain their closed geometry without a wall adjustment.
A saved half/quarter solve domain must be changed to Auto or Full before solving a
native formula. SOURCE-DISK uses a single mesh; horn-resolution mesh ladders are skipped.
Switching between native horns preserves shared body dimensions and mesh resolutions;
switching to or from SOURCE-DISK or a legacy formula resets incompatible geometry.
