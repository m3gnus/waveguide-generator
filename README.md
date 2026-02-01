# ATH Horn Design Platform

A web-based 3D visualization and design tool for acoustic horns (waveguides), supporting OSSE, R-OSSE, and OS-GOS profiles with integrated BEM acoustic simulation.

## Features

*   **Real-time 3D Rendering:** Visualize horn geometry instantly with multiple display modes (standard, zebra stripes, wireframe, curvature)
*   **Parametric Design:** Adjust throat angle, coverage angle, rollback, and more with live updates
*   **Multiple Models:** Support for OSSE (Oblate Spheroid), R-OSSE (Round-over), and OS-GOS horn profiles
*   **BEM Acoustic Simulation:** Run boundary element method simulations directly from the browser
*   **Export:** STL for 3D printing, Gmsh .geo for meshing, ATH config files, and CSV profiles
*   **Morphing:** Circular to rectangular throat morphing with customizable parameters

## Quick Start

### 1. Install and Run Frontend

```bash
npm install
npm run dev
```

Open http://localhost:3000 in your browser. The app is fully functional for geometry design and export.

### 2. BEM Simulation Setup (Optional)

For acoustic simulations, you need the Python backend:

```bash
cd server

# Install dependencies
pip3 install -r requirements.txt

# Install bempp-cl (BEM solver)
pip3 install git+https://github.com/bempp/bempp-cl.git

# Start backend (use python3, NOT python on macOS)
./start.sh
```

Backend runs on http://localhost:8000

**Important:** Use `python3` on macOS, not `python`

### 3. Run Your First Simulation

1. Open http://localhost:3000
2. Click the **"Simulation"** tab
3. Status should show "Connected to BEM solver" (green dot)
4. Click **"Run BEM Simulation"**
5. View results!

## Using the Platform

### Geometry Design

**Geometry Tab:**
- Adjust horn parameters with sliders
- Real-time 3D preview
- Multiple display modes (metal, zebra, wireframe, curvature)
- Export to STL, Gmsh, ATH config, CSV

### BEM Simulation

**Simulation Tab:**
- Configure frequency range (100-10000 Hz default)
- Choose simulation type (infinite baffle/free-standing)
- Run simulations and view results:
  - Frequency response
  - Directivity patterns
  - Impedance curves
  - Directivity Index (DI)

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
│   ├── solver.py       # bempp-cl solver
│   ├── requirements.txt
│   ├── start.sh        # Startup script
│   └── README.md       # Backend setup guide
└── tests/              # Unit and E2E tests
```

## Testing

```bash
# Unit tests
npm test

# E2E tests
npm run test:e2e

# E2E tests with browser visible
npm run test:e2e:headed
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

*   **[ARCHITECTURE.md](ARCHITECTURE.md)** - Detailed technical architecture, module design, and development roadmap
*   **[AGENT_INSTRUCTIONS.md](AGENT_INSTRUCTIONS.md)** - Guide for AI agents working on this project
*   **[AI_GUIDANCE.md](AI_GUIDANCE.md)** - AI collaboration guidelines and best practices
*   **[server/README.md](server/README.md)** - Python backend setup and bempp-cl installation details

## Technologies

*   **Frontend:** JavaScript (ES modules), Three.js 0.160
*   **Backend:** Python 3.8-3.13, FastAPI, bempp-cl 0.2.3
*   **Testing:** Jest (unit), Playwright (E2E)
*   **Build:** Webpack 5

## License

MIT
