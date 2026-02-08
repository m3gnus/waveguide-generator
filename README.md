# Waveguide Generator

Waveguide Generator is a browser-based tool for designing acoustic horns, previewing geometry in 3D, running BEM simulations, and exporting manufacturing/simulation files.

## Features

- OSSE and R-OSSE horn profile generation with live parameter controls.
- Real-time 3D rendering (standard, zebra, wireframe, curvature).
- Simulation workflow with backend job submission and result polling.
- Export support for:
  - `STL` (3D model)
  - `GEO` and `MSH` (Gmsh/BEM workflow)
  - `ABEC` project ZIP
  - `CSV` profiles
  - MWG config text

## Tech Stack

- Frontend: Vanilla JS + Three.js + Webpack
- Backend: FastAPI (Python) with optional bempp-cl solver integration
- Tests: Node.js built-in test runner + Python `unittest`

## Prerequisites

- Node.js 18+
- npm 9+
- Python 3.10+ (required for backend)

## Quick Start

### 1. Install frontend dependencies

```bash
npm ci
```

### 2. Set up backend virtual environment (optional but recommended)

```bash
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r server/requirements.txt
```

To enable full BEM solving:

```bash
./.venv/bin/pip install git+https://github.com/bempp/bempp-cl.git
```

If the solver is not installed, the UI still runs and the backend reports solver unavailability.

## Run

Start frontend and backend together:

```bash
npm start
```

- Frontend: [http://localhost:3000](http://localhost:3000)
- Backend: [http://localhost:8000](http://localhost:8000)

Run components separately:

```bash
npm run start:frontend
npm run start:backend
```

## Test and Build

```bash
npm test
npm run test:server
npm run build
```

## Project Layout

- `src/`: frontend app modules (geometry, export, simulation, viewer, UI)
- `server/`: FastAPI backend and solver code
- `tests/`: frontend and integration tests
- `scripts/`: local dev and utility scripts
- `PROJECT_DOCUMENTATION.md`: architecture and API details

## Troubleshooting

- Backend not connected:
  - Start backend on port `8000`
  - Check `curl http://localhost:8000/health`
- macOS Python command issues:
  - Use `python3` and explicit venv paths (`./.venv/bin/python`)
- bempp-cl install speed:
  - Initial install/build can take several minutes

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, workflow, and PR expectations.

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting instructions.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
