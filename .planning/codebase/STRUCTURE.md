# Codebase Structure

**Analysis Date:** 2026-02-25

## Directory Layout

```
260127 - Waveguide Generator/
├── src/                  # Frontend application source
├── server/               # FastAPI backend and solver stack
├── tests/                # JS test suites and fixtures
├── docs/                 # Project architecture and roadmap docs
├── scripts/              # Dev/start/diagnostic automation scripts
├── tools/                # Misc utility tooling
├── get-shit-done/        # GSD framework copy in repo
├── gsd-project/          # GSD project templates/agents bundle
├── launch/               # OS-specific launch scripts
├── install/              # Installer scripts
├── .codex/               # Codex skills/workflows/templates
├── package.json          # Frontend/tooling manifest and scripts
├── webpack.config.js     # Frontend bundling config
├── eslint.config.js      # Lint config
└── AGENTS.md             # Repository working contracts/guardrails
```

## Directory Purposes

**`src/`:**
- Purpose: browser app, geometry pipeline, exports, simulation UI/client
- Contains: app orchestration, mesh generation, frontend solver client, UI modules
- Key files: `src/main.js`, `src/app/App.js`, `src/geometry/pipeline.js`, `src/app/exports.js`

**`server/`:**
- Purpose: backend API, job runtime, solver and meshing pipelines
- Contains: FastAPI assembly, API routers, services, solver modules, backend tests
- Key files: `server/app.py`, `server/api/routes_*.py`, `server/solver/waveguide_builder.py`

**`tests/`:**
- Purpose: frontend/contract regression coverage
- Contains: Node test files (`*.test.js`) and fixtures
- Key files: `tests/TESTING.md`, `tests/mesh-payload.test.js`, `tests/export-gmsh-pipeline.test.js`

**`docs/`:**
- Purpose: architecture/source-of-truth docs and roadmap materials
- Key files: `docs/PROJECT_DOCUMENTATION.md`, `docs/FUTURE_ADDITIONS.md`

## Key File Locations

**Entry Points:**
- `src/main.js` - frontend boot
- `src/app/App.js` - frontend coordinator
- `server/app.py` - backend API app entry

**Configuration:**
- `package.json` - scripts/dependencies
- `server/requirements.txt` - backend dependency matrix
- `webpack.config.js`, `eslint.config.js`, `.prettierrc`

**Core Logic:**
- `src/geometry/` - geometry and canonical payload construction
- `src/export/` and `src/app/exports.js` - export orchestration
- `server/solver/` - meshing and BEM solver implementation

**Testing:**
- `tests/` - JS tests (Node test runner)
- `server/tests/` - backend tests (`unittest`)

**Documentation:**
- `AGENTS.md`, `README.md`, `server/README.md`, `docs/PROJECT_DOCUMENTATION.md`

## Naming Conventions

**Files:**
- JS/Python modules use mostly lowercase with separators (`kebab-case.js`, `snake_case.py`)
- Tests use `*.test.js` and `test_*.py`

**Directories:**
- Feature-based top-level split (`src`, `server`, `tests`, `docs`, `scripts`)

**Special Patterns:**
- Contract-sensitive modules explicitly listed in `AGENTS.md` with required parity tests

## Where to Add New Code

**Frontend feature work:**
- Implementation: `src/app/`, `src/ui/`, `src/geometry/` as appropriate
- Tests: matching `tests/*.test.js`

**Backend API/solver work:**
- Implementation: `server/api/`, `server/services/`, `server/solver/`
- Tests: matching `server/tests/test_*.py`

**Diagnostics and support tooling:**
- Scripts: `scripts/` and `scripts/diagnostics/`
- Docs: `docs/` and `tests/TESTING.md` updates when commands change

---

*Structure analysis: 2026-02-25*
*Update when key directories or entry points move*
