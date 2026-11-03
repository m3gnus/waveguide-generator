# 0.3.7 — unreleased draft

Draft notes for the changes planned for 0.3.7.
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
- Choose **Fixed velocity (1 m/s RMS)**, **Driver (T/S)** or **Rigid** for each
  baffle woofer. Set up a driver from the library or enter its values under
  **Drivers** in **Simulation**. BEAT CPU and BEAT Metal solve the driver with
  its air load; other engines solve the woofers at fixed velocity, 1 m/s RMS,
  with a notice explaining how to select an engine that solves the drivers.
- Get a suggested bare moving mass, **Mmd**, from **Mms** and **Sd**, with the
  free-air air load removed. The suggestion appears in grams under Mmd and is
  used only when you press **Use suggested Mmd**; it can be undone.
- Move between runs and chart panels inside an expanded chart, using its
  buttons or arrow keys. The view stays open while another run's model loads
  and keeps its comparison selection and chart preferences.

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
- **Solve** has one path for a design with a Sources and baffle assembly:
  every Solve builds the current revision if needed and solves the assembly.
  Its runs use the design's name and normal run numbers. A disabled Solve says
  what needs attention beside the button.
- Assembly runs belong to the design they were solved from, including their
  saved design exports. Field planes use the matching compiled mesh and remain
  available when that mesh is shown on the design.
- Assemblies use the same combined-response controls and defaults as CAD
  imports. Multiple signal groups get a combined response by default unless
  you turn it off; retained channel results can be combined again without a
  new solve.
- **Build** in **Sources and baffle…** closes the dialog and shows the built
  horn, enclosure and woofers in the viewport. **Mesh** shows the assembly mesh
  used by Solve and builds it when needed. After an edit, the geometry preview
  follows the design; the last mesh stays labelled while its replacement builds.
- New baffle woofers start with the ATH contour. Saved woofers with an explicit
  shape keep it. ATH imports also accept nested enclosure `LFSource` blocks.
- The viewport has one line of dimensions and solve facts, each with its own
  unit. It fits the available width without wrapping. Click the design's name
  beside **File** in the top bar to rename it; Enter or leaving the field
  accepts the name, and Escape cancels.
- Pan the viewport with a two-finger trackpad scroll or middle-button drag;
  mouse wheels and trackpad pinches zoom. Trackpad scrolling is a viewer
  preference. Geometry and Mesh keep their own display modes.
- Opening a field plane cuts away the model in front of it as the camera
  moves, keeping the plane visible and the section cap visible from both sides.
- Directivity and beam maps render smoothly at plot resolution, and the 3D
  balloon uses the same continuous colour ramp. Frequency and polar curves
  draw smooth lines through the solved points without overshooting them;
  tooltips still report the solved values. Chart labels, units, legends and
  frequency ticks are clearer, with fewer collisions on compact cards.
- Long operations share status-bar progress, including elapsed time and an
  estimate when available. **Build**, geometry exports and **Send to CAD** show
  their stages and offer **Stop**, including during long mesh builds.
- CAD models load faster by reusing discovery work and deferring hidden
  parametric geometry. Mesh checks are faster, and the quick triangle forecast
  avoids a full background build whenever a design opens or changes.

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
- Load a driver split by a CAD symmetry cut as the whole driver, correcting
  its level and air load. **CAD returns made before this fix must be sent
  again** to replace their recorded source areas. Reduced mesh-file imports
  also use the whole driver's area and refresh older preparations automatically.
- Include mirrored ports in coupled Passive cardioid aperture areas, so the
  air load and radiation use the same complete physical area.
- Let edited Fusion bodies solve without an unnecessary review hold, while
  keeping holds for stale models. CAD send outcomes and findings have one
  shared state, and **Approve and solve** requires a description of every finding.
  The linked throat is shown in Fusion's HF row.
- Answer woofer fit checks promptly without queuing behind meshing. A missing
  or outdated answer no longer leaves Build and Solve disabled indefinitely;
  the build still refuses a woofer that does not fit.
- Keep polar observation points visible when they are nearer the camera than
  the model, and keep missing chart samples transparent rather than painting
  over them. Hovering a map no longer leaves a blank patch.
- Cancelled requests now stop their work on the server, including the mesher.
  A stopped Build restores its previous state; a stopped geometry export leaves
  no partial output. Send to CAD rolls back while Stop is still possible; after
  publication can no longer be stopped, it finishes and reports the send.
- Export STL successfully for ATH imports with an enclosure by removing the
  inactive enclosure from the bore export. The saved design keeps its enclosure.

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
- Sources and baffle remains experimental and is not acoustically qualified.
  Earlier assembly checks completed at 200, 500, 1,000 and 2,000 Hz on BEAT CPU
  and Metal-BEM; those checks did not run BEAT Metal. BEMPP cannot solve these
  compiled assembly meshes. Woofer drivers require adaptive sampling and the
  ground plane to be off.
- Assembly and CAD-import solves still differ in fixed-velocity normalisation
  and engine fallback. Sharing the Solve path and combined-response controls
  does not establish acoustic equivalence between them.
- **Send to CAD → Stop** is covered by automated tests only; it has not been
  checked end to end in Fusion.
- This release was not checked as an installed app on Windows: Automatic CPU
  preparation and its progress, Julia discovery, the run-folder junction refusal
  and the Windows file opener are covered by automated tests only.
- STEP solid and Send to CAD carry the horn and enclosure; STL exports the horn
  bore. These exports omit the woofers and their baffle cutouts and state their
  scope at the export. Assembly geometry export, wiring, boxes and arrays are
  not part of this first phase.

The solve Mesh section includes an optional **Max surface deviation** control and
live triangle estimate. New horn designs start **Off**. An explicit setting or
Off survives formula changes; standalone source disks do not use it. The Mesh
card reports observed deviation or a labelled bound, without a new mesh-setting
warning. Explicit minimum sizes take precedence. CAD keeps its existing rule.

## Pending and excluded from these notes

The Custom profile drawing canvas and conversion from an existing horn remain
later work. Other experimental source-assembly launchers keep their existing
availability. Hard maximum surface deviation guarantees are not claimed.
