#!/bin/bash
# Double-click in Finder, or run from Terminal, to open Waveguide Generator.
#
# This is the only macOS launcher. It prepares the Python environment, brings
# the frontend build up to date when this is a source checkout, and opens the
# status window. Launching while an instance is already running attaches to that
# instance rather than failing: the lock owner's URL is opened and the status
# window reports the running server.
#
# Pass --no-gui for the terminal-only launcher.

set -u

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_DIR" || exit 1

fail() {
  echo
  echo "ERROR: $1"
  echo
  if [[ "${WG2_FINDER_APP:-0}" == "1" ]] && command -v osascript >/dev/null 2>&1; then
    MESSAGE="$1" osascript -e \
      'display alert "Waveguide Generator could not start" message (system attribute "MESSAGE") as critical'
  fi
  if [[ -t 0 ]]; then
    read -r -p "Press Return to close..." _unused
  fi
  exit 1
}

if [[ ! -f "launch/serve.py" ]] || [[ ! -d "server" ]] || \
   [[ ! -f "launchers/statusapp/__main__.py" ]]; then
  fail "launch-wg.command must remain in launchers/macos in the Waveguide Generator checkout."
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
    echo "Preparing the Waveguide Generator Python environment..."
    if ! "$BOOTSTRAP_PYTHON" scripts/bootstrap.py; then
      fail "The Waveguide Generator Python environment could not be prepared. Review the installation errors above."
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

# --- Frontend build ---------------------------------------------------------
#
# Folded in from the retired launch-wg-dev.command. A source checkout whose
# sources are newer than frontend/dist would otherwise serve a stale interface,
# and the old launcher's answer was to print a warning telling the user to quit
# and run a second launcher — which is the reason there were two.
#
# A rebuild is best-effort on purpose. An installed copy has no frontend
# sources, no node_modules and no npm, and must still start; only a checkout
# that can build gets rebuilt. Set WG2_SKIP_FRONTEND_BUILD=1 to serve the
# existing dist unconditionally.

build_frontend_if_stale() {
  local checker="$REPO_DIR/scripts/frontend_freshness.py"
  [[ -f "$checker" ]] || return 0
  if [[ "${WG2_SKIP_FRONTEND_BUILD:-0}" == "1" ]]; then
    echo "Skipping the frontend freshness check (WG2_SKIP_FRONTEND_BUILD=1)."
    return 0
  fi

  "$PYTHON" "$checker" --check --quiet
  local freshness="$?"
  if [[ "$freshness" -eq 0 ]]; then
    return 0
  fi
  if [[ "$freshness" -ne 1 ]]; then
    echo "WARNING: The frontend freshness check could not run; serving the existing build."
    return 0
  fi

  # Finder and non-interactive shells do not inherit nvm's PATH. Prefer the
  # caller's npm, else the newest installed nvm runtime by mtime.
  local npm=""
  if command -v npm >/dev/null 2>&1; then
    npm="$(command -v npm)"
  else
    local candidate
    for candidate in "$HOME"/.nvm/versions/node/*/bin/npm
    do
      if [[ -x "$candidate" ]] && { [[ -z "$npm" ]] || [[ "$candidate" -nt "$npm" ]]; }; then
        npm="$candidate"
      fi
    done
  fi

  if [[ -z "$npm" ]] || [[ ! -x "$REPO_DIR/frontend/node_modules/.bin/vite" ]]; then
    echo "WARNING: Local frontend sources are newer than frontend/dist, and this"
    echo "         checkout cannot rebuild it (needs Node.js and 'cd frontend && npm ci')."
    echo "         Starting with the existing build."
    echo
    return 0
  fi

  echo "Frontend sources changed; building the current local interface..."
  if ! (cd "$REPO_DIR/frontend" && PATH="$(dirname "$npm"):$PATH" "$npm" run build); then
    # A failed build must not block startup: the previous dist is still servable.
    echo
    echo "WARNING: The frontend build failed. Starting with the previous build."
    echo
    return 0
  fi
  if ! "$PYTHON" "$checker" --mark --quiet; then
    echo "WARNING: The frontend built, but its source stamp could not be recorded."
  fi
}

build_frontend_if_stale

# A checkout with no built interface cannot serve one, and the server's own
# failure for this is a starlette traceback ("Directory '.../frontend/dist' does
# not exist") several frames deep — which is precisely the "it can't start" a
# user reports. Say what is missing and how to fix it instead.
if [[ ! -f "$REPO_DIR/frontend/dist/index.html" ]]; then
  fail "The interface has not been built yet: frontend/dist/index.html is missing.
Run 'cd frontend && npm ci && npm run build' in the checkout, then open this launcher again."
fi

echo "Starting the Waveguide Generator status window..."
echo "Quit the status window to stop the server."
echo

exec "$PYTHON" -m launchers.statusapp "$@"
