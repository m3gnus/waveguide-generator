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
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is not installed."
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"
echo ""

# Check if bempp is installed
echo "Checking for bempp-cl..."
python3 -c "import bempp.api" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✅ bempp-cl is installed"
else
    echo "⚠️  Warning: bempp-cl not found. Server will run with mock solver."
    echo "   To install bempp-cl, run:"
    echo "   pip3 install git+https://github.com/bempp/bempp-cl.git"
fi

echo ""
echo "Starting server on http://localhost:8000..."
echo "Press Ctrl+C to stop"
echo ""

# Start the server
python3 app.py
