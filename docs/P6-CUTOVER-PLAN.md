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

### 0.2 No migrated job can be reopened or rerun — for two different reasons

Of the 35 v1 jobs, **26 carry a design snapshot and 9 do not**
(`script_snapshot_json` is NULL; the column arrived in a later v1 ALTER and was
not backfilled). Neither group can currently be reopened in v2:

- **The 9 without a snapshot** — the design was never stored. No migration can
  recover it.
- **The 26 with one** — the snapshot is v1's flat parameter bag: keyed `type`
  (`"OSSE"`), mixing v1 field names (`r0`, `a0`, `q`, `morphCorner`) with
  ATH-style keys (`Coverage.Angle`, `Term.q`, `Throat.Diameter`) and a `_blocks`
  map of ATH sections. v2's `DesignConfig` is a discriminated union on
  `formula`, so all 26 fail validation with `union_tag_not_found`.

The migration itself is unaffected — the snapshots copy byte-for-byte and their
hashes are verified. What is missing is v2's ability to *interpret* them, which
the rebuild plan never required for G6.

**Required (P6.1):** v2 degrades gracefully on every imported job — Rerun
disabled with a stated reason, the cause surfaced in job detail, no crash path
through rerun/compare. Fixtures come from the real v1 database, not synthetic
rows.

**Optional, and cheap enough to consider (decision §3.5):** because the v1 bag
already carries ATH-style keys and `_blocks`, a legacy snapshot can be rendered
back to ATH text and fed through v2's existing, well-tested `textcfg.parse` —
the same reader that opens legacy `.mwg` files — rather than needing a new field
mapping. That would make the 26 reopenable. It is a feature, not a data-safety
requirement, so it should not gate cutover.

### 0.3 The Node-free install goal is not yet true

Plan goal 6 says end users no longer need a Node runtime, because releases ship a
prebuilt SPA. Today `frontend/dist/` is in `.gitignore` and `launch-wg2.command`
hard-fails with *"The built frontend is missing"* when it is absent. As it
stands, installing v2 from a clone requires Node and a build.

**Settled (Magnus, 2026-08-05): option A — a GitHub Release artifact.** CI builds
the SPA once and attaches it to the tag; the installer downloads it. This matches
"releases ship a prebuilt SPA" literally, keeps build products out of git
history, and gives the beta the distribution channel §P6.5 needs anyway. The
alternatives were committing `dist/` on release tags (build products in history,
growing per release) and having the installer build it (simplest to write, but
abandons goal 6).

Implemented in `.github/workflows/release.yml`, which fires on a `v*` tag:
it refuses to build when the tag disagrees with `shared/version.json`, packages
`frontend/dist` as `waveguide-generator-v2-spa-<version>.tar.gz` with a
`.sha256` beside it, refuses to publish a bundle that has no `dist/index.html`,
and attaches both to the release. Packaging was rehearsed locally — a 658 KB
archive containing `dist/index.html`.

`launch-wg2.command` now points at the release when the interface is missing,
and keeps the npm build as the developer path rather than the only one.

---

## 1. Workstreams

Ordered by dependency, not by size. P6.1 and P6.3 can run in parallel; P6.2
blocks P6.4 and P6.5.

### P6.1 — v1 → v2 data migration — **done**

*Size: S (was L).* Shipped as `scripts/migrate_v1.py`, covered by
`server/tests/test_migrate_v1.py` (12 tests).

```
python scripts/migrate_v1.py --v1-root "../Waveguide Generator" --dry-run
python scripts/migrate_v1.py --v1-root "../Waveguide Generator" --report migration.json
python scripts/migrate_v1.py --rollback <backup-directory>
```

Verified end to end against the real 113 MB v1 database merging into a copy of a
live 12-job v2 database: **12 → 47 jobs, 62 content hashes all matching, 312
workspace projects copied, in under a second**; re-running imported nothing; and
rollback returned the database to byte-identical 12 jobs with an empty workspace.

Two defects the tests caught, both now fixed: second-resolution backup directory
names collided on a quick re-run and aborted the migration, and rollback left
imported projects behind whenever the pre-migration workspace was empty.

