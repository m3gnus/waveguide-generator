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

### 3. Install / Update

Run from the project folder, the folder containing `package.json`. Use the same script for first install, repair, and future updates:

| Platform      | Command                          |
| ------------- | -------------------------------- |
| macOS / Linux | `bash install/install-and-update.sh` |
| Windows       | double-click `install\install-and-update.bat` |

The setup scripts validate that you are in the full project folder, pull the latest code when the folder is a Git clone, install JavaScript and Python dependencies, and write the preferred backend interpreter to `.waveguide/backend-python.path`.

Installer verification runs backend preflight and prints required runtime readiness for `fastapi`, `gmsh`, `hornlab-waveguide-mesher`, and a solve backend. On Apple Silicon, install/update also builds and requires the HornLab Metal BEM native helper in Swift release mode so simulations use the fast Metal path instead of a debug helper. When Metal is not ready, the installer installs the Bempp cross-platform backend from `server/requirements-bempp.txt`; OpenCL is optional, and Bempp uses its numba CPU backend when no OpenCL runtime is available.

Network note: backend setup installs `hornlab-waveguide-mesher`, `hornlab-metal-bem`, and conditionally `hornlab-bempp-bem` from GitHub using pinned commit SHAs for reproducible installs.

### 4. Launch

| Platform | Command                           |
| -------- | --------------------------------- |
| macOS    | double-click `launch/mac.command` |
| Windows  | double-click `launch\windows.bat` |
| Linux    | `bash launch/linux.sh`            |

The app opens in your browser at `http://localhost:3000`. Close the terminal to stop it.

## Solver Dependencies

Waveguide Generator supports two solve backends. The Settings solver dropdown offers Auto, Metal BEM, and Bempp (cross-platform). Auto uses the Metal BEM release-helper path on Apple Silicon and falls back to Bempp on Windows, Linux, and Intel Mac hosts.

- Python: `>=3.10,<3.15`
- hornlab-waveguide-mesher: pinned git commit `715365fef7ffd42ce2458e00030f8231805e88ed` (required for `/api/mesh/build`, `/api/mesh/step`, and `/api/solve` mesh preparation)
- hornlab-metal-bem: pinned git commit `93ba809209bb4b195aa8593699e94647bf82b43e` (fast Metal solve backend; Apple Silicon macOS)
- hornlab-bempp-bem: pinned git commit `8c112bbc8c083e7e8aed973500aef847c69970cf` (Bempp cross-platform solve backend; installed when Metal is unavailable)
- gmsh: `>=4.11.1,<5.0` (required by the HornLab mesher)

OpenCL is a Bempp speed-up, not a hard requirement. Without OpenCL, Bempp runs through its numba CPU backend. On Linux, install `pocl` from your package manager for an OpenCL CPU runtime; on Windows, use up-to-date GPU drivers or install Intel's OpenCL runtime; on macOS x86_64, numba is fine.

On Apple Silicon, `/api/solve` requires the release native helper so Metal jobs use the fastest validated solver path. `npm run build:metal-helper` builds/verifies that helper.

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
npm run build:metal-helper
npm run doctor:backend
npm run doctor:backend:json
npm run doctor:backend:strict
```

macOS "cannot be opened because the developer cannot be verified": right-click `launch/mac.command` -> Open -> click Open in the security dialog.
