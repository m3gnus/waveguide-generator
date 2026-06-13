# Testing Guide

This document is the canonical inventory of all automated tests and run commands.

## Quick Start

From repository root:

```bash
npm test              # Run all JS tests
npm run test:server   # Run all Python tests
```

**Behavior**:

- `npm test` discovers and runs Node.js tests in `tests/*.test.js` using node:test
- `npm run test:server` discovers and runs Python unittest cases in `server/tests/*.py`

**Single-file execution**:

```bash
node --test tests/<name>.test.js                    # One JS test file
node scripts/run-backend-python.js --cwd server -m unittest tests.<module_name> # One Python test module
```

## JS test suites (`tests/`)

- `tests/app-mesh-integration.test.js`
- `tests/architecture-boundaries.test.js`
- `tests/ath-fixtures.test.js`
- `tests/backend-python-resolver.test.js`
- `tests/backend-runtime-doctor.test.js`
- `tests/backend-runtime-preflight.test.js`
- `tests/bem-mesh-integrity.test.js`
- `tests/config-import.test.js`
- `tests/config-roundtrip.test.js`
- `tests/csv-export.test.js`
- `tests/design-module.test.js`
- `tests/directivity-plane-contract.test.js`
- `tests/docs-parity.test.js`
- `tests/enclosure-regression.test.js`
- `tests/error-hardening.test.js`
- `tests/export-gmsh-pipeline.test.js`
- `tests/export-module.test.js`
- `tests/file-ops.test.js`
- `tests/folder-workspace.test.js`
- `tests/generation-artifacts.test.js`
- `tests/geometry-artifacts.test.js`
- `tests/geometry-module.test.js`
- `tests/geometry-params.test.js`
- `tests/geometry-parity.test.js`
- `tests/geometry-quality.test.js`
- `tests/mesh-payload.test.js`
- `tests/morph-implicit-target.test.js`
- `tests/mshParser.test.js`
- `tests/param-panel.test.js`
- `tests/polar-settings.test.js`
- `tests/references-guard.test.js`
- `tests/scale-regression.test.js`
- `tests/sim-advanced-settings.test.js`
- `tests/simulation-controller.test.js`
- `tests/simulation-export-bundle.test.js`
- `tests/simulation-flow.test.js`
- `tests/simulation-job-tracker.test.js`
- `tests/simulation-management-settings.test.js`
- `tests/simulation-module.test.js`
- `tests/simulation-reconciliation.test.js`
- `tests/simulation-settings.test.js`
- `tests/startup-messaging.test.js`
- `tests/task-index-rebuild.test.js`
- `tests/task-manifest.test.js`
- `tests/ui-behavior.test.js`
- `tests/ui-module.test.js`
- `tests/viewer-settings.test.js`
- `tests/viewport-tessellation-consistency.test.js`
- `tests/viewport-throat-disc.test.js`
- `tests/waveguide-payload.test.js`

Supporting fixtures:

- `tests/fixtures/abec/`

## Python backend suites (`server/tests/`)

- `server/tests/test_api_validation.py`
- `server/tests/test_charts.py`
- `server/tests/test_dependency_runtime.py`
- `server/tests/test_directivity_plot.py`
- `server/tests/test_import_boundaries.py`
- `server/tests/test_job_persistence.py`
- `server/tests/test_metal_solver_adapter.py`
- `server/tests/test_runtime_preflight.py`
- `server/tests/test_solver_backend_selection.py`
- `server/tests/test_solver_tag_contract.py`
- `server/tests/test_step_export.py`
- `server/tests/test_units.py`
- `server/tests/test_updates_endpoint.py`
- `server/tests/test_workspace_routes.py`

The backend suites cover solver backend selection across Metal and Bempp: `solver_backend` accepts `auto`, `metal`, and `bempp`; Auto uses the Metal BEM release-helper fast path when ready and falls back to Bempp on other hosts.

## Manual diagnostics (`scripts/diagnostics/`)

These are ad-hoc debugging helpers and are not part of the automated suites.

Run from repository root:

```bash
npm run diag:payload
npm run diag:geometry
npm run diag:mesher:reference-horn
npm run diag:mesher:closed
```

Generated diagnostic outputs are written to `scripts/diagnostics/out/`.

## Single-test execution

Run one JS test file:

```bash
node --test tests/<name>.test.js
```

Run one backend test module:

```bash
node scripts/run-backend-python.js --cwd server -m unittest tests.<module_name>
```
