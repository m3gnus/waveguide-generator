# P6 — cutover plan

**Status:** written 2026-08-05, after publishing v2 as a branch. Supersedes the
P6 sketch in `WG-REBUILD-PLAN.md` §8 for the work that remains, and keeps that
plan's gate **G6** as the definition of done.

P6 is the only phase between "v2 is feature-complete" and "v2 is the release".
Everything here is about *delivery* — getting v2 onto machines that are not this
one, without stranding what v1 users already have.

---

## 0. What measurement changed

Three findings from probing the real v1 database and launcher. Two shrink the
work; one adds a requirement that was not in the original plan.

### 0.1 The data migration is far smaller than R1-P0-6 assumed

The rebuild plan treated the v1→v2 jobs-database migration as a build-an-importer
task, sized around ~112 MB of solve history. It is not, because the port kept the
schema deliberately (plan §42: "schema ported as-is first — it doubles as the
migration target"). Measured against the live v1 database
(`server/data/simulations.db`, 109 MB, 35 jobs):

| Check | Result |
|---|---|
| v1 `PRAGMA user_version` | **4** — already v2's target |
| `simulation_jobs` columns | **identical set**, same order |
| `simulation_results` / `simulation_artifacts` | identical |
| Delta | v2 adds `job_events` (+ its index), created `IF NOT EXISTS` |

Pointing v2's `JobStore` at a copy of the v1 file and calling `initialize()`:

- `list_jobs()` → **35/35** jobs, correct statuses (30 complete, 5 error)
- `get_results()` → **30/30** parsed, zero failures
- `get_mesh_artifact()` → **32/32** returned data
- `job_events` created in place; `user_version` still 4

So the migration is **copy, open, verify** — not an importer. That moves P6.1
from a multi-week workstream to a few days, most of which is the backup/rollback
harness and the verification evidence G6 wants, not transformation code.

### 0.2 Legacy jobs have no design provenance — and never did

`script_snapshot_json` is **NULL for all 35** v1 jobs. The column exists (a later
v1 ALTER) but nothing populated it for this history.

This is not a migration defect and no migration can fix it: the design that
produced those results was never stored. It means migrated jobs can be listed,
and their results and meshes read, but they **cannot be reopened as a design or
rerun**. v2's UI currently assumes a snapshot is available for rerun.

**New P6 requirement:** v2 must degrade gracefully on snapshot-less rows — Rerun
disabled with a stated reason, "no design snapshot" surfaced in the job detail,
and no crash path through the rerun/compare flows. Add fixtures from the real v1
database, not synthetic rows.

### 0.3 The Node-free install goal is not yet true

Plan goal 6 says end users no longer need a Node runtime, because releases ship a
prebuilt SPA. Today `frontend/dist/` is in `.gitignore` and `launch-wg2.command`
hard-fails with *"The built frontend is missing"* when it is absent. As it
stands, installing v2 from a clone requires Node and a build.

**Decision required before installers are written** (§P6.2). Options:

| Option | End users need Node | Repo stays clean | Notes |
|---|---|---|---|
| **A. GitHub Release artifact** | No | Yes | CI builds `dist/`, attaches a tarball to a tagged release; installer downloads it. Recommended — matches "releases ship a prebuilt SPA" literally. |
| B. Commit `dist/` on release tags only | No | Mostly | Build products in history; bloat grows per release. |
| C. Installer builds the SPA | **Yes** | Yes | Simplest to write; abandons goal 6. |

Option A also gives the beta a real distribution channel, which §P6.5 needs
anyway.

---

## 1. Workstreams

Ordered by dependency, not by size. P6.1 and P6.3 can run in parallel; P6.2
blocks P6.4 and P6.5.

### P6.1 — v1 → v2 data migration

*Size: S (was L).* Owner deliverable: a migration command plus its evidence.

1. **Backup first, always.** Copy the v1 database and the v2 data directory to a
   timestamped backup path before touching anything; refuse to run if the backup
   cannot be written. Print the restore command on completion.
2. **Copy, don't transform.** Place the v1 file at v2's `db/` location, open it
   through `JobStore.initialize()` so `job_events` is added, leave
   `user_version` alone.
3. **Idempotent.** Re-running detects an already-migrated database (marker row or
   a v2-side migration record) and no-ops rather than duplicating. Migrating into
   a *non-empty* v2 database must merge by job id, or refuse — decide and state
   which; refusing is acceptable for the first release.
4. **Verify and report.** Pre/post counts per table, plus content hashes of
   `results_json` and `msh_text` per job id. G6 evidence is this report.
5. **Prove rollback.** Restore from the backup and re-verify counts and hashes —
   a scripted test, not a manual step.
6. **Snapshot-less jobs** (§0.2): fixtures from the real database, graceful
   degradation, no rerun crash path.
7. **Also migrate:** workspace preferences and current editor state. These are
   *not* in the database — locate the v1 sources and handle them explicitly
   rather than letting them fall through the copy.

**Gate evidence:** migration report with pre/post counts and hashes, a rollback
test in CI, and one full round trip on a real 109 MB database.

### P6.2 — Installers and the distribution decision

*Size: L. The biggest remaining piece.*

Decide §0.3 first — everything else here depends on it.

v1's installer is 365 lines of shell + 471 of batch + `check_venv.py`, backed by
two contract test suites (`tests/installer-contract.test.js`,
`installer-env-contract.test.js`, ~300 lines). That is the bar, and it encodes
years of platform gotchas. v2's installer should be **smaller** — no Node, no
`npm ci` — but must keep the same behaviors:

- Git self-update via fast-forward-only pull, with a clear message when the
  branch has no upstream or the tree is dirty.
- Prerequisite detection with actionable errors and version floors (Python 3.13;
  Git; VC++ redistributable on Windows; Xcode CLT for the Metal helper on macOS).
- Idempotent environment creation — `scripts/bootstrap.py` already does the
  Python half and is reusable as-is.
- **Installation under a parent path containing spaces** (R1-P1-7). The repo dir
  has no spaces, users' parent folders do; v1 has active bugs from exactly this.
- Port selection, first-run browser open, and a documented uninstall.

Port the two installer contract suites to v2 alongside the scripts. They are the
only automated coverage that catches installer regressions.

### P6.3 — CI and qualification runners

*Size: M. Partly done.*

`.github/workflows/ci.yml` now runs on every push and PR: frontend (vitest +
`tsc --noEmit` + build), shared frame codec (`node --test`), generated-file drift
(`gen_requirements.py --check`), ruff, and the server suite on ubuntu + macos.
`ruff.toml` pins the rule set so a ruff release cannot turn the gate red on its
own.

Remaining:

- **Watch the first runs.** The server job installs the four pinned HornLab
  modules from Git on hosted runners; that path has never executed in CI. Expect
  to iterate on it once.
- **Windows job** — after P6.4 establishes what works there at all.
- **Linux smoke** — bootstrap + serve + `/health`, no solve.
- **Qualification runners** (R2-P1.3): the nightly constellation run already
  exists. Extend it to publish per-run archived evidence (preflight report,
  payloads, result artifacts) at a stable location that G6 can link to. Hosted CI
  must never run real solvers; that separation is deliberate.

### P6.4 — Windows

*Size: L. Entirely unstarted.*

Nothing about v2 has run on Windows. The order that avoids wasted work:

1. Bootstrap and serve — Python 3.13 venv, locked deps, pinned modules, uvicorn.
2. gmsh worker thread. The plan flags this as a known Windows risk area
   (`interruptible=False`, single-worker identity).
3. bempp/OpenCL solve path on the Windows box, through the qualification runner.
4. Installer (`install.bat` equivalent) and the parent-path-with-spaces case.
5. Upgrade-over-v1 and rollback E2E.

Treat items 1–3 as a spike with a written findings doc before committing to the
installer.

### P6.5 — Beta

*Size: M, mostly elapsed time.*

Two weeks, with the matrix G6 requires defined **before** it starts: machines,
modes, representative designs, failure budget, and rollback triggers. Runs
side-by-side on :3100 against isolated data, distributed via whatever §0.3
decides. v1 stays the default throughout.

v1 feature freeze is at G5 and has not been declared. Do that when the beta
starts, not before — v1 is still the daily driver.

### P6.6 — Cutover mechanics

*Size: S. Mechanical, once the gates pass.*

Per plan §205, and nothing here is force-pushed:

1. `main` → rename to `v1` (stays installable one release cycle).
2. `v2` → default branch, then optionally rename to `main`.
3. v2 takes port 3000; v1's port moves or v1 is retired.
4. Tag the release; attach the SPA artifact if §0.3 chose A.
5. Update the install docs and the repo description.

---

## 2. Gate G6 checklist

Cutover happens when every row is true and its evidence is linked.

| # | Requirement | Source | Status |
|---|---|---|---|
| 1 | Fresh-machine install on macOS | G6 | Blocked on P6.2 |
| 2 | Fresh-machine install on Windows | G6 | Blocked on P6.2, P6.4 |
| 3 | Linux smoke | G6 | Blocked on P6.3 |
| 4 | Upgrade-over-v1 E2E, both OSes | G6 | Blocked on P6.1, P6.2 |
| 5 | Rollback E2E, both OSes | G6 / R1-P0-6 | Blocked on P6.1 |
| 6 | Migration pre/post counts + artifact hashes | R1-P0-6 | Blocked on P6.1 |
| 7 | Two-week beta against a defined matrix | G6 | Blocked on P6.5 |
| 8 | Traceability-table sweep — every row tested, deferrals have written workarounds | §3, G6 | Table exists; sweep not run |
| 9 | Qualification-runner evidence linked from the gate | R2-P1.3 | Runner exists; publishing not wired |
| 10 | Snapshot-less legacy jobs degrade gracefully | §0.2, new | Not started |

---

## 3. Open decisions

1. **Distribution (§0.3)** — A, B, or C. Blocks P6.2. Recommend A.
2. **Migrating into a non-empty v2 database** — merge by job id, or refuse?
   Refusing is fine for the first release if it is stated clearly.
3. **v1's fate after cutover** — one release cycle installable is the plan;
   confirm the retirement date when the beta starts.
4. **Whether the internal `docs/BATCH-*-BRIEF.md` and review documents stay in
   the published repo.** They are honest engineering history and cost nothing to
   keep, but they are agent working notes, not user documentation.

---

## 4. What is explicitly *not* in P6

Command palette, named snapshots, and multiple layout presets stay deferred to
post-cutover (plan §16, R2-P2.1). Electron/Tauri packaging remains a non-goal.
Process-isolated solves remain a possible later spike with their own gates, not a
v2 default.
