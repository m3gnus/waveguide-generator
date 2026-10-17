# Imported mesh workspace

Required before 0.3.6. The user-facing modes are **Parametric** and **Imported mesh**.
Imported mesh covers both a standalone mesh file and geometry prepared through CAD Link.
CAD Link remains the name of the optional CAD connection tool inside that workspace.

## Intended workflow

1. Select Imported mesh. A visible **Import mesh** action opens a file chooser without
   needing the design file menu, a CAD connection, or a generated parametric design.
2. Import a supported ASCII Gmsh 2.2 triangular surface mesh. Keep existing format,
   size, units, orientation and full-domain admission checks; do not add new formats.
3. In **Radiating surfaces**, list every mesh surface label with its tag and triangle
   count. Check the labels to use as sources. Unchecked labels are rigid. Untagged
   triangles remain inspection-only and cannot silently become sources. State clearly
   when no source is selected. Each selected group remains an independent source.
4. Choose units/orientation, prepare the mesh and solve through the existing shared
   import workflow. Changing assignments invalidates the prepared mesh until prepared
   again. Preparation or import failures must not solve an older/different model.
5. CAD Link remains available in Imported mesh as another geometry input. A file mesh
   must use its own source assignments and solve plan even when a CAD return is retained.
   Switching modes must preserve the parametric design and restore the selected imported
   input without a stale asynchronous CAD or file load replacing the user's selection.
6. Saved setups, run restoration and replay packages open in Imported mesh. Exact replay
   settings and generation guards stay intact. Retained meshes without original source
   data show their assignments and clearly explain any reimport requirement for editing.

## Compatibility and limits

Keep existing internal mode identifiers and dock/storage keys where possible, so the
visible rename does not require a data migration. Preserve CAD-specific tools and controls
for CAD inputs. Parametric mode contains generated geometry, not standalone file setup.
Do not change solver numerics, mesh formats, electrical models or release version numbers.
There is no new face painting or mesh-label renaming: source selection uses existing labels.

## Review and verification

Astra reviews this plan against the user's goals before implementation. Record its feedback
and resulting decisions here. After implementation, obtain a scoped independent review of
the actual diff and address concrete findings. Verify visible import, arbitrary label
selection, disabled untagged groups, reprepare after edits, correct file/CAD solve routing,
mode switching, restored setup/replay ownership, and asynchronous intent protection using
focused frontend tests. Run TypeScript/build checks and inspect the resulting UI.

## Review decisions

Astra reviewed the plan before implementation. Adopted recommendations:

- Persist the selected file/CAD input independently of loaded viewport geometry, including
  pending or failed imports. An empty/failed file input cannot fall back to retained CAD.
- Use this selection consistently in viewport, solve planning, status, result coherence,
  restoration and mode round-trips. Keep the internal `cad` mode identifier.
- Fence passive CAD restoration and delayed receipt listing against newer file intent.
  Explicit CAD selection may change inputs; background CAD refresh may not.
- Reuse the existing file chooser for the prominent top-bar action. Keep file setup in
  Geometry, and expose File mesh / CAD Link as a small input selector in the workspace.
- Show retained source assignments even where original data must be reimported to edit.

Implemented and reviewed. Astra's scoped implementation review found five issues:
explicit CAD actions retaining File ownership, passive CAD arrivals cancelling file work,
shared planning missing viewport changes, superseded file reads retaining busy state, and
results following missing file identity dependencies. All five were fixed; the scoped
second review cleared them with no remaining blocking findings.

Verification: Node 20 production build and TypeScript passed. Full frontend broker gate
passed 3,838 tests plus 5 API-generator tests. After final layout and queued-solve coverage,
161 affected coordinator/viewport/workspace tests passed, including same-mode queued CAD
Solve cancellation and renaming a retained CAD dock. Browser checks covered visible import, a custom Cone patch label,
units/source selection, preparation enabling Solve, saved setup restoration with retained
assignments, Parametric round-trip, and the 800-pixel action layout. Installed Mac/Windows
qualification remains owned by the release process; this source change does not claim it.
