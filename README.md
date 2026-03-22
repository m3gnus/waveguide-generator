# Waveguide Generator

A browser-based tool for designing acoustic waveguides and horns — live 3D preview, parameter controls, BEM simulation, and file exports for manufacturing and simulation workflows.

![Waveguide Generator App Screenshot](docs/images/waveguide-generator-screenshot.png)

## Features

- **Horn Profiles** — R-OSSE and OSSE geometry with live parameter controls
- **3D Preview** — real-time viewport with standard, zebra, wireframe, and curvature shading
- **BEM Simulation** — boundary element solver with job submission, progress tracking, and polar directivity plots
- **Exports** — STL, CSV profiles, MWG config, simulation mesh (.msh), and VACS-style results
- **Task Management** — folder workspaces, task history with ratings, and auto-export on completion

## Quick Start

### 1. Prerequisites

- **[Node.js 18+](https://nodejs.org/)** — install the LTS version
- **[Python 3.10–3.14](https://www.python.org/downloads/)** — on Windows, tick _"Add python.exe to PATH"_ during install

### 2. Get the project

```bash
git clone https://github.com/m3gnus/waveguide-generator.git
cd waveguide-generator
```

Or download the ZIP from [GitHub](https://github.com/m3gnus/waveguide-generator) → **Code** → **Download ZIP**, then extract it.

### 3. Install

Run once from the project folder (the folder containing `package.json`):

| Platform      | Command                          |
| ------------- | -------------------------------- |
| macOS / Linux | `bash install/install.sh`        |
| Windows       | double-click `install/install.bat` |

The installer sets up a Python virtual environment and installs all dependencies including `gmsh` and `bempp-cl`.

### 4. Launch

| Platform | Command                              |
| -------- | ------------------------------------ |
| macOS    | double-click `launch/mac.command`    |
| Windows  | double-click `launch\windows.bat`    |
| Linux    | `bash launch/linux.sh`               |

The app opens in your browser at `http://localhost:3000`. Close the terminal to stop it.

## OpenCL Setup

BEM simulation requires an OpenCL runtime.

**macOS (Apple Silicon)** — run the helper script to set up a CPU OpenCL environment:

```bash
./scripts/setup-opencl-backend.sh
```

> Note: Apple Silicon does not support OpenCL GPU — the solver runs on CPU via [pocl](https://portablecl.org/).

**Windows / Linux** — OpenCL GPU support depends on your vendor drivers (NVIDIA, AMD, or Intel). For CPU-only fallback on Linux, install `pocl-opencl-icd` from your package manager.

## Manual Dependency Install

If the installer fails to set up the solver dependencies, you can install them manually:

**gmsh** (mesh generation):

```bash
# macOS / Linux
.venv/bin/pip install --pre --extra-index-url https://gmsh.info/python-packages-dev -r server/requirements-gmsh.txt

# Windows
.venv\Scripts\python.exe -m pip install --pre --extra-index-url https://gmsh.info/python-packages-dev -r server\requirements-gmsh.txt
```

**bempp-cl** (BEM solver):

```bash
# macOS / Linux
.venv/bin/pip install git+https://github.com/bempp/bempp-cl.git

# Windows
.venv\Scripts\python.exe -m pip install git+https://github.com/bempp/bempp-cl.git
```

> The first `bempp-cl` install can take several minutes — this is normal.

## Project Layout

```
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

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

## Troubleshooting

**Backend not connected** — start the app via a launcher or `npm start`, then check:

```bash
curl http://localhost:8000/health
```

**Check backend health**:

```bash
npm run doctor:backend        # text report
npm run preflight:backend     # quick runtime check
```

**macOS: "cannot be opened because the developer cannot be verified"** — right-click `launch/mac.command` → Open → click Open in the security dialog.

**Python not found on Windows** — reinstall from [python.org](https://www.python.org/downloads/windows/) and tick _"Add python.exe to PATH"_. If the detected path is under `WindowsApps`, disable the Python app execution aliases in Windows Settings.

**Wrong folder** — make sure you're running install/launch from the project root (the folder containing `package.json`).

## Acknowledgments

- [AT-Horns](https://at-horns.eu/)
- [bempp-cl](https://github.com/bempp/bempp-cl)
- [Gmsh](https://gmsh.info/)

## Documentation

For architecture, module contracts, and API details see the [docs/](docs/) folder.

## License

MIT — see [LICENSE](LICENSE).
