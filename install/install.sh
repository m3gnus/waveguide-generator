#!/bin/bash
# Waveguide Generator — one-time installer for macOS and Linux
# Run from the project root: bash install/install.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  WG - Waveguide Generator — Setup                           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

print_project_folder_help() {
    echo "ERROR: This does not look like the full Waveguide Generator project folder."
    echo "Current folder: $PWD"
    echo ""
    echo "Fix steps:"
    echo "  1. Download the full project ZIP from GitHub."
    echo "  2. Extract the ZIP completely."
    echo "  3. Open the extracted folder (usually waveguide-generator-main)."
    echo "  4. Re-run this script."
    echo ""
    echo "GitHub: https://github.com/m3gnus/waveguide-generator"
}

# ── Project folder sanity check ───────────────────────────────────
echo "Verifying project folder..."
missing=0
for file in package.json install/install.sh server/requirements.txt server/requirements-gmsh.txt launch/mac.command launch/linux.sh; do
    if [[ ! -f "$file" ]]; then
        echo "  - Missing: $file"
        missing=1
    fi
done
if [[ "$missing" -ne 0 ]]; then
    echo ""
    print_project_folder_help
    exit 1
fi
echo "  Project folder looks good."
echo ""

# ── Node.js ────────────────────────────────────────────────────────
echo "Checking Node.js..."
if ! command -v node >/dev/null 2>&1; then
    echo "ERROR: Node.js is not installed."
    echo "       Install from https://nodejs.org/ and re-run this script."
    exit 1
fi
echo "  Node.js: $(node --version)"

if ! command -v npm >/dev/null 2>&1; then
    echo "ERROR: npm is not installed (should come with Node.js)."
    exit 1
fi
echo "  npm:     $(npm --version)"
echo ""

# ── Frontend dependencies ──────────────────────────────────────────
if [[ ! -f "package.json" ]]; then
    echo "ERROR: package.json not found in this folder."
    echo "       Make sure you are running install/install.sh from the full project folder."
    exit 1
fi

echo "Installing frontend dependencies..."
if [[ -f "package-lock.json" ]]; then
    npm ci
else
    echo "WARNING: package-lock.json was not found."
    echo "         This usually means the project was not downloaded or extracted completely."
    echo "         Falling back to npm install..."
    npm install
fi
echo "  Done."
echo ""

# ── Python ─────────────────────────────────────────────────────────
echo "Checking Python 3..."
PYTHON_BIN=""
PYTHON_VERSION=""
PYTHON_PATH=""
FIRST_PYTHON_BIN=""
FIRST_PYTHON_VERSION=""
FIRST_PYTHON_PATH=""

for cmd in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        candidate_path="$(command -v "$cmd")"
        candidate_version="$($cmd -c "import sys; print('{}.{}.{}'.format(*sys.version_info[:3]))" 2>/dev/null || true)"

        if [[ -z "$FIRST_PYTHON_BIN" ]]; then
            FIRST_PYTHON_BIN="$cmd"
            FIRST_PYTHON_PATH="$candidate_path"
            FIRST_PYTHON_VERSION="$candidate_version"
        fi

        if "$cmd" -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,15) else 1)" >/dev/null 2>&1; then
            PYTHON_BIN="$cmd"
            PYTHON_PATH="$candidate_path"
            PYTHON_VERSION="$candidate_version"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    echo "ERROR: Python 3.10 through 3.14 is required."
    if [[ -n "$FIRST_PYTHON_BIN" ]]; then
        echo "       Detected command: $FIRST_PYTHON_BIN"
        [[ -n "$FIRST_PYTHON_PATH" ]] && echo "       Detected path: $FIRST_PYTHON_PATH"
        if [[ -n "$FIRST_PYTHON_VERSION" ]]; then
            echo "       Detected version: $FIRST_PYTHON_VERSION"
            echo "       This version is outside the supported range."
        fi
    else
        echo "       No Python command was detected in PATH."
    fi
    echo "       Install from https://www.python.org/ and re-run this script."
    exit 1
fi

echo "  Python command: $PYTHON_BIN"
[[ -n "$PYTHON_VERSION" ]] && echo "  Python version: $PYTHON_VERSION"
[[ -n "$PYTHON_PATH" ]] && echo "  Python path: $PYTHON_PATH"
echo ""

# ── Virtual environment ────────────────────────────────────────────
echo "Creating Python virtual environment (.venv)..."
if [[ -d ".venv" ]]; then
    echo "  .venv already exists, skipping creation."
else
    "$PYTHON_BIN" -m venv .venv
    echo "  Created."
fi

echo "Installing backend dependencies..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r server/requirements.txt
echo "  Core backend requirements installed."

echo "Installing gmsh Python package (required for /api/mesh/build)..."
if .venv/bin/pip install --quiet -r server/requirements-gmsh.txt; then
    echo "  gmsh installed from default index."
else
    echo "  Default gmsh install failed. Retrying with gmsh.info snapshot index..."
    if [[ "$(uname -s)" == "Linux" ]] && .venv/bin/pip install --quiet --pre --force-reinstall --no-cache-dir \
        --extra-index-url https://gmsh.info/python-packages-dev-nox \
        -r server/requirements-gmsh.txt; then
        echo "  gmsh installed from gmsh.info headless Linux snapshot index."
    elif .venv/bin/pip install --quiet --pre --force-reinstall --no-cache-dir \
        --extra-index-url https://gmsh.info/python-packages-dev \
        -r server/requirements-gmsh.txt; then
        echo "  gmsh installed from gmsh.info snapshot index."
    else
        echo "  WARNING: Could not install gmsh Python package automatically."
        echo "           Backend setup will continue, but /api/mesh/build needs gmsh."
        echo "           Try manually:"
        echo "             .venv/bin/pip install --pre --extra-index-url https://gmsh.info/python-packages-dev -r server/requirements-gmsh.txt"
        if [[ "$(uname -s)" == "Linux" ]]; then
            echo "             .venv/bin/pip install --pre --extra-index-url https://gmsh.info/python-packages-dev-nox -r server/requirements-gmsh.txt"
        fi
    fi
fi
echo ""

# ── Automatic: bempp-cl ────────────────────────────────────────────
echo "Installing bempp-cl (needed for acoustic simulations)..."
if .venv/bin/pip install git+https://github.com/bempp/bempp-cl.git; then
    echo "  bempp-cl installed."
else
    echo "  WARNING: bempp-cl automatic install failed."
    echo "           You can retry later with:"
    echo "             .venv/bin/pip install git+https://github.com/bempp/bempp-cl.git"
fi
echo ""

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Setup complete!                                             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "To start the app:"
echo "  - macOS: double-click launch/mac.command"
echo "  - Linux: run bash launch/linux.sh"
echo "  - Or run npm start"
if [[ "$(uname -s)" == "Darwin" ]]; then
    echo ""
    echo "For true bempp OpenCL on Apple Silicon:"
    echo "  - Run ./scripts/setup-opencl-backend.sh"
fi
echo ""
