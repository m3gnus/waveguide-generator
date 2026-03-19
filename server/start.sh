#!/bin/bash
# MWG Horn BEM Solver Startup Script

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PREFERRED_PYTHON_FILE="$ROOT_DIR/.waveguide/backend-python.path"

echo "🚀 Starting MWG Horn BEM Solver Backend..."
echo ""

# Check if we're in the server directory
if [ ! -f "app.py" ]; then
    echo "❌ Error: app.py not found. Please run this script from the server/ directory."
    exit 1
fi

PYTHON_BIN_OVERRIDE="${PYTHON_BIN:-}"
WG_BACKEND_PYTHON_OVERRIDE="${WG_BACKEND_PYTHON:-}"
PYTHON_BIN=""
PYTHON_SOURCE=""

# Resolve backend interpreter using the same priority contract as npm start.
if [ -n "$PYTHON_BIN_OVERRIDE" ]; then
    PYTHON_BIN="$PYTHON_BIN_OVERRIDE"
    PYTHON_SOURCE="env:PYTHON_BIN"
elif [ -n "$WG_BACKEND_PYTHON_OVERRIDE" ]; then
    PYTHON_BIN="$WG_BACKEND_PYTHON_OVERRIDE"
    PYTHON_SOURCE="env:WG_BACKEND_PYTHON"
elif [ -f "$PREFERRED_PYTHON_FILE" ]; then
    MARKER_PYTHON="$(head -n 1 "$PREFERRED_PYTHON_FILE" | tr -d '\r')"
    if [ -n "$MARKER_PYTHON" ] && [ -x "$MARKER_PYTHON" ]; then
        PYTHON_BIN="$MARKER_PYTHON"
        PYTHON_SOURCE="marker:$PREFERRED_PYTHON_FILE"
    fi
fi

if [ -z "$PYTHON_BIN" ] && [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
    PYTHON_SOURCE="fallback:.venv"
elif [ -z "$PYTHON_BIN" ] && [ -x "$ROOT_DIR/.venv/Scripts/python.exe" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/Scripts/python.exe"
    PYTHON_SOURCE="fallback:.venv"
elif [ -z "$PYTHON_BIN" ] && [ -x "$HOME/.waveguide-generator/opencl-cpu-env/bin/python" ]; then
    PYTHON_BIN="$HOME/.waveguide-generator/opencl-cpu-env/bin/python"
    PYTHON_SOURCE="fallback:opencl-cpu-env"
elif [ -z "$PYTHON_BIN" ] && command -v python3 &> /dev/null; then
    PYTHON_BIN="python3"
    PYTHON_SOURCE="fallback:python3"
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "❌ Error: Python 3 is not installed."
    exit 1
fi

echo "✅ Python found: $($PYTHON_BIN --version)"
echo "   Source: $PYTHON_SOURCE"
if [ "$PYTHON_SOURCE" = "fallback:python3" ]; then
    echo "⚠️  No verified project interpreter found. Re-run install/install.sh or install/install.bat."
fi

# Check minimum Python version
$PYTHON_BIN - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"❌ Python 3.10+ is required, found {sys.version}")
PY

echo ""

# Check if bempp is installed
echo "Checking for bempp-cl..."
$PYTHON_BIN - <<'PY'
import importlib.util
import sys
has_new = importlib.util.find_spec("bempp_cl")
has_old = importlib.util.find_spec("bempp_api")
sys.exit(0 if (has_new or has_old) else 1)
PY
if [ $? -eq 0 ]; then
    echo "✅ bempp-cl is installed"
else
    echo "⚠️  Warning: bempp-cl not found."
    echo "   The server can start, but /api/solve will stay unavailable until bempp-cl is installed."
    echo "   To install bempp-cl, run:"
    echo "   $PYTHON_BIN -m pip install git+https://github.com/bempp/bempp-cl.git"
fi

echo ""
echo "Starting server on http://localhost:8000..."
echo "Press Ctrl+C to stop"
echo ""

# Start the server
$PYTHON_BIN app.py
