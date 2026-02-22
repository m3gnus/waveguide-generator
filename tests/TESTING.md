# Testing Guide

This is the canonical test inventory for the repository.

## Automated test commands

Run from repository root:

```bash
npm test
npm run test:server
npm run test:ath
npm run test:abec <bundle-path>
```

Command behavior:
- `npm test` runs Node tests from `tests/` only.
- `npm run test:server` runs Python `unittest` discovery in `server/tests/`.
- `npm run test:ath` and `npm run test:abec` run parity/contract checks.

## JS test suites (`tests/`)

- `tests/abec-bundle-parity.test.js`
- `tests/abec-circsym.test.js`
- `tests/app-mesh-integration.test.js`
- `tests/bem-mesh-integrity.test.js`
- `tests/config-roundtrip.test.js`
- `tests/csv-export.test.js`
- `tests/enclosure-regression.test.js`
- `tests/export-gmsh-pipeline.test.js`
- `tests/geometry-artifacts.test.js`
- `tests/geometry-params.test.js`
- `tests/geometry-quality.test.js`
- `tests/mesh-payload.test.js`
- `tests/polar-settings.test.js`
- `tests/references-guard.test.js`
- `tests/simulation-flow.test.js`
- `tests/ui-behavior.test.js`
- `tests/viewport-tessellation-consistency.test.js`
- `tests/waveguide-payload.test.js`

Supporting fixtures:
- `tests/fixtures/abec/`

## Python backend suites (`server/tests/`)

- `server/tests/test_api_validation.py`
- `server/tests/test_dependency_runtime.py`
- `server/tests/test_device_interface.py`
- `server/tests/test_directivity_plot.py`
- `server/tests/test_gmsh_endpoint.py`
- `server/tests/test_gmsh_geo_mesher.py`
- `server/tests/test_impedance.py`
- `server/tests/test_mesh_validation.py`
- `server/tests/test_observation.py`
- `server/tests/test_observation_distance.py`
- `server/tests/test_occ_resolution_semantics.py`
- `server/tests/test_reference_smoke.py`
- `server/tests/test_solver_hardening.py`
- `server/tests/test_solver_tag_contract.py`
- `server/tests/test_units.py`
- `server/tests/test_updates_endpoint.py`

## Manual diagnostics (`scripts/diagnostics/`)

These are ad-hoc debugging helpers and are not part of the automated suites.

Run from repository root:

```bash
npm run diag:payload
npm run diag:geometry
npm run diag:occ:tritonia
npm run diag:occ:closed
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
