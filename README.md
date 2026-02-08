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

![Waveguide Generator App Screenshot](docs/images/waveguide-generator-screenshot.png)

## Tech Stack

- Frontend: Vanilla JS + Three.js + Webpack
- Backend: FastAPI (Python) with optional bempp-cl solver integration
- Tests: Node.js built-in test runner + Python `unittest`

## Prerequisites

- Node.js 18+
- npm 9+
- Python 3.10+ (Python 3.12 recommended for the widest backend package compatibility)

## macOS Install (Step by Step)

### 1. Install Node.js and npm

- Download and install Node.js LTS from [nodejs.org](https://nodejs.org/).
- Verify:

```bash
node -v
npm -v
```

### 2. Check Python 3

- macOS usually has Python 3. Verify:

```bash
python3 --version
```

If missing, install Python 3 from [python.org](https://www.python.org/downloads/macos/).

### 3. Clone the repository

```bash
git clone https://github.com/m3gnus/waveguide-generator.git
cd waveguide-generator
```

### 4. Install frontend dependencies

```bash
npm ci
```

### 5. Set up backend virtual environment

```bash
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r server/requirements.txt
```

### 6. (Optional) Install full BEM solver

```bash
./.venv/bin/pip install git+https://github.com/bempp/bempp-cl.git
```

If you skip this, the app still runs but solver features will report unavailable.

### 7. Start the app

```bash
npm start
```

- Frontend: [http://localhost:3000](http://localhost:3000)
- Backend: [http://localhost:8000](http://localhost:8000)

### 8. Verify backend health

```bash
curl http://localhost:8000/health
```

## Windows Install (Step by Step)

### 1. Install Git

- Install Git for Windows from [git-scm.com](https://git-scm.com/download/win).

### 2. Install Node.js and npm

- Install Node.js LTS from [nodejs.org](https://nodejs.org/).
- Verify in PowerShell:

```powershell
node -v
npm -v
```

### 3. Install Python 3

- Install Python 3 from [python.org](https://www.python.org/downloads/windows/).
- Enable `Add python.exe to PATH` during install.
- Verify:

```powershell
py -3 --version
```

### 4. Clone the repository

```powershell
git clone https://github.com/m3gnus/waveguide-generator.git
cd waveguide-generator
```

### 5. Install frontend dependencies

```powershell
npm ci
```

### 6. Set up backend virtual environment

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r server/requirements.txt
```

### 7. (Optional) Install full BEM solver

```powershell
.\.venv\Scripts\python.exe -m pip install git+https://github.com/bempp/bempp-cl.git
```

If you skip this, the app still runs but solver features will report unavailable.

### 8. Start the app

Open two PowerShell windows from the project root:

- Window 1 (frontend):

```powershell
npm run start:frontend
```

- Window 2 (backend):

```powershell
.\.venv\Scripts\python.exe server\app.py
```

- Frontend: [http://localhost:3000](http://localhost:3000)
- Backend: [http://localhost:8000](http://localhost:8000)

### 9. Verify backend health

```powershell
Invoke-RestMethod http://localhost:8000/health
```

## Linux Install (Step by Step)

### 1. Install system dependencies

For Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y git curl gmsh python3.12 python3.12-venv
```

### 2. Install Node.js LTS (recommended via nvm)

```bash
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm install --lts
```

Verify:

```bash
node -v
npm -v
python3.12 --version
```

### 3. Clone the repository

```bash
git clone https://github.com/m3gnus/waveguide-generator.git
cd waveguide-generator
```

### 4. Install frontend dependencies

```bash
npm ci
```

### 5. Set up backend virtual environment

```bash
python3.12 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r server/requirements.txt
```

If `python3.12` is not available in your distro repo, install it with your distro’s recommended method (or use `pyenv`) and then run the same `python3.12 -m venv ...` command.

`gmsh` in `server/requirements.txt` is intentionally optional because wheels are not available on every Python/architecture combination.  
`.geo -> .msh` generation still works with the system `gmsh` CLI installed above.

### 6. (Optional) Install full BEM solver

```bash
./.venv/bin/pip install git+https://github.com/bempp/bempp-cl.git
```

If you skip this, the app still runs but solver features will report unavailable.

### 7. Start the app

```bash
npm start
```

- Frontend: [http://localhost:3000](http://localhost:3000)
- Backend: [http://localhost:8000](http://localhost:8000)

### 8. Verify backend health

```bash
curl http://localhost:8000/health
```

## Test and Build

```bash
npm test
npm run test:ath
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

## Acknowledgments

- [ATH Horns](https://at-horns.eu/)
- [bempp-cl](https://github.com/bempp/bempp-cl)
- [Gmsh](https://gmsh.info/)

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
