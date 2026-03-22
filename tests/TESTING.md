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
cd server && python3 -m unittest tests.<module_name> # One Python test module
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
- `server/tests/test_benchmark_solver_script.py`
- `server/tests/test_dependency_runtime.py`
- `server/tests/test_device_interface.py`
- `server/tests/test_directivity_plot.py`
- `server/tests/test_geometry_parity.py`
- `server/tests/test_impedance.py`
- `server/tests/test_import_boundaries.py`
- `server/tests/test_job_persistence.py`
- `server/tests/test_mesh_cleaner.py`
- `server/tests/test_mesh_validation.py`
- `server/tests/test_observation.py`
- `server/tests/test_observation_distance.py`
- `server/tests/test_occ_resolution_semantics.py`
- `server/tests/test_reference_smoke.py`
- `server/tests/test_runtime_preflight.py`
- `server/tests/test_solver_hardening.py`
- `server/tests/test_solver_tag_contract.py`
- `server/tests/test_symmetry_benchmark.py`
- `server/tests/test_symmetry_regression.py`
- `server/tests/test_reference_horn_benchmark.py`
- `server/tests/test_units.py`
- `server/tests/test_updates_endpoint.py`

## Manual diagnostics (`scripts/diagnostics/`)

These are ad-hoc debugging helpers and are not part of the automated suites.

Backend research helpers:

- `cd server && python3 scripts/benchmark_solver.py <mesh.msh> [options]`
- `cd server && python3 scripts/benchmark_solver.py --preset reference-horn [--json] [--device auto|opencl_cpu|opencl_gpu] [--precision-modes single,double]`
- `cd server && python3 scripts/benchmark_symmetry.py [--case NAME] [--iterations N] [--json]`
- `cd server && python3 scripts/benchmark_reference_horn.py [options]` — bounded reference-horn repro path (mesh prep + optional 1-frequency solve with precision mode testing)

Run from repository root:

```bash
npm run diag:payload
npm run diag:geometry
npm run diag:occ:reference-horn
npm run diag:occ:closed
npm run benchmark:reference-horn
```

Generated diagnostic outputs are written to `scripts/diagnostics/out/`.

## Single-test execution

Run one JS test file:

```bash
node --test tests/<name>.test.js
```

Run one backend test module:

```bash
cd server && python3 -m unittest tests.<module_name>
```
