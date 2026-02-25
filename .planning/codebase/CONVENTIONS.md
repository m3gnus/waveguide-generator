# Coding Conventions

**Analysis Date:** 2026-02-25

## Naming Patterns

**Files:**
- Frontend modules use lowercase/kebab-case naming (`src/app/exports.js`, `src/geometry/pipeline.js`)
- Backend Python modules use `snake_case.py` (`server/services/update_service.py`)
- JS tests use `*.test.js`; backend tests use `test_*.py`

**Functions and variables:**
- JavaScript: `camelCase`
- Python: `snake_case`
- Constants: UPPER_SNAKE_CASE (seen in solver/runtime modules)

## Code Style

**Formatting:**
- Prettier configured in `.prettierrc`
- Single quotes, semicolons, width 100, 2-space indentation, trailing commas `es5`

**Linting:**
- ESLint flat config in `eslint.config.js`
- Lint scope currently frontend-only (`src/`), server excluded in lint ignores
- Recommended rules intentionally downgraded to warnings to avoid blocking CI

## Import Organization

**JavaScript:**
- ES module syntax (`type: module` in `package.json`)
- Typical flow: external imports first, then local module imports

**Python:**
- Stdlib imports first, then third-party, then local package imports
- `server/app.py` deliberately re-exports symbols for backward-compatible tests

## Error Handling

**Patterns:**
- Backend validates API inputs early and returns structured HTTP errors (`422`/`503` patterns)
- Frontend solver client has explicit API-error handling helpers (`src/solver/apiErrors.js`)
- Contract invariants are enforced through assertions/tests (surface tags, payload shape)

## Logging

**Framework:**
- Python `logging` configured centrally in `server/app.py`
- JS tooling scripts use console output for operational progress

## Comments

**Observed style:**
- Comments explain rationale/constraints, especially around runtime and compatibility behavior
- Contract notes in docs and AGENTS act as authoritative behavioral comments

## Function and Module Design

**Design tendencies:**
- Frontend split into focused modules (`geometry`, `export`, `solver`, `ui`, `app`)
- Backend split into routers/services/solver layers
- Contract-critical modules explicitly protected by required parity tests before change

## Testing/Change Discipline

- Follow AGENTS guardrails for targeted tests before full suites
- Keep edits localized unless a contract break requires coordinated frontend+backend updates
- Keep docs aligned with runtime behavior (especially mesh pipeline semantics)

---

*Convention analysis: 2026-02-25*
*Update when lint/format policy or module contracts change*
