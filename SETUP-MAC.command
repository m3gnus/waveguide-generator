#!/bin/bash
# Waveguide Generator quick setup entrypoint for macOS

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  WG - Waveguide Generator — Quick Setup                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

echo "Verifying project folder..."
missing=0
for file in package.json install/install.sh server/requirements.txt server/requirements-gmsh.txt launch/mac.command; do
    if [[ ! -f "$file" ]]; then
        echo "  - Missing: $file"
        missing=1
    fi
done

if [[ "$missing" -ne 0 ]]; then
    echo ""
    echo "ERROR: This does not look like the full Waveguide Generator project folder."
    echo "Current folder: $PWD"
    echo ""
    echo "Fix steps:"
    echo "  1. Download the full project ZIP from GitHub."
    echo "  2. Extract the ZIP completely."
    echo "  3. Open the extracted folder (usually waveguide-generator-main)."
    echo "  4. Double-click SETUP-MAC.command again."
    echo ""
    echo "GitHub: https://github.com/m3gnus/waveguide-generator"
    read -r -p "Press Enter to close..." _
    exit 1
fi

echo "  Project folder looks good."
echo ""
exec bash install/install.sh
