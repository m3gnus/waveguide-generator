#!/bin/bash
# Waveguide Generator — installer/updater for macOS and Linux
# Run from the project root: bash install/install.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PREFERRED_PYTHON_FILE="$ROOT/.waveguide/backend-python.path"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  WG - Waveguide Generator — Install / Update                ║"
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

update_from_git() {
    if [[ "${WAVEGUIDE_INSTALL_AFTER_PULL:-0}" == "1" ]]; then
        echo "Code update already applied; continuing with the updated installer."
        echo ""
        return 0
    fi

    if [[ ! -d ".git" ]]; then
        echo "Code update skipped: this folder is not a Git clone."
        echo "ZIP downloads can be repaired by this script, but updating requires downloading a fresh ZIP."
        echo ""
        return 0
    fi

    if ! command -v git >/dev/null 2>&1; then
        echo "ERROR: This folder is a Git clone, but Git is not installed or not available in PATH."
        echo "       Install Git, then re-run this script."
        exit 1
    fi

    echo "  $(git --version)"
    echo "Checking for code updates..."
    before_commit="$(git rev-parse HEAD)"
    if ! git pull --ff-only; then
        echo ""
        echo "ERROR: Code update failed."
        echo "       This installer only performs safe fast-forward updates."
        echo "       If you have local changes, commit or stash them before updating."
        exit 1
    fi
    after_commit="$(git rev-parse HEAD)"
    if [[ "$before_commit" != "$after_commit" ]]; then
        echo "  Updated ${before_commit:0:7} -> ${after_commit:0:7}."
        echo "Restarting with the updated installer..."
        echo ""
        WAVEGUIDE_INSTALL_AFTER_PULL=1 exec bash "$ROOT/install/install.sh"
    fi
    echo ""
}

# ── Project folder sanity check ───────────────────────────────────
echo "Verifying project folder..."
missing=0
for file in package.json package-lock.json install/install.sh server/requirements.txt server/requirements-gmsh.txt server/requirements-bempp.txt launch/mac.command launch/linux.sh; do
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

update_from_git

# ── Git dependency transport ──────────────────────────────────────
if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: Git is required to install pinned backend dependencies."
    echo "       This also applies when the project was downloaded as a ZIP."
    echo "       Install Git from https://git-scm.com/ and re-run this script."
    exit 1
fi

# ── Node.js ────────────────────────────────────────────────────────
echo "Checking Node.js..."
if ! command -v node >/dev/null 2>&1; then
    echo "ERROR: Node.js is not installed."
    echo "       Install from https://nodejs.org/ and re-run this script."
    exit 1
fi
echo "  Node.js: $(node --version)"

if ! node -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 20 || (major === 20 && minor >= 19) ? 0 : 1)"; then
    echo "ERROR: Node.js 20.19 or newer is required."
    echo "       Install a current LTS version from https://nodejs.org/ and re-run this script."
    exit 1
fi

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
npm ci
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
    if [[ -x ".venv/bin/python" ]] &&
        .venv/bin/python -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix and (3,10) <= sys.version_info[:2] < (3,15) else 1)" >/dev/null 2>&1; then
        echo "  Existing .venv is valid; reusing it."
    else
        backup_path=".venv.incompatible.$(date +%Y%m%d%H%M%S)"
        backup_suffix=0
        while [[ -e "$backup_path" ]]; do
            backup_suffix=$((backup_suffix + 1))
            backup_path=".venv.incompatible.$(date +%Y%m%d%H%M%S).$backup_suffix"
        done
        echo "  Existing .venv is broken or uses an unsupported Python."
        echo "  Preserving it as $backup_path"
        mv ".venv" "$backup_path"
    fi
fi
if [[ ! -d ".venv" ]]; then
    "$PYTHON_BIN" -m venv .venv
    echo "  Created."
fi

echo "Installing backend dependencies..."
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r server/requirements.txt
echo "  Core backend requirements installed."

echo "Installing gmsh Python package (required for /api/mesh/build)..."
if .venv/bin/python -m pip install --quiet -r server/requirements-gmsh.txt; then
    echo "  gmsh Python package installed from default index."
else
    echo "  Default gmsh install failed. Retrying with gmsh.info snapshot index..."
    if [[ "$(uname -s)" == "Linux" ]] && .venv/bin/python -m pip install --quiet --pre --force-reinstall --no-cache-dir \
        --extra-index-url https://gmsh.info/python-packages-dev-nox \
        -r server/requirements-gmsh.txt; then
        echo "  gmsh Python package installed from gmsh.info headless Linux snapshot index."
    elif .venv/bin/python -m pip install --quiet --pre --force-reinstall --no-cache-dir \
        --extra-index-url https://gmsh.info/python-packages-dev \
        -r server/requirements-gmsh.txt; then
        echo "  gmsh Python package installed from gmsh.info snapshot index."
    else
        echo "ERROR: Could not install gmsh Python package automatically."
        echo "Try manually:"
        echo "  .venv/bin/pip install --pre --extra-index-url https://gmsh.info/python-packages-dev -r server/requirements-gmsh.txt"
        if [[ "$(uname -s)" == "Linux" ]]; then
            echo "  .venv/bin/pip install --pre --extra-index-url https://gmsh.info/python-packages-dev-nox -r server/requirements-gmsh.txt"
        fi
        exit 1
    fi
