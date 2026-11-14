# Waveguide Generator documentation

This directory separates current product contracts from design gates and dated evidence. A
document under `history/`, `legacy/`, or a dated folder in `validation/` describes a past
state; it is not an instruction to reimplement that state. The measurement template is
the exception in `validation/`: it is a blank form to copy, not evidence.

| Folder | Holds |
|---|---|
| `docs/` | Guides for users, developers and maintainers |
| [`reference/`](#reference-contracts) | Canonical contracts: formats, protocols, APIs, updater and installer behaviour |
| [`architecture/`](#architecture) | How a part of WG is built and who owns what |
| [`plans/`](plans/README.md) | Accepted designs, implementation records and open proposals |
| [`validation/`](#validation-and-qualification) | Qualification procedures, templates and dated evidence |
| [`data/`](#driver-library-data) | Driver-library review and source research |
| [`history/`, `legacy/`](#history-and-legacy) | Archived and historical documents |

## Guides

| Document | Audience | Status | Authority / last verified |
|---|---|---|---|
| [User manual](USER-MANUAL.md) | First-time users | Step-by-step tasks: install, design and solve, Fusion, results, when WG needs you | UI and bundled add-in, 2026-10-25 |
| [User guide](USER-GUIDE.md) | Users | Current overview | UI and API, 2026-08-13 |
| [Install guide](INSTALL.md) | Users and maintainers | Every installer, launcher option and update detail; moved from the README | Installers and launchers, 2026-10-06 |
| [Finish a speaker in Fusion and solve it](CAD-LINK-GUIDE.md) | Users | Current CAD Link and driver workflow | WG CAD Link, frontend and bundled WGLink source, 2026-10-25 |
| [Development guide](DEVELOPMENT.md) | Contributors and AI agents | Current orientation | Repository layout and tests, 2026-08-13 |
| [Failure messages](WORDING.md) | Contributors | Current rules for user-facing failure text | `frontend/src/api/httpError.ts` |
| [Release guide](RELEASING.md) | Maintainers | Current release process; moved from the README | `release.yml` and `scripts/bump_version.py`, 2026-10-06 |
| [Dependency compatibility](DEPENDENCY-COMPATIBILITY.md) | Maintainers | Current pinned-module compatibility record, updated with each pin move | `pins.json` |

## Reference contracts

| Document | Audience | Status | Authority / last verified |
|---|---|---|---|
| [Configuration format](reference/CFG-FORMAT.md) | Developers and integrations | Canonical contract | `server/design/`, 2026-08-13 |
| [ICW advanced inputs and FREEFORM circle stations](reference/ICW-ADVANCED-AND-FREEFORM-CIRCLE.md) | Developers, integrations and users of ICW designs | Canonical contract | `server/design/schema.py`, `server/preview/translate.py` and the pinned mesher, 2026-10-12 |
| [Native geometry formulas](reference/native-formulas.md) | Users and API clients | Current reference | Geometry panel formulas |
| [Throat stretch](reference/throat-stretch.md) | Users and developers of OSSE and R-OSSE designs | Current contract | Pinned mesher and ATH import |
| [Adaptive frequency sampling](reference/adaptive-frequency-sampling.md) | Solver developers and users | Experimental option, off by default | BEAT exterior sweeps |
| [Headless CLI](reference/CLI.md) | Automation and integration clients | Canonical contract v1 | `server/cli/`, 2026-08-20 |
| [External evaluation API](reference/EXTERNAL-EVALUATION.md) | Language-neutral client developers | Canonical contract v1 | FastAPI, CLI, catalog, and examples, 2026-08-20 |
| [OpenAPI snapshot](reference/openapi.v1.json) | Generated HTTP clients | Generated release contract | `scripts/gen_openapi.py`, 2026-08-20 |
| [Binary frame format](reference/FRAME-SPEC.md) | Preview clients and servers | Canonical contract | `server/protocol/` and `shared/js/` |
| [WebSocket protocol](reference/WS-PROTOCOL.md) | Preview/jobs clients and servers | Canonical contract | `server/preview/`, `server/jobs/`, and frontend socket managers |
| [Solve symmetry](reference/SYMMETRY-CONTRACT.md) | Solver and geometry developers | Canonical contract | `server/solver/symmetry.py` and its tests |
| [Result contract](reference/RESULT-CONTRACTS.md) | Solver, chart, export, and integration developers | Canonical contracts v1/v2 | native and imported result mappers, 2026-08-20 |
| [Export contract](reference/EXPORT-CONTRACTS.md) | UI, API, and CAD developers | Canonical contract | `server/exports/` and `frontend/src/results/`, 2026-08-13 |
| [Automatic export](reference/AUTOMATIC-EXPORT.md) | UI and server developers | Current ownership and recovery contract | Backend export lane |
| [Portable mesh replay](reference/MESH-REPLAY.md) | Developers and support | Current format v1 | `.wgreplay` packages |
| [Passive-cardioid input contract](reference/CARDIOID-INPUT-CONTRACT.md) | CAD and solver developers | Input contract | Established 2026-08-13 |
| [CAD connection setup](reference/CAD-CONNECTION-SETUP.md) | CAD Link and settings developers | Implemented contract | Implemented 2026-08-14 |
| [Multi-instance CAD identity](reference/MULTI-INSTANCE-CAD-IDENTITY.md) | CAD adapter, server, and UI developers | Implemented first-slice contract | Fusion status, return listing/ingest, and UI, 2026-08-20 |
| [Shutdown and recovery](reference/SHUTDOWN-AND-RECOVERY.md) | Server, launcher, and release maintainers | Current behaviour; process-level tests and an RC gate | `launch/serve.py`, `server/platform/`, 2026-09-13 |
| [Update transaction contract](reference/UPDATE-TRANSACTION-CONTRACT.md) | Updater and launcher maintainers | Restart latch (section 4) and data compatibility (section 6) current; layer transaction historical, removed in 0.3.6 | `server/updates/restart.py`; status reviewed 2026-10-17 |
| [Signed full-installer client](reference/INSTALLER-CLIENT-CONTRACT.md) | Updater maintainers | Current client contract | `server/updates/installer_client.py` |
| [Installer handoff](reference/INSTALLER-HANDOFF-CONTRACT.md) | Installer and updater maintainers | Current full-installer handoff contract | `server/updates/` and platform installers |
| [Update signing runbook](reference/UPDATE-SIGNING.md) | Release maintainers | Current runbook | `SHA256SUMS` signing in the release workflow |
| [Full-installer native qualification](reference/UPDATER-NATIVE-QUALIFICATION.md) | Release maintainers | Current qualification procedure | Installed release candidates |
| [Windows native installer recovery](reference/WINDOWS-INSTALLER-RECOVERY.md) | Windows launcher and installer maintainers | Current native admission, recovery and diagnostics contract; Windows artifact evidence required | `launchers/windows/` and Windows installers |
| [Dependency installation integrity](reference/DEPENDENCY-INTEGRITY.md) | Maintainers | Current contract | `scripts/hash_lock_requirements.py` |
| [Build provenance](reference/BUILD-PROVENANCE.md) | Release maintainers | Current option | GitHub Actions artifacts |

## Architecture

| Document | Covers |
|---|---|
| [CAD operations](architecture/CAD-OPERATIONS.md) | The contract for work that crosses between WG and a CAD program |
| [User-visible folders](architecture/FOLDERS.md) | The WG folder, its children, persistence and recovery |
| [Fusion request display](architecture/FUSION-REQUEST-DISPLAY.md) | Which frontend module shows a Fusion Solve request, and when |
| [Full-3D BEM solver port](architecture/BEM-SOLVER-PORT.md) | The application boundary for BEAT and Metal |
| [Official BEAT bridge](architecture/OFFICIAL-BEAT-BRIDGE.md) | The production BEAT bridge |
| [Native source contours](architecture/NATIVE-SOURCE-CONTOURS.md) | Ingesting native source contours v1 |
| [Source contour authoring](architecture/source-contour-editor.md) | The source contour and assembly editors |
| [Front-baffle woofer](architecture/front-baffle-woofer.md) | Ingesting a native front-baffle woofer |
| [Shared horn and woofer](architecture/shared-horn-woofer.md) | A native horn and woofer solved together |

## Plans

| Document | Audience | Status | Authority / last verified |
|---|---|---|---|
| [Design plans and implementation records](plans/README.md) | Contributors and maintainers | Index of implemented contracts, accepted gates and open proposals | Planning classification reviewed 2026-10-16; qualification remains artifact-specific |
| [External STEP isolation](plans/STEP-PARSER-ISOLATION.md) | Security reviewers and CAD developers | Implemented boundary; explicit enforcement and qualification limits | Implementation record, 2026-08-20 |

## Validation and qualification

| Document | Audience | Status | Authority / last verified |
|---|---|---|---|
| [Solver qualification](validation/SOLVER-QUALIFICATION.md) | Release and solver maintainers | Current qualification guidance with separately labeled historical evidence | Official BEAT bridge and retained dated reports; status reviewed 2026-10-16 |
| [Legacy BEAT input qualification](validation/LEGACY-BEAT-INPUT-QUALIFICATION.md) | Solver maintainers | Current procedure for installed legacy input facades | `scripts/beat_conformance/` |
| [Metal exterior driver network](validation/metal-exterior-driver-network.md) | Solver maintainers | Experimental route qualification | `WG2_METAL_EXTERIOR_TRANSDUCERS` |
| [CAD Link Fusion acceptance](validation/CADLINK-FUSION-ACCEPTANCE.md) | Release owner | Current checklist | Live Fusion walk-through |
| [Windows installer gates](validation/windows-installer-gates.md) | Release maintainers | Current checklist | Windows setup executable |
| [Measurement template](validation/MEASUREMENT-TEMPLATE.md) | Anyone validating a build against a solve | Current template | Copy per case; pairs with the Results panel's measured overlay |
| [Validation cases](validation/CASES.md) | Anyone judging solver accuracy | Living index | Measured-vs-simulated references; first entry is the published CAFMEH-P3 comparison |
| [ABEC validation artifacts](validation/ABEC-ARTIFACTS-README.md) | Solver maintainers | Retained reference fixtures | `abec-rerun/` and `abec-g8-circsym-ib/` |
| [G8 ABEC reference](validation/abec-g8-circsym-ib/README.md) | Solver maintainers | External reference case | Axisymmetric waveguide in an infinite baffle |
| [August 2026 validation](validation/2026-08/README.md) | Maintainers | Dated evidence | Captured machines and commits; not current release status |
| [September 2026 validation](validation/2026-09/README.md) | Maintainers | Dated evidence | macOS Gatekeeper measurements, macOS 26.5.2, 2026-09-02 |
| [0.3.7 gate corrections](validation/037-gate-failures-261103.md) | Maintainers | Dated evidence, 2026-11-03 | 0.3.7 checkpoint corrections |

Dated reports not listed in their month's index:
[frontend async mutation audit](validation/2026-08/ASYNC-MUTATION-AUDIT.md),
[Windows validation 2026-08-20](validation/2026-08/WINDOWS-2026-08-20.md),
[Windows axisymmetric and infinite-baffle validation](validation/2026-08/axisym-coupled-infinite-baffle-windows.md) and
[solver qualification, 0.3.4 wave](validation/2026-09/SOLVER-QUALIFICATION-0.3.4.md).

## Driver library data

| Document | Covers |
|---|---|
| [Driver library review](data/driver-library-review.md) | What the bundled driver library and its master data contain |
| [Manufacturer import, 2026-11-08](data/driver-research/import-261108/README.md) | The manufacturer-site import |
| [STX catalogue expansion](data/driver-research/stx-import/driver-library-stx-report.md) | STX source research, 2026-10-06 |
| [Source research batch 01](data/driver-research/batch-01/batch-01-report.md) | Batch 01 source research, 2026-10-06, with its [air-mass note](data/driver-research/batch-01/air-mass-note.md) |

## History and legacy

| Document | Audience | Status | Authority / last verified |
|---|---|---|---|
| [V1 compatibility](legacy/V1-COMPATIBILITY.md) | Maintainers and migration work | Maintained legacy summary | Current code plus archived v1 inventory |
| [August 2026 rebuild](history/REBUILD-2026-08.md) | Maintainers | Historical summary | Git history; not an implementation brief |
| [Update channels](history/UPDATE-CHANNELS.md) | Release and updater maintainers | Historical layer-updater channel design; main-build proposal dropped | Archived from 0.3.6 |
| [Suite review, 2026-09-07](history/2026-09-07-suite-review.md) | Maintainers | Dated review | Review window 2026-08-24 to 2026-09-07 |
| [0.3.4 module inputs](history/RELEASE-MODULES-034.md) | Maintainers | Historical | The 0.3.4 candidate's pins |
| [0.3.7 release notes draft](history/RELEASE-NOTES-0.3.7-DRAFT.md) | Maintainers | Historical | Pre-release draft; 0.3.7 is released |

Other READMEs sit beside the code or fixtures they describe, for example
[`integrations/wglink/`](../integrations/wglink/README.md),
[`frontend/vendor/slvs/`](../frontend/vendor/slvs/BUILD.md),
[`frontend/src/sketch/solver/`](../frontend/src/sketch/solver/README.md) and
[`scripts/beat_conformance/`](../scripts/beat_conformance/README.md).

Maintainer backlogs and working briefs are workspace-local and intentionally absent
from the public repository. Stable contracts and accepted design decisions belong
here; private or superseded working notes belong in the workspace archive.
