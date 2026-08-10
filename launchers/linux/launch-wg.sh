#!/usr/bin/env bash
# Start the cross-platform status window. Pass --no-gui for terminal-only mode.

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR" || exit 1

fail() {
  printf '\nERROR: %s\n\n' "$1" >&2
  exit 1
}

if [[ ! -f "launch/serve.py" ]] || [[ ! -d "server" ]] || \
   [[ ! -f "launchers/statusapp/__main__.py" ]]; then
  fail "launch-wg.sh must remain in launchers/linux in the Waveguide Generator checkout."
fi

PYTHON=""
if [[ -n "${WG2_PYTHON:-}" ]]; then
  [[ -x "$WG2_PYTHON" ]] || fail "WG2_PYTHON is not executable: $WG2_PYTHON"
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
    [[ -n "$BOOTSTRAP_PYTHON" ]] || \
      fail "CPython 3.13 is required. Install it, then run 'python3.13 scripts/bootstrap.py'."
    printf '%s\n' "Preparing the Waveguide Generator Python environment..."
    "$BOOTSTRAP_PYTHON" scripts/bootstrap.py || \
      fail "The Waveguide Generator Python environment could not be prepared. Review the installation errors above."
  fi
  PYTHON="$REPO_DIR/.venv/bin/python"
fi

if [[ -n "${WG2_PYTHON:-}" ]] && \
   ! "$PYTHON" -c "import fastapi, uvicorn" >/dev/null 2>&1
then
  fail "The selected Python environment cannot import FastAPI and Uvicorn."
fi

exec "$PYTHON" -m launchers.statusapp "$@"
