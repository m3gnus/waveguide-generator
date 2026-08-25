# August 2026 validation evidence

Status: historical measurements. These reports describe the machines, commits, and
constraints stated inside them; they are not a current release status dashboard.

- [Migration dry run](MIGRATION-DRYRUN.md) — original corpus classification on
  2026-08-03.
- [Windows validation](WINDOWS-VALIDATION.md) — first Windows qualification pass and
  captured viewport evidence.
- [Windows performance](WINDOWS-PERFORMANCE.md) — measured startup, interaction, store,
  solve, and frontend work.
- [Apple Silicon performance](MACOS-PERFORMANCE.md) — regression validation and preview
  profiling after the Windows changes.
- [Live passive-cardioid CAD campaign](LIVE-PASSIVE-CARDIOID.md) — browser UI,
  imported Metal solve, radiation matrices, downloads, and permanent archive on
  2026-08-20.
- [Result and archive sizes](ARTIFACT-SIZES.md) — measured archive, retained
  artifact, and snapshot-wire sizes across the available local corpus on
  2026-08-20, with explicit evidence still required before hard limits.
- [First-solve initialization](FIRST-SOLVE-WARMUP.md) - check 12's cost
  re-measured on 2026-08-25 and then removed by warming the solver worker
  child; before/after at the solver boundary and end to end, plus what this
  machine could not show.
- [Wall-clearance acoustic replay note](WALL-CLEARANCE-ACOUSTICS.md) — a
  non-reproducible local run-101 observation from 2026-08-22; the committed
  snapshots support design identity only, while unpublished artifact hashes do
  not independently validate the mesh or acoustic claims.

Current release gates are maintained in the workspace-local maintainer backlog, not in
this dated evidence directory.
