# 0.3.7 — unreleased draft

Draft checked against the 0.3.7 additions source, based on Waveguide Generator
`b05750ce8`, including the Sources and baffle launcher change.
This is not a release announcement or a statement of platform qualification.

## Added

- Open **Sources and baffle…** from the parametric enclosure controls without
  a special URL. Add and edit front-baffle woofers alongside the current horn,
  keep them in the saved design and Undo history, and build or solve the
  assembly from that workspace. It retains its **Experimental** badge:
  results are geometry-checked only and not yet acoustically qualified.
- Edit **Custom profile** (FREEFORM) using the app's anchor table, including
  throat and mouth, smooth/corner joins, angles, point import and a separate
  vertical profile. Edits preview curve deviation and generated-profile change
  before applying.
- Import supported Gmsh surface meshes directly in the **Imported mesh**
  workspace. Assign a role to each surface group, set units and axes, and use
  full, half or quarter symmetry settings.
- Use the shared **Simulation** controls for file meshes and CAD models,
  including shared or separate drivers, grouped crossover bands and coupled
  Passive cardioid where its source topology is supported.
- Compose OSSE throat adapters, mouth ends and axial scaling for supported
  designs. Authored adapters can be combined with a roundover or terminating
  arc; the controls adapter remains a separate construction.
- Build supported enclosure designs with a zero side margin at a flush mouth:
  the enclosure sides continue back from the fitted mouth rim.

## Changed

- Imported-model preparation status and its next action now have one home in
  **Results**. Mesh preparation runs automatically after import and after
  settings changes; setup edits save automatically and are restored when the
  same file is imported again.
- The 22,000-triangle **Solve anyway** choice is shared by parametric, file and
  CAD models. A separate memory refusal still requires a coarser mesh.

## Fixed

- Preserve imported surface roles and solve settings when reopening a file,
  including matching groups when a refreshed file has some changed group names.
- Keep the OSSE design intact when a requested feature combination is
  unsupported; accepted switches preserve shared design values and can be
  undone.
- Route eligible Metal solves through the supported compatible path while
  continuing to identify the selected engine as **Metal**.

## Known limitations

- Full file meshes can use **Auto · cut for speed** or an explicit half/quarter
  choice when geometry and source groups mirror. Cutting supports up to 5,000
  input triangles. Larger meshes and cuts that cannot be verified keep the valid
  full mesh ready to solve, with a status note. Export the half or quarter to
  solve faster; choose **Full model** to keep the whole mesh.
- Coupled Passive cardioid requires an MF driver and port topology, and cannot
  be combined with Crossover.
- Flush enclosure construction has geometry guards; zero margin does not make
  every horn shape supported. Fusion and other CAD export routes keep their own
  stated capability limits.
- Solve a design assembly from **Sources and baffle**, rather than ordinary
  toolbar Solve. Coupled exterior drivers require a supported BEAT engine;
  Metal-BEM uses prescribed velocity and shows a fallback notice. BEMPP cannot
  solve these compiled assembly meshes. Assembly geometry export, wiring,
  boxes and arrays are not part of this first phase.

## Pending and excluded from these notes

The Custom profile drawing canvas and conversion from an existing horn remain
later work. Other experimental source-assembly launchers keep their existing
availability. Tessellation controls and maximum surface deviation claims are
not included. These are not delivered features described by this draft.
