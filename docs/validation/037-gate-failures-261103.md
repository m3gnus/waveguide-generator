# 0.3.7 gate corrections — 2026-11-03

Source base: `e438998d9`; failed checkpoint: `28f62c370`.
The mesher pin and checkout are unchanged. No push, merge or release operation
is part of these corrections.

## UI catalog compatibility (item 3)

The installed pinned mesher catalog retains `editor.Drive` with legacy key and
label `Drive`. WG keeps that catalog identity while displaying the sensible
section title **Drivers**. The new visible **Crossover** section is also absent
from the pinned catalog, so WG excludes its new catalog entry for 0.3.7.
The exact installed-catalog equality assertion is unchanged.

For **0.3.8**, update the mesher's `hornlab_mesher/control_labels.json` and WG's
catalog together: replace `editor.Drive` with `section.Drivers` (legacy key and
label `Drivers`) and add `section.Crossover` (legacy key and label `Crossover`).
Then remove WG's two compatibility mappings and move the mesher pin only after
that coordinated contract is qualified.

Validation: `test_errors_round3.py` **49 passed** against the installed pinned
catalog; the first run exposed the additional Crossover entry, which was
reconciled without altering the contract test. Node 20 `npx tsc --noEmit`
passed; affected Vitest files
`refusalCatalog.test.ts` and `parameterRegistry.test.ts`: **45 passed**.

## Ubuntu session investigation (item 5; no runtime change)

Compared the successful [CI run 41510481001](https://github.com/m3gnus/waveguide-generator/actions/runs/41510481001)
at `d3343c61a` with failed [CI run 41709830987](https://github.com/m3gnus/waveguide-generator/actions/runs/41709830987)
at `28f62c370`, and the source through this worktree's `e438998d9` base.
The old Ubuntu log explicitly passes the CAD scenario (141.03 seconds).
The new log reaches reviewed preparation and submission, then the native BEMPP
worker reports four inventory failures for a missing temporary session and
refuses imported geometry on numba. The parent registry's successful preflight
therefore does not establish that the solve worker has a valid session.

The failure path is:

1. `BemppProcessHost._ensure_started()` passes `temporary_directory_root()` at
   worker creation, and reuses an already-live worker without re-adopting a root.
2. `_start_in_parent_session()` calls `adopt_parent_session()`. A root outside
   the system temporary directory, missing its owner file, absent or a symlink
   is rejected. A child has no active session of its own.
3. `_run_probe()` requires `spawned_directory_root(required=True)`. A missing
   adopted root raises the exact error in CI; imported BEMPP then rejects numba.

`temp_session.py`, `bempp_opencl.py`, `bempp_process.py`, `bempp.py`,
`bempp_imported.py`, `warmup.py`, the engine registry, `test_real_pipeline.py`,
its ASGI request helper, root conftest and CI workflow are unchanged between
`d3343c61a` and the supplied worktree base. Their direct import paths and the
session validation/worker-start bodies were inspected, not just filenames.
The relevant session behavior predates the green run: spawn handoff is from
`3f53cb6f1`, the helper's explicit requirement is from `034effd92`, and the
real-pipeline session fixture is from `7dfa2e69b`; all are ancestors of `d3343c61a`.

Intervening integration changes inspected:

- `162226dfc`: plain request middleware replaces three HTTP middleware layers;
  its imports and request path neither activate nor close temporary sessions.
- `aad1dc0c6`, `0f5934263`, `0fcce42e8`: operation stage registration, disconnect
  watching, commit fencing and history expiry do not mutate session roots.
  Parent-owned assembly build scratch directories do not participate in this
  linked-CAD scenario. Mesher-child spawn/session code is unchanged.
- `a929d399f`: assembly script snapshots add a thread hop and optional submission
  data; the linked-CAD request carries no design snapshot and uses the existing
  CAD path. No session handoff is changed.
- `025699a6d`: the OpenCL helper's imported JSON writer changes directory-fsync
  error handling, without changing temporary-directory selection, environment
  variables, adoption or imports of session code.

A small owned-worker diagnostic reproduced a **pre-existing possible mechanism**:
start a BEMPP host with no parent session, activate a session in the parent, then
reuse the host. The PID stays the same and its child still raises the exact
missing-session error. The pipeline fixture clears the parent's probe cache and
creates a session, but does not discard an earlier worker on fixture entry.
Its scenario teardown does close the host. This demonstrates an isolation gap;
it does **not** prove that a stale worker caused the recorded CI failure.
The logs contain neither the spawn root nor the failed worker's adoption verdict.

**Attribution:** no introducing commit since `d3343c61a` is established. The
session failure mechanism and possible fixture gap already existed there; a
change in test scheduling could expose them, but the available evidence cannot
identify that trigger. Calling the observed failure inherited, or blaming the
middleware/operation commits, would overstate the evidence. No production or
pipeline-test fix was made. An Ubuntu reproduction must record the parent root,
worker PID/reuse, child system temp root and adoption verdict before assigning
causality. Keep the supported-route failure open until that evidence exists.

## Corrections and validation

- Item 1, `67a0d156c`: one owner for `source_completion_factors` under
  `server.mesh.source_completion`; its tagged parser moved to
  `server.mesh.tagged_msh`. The CAD survey wrapper preserves `SurveyUnavailable`.
  Both relocated implementations are AST-identical to the original bodies after
  import/exception-name substitution. Duplication check: all consumers reuse
  these owners and the existing `surface_topology.completion_factor`.
- Item 2, `1e732b45c`: `request_middleware` sits below `app : cli` and above
  `updates`; no contract or ignore edge was relaxed.
- Item 3, `39c1855e9`: the compatibility mapping above.
- Item 4, `845b5a692`: the predicate still rejects incorrect areas; explicit
  completion markers are required. Two real stored-record cases prove that
  unmarked cut-area and whole-area preparations are replaced, their retained
  records remain unchanged, and the new marked preparation is reused.

Checks against the final code at `67a0d156c`, using the lander's clean pinned
venv and Node **20.20.2**:

| Check | Result |
| --- | --- |
| Repository `lint-imports` | **9 kept, 0 broken** |
| Ruff, all 8 touched Python files | passed |
| Passive cardioid area, Metal source fingerprints, CAD frame inference, domain decision | **155 passed** |
| `test_errors_round3.py` (including exact installed catalog equality) | **49 passed** |
| `test_file_mesh_cut.py` | **39 passed** |
| Real CAD frame inference, automatic domain, domain contract, request logging, real pipeline | **104 passed** |
| Node 20 `npx tsc --noEmit` | passed |
| `refusalCatalog.test.ts`, `parameterRegistry.test.ts` | **45 passed**, 2 files |
| Node 20 `npx vite build` | passed |

Python total: **347 passed**, no skips. Every Python invocation used
`scripts/run_tests.py` with explicit files. The 104-case integration run used
broker job `261103-220154-suite-5cf0`, exit 0, **267.76 seconds**, on AC power;
its retained log is `logs/261103-220154-suite-5cf0.log` in the compute-broker
scratchpad. The successful local real pipeline does not qualify Ubuntu or erase
its CI failure. Frontend assets were built for app-mounting tests and removed
after validation.
