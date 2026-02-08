# Waveguide Generator

Waveguide Generator is a browser-based tool for designing acoustic horns, previewing geometry in 3D, running BEM simulations, and exporting manufacturing/simulation files.

## App Screenshot

![Waveguide Generator App Screenshot](docs/images/waveguide-generator-screenshot.png)

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

## Add a Screenshot to GitHub README

1. Start the app (`npm start`) and open [http://localhost:3000](http://localhost:3000).
2. On macOS, take a screenshot (`Shift + Command + 4`, then `Space`, then click browser window).
3. Save the image as `waveguide-generator-screenshot.png`.
4. Put it in `docs/images/`.
5. Add this line near the top of `README.md`:

```md
![Waveguide Generator App Screenshot](docs/images/waveguide-generator-screenshot.png)
```

6. Commit and push:

```bash
git add README.md docs/images/waveguide-generator-screenshot.png
git commit -m "Add app screenshot to README"
git push
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
