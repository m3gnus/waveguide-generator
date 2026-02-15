# Waveguide Generator

A browser-based tool for designing acoustic horns — live 3D preview, parameter controls, BEM simulation, and file exports for manufacturing and simulation workflows.

![Waveguide Generator App Screenshot](docs/images/waveguide-generator-screenshot.png)

## Prerequisites

Two things need to be installed on your computer before you begin:

- **[Node.js 18+](https://nodejs.org/)** — download and install the LTS version
- **[Python 3.10 - 3.13](https://www.python.org/downloads/)** — on Windows, tick *"Add python.exe to PATH"* during install

That's all. Everything else is handled by the install script.

## Install

Run this **once** from the project folder to install all dependencies:

**macOS / Linux** — open a terminal in the project folder and run:
```bash
bash install/install.sh
```

**Windows** — double-click `install\install.bat`.

The script checks your environment, installs all dependencies, and sets up a Python virtual environment. It will also ask whether you want to install the optional BEM acoustic solver (see below).

## Run the app

**macOS** — double-click `launch/mac.command`
**Windows** — double-click `launch\windows.bat`
**Linux** — run `bash launch/linux.sh`

The app opens automatically in your browser at `http://localhost:3000`. Close the terminal window to stop it.

## Optional: BEM Solver

The install script will ask whether to install `bempp-cl`, a BEM acoustic solver. This enables the **Start BEM Simulation** feature. It is optional — all other features (3D preview, mesh export, ABEC export, etc.) work without it. Installation can take 5–10 minutes the first time.

Supported backend dependency matrix:
- Python: `>=3.10,<3.14`
- gmsh Python package: `>=4.10,<5.0`
- bempp-cl: `>=0.4,<0.5` (`/api/solve`)
- legacy `bempp_api` fallback: `>=0.3,<0.4` (`/api/solve (legacy fallback)`)

To install it later:

```bash
# macOS / Linux
.venv/bin/pip install git+https://github.com/bempp/bempp-cl.git

# Windows
.venv\Scripts\python.exe -m pip install git+https://github.com/bempp/bempp-cl.git
```

## Features

- R-OSSE and OSSE horn profile generation with live parameter controls
- Real-time 3D rendering (standard, zebra, wireframe, curvature)
- BEM simulation workflow with backend job submission and result plotting
- Export: STL, GEO, MSH, ABEC project ZIP, CSV profiles, MWG config text

## Project layout

```
src/          Frontend app modules (geometry, export, simulation, viewer, UI)
server/       FastAPI backend and solver code
tests/        Frontend and integration tests
scripts/      Dev utilities
install/      One-time setup scripts (mac/linux and windows)
launch/       Double-click launchers (mac, windows, linux)
docs/         Architecture and technical reference
```

## Development

```bash
npm test              # JS unit tests
npm run test:server   # Python backend tests
npm run test:ath      # ATH parity check (strict infra diagnostics)
npm run build         # Production bundle
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and contribution guidelines.
See [docs/PROJECT_DOCUMENTATION.md](docs/PROJECT_DOCUMENTATION.md) for architecture and API details.

## Troubleshooting

**Backend not connected** — start the app via a launcher or `npm start`, then check:
```bash
curl http://localhost:8000/health
```

**ATH parity infra failures** — `npm run test:ath` runs in strict mode by default and prints concrete fix steps (`ATH_PARITY_STRICT_INFRA=0` disables strict mode).
Typical checks:
```bash
python3 -c "import gmsh; print(gmsh.__version__)"
gmsh -version
curl http://localhost:8000/health
```

**macOS: "cannot be opened because the developer cannot be verified"** — right-click `launch/mac.command` → Open → click Open in the security dialog.

**Python not found on Windows** — reinstall Python from [python.org](https://www.python.org/downloads/windows/) and tick *"Add python.exe to PATH"*.

**bempp-cl install is slow** — this is normal. The first install can take several minutes.

## Acknowledgments

- [AT-Horns](https://at-horns.eu/)
- [bempp-cl](https://github.com/bempp/bempp-cl)
- [Gmsh](https://gmsh.info/)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
