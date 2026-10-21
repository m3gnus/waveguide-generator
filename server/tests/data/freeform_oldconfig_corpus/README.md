# Older FREEFORM saved-design compatibility corpus

Status: frozen regression evidence for contributors changing profile handling.

The reference is WG `47ce742d1178369ffcfa416af09232899163a9cb` with mesher
`1702cfd5c3bf23060b864af824023710936d7861` (the 0.3.6 reference, including
its round throat). `manifest.json` names every source and covered feature.
Inputs are small saved text files or flat WG design JSON, including legacy
millimetre-axis payloads. No external checkout, audit folder or network access
is required at test time.

`expected/<case>/config.json` freezes both `design_to_mesher_config` and the
solver's domain/placement configuration, plus the loaded FREEFORM state.
`mesh.json` hashes the exact UTF-8 solve `msh_text`; `step.json` hashes the
manufacturable STEP returned by the public solid export builder. SHA-256 comparisons
are exact, with no numeric tolerance, sorting of entities or mesh reordering.
The STEP export includes saved wall and enclosure geometry. The app falls back
to a surface body for a bare design with no material to enclose.

The test driver calls existing owners: `textcfg.parse` for saved text,
`DesignConfig.model_validate` (which invokes `apply_migrations`) for flat
JSON/autosave-wire designs, `textcfg.serialize` for save/reload,
`design_to_mesher_config`, `_solver_mesher_config`, `build_solver_mesh` and
`build_step_solid`. Geometry builders run in the app's real killable native worker.
Mesh caching is bypassed. Saved mesh settings are never replaced for the test.
The round-trip assertion compares exact numeric `t`, radii, angles and station
properties; it disregards the Expr `raw` spelling metadata that a text save
necessarily adds to numeric JSON values. Frozen configurations separately
retain the actual translated expression spelling.

Only these STEP writer fields are normalized:

- The single `FILE_NAME` timestamp becomes `2000-01-01T00:00:00`.
- The process-global sequence in `Open CASCADE STEP translator <version> N`
  product names becomes zero; version and child suffixes such as `.2` stay.
- The process-global `NEXT_ASSEMBLY_USAGE_OCCURRENCE` label offset is subtracted,
  retaining the sequence differences, ordering and every entity reference.

These counters advance between exports even after Gmsh is finalized. All other
header and DATA bytes are kept. Focused tests prove coordinate, reference,
version, product-suffix and relative-label changes remain visible.

Reference refusals are explicit error fixtures, not dropped or silently skipped
cases. A change from success to refusal (or vice versa), or a different refusal,
fails the same artifact check. Native crashes and timeouts cannot be frozen as
accepted refusals. The incomplete documentation excerpt is intentionally kept
alongside its completed saved-design counterpart.

## Running

Use the repository launcher and the intended installed mesher environment:

```sh
python scripts/run_tests.py server/tests/test_freeform_oldconfig_corpus.py -m 'not slow' -q
python scripts/run_tests.py server/tests/test_freeform_oldconfig_corpus.py -m slow -q
```

All resolved-config, round-trip and mesh checks belong to the normal selection.
Every STEP check carries the repository's existing `slow` marker. Split targeted
runs if necessary to keep each command under two minutes. The native worker has
a 45-second deadline per build. No full suite is needed for this corpus slice.

## Regeneration

Run one artifact of one case per command in the reference environment:

```sh
python scripts/freeze_freeform_oldconfig_corpus.py --case FF01-default --artifact config
python scripts/freeze_freeform_oldconfig_corpus.py --case FF01-default --artifact mesh
python scripts/freeze_freeform_oldconfig_corpus.py --case FF01-default --artifact step
```

An existing artifact is refused unless `--overwrite` is explicitly appended.
The script checks that production files match the reference and that the installed
mesher's direct-URL commit matches the reference. The test suite never regenerates
fixtures. Inspect any deliberate replacement as a compatibility decision.

Regeneration rebuilds twice in one native worker and refuses differing hashes.
The test compares each artifact again in a fresh parent/worker process, adding
cross-process evidence. No mesh cache hit can stand in for a repeat build.

The recorded reference environment is macOS arm64, Python 3.13.1, Gmsh 4.15.2,
NumPy 2.4.6, SciPy 1.17.1, meshio 5.3.5 and Pydantic 2.13.4. Exact native-byte
compatibility on another platform or native-library version is evidence still
to collect; this gate does not silently replace byte comparison with tolerance.

## Inventory and deduplication

The inventory covered JSON/text fixtures under `server/tests`, `shared` and
`frontend/src`, the FREEFORM family defaults, saved/autosave tests, text-config
tests, the mesher's FREEFORM example and test recipes, documentation examples,
and all FREEFORM entries in the compatibility audit. This checkout has no
standalone FREEFORM preset files or `.mwg`/`.wg` fixtures; the legacy design-I/O
test's external v1 checkout is absent. The two checked-in v1 job records are
represented as flat WG designs, without job bookkeeping or identities. Their
original legacy meridians, stations, source and mesh controls are retained.

Sources sharing the same effective design and settings are listed together in
the manifest rather than duplicating the input. Different saved mesh settings,
station spellings or migrations remain separate cases. Invalid-schema unit
inputs (missing endpoints, illegal station shapes and non-positive lengths)
are not saved-design fixtures; their existing rejection tests remain the owner.
The corpus covers saved examples and feature combinations; it is not an
exhaustive enumeration of the continuously variable design space.
