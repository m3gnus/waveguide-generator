# 0.3.7 — unreleased draft

Draft based on Waveguide Generator `cb0aea323c4c8044020601a1fb8f2c236680f24b`.
This is not a release announcement or a statement of platform qualification.

## Added

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

## Pending and excluded from these notes

Custom-profile server support does not expose a profile table or canvas in the
app, and cloud assembly launchers remain hidden. Tessellation controls and
maximum surface deviation claims are not included. These are not delivered
features described by this draft.