Still open here: item 6 below (graceful degradation for imported jobs, §0.2) is
UI work and is not done.

The requirements this satisfies:

1. **Backup first, always.** Copy the v1 database and the v2 data directory to a
   timestamped backup path before touching anything; refuse to run if the backup
   cannot be written. Print the restore command on completion.
2. **Copy, don't transform.** Place the v1 file at v2's `db/` location, open it
   through `JobStore.initialize()` so `job_events` is added, leave
   `user_version` alone.
3. **Idempotent.** A `v1_migrations` marker keyed on a source fingerprint (size
   plus a digest of the job ids) short-circuits a repeat, and `INSERT OR IGNORE`
   keeps row-level idempotence even if the marker is lost. A non-empty v2
   database is **merged by job id**; the existing v2 row always wins.
4. **Verify and report.** Pre/post counts per table, plus content hashes of
   `results_json` and `msh_text` per job id. G6 evidence is this report.
5. **Prove rollback.** Restore from the backup and re-verify counts and hashes —
   a scripted test, not a manual step.
6. **Imported jobs** (§0.2): fixtures from the real database, graceful
   degradation, no rerun crash path. **Not done — the only P6.1 item left.**
7. **Also migrate:** saved projects. v1 keeps them in `output/` beside the
   checkout (312 folders, 86 MB here) unless redirected by
   `server/data/workspace_settings.json`, which the tool honours. v1's
   `.waveguide/` holds machine-local launcher state and is deliberately not
   migrated. The in-browser editor state cannot be moved by a script — it is
   `localStorage` on a different origin — so that path stays "save from v1, open
   in v2", which v2's legacy `.mwg` reader already supports.

**Gate evidence:** migration report with pre/post counts and hashes, a rollback
test in CI, and one full round trip on a real 109 MB database.

### P6.2 — Installers and the distribution decision

*Size: L. The biggest remaining piece.*

§0.3 is settled and the release pipeline exists, so the installer's job is now
well defined: get the repo at a tag, download and verify that tag's SPA archive,
create the Python environment, and launch.

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
| 4 | Upgrade-over-v1 E2E, both OSes | G6 | Migration done; blocked on P6.2 |
| 5 | Rollback E2E, both OSes | G6 / R1-P0-6 | **Done on macOS** (scripted); Windows pending |
| 6 | Migration pre/post counts + artifact hashes | R1-P0-6 | **Done** — `--report` emits it |
| 7 | Two-week beta against a defined matrix | G6 | Blocked on P6.5 |
| 8 | Traceability-table sweep — every row tested, deferrals have written workarounds | §3, G6 | Table exists; sweep not run |
| 9 | Qualification-runner evidence linked from the gate | R2-P1.3 | Runner exists; publishing not wired |
| 10 | Imported jobs degrade gracefully (no reopen/rerun) | §0.2, new | Not started |

---

## 3. Open decisions

1. **Distribution (§0.3)** — A, B, or C. Blocks P6.2. Recommend A.
2. ~~**Migrating into a non-empty v2 database** — merge or refuse?~~ **Settled:
   merge by job id.** Refusing was never viable — this machine already has 12 v2
   jobs alongside 35 v1 jobs, so refusing would force discarding one side. An
   existing v2 row always wins; re-running imports nothing twice.
3. **v1's fate after cutover** — one release cycle installable is the plan;
   confirm the retirement date when the beta starts.
4. **Whether the internal `docs/BATCH-*-BRIEF.md` and review documents stay in
   the published repo.** They are honest engineering history and cost nothing to
   keep, but they are agent working notes, not user documentation.
5. **Legacy snapshot translation (§0.2)** — build the v1-bag → ATH text →
   `textcfg.parse` path so the 26 snapshot-carrying jobs can be reopened, or
   ship with rerun disabled for all imported jobs? Not a cutover blocker either
   way.

---

## 4. What is explicitly *not* in P6

Command palette, named snapshots, and multiple layout presets stay deferred to
post-cutover (plan §16, R2-P2.1). Electron/Tauri packaging remains a non-goal.
Process-isolated solves remain a possible later spike with their own gates, not a
v2 default.
