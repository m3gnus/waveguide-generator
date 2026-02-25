# Testing Patterns

**Analysis Date:** 2026-02-25

## Test Framework

**Runner:**
- JavaScript: Node built-in test runner (`node --test`)
- Backend: Python `unittest` discovery

**Run Commands:**
```bash
npm test
npm run test:server
node --test tests/<name>.test.js
cd server && python3 -m unittest tests.<module_name>
```

## Test File Organization

**Location:**
- JS tests in top-level `tests/`
- Backend tests in `server/tests/`

**Naming:**
- JS: `*.test.js`
- Python: `test_*.py`

**Fixtures:**
- JS fixtures under `tests/fixtures/`
- Additional diagnostics under `scripts/diagnostics/` (manual, non-gating)

## Coverage and Critical Contracts

**Contract-critical guardrails (from AGENTS):**
- Geometry/tag pipeline changes must run parity/regression tests (`mesh-payload`, `geometry-artifacts`, `enclosure-regression`)
- Export/solver payload changes require export and payload contract tests
- Backend mesh/build/solve modules have mapped mandatory server test sets

**Regression focus:**
- Canonical surface-tag mapping (`1/2/3/4`)
- Presence of source tag (`2`) in simulation payloads
- OCC endpoint behavior and dependency runtime gating
- API validation and solver hardening behavior

## Test Structure Patterns

**JS suites:**
- Feature-focused test files (one concern area per file)
- Node assertions and async tests around frontend logic and payload contracts

**Python suites:**
- `unittest.TestCase` modules grouped by subsystem (`dependency_runtime`, `gmsh_endpoint`, `solver_hardening`, etc.)
- Mocking/patching used for runtime dependency scenarios and API behavior

## Quality Workflow

- Run targeted tests first for touched modules
- Run full JS + backend suites before merge
- Keep test inventory docs synchronized with actual files and scripts

## Known Constraints

- Some backend tests are runtime-conditional (gmsh/bempp availability)
- Diagnostics scripts are helpful for investigation but are not automated gates

---

*Testing analysis: 2026-02-25*
*Update when test runners, suite layout, or contract-critical mappings change*
