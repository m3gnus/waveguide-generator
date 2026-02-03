# MWG - Mathematical Waveguide Generator

A web-based 3D visualization and design tool for acoustic horns (waveguides), supporting OSSE and R-OSSE profiles with integrated BEM acoustic simulation.

## Features

*   **Real-time 3D Rendering:** Visualize horn geometry instantly with multiple display modes (standard, zebra stripes, wireframe, curvature)
*   **Parametric Design:** Adjust throat angle, coverage angle, rollback, and more with live updates
*   **Multiple Models:** Support for OSSE (Oblate Spheroid) and R-OSSE (Round-over) horn profiles
*   **BEM Acoustic Simulation:** Run boundary element method simulations directly from the browser
*   **Export:** STL for 3D printing, Gmsh .geo for meshing, MWG config files, and CSV profiles
*   **Morphing:** Circular to rectangular throat morphing with customizable parameters

## Quick Start

### 1. One-Time Setup

Run the setup script to install all dependencies (Node.js and Python):

```bash
./setup.sh
```

This installs:
- Frontend dependencies (npm packages)
- Backend dependencies (Python packages)
- BEM solver (bempp-cl) for acoustic simulations

**Note:** bempp-cl is a large package and may take 5-10 minutes to install.

### 2. Start the Application

```bash
npm start
```

This starts both servers:
- **Frontend:** http://localhost:3000 (3D visualization and design)
- **Backend:** http://localhost:8000 (BEM acoustic simulations)

Press `Ctrl+C` to stop both servers.

### 3. Run Your First Simulation

1. Open http://localhost:3000 in your browser
2. Click the **"Simulation"** tab
3. Status should show "Connected to BEM solver" (green dot)
4. Click **"Run BEM Simulation"**
5. View results!

### Alternative: Run Servers Separately

If you only need the frontend or backend:

```bash
npm run start:frontend   # Frontend only (port 3000)
npm run start:backend    # Backend only (port 8000)
```

## Using the Platform

### Geometry Design

**Geometry Tab:**
- Adjust horn parameters with sliders
- Real-time 3D preview
- Multiple display modes (metal, zebra, wireframe, curvature)
- Export to STL, Gmsh, MWG config, CSV

### BEM Simulation

**Simulation Tab:**
- Configure frequency range (100-10000 Hz default)
- Choose simulation type (infinite baffle/free-standing)
- **ABEC.Polars Directivity Maps:**
  - User-adjustable polar configuration (angle range, normalization, distance, inclination)
  - Professional 2D heatmap visualization (frequency vs angle)
  - Color-coded SPL maps matching industry standards
- **Post-Processing Smoothing:**
  - Fractional octave smoothing (1/1, 1/2, 1/3, 1/6, 1/12, 1/24, 1/48)
  - Variable smoothing (frequency-dependent bandwidth for EQ work)
  - Psychoacoustic smoothing (perception-based weighting)
  - ERB smoothing (matches ear's frequency resolution)
  - Keyboard shortcuts (Ctrl+Shift+1-9, X, Y, Z)
  - Apply/remove smoothing without re-running simulation
- Run simulations and view results:
  - Frequency response
  - Directivity patterns
  - Impedance curves
  - Directivity Index (DI)
  - Polar directivity heatmaps
- **Export Results:**
  - PNG/SVG images of all charts
  - CSV data files (frequency, SPL, DI, impedance)
  - JSON format (complete results with metadata)
  - Text reports with summary statistics

**Note:** Simulations work in two modes:
- **With backend:** Real BEM physics (takes seconds to minutes)
- **Without backend:** Mock data for UI testing (instant)

## Project Structure

```
├── src/
│   ├── geometry/       # Horn math and mesh generation
│   ├── viewer/         # Three.js 3D visualization
│   ├── config/         # Parameter management
│   ├── solver/         # BEM solver interface
│   ├── ui/             # User interface components
│   └── export/         # Export functionality
├── server/             # Python BEM backend
│   ├── app.py          # FastAPI application
│   ├── solver/         # bempp-cl solver package
│   ├── requirements.txt
│   ├── start.sh        # Startup script
│   └── README.md       # Backend setup guide
└── tests/              # Unit tests
```

## Testing

```bash
# Unit tests
npm test

```

## Troubleshooting

### "BEM solver not available"
- Backend isn't running
- Start it: `cd server && ./start.sh`

### "python: command not found" (macOS)
- Use `python3` instead of `python`
- macOS doesn't have a `python` command by default

### Python 3.13 Compatibility
- ✅ Fixed! Using pydantic>=2.10.0 for Python 3.13 support

### Slow Installation
- bempp-cl is a large package (~100MB with dependencies)
- Installation takes 5-10 minutes
- This is normal

## Documentation

*   **[docs/README.md](docs/README.md)** - Documentation index
*   **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** - Detailed technical architecture, module design, and development roadmap
*   **[docs/AGENT_INSTRUCTIONS.md](docs/AGENT_INSTRUCTIONS.md)** - Guide for AI agents working on this project
*   **[docs/AI_GUIDANCE.md](docs/AI_GUIDANCE.md)** - AI collaboration guidelines and best practices
*   **[server/README.md](server/README.md)** - Python backend setup and bempp-cl installation details

## Technologies

*   **Frontend:** JavaScript (ES modules), Three.js 0.160
*   **Backend:** Python 3.8-3.13, FastAPI, bempp-cl 0.2.3
*   **Testing:** Jest (unit), Playwright (E2E)
*   **Build:** Webpack 5

## License

MIT
