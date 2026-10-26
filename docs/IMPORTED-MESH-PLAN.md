# Imported mesh workspace

The user-facing modes are **Parametric** and **Imported mesh**.
Imported mesh covers both a standalone mesh file and geometry prepared through CAD Link.
CAD Link remains the name of the optional CAD connection tool inside that workspace.

## Current workflow

1. Select Imported mesh. A visible **Import mesh** action opens a file chooser without
   needing the design file menu, a CAD connection, or a generated parametric design.
2. Import a supported ASCII Gmsh 2.2 triangular surface mesh. Full models and declared
   half and quarter models are accepted. The mesh must pass the checks for its chosen
   symmetry, size and orientation.
3. In **Surfaces**, check the role for each group: **HF driver**, **MF driver**,
   **LF driver**, **Port or passive opening** or **Rigid wall**. WG reads its own
   `role=` group names and guesses from other names. Untagged triangles appear in a
   separate row, initially set to **Rigid wall**. Roles can be changed in WG; the
   group names themselves still come from the file.
4. Check **Size**, **Symmetry** and the axes in **Model details**. WG detects open
   symmetry planes, mirrors a negative-side model by default and offers removal of a
   closed face on a declared plane. It prepares automatically after import or an edit;
   a problem or missing choice appears in the status line.
5. CAD Link remains available in Imported mesh as another geometry input. A file mesh
   uses its own surface roles and solve settings even when a CAD model is retained.
   Switching modes must preserve the parametric design and restore the selected imported
   input without a stale asynchronous CAD or file load replacing the user's selection.
6. The shared **Simulation** tab provides drivers for file meshes. Same-role surfaces
   can share driver settings or be driven separately. Setup edits save automatically;
   the same file reopens its settings, and a changed file with the same name and group
   names can reuse them. Runs and replay packages retain their exact settings.

## Compatibility and limits

Keep existing internal mode identifiers and dock/storage keys where possible, so the
visible rename does not require a data migration. Preserve CAD-specific tools and controls
for CAD inputs. Parametric mode contains generated geometry, not standalone file setup.
Do not change solver numerics, mesh formats, electrical models or release version numbers.
The earlier restriction to selecting existing labels as sources has been lifted: each
group can be assigned a different role. WG does not paint faces or rename the file's
group names.

Automatic cutting of a full symmetric file mesh to a half or quarter is still in
progress. The initial target is about 5,000 triangles, with the exact cutting limit
to be set from measurements; this is not a limit on importing a mesh. Exporting a
half or quarter is available now. Crossover and Passive cardioid controls for file
meshes remain open work; the port role is already available.

## Review and verification

The earlier workspace implementation was reviewed and verified as recorded below.
Current focused tests also cover role choices, the untagged wall, automatic preparation,
declared symmetry, closed-face removal, driver sharing and re-import memory.

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
