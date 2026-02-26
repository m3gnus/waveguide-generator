#!/bin/bash
# Waveguide Generator — one-time installer for macOS and Linux
# Run from the project root: bash install/install.sh

set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  WG - Waveguide Generator — Setup                           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Node.js ────────────────────────────────────────────────────────
echo "Checking Node.js..."
if ! command -v node &>/dev/null; then
    echo "ERROR: Node.js is not installed."
    echo "       Install from https://nodejs.org/ and re-run this script."
    exit 1
fi
NODE_VERSION=$(node --version)
echo "  Node.js: $NODE_VERSION"

# ── npm ────────────────────────────────────────────────────────────
if ! command -v npm &>/dev/null; then
    echo "ERROR: npm is not installed (should come with Node.js)."
    exit 1
fi
echo "  npm:     $(npm --version)"
echo ""

# ── Frontend dependencies ──────────────────────────────────────────
if [ ! -f "package.json" ]; then
    echo "ERROR: package.json not found in this folder."
    echo "       Make sure you are running install/install.sh from the full project folder."
    exit 1
fi

echo "Installing frontend dependencies..."
if [ -f "package-lock.json" ]; then
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
for cmd in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        VERSION=$("$cmd" -c "import sys; print(sys.version_info[:2])")
        if "$cmd" -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,14) else 1)" 2>/dev/null; then
            PYTHON_BIN="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: Python 3.10 through 3.13 is required."
    echo "       Install from https://www.python.org/ and re-run this script."
    exit 1
fi
echo "  Python:  $($PYTHON_BIN --version)"
echo ""

# ── Virtual environment ────────────────────────────────────────────
echo "Creating Python virtual environment (.venv)..."
if [ -d ".venv" ]; then
    echo "  .venv already exists, skipping creation."
else
    "$PYTHON_BIN" -m venv .venv
    echo "  Created."
fi

echo "Installing backend dependencies..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r server/requirements.txt
echo "  Done."
echo ""

# ── Optional: bempp-cl ─────────────────────────────────────────────
echo "Skip BEM solver install? (needed only for acoustic simulations)"
read -r -p "  Install bempp-cl? This can take 5–10 minutes. [y/N] " REPLY
if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    echo "Installing bempp-cl..."
    .venv/bin/pip install git+https://github.com/bempp/bempp-cl.git
    echo "  Done."
else
    echo "  Skipped. You can install later:"
    echo "    .venv/bin/pip install git+https://github.com/bempp/bempp-cl.git"
fi
echo ""

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Setup complete!                                             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "To start the app:"
echo "  • Double-click  launch/mac.command   (macOS)"
echo "  • Run           launch/linux.sh      (Linux)"
echo "  • Or:           npm start"
if [[ "$(uname -s)" == "Darwin" ]]; then
    echo ""
    echo "For true bempp OpenCL on Apple Silicon:"
    echo "  • Run           ./scripts/setup-opencl-backend.sh"
fi
echo ""
