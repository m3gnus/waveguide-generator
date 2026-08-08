#!/bin/bash
# Double-click in Finder, or run from Terminal, to start Waveguide Generator v2.

set -u

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR" || exit 1

fail() {
  echo
  echo "ERROR: $1"
  echo
  if [[ -t 0 ]]; then
    read -r -p "Press Return to close..." _unused
  fi
  exit 1
}

if [[ ! -f "launch/serve.py" ]] || [[ ! -d "server" ]]; then
  fail "launch-wg2.command must remain in the waveguide-generator-v2 folder."
fi

if [[ ! -f "frontend/dist/index.html" ]]; then
  # Running v2 is not supposed to require Node. Releases ship the built SPA as
  # an attached archive, so point there first and leave the local build as the
  # developer path rather than the only one.
  fail "The built interface is missing.

Run the installer, which downloads it from the release and verifies it against
the published checksum before extracting anything:
  bash scripts/install.sh
or double-click install-wg2.command.

If you are working on the interface itself, build it instead:
  cd frontend && npm ci && npm run build"
fi

# WG2_PYTHON can explicitly select another interpreter. Otherwise the launcher
# creates and validates the repository-local v2 environment.
PYTHON=""
if [[ -n "${WG2_PYTHON:-}" ]]; then
  if [[ ! -x "$WG2_PYTHON" ]]; then
    fail "WG2_PYTHON is not executable: $WG2_PYTHON"
  fi
  PYTHON="$WG2_PYTHON"
else
  if [[ ! -x "$REPO_DIR/.venv/bin/python" ]] || \
     ! "$REPO_DIR/.venv/bin/python" scripts/bootstrap.py --check >/dev/null 2>&1
  then
    BOOTSTRAP_PYTHON=""
    for candidate in python3.13 python3
    do
      if command -v "$candidate" >/dev/null 2>&1 && \
         "$candidate" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 13))' >/dev/null 2>&1
      then
        BOOTSTRAP_PYTHON="$(command -v "$candidate")"
        break
      fi
    done
    if [[ -z "$BOOTSTRAP_PYTHON" ]]; then
      fail "CPython 3.13 is required. Install it, then run 'python3.13 scripts/bootstrap.py'."
    fi
    echo "Preparing the Waveguide Generator v2 Python environment..."
    if ! "$BOOTSTRAP_PYTHON" scripts/bootstrap.py; then
      fail "The v2 Python environment could not be prepared. Review the installation errors above."
    fi
  fi
  PYTHON="$REPO_DIR/.venv/bin/python"
fi

if [[ -z "$PYTHON" ]]; then
  fail "No Python environment was selected."
fi

# The repository environment was already validated by bootstrap.py, whose
# probe ends by importing these packages. Only an explicit override bypasses
# that validation and still needs the standalone import check.
if [[ -n "${WG2_PYTHON:-}" ]] && \
   ! "$PYTHON" -c "import fastapi, uvicorn" >/dev/null 2>&1
then
  fail "The selected Python environment cannot import FastAPI and Uvicorn."
fi

echo "Starting Waveguide Generator v2..."
echo "Close this window or press Control-C to stop it."
echo

exec "$PYTHON" launch/serve.py "$@"
