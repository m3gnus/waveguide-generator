#!/bin/bash
# WG - Waveguide Generator Setup Script
# One-time installation of all dependencies

set -e  # Exit on error

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  WG - Waveguide Generator Setup                ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Check Node.js
echo "📦 Checking Node.js..."
if ! command -v node &> /dev/null; then
    echo "❌ Error: Node.js is not installed."
    echo "   Please install Node.js from https://nodejs.org/"
    exit 1
fi
echo "✅ Node.js found: $(node --version)"
echo ""

# Check npm
if ! command -v npm &> /dev/null; then
    echo "❌ Error: npm is not installed."
    exit 1
fi
echo "✅ npm found: $(npm --version)"
echo ""

# Install frontend dependencies
echo "📥 Installing frontend dependencies..."
npm install
if [ $? -eq 0 ]; then
    echo "✅ Frontend dependencies installed"
else
    echo "❌ Failed to install frontend dependencies"
    exit 1
fi
echo ""

# Check Python 3
echo "🐍 Checking Python 3..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is not installed."
    echo "   Please install Python 3 from https://www.python.org/"
    exit 1
fi
echo "✅ Python 3 found: $(python3 --version)"
echo ""

# Check pip3
if ! command -v pip3 &> /dev/null; then
    echo "❌ Error: pip3 is not installed."
    exit 1
fi
echo "✅ pip3 found: $(pip3 --version)"
echo ""

# Install backend dependencies
echo "📥 Installing backend dependencies..."
cd server
pip3 install -r requirements.txt
if [ $? -eq 0 ]; then
    echo "✅ Backend dependencies installed"
else
    echo "❌ Failed to install backend dependencies"
    cd ..
    exit 1
fi
cd ..
echo ""

# Optional: Install bempp-cl
echo "🔬 Installing BEM solver (bempp-cl)..."
echo "   This is a large package and may take 5-10 minutes..."
pip3 install git+https://github.com/bempp/bempp-cl.git
if [ $? -eq 0 ]; then
    echo "✅ bempp-cl installed successfully"
else
    echo "⚠️  Warning: bempp-cl installation failed or was skipped"
    echo "   The app will still work with mock simulation data"
    echo "   You can install it later with:"
    echo "   pip3 install git+https://github.com/bempp/bempp-cl.git"
fi
echo ""

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✅ Setup Complete!                                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "To start the application, run:"
echo ""
echo "    npm start"
echo ""
echo "This will start both the frontend (http://localhost:3000)"
echo "and backend (http://localhost:8000) servers."
echo ""
