# 0.3.7 — unreleased draft

Draft checked against the 0.3.7 additions source, based on Waveguide Generator
`d17767bb9`.
This is not a release announcement or a statement of platform qualification.

## Added

- Open **Sources and baffle…** from the parametric enclosure controls without
  a special URL. Add and edit front-baffle woofers alongside the current horn,
  keep them with the design and in Undo history, and build or solve the
  assembly from that workspace. Opening it changes nothing until the first edit. It retains its **Experimental** badge:
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
- Import ATH V2026-09a `Scale.Z`, R-OSSE rotation and
  `Mesh.ThroatExtSegments` settings. Designs saved before this release build
  exactly as they did.
- Overlay any solved run under **+ compare**, including runs of other designs.

## Changed

- Imported-model preparation status and its next action now have one home in
  **Results**. Mesh preparation runs automatically after import and after
  settings changes; setup edits save automatically and are restored when the
  same file is imported again.
- The 22,000-triangle **Solve anyway** choice is shared by parametric, file and
  CAD models. A separate memory refusal still requires a coarser mesh.
- **Send to CAD** now opens or updates the design in Fusion without a click in
  Fusion, and Waveguide Generator shows what actually happened there. If Fusion
  is busy, for example with a sketch open, the send waits and continues by
  itself. Fusion needs the WGLink add-in that ships with this release.
- Failure messages now cover what you can act on, and each says what is wrong
  and what to do. Faults in the program go to developer diagnostics instead.
- The File menu has one **Export**. The design file is a row inside it, next to
  the geometry formats. Exporting a run from **Results** is unchanged.
- A woofer that the enclosure cannot hold is refused where you enter it, with
  what is short and which margin to raise. A woofer that no longer fits holds
  Build and Solve with its reason. Margins change only when you change them.
- The run list shows each run's time, mesh, sweep and engine, and Settings has
  a section list.

## Fixed

- Preserve imported surface roles and solve settings when reopening a file,
  including matching groups when a refreshed file has some changed group names.
- Keep the OSSE design intact when a requested feature combination is
  unsupported; accepted switches preserve shared design values and can be
  undone.
- Route eligible Metal solves through the supported compatible path while
  continuing to identify the selected engine as **Metal**.
- Keep a frequency change made in Fusion's Waveguide Generator panel until a
  solve has used it. A refused Solve no longer loses the change or shows it as
  used.
- Find room for a free woofer beside the mouth as well as above and below it.

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
  toolbar Solve. Remove the assembly to return to ordinary Solve. Assembly solves
  were checked to complete at 200, 500, 1,000 and 2,000 Hz on BEAT CPU and
  Metal-BEM; BEAT Metal was not run. BEMPP cannot solve these compiled assembly
  meshes. Drivers and crossovers cannot be set in this workspace. STEP solid, STL
  and Send to CAD carry the horn and enclosure only, and say so at the export.
  Assembly geometry export, wiring, boxes and arrays are not part of this first
  phase.

The solve Mesh section includes an optional **Max surface deviation** control and
live triangle estimate. New horn designs start **Off**. An explicit setting or
Off survives formula changes; standalone source disks do not use it. The Mesh
card reports observed deviation or a labelled bound, without a new mesh-setting
warning. Explicit minimum sizes take precedence. CAD keeps its existing rule.

## Pending and excluded from these notes

The Custom profile drawing canvas and conversion from an existing horn remain
later work. Other experimental source-assembly launchers keep their existing
availability. Hard maximum surface deviation guarantees are not claimed.
