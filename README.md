# Waveguide Generator

Waveguide Generator is a browser-based tool for designing acoustic horns, previewing geometry in 3D, running BEM simulations, and exporting manufacturing/simulation files.

## What You Can Do

- Design OSSE and R-OSSE horn profiles with live parameter controls.
- View geometry in real time (standard, zebra, wireframe, curvature).
- Run acoustic simulations from the Simulation tab.
- Export design data:
  - `STL` (3D model)
  - `GEO` and `MSH` (Gmsh/BEM workflow)
  - `ABEC` project ZIP
  - `CSV` profiles
  - MWG config text

## Quick Setup

### 1. Install frontend dependencies

```bash
npm ci
```

### 2. (Optional, for real simulations) Set up backend in a local virtual environment

```bash
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r server/requirements.txt
./.venv/bin/pip install git+https://github.com/bempp/bempp-cl.git
```

If you skip backend setup, the UI still runs and uses mock simulation behavior when the solver is unavailable.

## Run the App

### Start both frontend and backend

```bash
npm start
```

- Frontend: [http://localhost:3000](http://localhost:3000)
- Backend: [http://localhost:8000](http://localhost:8000)

### Start only one side

```bash
npm run start:frontend
npm run start:backend
```

## Basic User Workflow

1. Open the Geometry tab and set horn parameters.
2. Keep `Real-time Updates` on (or click `Update Model`).
3. Use the Simulation tab to set frequency range and run solver.
4. Export files from the Geometry tab when ready.

## Verification Commands

```bash
npm test
npm run test:server
npm run build
```

## Troubleshooting

### Simulation says backend is not connected

- Make sure backend is running on port `8000`.
- Check health endpoint:

```bash
curl http://localhost:8000/health
```

### `python` command issues on macOS

Use `python3` and `./.venv/bin/python` explicitly.

### bempp-cl install is slow

This is normal. Building/installing `bempp-cl` can take several minutes.

## More Technical Detail

See `PROJECT_DOCUMENTATION.md` for architecture, data contracts, API details, and development/testing notes.
