#!/bin/bash
# MWG Horn BEM Solver Startup Script

echo "🚀 Starting MWG Horn BEM Solver Backend..."
echo ""

# Check if we're in the server directory
if [ ! -f "app.py" ]; then
    echo "❌ Error: app.py not found. Please run this script from the server/ directory."
    exit 1
fi

# Check if Python 3 is available
if [ -x "../.venv/bin/python" ]; then
    PYTHON_BIN="../.venv/bin/python"
elif command -v python3 &> /dev/null; then
    PYTHON_BIN="python3"
else
    echo "❌ Error: Python 3 is not installed."
    exit 1
fi

echo "✅ Python found: $($PYTHON_BIN --version)"

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
    echo "⚠️  Warning: bempp-cl not found. Server will run with mock solver."
    echo "   To install bempp-cl, run:"
    echo "   $PYTHON_BIN -m pip install git+https://github.com/bempp/bempp-cl.git"
fi

echo ""
echo "Starting server on http://localhost:8000..."
echo "Press Ctrl+C to stop"
echo ""

# Start the server
$PYTHON_BIN app.py
