# Technology Stack

**Analysis Date:** 2026-02-25

## Languages

**Primary:**
- JavaScript (ES modules) - frontend app and Node tooling (`src/`, `scripts/`, test runner in `tests/`)
- Python 3.10-3.13 supported - backend API and solver runtime (`server/`)

**Secondary:**
- Markdown - project and operational documentation (`docs/`, `README.md`, `AGENTS.md`)
- Shell script - local startup/setup helpers (`server/start.sh`, `scripts/setup-opencl-backend.sh`)

## Runtime

**Environment:**
- Browser runtime for Three.js UI (`src/main.js`, `src/app/App.js`)
- Node.js runtime for dev server, build, and JS tests (`scripts/dev-server.js`, `webpack`)
- Python runtime for FastAPI and BEM/mesh pipelines (`server/app.py`)

**Package Manager:**
- npm (`package-lock.json` present)
- pip for backend dependencies (`server/requirements.txt`)

## Frameworks

**Core:**
- Three.js (`three`) - 3D rendering and geometry visualization
- FastAPI + Uvicorn - backend API and async service hosting
- Express (dependency) - lightweight local dev server wiring

**Testing:**
- Node built-in test runner (`node --test`) for JS suites in `tests/`
- Python `unittest` discovery for backend suites in `server/tests/`

**Build/Dev:**
- Webpack + webpack-cli - frontend production bundle
- ESLint flat config + Prettier - code quality and formatting checks

## Key Dependencies

**Critical:**
- `three` - geometry rendering and viewport mesh display
- `fastapi`, `uvicorn` - backend API host and routes
- `gmsh` Python API (`>=4.15,<5.0`) - OCC mesh generation (`/api/mesh/build`)
- `bempp-cl` (`>=0.4,<0.5`, installed separately) - BEM simulation runtime (`/api/solve`)
- `meshio`, `trimesh`, `numpy`, `scipy`, `numba` - mesh processing and numerical solve support

**Infrastructure:**
- `jszip` - export bundling for generated artifacts
- `pydantic` - backend request/response model validation

## Configuration

**Environment:**
- Frontend backend URL defaults to `http://localhost:8000` (`src/config/backendUrl.js`)
- Backend logging/device behavior configurable via env vars like `MWG_LOG_LEVEL`, `WG_DEVICE_MODE`
- Python runtime/dependency support matrix enforced in `server/solver/deps.py`

**Build:**
- `webpack.config.js`, `eslint.config.js`, `.prettierrc`
- Backend dependency manifest in `server/requirements.txt`

## Platform Requirements

**Development:**
- Node + npm for frontend/tooling
- Python 3.10-3.13 with venv for backend
- Gmsh Python package required for OCC mesh build endpoint

**Production/Runtime expectations:**
- Backend exposes FastAPI on port `8000`
- Browser frontend served on port `3000` in local dev flow
- Solver path requires supported bempp runtime; otherwise `/api/solve` is unavailable

---

*Stack analysis: 2026-02-25*
*Update after major dependency or runtime policy changes*
