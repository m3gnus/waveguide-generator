#!/bin/bash
# Waveguide Generator launcher for Linux
# Make executable with: chmod +x linux.sh
# Then double-click (set "Run in terminal" in file manager) or run from terminal

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
npm start
