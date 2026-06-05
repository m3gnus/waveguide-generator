# Contributing

## Development Setup

1. Install all dependencies:
   ```bash
   # macOS / Linux
   bash install/install.sh
   ```
   ```bat
   :: Windows
   install\install.bat
   ```
   Or manually:
   ```bash
   npm ci
   python3 -m venv .venv
   .venv/bin/pip install --upgrade pip
   .venv/bin/pip install -r server/requirements.txt
   .venv/bin/pip install -r server/requirements-gmsh.txt
   .venv/bin/pip install git+https://github.com/bempp/bempp-cl.git@d4f23c4b77b4e86e0b2c9da42db39fea2995bb33
   mkdir -p .waveguide
   printf '%s\n' "$PWD/.venv/bin/python" > .waveguide/backend-python.path
   ```

2. Start the app:
   ```bash
   npm start
   ```

## Branches and Commits

- Create focused branches from `main`.
- Keep commits small and descriptive.
- Reference related issue numbers when relevant.

## Test Requirements

Before opening a pull request, run:

```bash
npm test
npm run test:server
npm run build
```

Backend npm scripts use `.waveguide/backend-python.path` when present, so they run against the interpreter prepared by the installer/manual setup.

## Pull Requests

- Explain what changed and why.
- Include screenshots/GIFs for UI changes.
- Note any solver/backend behavior changes.
- Call out follow-up work explicitly.
