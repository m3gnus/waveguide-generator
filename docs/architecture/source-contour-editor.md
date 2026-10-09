# Source contour authoring

Open **Geometry → Edit source contour** in either workspace mode. The source
editor drafts a full circular meridian in millimetres with aligned +Z motion.
Drawing creates explicit line patches; the point table and arc center/direction
fields edit that same recipe. The pole stays at radius zero and the final rim
stays at z=0. Invalid edits remain visible as dashed drafts. Saving, splitting
and exporting require canonical validation. Undo retains the last 20 edits.

`server/source_editor.py` delegates validation, evaluation, shape expansion and
exact midpoint splitting to `hornlab_mesher.source_contour`. It does not fit
curves or infer patch roles from weights. Splitting preserves the left patch ID,
creates one join and right patch, and copies the role and literal drive weight.
Removing a join only merges lines with equal roles and weights. Point, patch,
physical-source and channel IDs survive numeric edits and preset round trips.

The editor supports flat, independently dimensioned dome/surround/land and
cone/dust-cap/surround/land starting shapes. Applying a shape creates its preset
patch identities and unit moving weights; Undo recovers the previous recipe.
All patches still belong to one physical source and one prescribed-motion
channel. Zero and negative weights are retained. Geometry and excitation hashes
come from the canonical model and remain separate from tessellation.

The current draft uses the existing durable settings namespace mechanism.
Saved presets are validated contour/drive documents in `source_presets.json` in
the application data directory. Atomic publication, 128-preset/8-MiB ceilings
and expected-revision checks protect against torn saves and stale replacements
or deletion. Unreadable libraries refuse edits. **Refresh presets** retrieves
the latest library; load a preset before updating its revision. Source presets
are independent of the attachment and mesh settings.

`POST /api/source-editor/export` exports through the existing isolated native
builders. Choose a rectangular front baffle with aperture and XYZ placement, or
a circular conical horn with housing and backing clearances. One export runs at
a time; approved update restarts refuse new exports. The native 250,000-triangle
ceiling and canonical clearance checks remain intact. The ZIP contains
`geometry.step`, `preview.msh` and `source.json` with exact member hashes and
temporary face selectors. The established general imported geometry consumer
can ingest the unpacked bundle; its own 22,000-triangle and memory admission
checks still apply. Export performs no acoustic solve and does not assign the
new source to the existing parametric horn solver.

Contour preview curves use canonical evaluation for drawing; they are display
samples, not a mesh-tolerance certificate. Mesh output uses the unchanged exact
native geometry builders. The installed mesher must provide native source
contour and front-baffle APIs; older dependencies return a visible unsupported
message without preventing application startup. Dependency pins remain a
separate integration step.

## Shared horn, woofer and passive passages

**Geometry → Edit horn + woofer assembly** opens the WG-owned assembly editor.
It reuses the canonical contour editor and source-preset library independently
for each diaphragm. Applying a contour retains its identity, roles, signed/zero
weights and normal/axial motion. The assembly draft has a separate durable
settings namespace and JSON save/load, containing both source documents,
enclosure/XY placement, horn dimensions, passive bodies and mesh density.

The front layout and canonical horn section preview show the two apertures,
wall and passive-body meridians. Add a central plug or annular vanes, edit their
IDs and inlet/outlet radii, and set common axial planes. The mesher owns the
canonical passive geometry, topology, clearances and local refinement. WG owns
authoring, validation/ingestion routes and publishing prepared geometry into
the existing imported workspace. Supported scope is the mesher's
`docs/phase-plug-passages.md`; no automatic channel-design algorithm is implied.

`POST /api/source-editor/assembly/validate` returns canonical preview geometry,
channels and passage contract. `assembly/export` returns the three-member ZIP;
`assembly/ingest` rebuilds through the general STEP importer and returns the
immutable ingestion envelope plus the imported request contract. These export
and ingestion operations share the existing one-export lock and update restart
gate. Invalid/stale previews disable preparation and export.

**Prepare assembly in WG** fetches and parses the verified persisted mesh before
selecting the new geometry and both authoritative drive channels. Failure or
later geometry intent retains the previous valid selection. The imported
submission repeats required features, physical-source IDs and literal patch
weights; changed assignment/motion requires editing and preparing again.
Prescribed contour channels cannot receive a lumped driver model.
Preparation selects Metal, the supported native contour consumer; the readiness
plan reports runtime availability without allocating a solve job. CAD-folder
refreshes retain native geometry, and native preparations write no unrelated CAD
project setup. Preparation performs no acoustic solve.

An older mesher that lacks the assembly passage APIs returns an actionable
unsupported response without preventing application startup. Paired development
checks do not qualify the installed dependency pin.