fi

if ! .venv/bin/python -c "import gmsh; print(gmsh.__version__)" >/dev/null 2>&1; then
    echo "ERROR: gmsh Python package is still not importable in .venv."
    echo "/api/mesh/build requires Python gmsh."
    exit 1
fi
echo "  gmsh Python version: $(.venv/bin/python -c "import gmsh; print(gmsh.__version__)")"
echo ""

echo "Building Metal native release helper when available..."
if node scripts/run-backend-python.js server/scripts/build_metal_native_release.py; then
    echo "  Metal native helper check complete."
else
    echo "ERROR: Metal native release helper build failed."
    echo "       Apple Silicon installs require this for the fast Metal BEM solve path."
    echo "       Re-run after fixing the issue above, or run: npm run build:metal-helper"
    exit 1
fi
echo ""

echo "Checking Metal BEM backend..."
METAL_READY=0
if _METAL_STATUS_OUTPUT="$(PYTHONPATH="$ROOT/server" .venv/bin/python - <<'METALCHECK' 2>&1
import sys
from solver.metal_solver import metal_backend_status

status = metal_backend_status()
if status.get("available"):
    print(status.get("reason") or "Metal BEM backend is ready.")
    sys.exit(0)
print(status.get("reason") or "Metal BEM backend is not available on this host.")
sys.exit(1)
METALCHECK
)"; then
    METAL_READY=1
    echo "  Metal BEM is ready."
    [[ -n "$_METAL_STATUS_OUTPUT" ]] && echo "  $_METAL_STATUS_OUTPUT"
else
    echo "  WARNING: Metal BEM is not ready."
    [[ -n "$_METAL_STATUS_OUTPUT" ]] && echo "  $_METAL_STATUS_OUTPUT"
fi
echo ""

SOLVER_BACKEND_SUMMARY="Metal or Bempp solve backend: not ready"
if [[ "$METAL_READY" -eq 1 ]]; then
    echo "Skipping Bempp install because Metal BEM is ready."
    SOLVER_BACKEND_SUMMARY="Metal or Bempp solve backend: Metal BEM ready (Bempp install skipped)"
else
    echo "Installing Bempp cross-platform backend..."
    if .venv/bin/python -m pip install --quiet -r server/requirements-bempp.txt; then
        if _BEMPP_STATUS_OUTPUT="$(PYTHONPATH="$ROOT/server" .venv/bin/python - <<'BEMPPPROBE' 2>&1
import sys

try:
    import hornlab_bempp_bem  # noqa: F401
except Exception as exc:
    print(f"Bempp import failed: {exc}")
    sys.exit(1)

try:
    import pyopencl as cl  # type: ignore
    platforms = cl.get_platforms()
    device_count = 0
    for platform in platforms:
        try:
            device_count += len(platform.get_devices())
        except Exception:
            pass
    if platforms and device_count:
        print("bempp ready with OpenCL acceleration")
    else:
        raise RuntimeError("no OpenCL platforms/devices found")
except Exception:
    print(
        "bempp ready using the numba CPU backend "
        "(works everywhere, slower; speed-up hint: install an OpenCL runtime - "
        "Linux: pocl from your package manager; macOS x86_64: none needed, numba is fine)"
    )
BEMPPPROBE
)"; then
            echo "  $_BEMPP_STATUS_OUTPUT"
            SOLVER_BACKEND_SUMMARY="Metal or Bempp solve backend: Bempp ready"
        else
            echo "ERROR: Bempp installed but is not importable."
            [[ -n "$_BEMPP_STATUS_OUTPUT" ]] && echo "  $_BEMPP_STATUS_OUTPUT"
            echo "  Manual command:"
            echo "    .venv/bin/python -m pip install -r server/requirements-bempp.txt"
            exit 1
        fi
    else
        echo "ERROR: Bempp install failed and no Metal solve backend is ready."
        echo "  Manual command:"
        echo "    .venv/bin/python -m pip install -r server/requirements-bempp.txt"
        exit 1
    fi
fi
echo ""

echo "Recording backend interpreter contract..."
mkdir -p "$ROOT/.waveguide"

_BACKEND_PYTHON="$ROOT/.venv/bin/python"

printf '%s\n' "$_BACKEND_PYTHON" > "$PREFERRED_PYTHON_FILE"
echo "  Preferred backend interpreter: $_BACKEND_PYTHON"
echo "  Marker file: $PREFERRED_PYTHON_FILE"
echo ""

echo "Running backend dependency preflight..."
if node scripts/preflight-backend-runtime.js --strict; then
    echo "  Backend preflight: required checks ready."
else
    echo "ERROR: Backend preflight detected missing/unsupported required checks."
    echo "       Fix the reported items, then re-run:"
    echo "         npm run preflight:backend:strict"
    exit 1
fi
echo ""

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Install / update complete!                                  ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "To start the app:"
echo "  - macOS: double-click launch/mac.command"
echo "  - Linux: run bash launch/linux.sh"
echo "  - Or run npm start"
echo ""
echo "$SOLVER_BACKEND_SUMMARY"
echo ""
