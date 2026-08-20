# Waveguide Generator documentation

This directory separates current product contracts from design gates and dated evidence. A
document under `history/`, `legacy/`, or a dated folder in `validation/` describes a past
state; it is not an instruction to reimplement that state. The measurement template is
the exception in `validation/`: it is a blank form to copy, not evidence.

| Document | Audience | Status | Authority / last verified |
|---|---|---|---|
| [User guide](USER-GUIDE.md) | Users | Current overview | UI and API, 2026-08-13 |
| [Development guide](DEVELOPMENT.md) | Contributors and AI agents | Current orientation | Repository layout and tests, 2026-08-13 |
| [Configuration format](reference/CFG-FORMAT.md) | Developers and integrations | Canonical contract | `server/design/`, 2026-08-13 |
| [Headless CLI](reference/CLI.md) | Automation and integration clients | Canonical contract v1 | `server/cli/`, 2026-08-20 |
| [External evaluation API](reference/EXTERNAL-EVALUATION.md) | Language-neutral client developers | Canonical contract v1 | FastAPI, CLI, catalog, and examples, 2026-08-20 |
| [OpenAPI snapshot](reference/openapi.v1.json) | Generated HTTP clients | Generated release contract | `scripts/gen_openapi.py`, 2026-08-20 |
| [Binary frame format](reference/FRAME-SPEC.md) | Preview clients and servers | Canonical contract | `server/protocol/` and `shared/js/` |
| [WebSocket protocol](reference/WS-PROTOCOL.md) | Preview/jobs clients and servers | Canonical contract | `server/preview/`, `server/jobs/`, and frontend socket managers |
| [Solve symmetry](reference/SYMMETRY-CONTRACT.md) | Solver and geometry developers | Canonical contract | `server/solver/symmetry.py` and its tests |
| [Result contract](reference/RESULT-CONTRACTS.md) | Solver, chart, export, and integration developers | Canonical contracts v1/v2 | native and imported result mappers, 2026-08-20 |
| [Export contract](reference/EXPORT-CONTRACTS.md) | UI, API, and CAD developers | Canonical contract | `server/exports/` and `frontend/src/results/`, 2026-08-13 |
| [External STEP isolation](plans/STEP-PARSER-ISOLATION.md) | Security reviewers and CAD developers | Accepted design gate | Child-process boundary, 2026-08-13 |
| [V1 compatibility](legacy/V1-COMPATIBILITY.md) | Maintainers and migration work | Maintained legacy summary | Current code plus archived v1 inventory |
| [August 2026 rebuild](history/REBUILD-2026-08.md) | Maintainers | Historical summary | Git history; not an implementation brief |
| [Measurement template](validation/MEASUREMENT-TEMPLATE.md) | Anyone validating a build against a solve | Current template | Copy per case; pairs with the Results panel's measured overlay |
| [Validation cases](validation/CASES.md) | Anyone judging solver accuracy | Living index | Measured-vs-simulated references; first entry is the published CAFMEH-P3 comparison |
| [August 2026 validation](validation/2026-08/README.md) | Maintainers | Dated evidence | Captured machines and commits; not current release status |

Maintainer backlogs and working briefs are workspace-local and intentionally absent
from the public repository. Stable contracts and accepted design decisions belong
here; private or superseded working notes belong in the workspace archive.
