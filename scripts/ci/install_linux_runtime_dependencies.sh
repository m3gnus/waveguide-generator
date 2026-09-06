#!/usr/bin/env bash
# Install the system libraries the Linux bundle documents as prerequisites.
# This is for the CI installed-package qualification environment only; the
# user installer still refuses to install anything when its real gmsh import
# cannot load these libraries.

set -euo pipefail

# Keep this list synchronized with the Ubuntu 24.04 command in
# scripts/build_bundle.py, README.md, and docs/DEVELOPMENT.md. Do not replace
# the installer's preflight with --skip-checks.
sudo apt-get update
sudo apt-get install --yes --no-install-recommends \
    libglu1-mesa libgl1 libgomp1 libfontconfig1 \
    libxrender1 libxcursor1 libxft2 libxinerama1 \
    libxi6 libxext6
