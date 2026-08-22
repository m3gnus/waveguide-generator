# Development guide

Status: current contributor and agent orientation, verified 2026-08-13.

## Source-of-truth order

1. Executable code and tests are the behavior authority.
2. Documents under `reference/` state the intended stable boundary.
3. `plans/` contains accepted design gates that constrain future implementation.
   Maintainer task tracking is workspace-local and is not published with the repository.
4. `legacy/`, `history/`, and the dated folders in `validation/` are evidence, not task
   instructions. `validation/MEASUREMENT-TEMPLATE.md` is the exception: a blank form to
   copy when validating a build against a solve.

When a reference and code disagree, stop and reconcile them in the same change. Do not
implement an old `BATCH-*-BRIEF.md`: those one-use work orders were removed after the
August rebuild and remain available in Git history.

## Architecture map

- `frontend/src/stores/` owns editor, preferences, autosave, and run-export state.
- `frontend/src/api/` owns HTTP/WebSocket boundaries.
- `frontend/src/viewport/` renders binary preview frames; it does not derive geometry.
- `server/design/` parses, migrates, validates, and serializes design documents.
- `server/cli/` provides headless design and local solve-readiness commands.
- `server/preview/` translates a design into the pinned mesher preview API.
- `server/jobs/` owns durable jobs, snapshot/cursor events, retention, and recovery.
- `server/solver/` builds solver requests and maps every backend to one result contract.
- `server/exports/` owns authoritative geometry and CAD-link bundle exports.
- `server/cadlink/` owns CAD identity, registries, return ingestion, and Onshape access.
- `shared/` contains the cross-language binary-frame contract and fixtures.
- `pins.json` fixes the HornLab mesher, solver, plotting, and simulation repositories.

The authoritative flow is:

```text
design text/UI -> typed design -> HornLab mesher -> preview or solver artifact
                                     |                    |
                                     +-> CAD/mesh export  +-> backend -> result contract
```

## Names and legacy evidence

The product and GitHub repository are `Waveguide Generator` / `waveguide-generator`.
`WG2_*`, `v2-snapshot`, the historical SPA asset name, and the macOS bundle identifier
are compatibility identifiers and must not be changed as part of cosmetic renaming.
The retired application is preserved on `origin/v1`; use immutable Git objects from
that branch when legacy behavior must be rechecked. A removed sibling checkout is not
a valid runtime dependency.

## Verification

Run checks in proportion to the change:

```bash
.venv/bin/python -m ruff check server scripts shared launch launchers
.venv/bin/python -m pytest server/tests scripts/tests -q
node --test shared/js/frame.test.mjs
cd frontend && npm test && npm run build
```

The lint line is first because it is the cheapest and the easiest to forget: it
is the same command the **Generated-file drift** job runs, and that job has
turned `main` red for a single unused import. `ruff` is in
`server/requirements-dev.txt` for exactly this reason. The other two checks that
job runs are worth the same habit before a release:

```bash
.venv/bin/python scripts/gen_requirements.py --check
.venv/bin/python scripts/bump_version.py --check
```

Geometry, solver, platform, or release work may also require the pinned sibling suites,
constellation checks, a real browser, or owned qualification hardware. Hosted CI never
runs real Metal or BEMPP solves.

## Building the standalone macOS app

`scripts/build_bundle.py` creates the relocatable CPython runtime layer, the tracked
application layer, the ad-hoc-signed `.app`, and the DMG and update assets under
`build/bundle/`. It requires Apple Silicon macOS, `uv`, Swift, `codesign`, and
`hdiutil`; it installs only `server/requirements-runtime.txt` and
`server/requirements-pins.txt`, never the development requirements.

Release builds pass the checksum file beside the SPA tarball automatically:

```bash
python3 scripts/build_bundle.py \
  --spa waveguide-generator-v2-spa-<version>.tar.gz \
  --output build/bundle
```

Without `--spa`, `frontend/dist` must already contain the matching verified
`.wg2-spa.json` release stamp. `--runtime-only` and `--app-only` build an individual
update layer, `--python-version` can rehearse another CPython 3.13 patch, and
`--skip-verify` skips the copied-bundle gate (backends ready, `/` and `/health`
answer, and `codesign --verify --strict` still passes after the run, which proves the
launcher's cache redirection keeps the sealed bundle unmodified). A normal local build
should leave verification enabled:

```bash
.venv/bin/python scripts/build_bundle.py --output build/bundle
```

The runtime id is content-addressed from the two requirement files and the exact pinned
Python patch. Layer ZIPs use sorted entries, fixed timestamps, and preserved Unix modes
so repeated builds are reproducible in spirit. Internal file symlinks are materialized
as regular files: the in-app extractor rejects every link member along with absolute
paths, `..` traversal, and AppleDouble metadata.

## Standalone application updates

The server keeps release discovery read-only. In bundle mode (`WG2_BUNDLE=1`) it reads
the installed version from `Resources/app/APP-MANIFEST.json` and the installed runtime
id from `Resources/runtime/RUNTIME-MANIFEST.json`. A newer release is installable only
after its separate app manifest has been size-limited, checksum-verified, and read; the
app ZIP and its checksum must exist, and a different runtime id must resolve to a
runtime ZIP/checksum pair. Content-addressed runtimes can come from one of the 20 most
recent releases because the release job deliberately does not upload an unchanged
runtime again.

`POST /api/updates/install` starts one background `BundleUpdateInstaller`. Archives
download through retained `.part` files under `<data>/updates/<version>/`, are capped by
their advertised size, verified against their `.sha256` assets, and extracted into
complete staging directories. `/api/updates/status` reports `installState` (`idle`,
`downloading`, `verifying`, `ready`, or `failed`), byte progress, and the last staging
error. A second install request while work is active returns that state and does not
start another worker.

After staging, the server atomically writes the status owner's temporary
`update.json` request with this schema:

```json
{
  "schemaVersion": 1,
  "kind": "apply_bundle",
  "version": "0.2.5",
  "stagedAppDir": "<data>/updates/0.2.5/staged/app",
  "stagedRuntimeDir": null
}
```

`stagedRuntimeDir` is either `null` or the matching staged runtime directory. The
desktop launcher resolves both paths and refuses a request unless they are existing
directories inside the application data directory. It then stops the server and
starts `launchers/apply_update.py` from the staged app layer, using the staged runtime's
Python when present. The updater waits for the desktop parent, renames live layers to
`*.previous`, renames staged layers into place, removes quarantine attributes, ad-hoc
signs the changed bundle, and relaunches it (`open -n <bundle> --args <original CLI
arguments>` on macOS, the exe path on Windows, so `--port`/`--data-dir` survive the
restart). The launcher removes `*.previous`, signs again, and deletes the downloaded
archives under `<data>/updates/` only after `/health` and the frontend are healthy; a
failed first start restores the previous layers and records the result in
`<data>/logs/update.log`.

`WG2_UPDATES_API_BASE` (default `https://api.github.com`) redirects the latest-release
and release-list requests for an end-to-end rehearsal against a local fake; asset URLs
are taken verbatim from the payload. Plain `http://` is accepted only for loopback
hosts, and only while the API base itself is loopback.

The bundle stub's `PYTHONPYCACHEPREFIX` and `NUMBA_CACHE_DIR` remain inherited by the
staged updater and restarted application. Runtime imports therefore continue to write
only below the user cache directory, never into the signed bundle.

## Documentation changes

Every new document must state its audience or status. Repository plans must record an
accepted design decision, not a personal task list. Dated measurements include the
machine and tested state. Moving a contract requires updating any test or docstring that
reads it directly.
