# Waveguide Generator

A browser-based tool for designing acoustic waveguides and horns: live 3D preview, parameter controls, BEM simulation, and file exports for manufacturing and simulation workflows.

![Waveguide Generator App Screenshot](docs/images/waveguide-generator-screenshot.png)

## Features

- **Horn Profiles**: R-OSSE and OSSE geometry with live parameter controls
- **3D Preview**: real-time viewport with standard, zebra, wireframe, and curvature shading
- **BEM Simulation**: HornLab mesher-backed jobs with progress tracking and directivity plots
- **Exports**: STL, single-layer STEP surface, CSV profiles, MWG config, simulation mesh (.msh), and VACS-style results
- **Task Management**: folder workspaces, task history with ratings, and auto-export on completion

## Docs

| Document                                               | Purpose                                 |
| ------------------------------------------------------ | --------------------------------------- |
| [Architecture](docs/architecture.md)                   | Durable layer and contract overview     |
| [Module Contracts](docs/modules/README.md)             | Stable module boundaries and invariants |
| [Project Documentation](docs/PROJECT_DOCUMENTATION.md) | Runtime reference and API details       |
| [Testing Guide](tests/TESTING.md)                      | Test map, commands, and diagnostics     |

## Quick Start

### 1. Prerequisites

- **[Node.js 18+](https://nodejs.org/)**: install the LTS version
- **[Python 3.10-3.14](https://www.python.org/downloads/)**: on Windows, tick "Add python.exe to PATH" during install

### 2. Get the project

```bash
git clone https://github.com/m3gnus/waveguide-generator.git
cd waveguide-generator
```

Or download the ZIP from [GitHub](https://github.com/m3gnus/waveguide-generator) -> **Code** -> **Download ZIP**, then extract it.

### 3. Install

Run once from the project folder, the folder containing `package.json`:

| Platform      | Command                          |
| ------------- | -------------------------------- |
| macOS / Linux | `bash install/install.sh`        |
| Windows       | double-click `install/install.bat` |

The setup scripts validate that you are in the full project folder, install JavaScript and Python dependencies, and write the preferred backend interpreter to `.waveguide/backend-python.path`.

Installer verification runs backend preflight and prints required runtime readiness for `fastapi`, `gmsh`, `hornlab-waveguide-mesher`, solver backend availability, and OpenCL when the BEMPP path is used.

Network note: backend setup installs `hornlab-waveguide-mesher` and `hornlab-metal-bem` from GitHub using pinned commit SHAs for reproducible installs. If Metal BEM is not ready on the host, setup also installs the pinned `bempp-cl` fallback.

### 4. Launch

| Platform | Command                           |
| -------- | --------------------------------- |
| macOS    | double-click `launch/mac.command` |
| Windows  | double-click `launch\windows.bat` |
| Linux    | `bash launch/linux.sh`            |

The app opens in your browser at `http://localhost:3000`. Close the terminal to stop it.

## Solver Dependencies

If no solver backend is ready, the app still works for 3D preview and local STL/config/profile exports, but **Start BEM Simulation** requires a ready solver backend plus the HornLab mesher.

- Python: `>=3.10,<3.15`
- hornlab-waveguide-mesher: pinned git commit `334e51f8455def6c60e0683fbc29ae46ae6d6230` (required for `/api/mesh/build`, `/api/mesh/step`, and `/api/solve` mesh preparation)
- hornlab-metal-bem: pinned git commit `0cc9c7426173ac51bf9333a0f51f4d2012c92dcc` (optional Metal solver backend)
- gmsh: `>=4.11,<5.0` (required by the HornLab mesher)
- bempp-cl: pinned git commit `d4f23c4b77b4e86e0b2c9da42db39fea2995bb33` / version `0.4.2` (optional BEMPP solver backend)

The maintained runtime uses the HornLab Metal BEM backend when available. Otherwise, the BEMPP fallback path needs `bempp-cl`, `pyopencl`, and OpenCL. There is no legacy `bempp_api` fallback path.

Manual install examples:

```bash
# macOS / Linux BEMPP fallback
.venv/bin/pip install pyopencl
.venv/bin/pip install git+https://github.com/bempp/bempp-cl.git@d4f23c4b77b4e86e0b2c9da42db39fea2995bb33

# Windows BEMPP fallback
.venv\Scripts\python.exe -m pip install pyopencl
.venv\Scripts\python.exe -m pip install git+https://github.com/bempp/bempp-cl.git@d4f23c4b77b4e86e0b2c9da42db39fea2995bb33
```

## OpenCL Setup

For BEMPP fallback investigations on macOS Apple Silicon, use the OpenCL CPU helper:

```bash
./scripts/setup-opencl-backend.sh
```

This creates a dedicated environment at `$HOME/.waveguide-generator/opencl-cpu-env/`.
The helper updates `.waveguide/backend-python.path` so launcher and backend startup use the OpenCL environment by default for this repo.

Other platforms:

- **Windows/Linux**: OpenCL GPU setup depends on vendor drivers (NVIDIA/AMD/Intel).
- **Linux CPU fallback**: install `pocl-opencl-icd` via your package manager.

## Mesh Control Guide

| Control                                      | Affects                                                        | Does not affect                                |
| -------------------------------------------- | -------------------------------------------------------------- | ---------------------------------------------- |
| Surface sample controls                      | Live JS viewport/local export tessellation and mesher sampling | Mesh element-size fields                       |
| `Solve * Resolution` + enclosure resolutions | HornLab mesher solve/export `.msh` artifacts                   | Live JS viewport triangle count                |
| `Quadrants` + `Auto`                         | HornLab solve/export mesh symmetry domain                      | STEP and viewport preview full-domain contracts |
| `Auto-download solve mesh`                   | Whether `.msh` is downloaded after solve                       | Mesh generation itself                         |
| `Task Exports` settings                      | Export bundle formats for completed tasks                      | Solver execution                               |

JS canonical/viewport geometry remains full-domain. HornLab solve/export mesh generation honors `Mesh.Quadrants`; the UI Auto action chooses the smallest supported symmetry domain it can detect conservatively.

## Project Layout

```text
src/          Frontend modules (geometry, export, simulation, viewer, UI)
server/       FastAPI backend and BEM solver
tests/        Frontend tests
server/tests/ Backend tests
install/      Setup scripts
launch/       Double-click launchers
docs/         Architecture and reference docs
```

## Development

```bash
npm test              # Frontend tests
npm run test:server   # Backend tests
npm run build         # Production bundle
```

## Troubleshooting

Backend not connected: start the app via a launcher or `npm start`, then check:

```bash
curl http://localhost:8000/health
```

Backend runtime checks:

```bash
node scripts/run-backend-python.js -c "import gmsh; print(gmsh.__version__)"
npm run preflight:backend
npm run preflight:backend:strict
npm run doctor:backend
npm run doctor:backend:json
npm run doctor:backend:strict
```

macOS "cannot be opened because the developer cannot be verified": right-click `launch/mac.command` -> Open -> click Open in the security dialog.
