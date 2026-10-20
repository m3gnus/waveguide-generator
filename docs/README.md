# Waveguide Generator documentation

This directory separates current product contracts from design gates and dated evidence. A
document under `history/`, `legacy/`, or a dated folder in `validation/` describes a past
state; it is not an instruction to reimplement that state. The measurement template is
the exception in `validation/`: it is a blank form to copy, not evidence.

| Document | Audience | Status | Authority / last verified |
|---|---|---|---|
| [User guide](USER-GUIDE.md) | Users | Current overview | UI and API, 2026-08-13 |
| [Install guide](INSTALL.md) | Users and maintainers | Every installer, launcher option and update detail; moved from the README | Installers and launchers, 2026-10-06 |
| [Finish a speaker in Fusion and solve it](CAD-LINK-GUIDE.md) | Users | Current CAD Link and driver workflow | WG CAD Link, frontend and bundled WGLink source, 2026-09-30 |
| [Development guide](DEVELOPMENT.md) | Contributors and AI agents | Current orientation | Repository layout and tests, 2026-08-13 |
| [Release guide](RELEASING.md) | Maintainers | Current release process; moved from the README | `release.yml` and `scripts/bump_version.py`, 2026-10-06 |
| [Configuration format](reference/CFG-FORMAT.md) | Developers and integrations | Canonical contract | `server/design/`, 2026-08-13 |
| [ICW advanced inputs and FREEFORM circle stations](reference/ICW-ADVANCED-AND-FREEFORM-CIRCLE.md) | Developers, integrations and users of ICW designs | Canonical contract | `server/design/schema.py`, `server/preview/translate.py` and the pinned mesher, 2026-10-12 |
| [Headless CLI](reference/CLI.md) | Automation and integration clients | Canonical contract v1 | `server/cli/`, 2026-08-20 |
| [External evaluation API](reference/EXTERNAL-EVALUATION.md) | Language-neutral client developers | Canonical contract v1 | FastAPI, CLI, catalog, and examples, 2026-08-20 |
| [OpenAPI snapshot](reference/openapi.v1.json) | Generated HTTP clients | Generated release contract | `scripts/gen_openapi.py`, 2026-08-20 |
| [Binary frame format](reference/FRAME-SPEC.md) | Preview clients and servers | Canonical contract | `server/protocol/` and `shared/js/` |
| [WebSocket protocol](reference/WS-PROTOCOL.md) | Preview/jobs clients and servers | Canonical contract | `server/preview/`, `server/jobs/`, and frontend socket managers |
| [Solve symmetry](reference/SYMMETRY-CONTRACT.md) | Solver and geometry developers | Canonical contract | `server/solver/symmetry.py` and its tests |
| [Result contract](reference/RESULT-CONTRACTS.md) | Solver, chart, export, and integration developers | Canonical contracts v1/v2 | native and imported result mappers, 2026-08-20 |
| [Export contract](reference/EXPORT-CONTRACTS.md) | UI, API, and CAD developers | Canonical contract | `server/exports/` and `frontend/src/results/`, 2026-08-13 |
| [Update channels](history/UPDATE-CHANNELS.md) | Release and updater maintainers | Historical layer-updater channel design; main-build proposal dropped | Archived from 0.3.6 |
| [Update transaction contract](reference/UPDATE-TRANSACTION-CONTRACT.md) | Updater and launcher maintainers | Restart latch (section 4) and data compatibility (section 6) current; layer transaction historical, removed in 0.3.6 | `server/updates/restart.py`; status reviewed 2026-10-17 |
| [Installer handoff](reference/INSTALLER-HANDOFF-CONTRACT.md) | Installer and updater maintainers | Current full-installer handoff contract | `server/updates/` and platform installers |
| [Windows native installer recovery](reference/WINDOWS-INSTALLER-RECOVERY.md) | Windows launcher and installer maintainers | Current native admission, recovery and diagnostics contract; Windows artifact evidence required | `launchers/windows/` and Windows installers |
| [Solver qualification](validation/SOLVER-QUALIFICATION.md) | Release and solver maintainers | Current qualification guidance with separately labeled historical evidence | Official BEAT bridge and retained dated reports; status reviewed 2026-10-16 |
| [Shutdown and recovery](reference/SHUTDOWN-AND-RECOVERY.md) | Server, launcher, and release maintainers | Current behaviour; process-level tests and an RC gate | `launch/serve.py`, `server/platform/`, 2026-09-13 |
| [Multi-instance CAD identity](reference/MULTI-INSTANCE-CAD-IDENTITY.md) | CAD adapter, server, and UI developers | Implemented first-slice contract | Fusion status, return listing/ingest, and UI, 2026-08-20 |
| [Design plans and implementation records](plans/README.md) | Contributors and maintainers | Index of implemented contracts, accepted gates and open proposals | Planning classification reviewed 2026-10-16; qualification remains artifact-specific |
| [External STEP isolation](plans/STEP-PARSER-ISOLATION.md) | Security reviewers and CAD developers | Implemented boundary; explicit enforcement and qualification limits | Implementation record, 2026-08-20 |
| [V1 compatibility](legacy/V1-COMPATIBILITY.md) | Maintainers and migration work | Maintained legacy summary | Current code plus archived v1 inventory |
| [August 2026 rebuild](history/REBUILD-2026-08.md) | Maintainers | Historical summary | Git history; not an implementation brief |
| [Measurement template](validation/MEASUREMENT-TEMPLATE.md) | Anyone validating a build against a solve | Current template | Copy per case; pairs with the Results panel's measured overlay |
| [Validation cases](validation/CASES.md) | Anyone judging solver accuracy | Living index | Measured-vs-simulated references; first entry is the published CAFMEH-P3 comparison |
| [August 2026 validation](validation/2026-08/README.md) | Maintainers | Dated evidence | Captured machines and commits; not current release status |
| [September 2026 validation](validation/2026-09/README.md) | Maintainers | Dated evidence | macOS Gatekeeper measurements, macOS 26.5.2, 2026-09-02 |

Maintainer backlogs and working briefs are workspace-local and intentionally absent
from the public repository. Stable contracts and accepted design decisions belong
here; private or superseded working notes belong in the workspace archive.
