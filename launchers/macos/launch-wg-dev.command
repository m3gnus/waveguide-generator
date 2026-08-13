#!/bin/bash
# Review/development launcher: build the current local frontend when needed,
# then use the same validated runtime path as the normal macOS app.

set -u

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
CHECKER="$REPO_DIR/scripts/frontend_freshness.py"
LAUNCHER="$REPO_DIR/launchers/macos/launch-wg.command"

fail() {
  printf '\nERROR: %s\n\n' "$1" >&2
  if [[ -t 0 ]]; then
    read -r -p "Press Return to close..." _unused
  fi
  exit 1
}

if [[ ! -f "$CHECKER" ]] || [[ ! -x "$LAUNCHER" ]]; then
  fail "launch-wg-dev.command must remain in launchers/macos in the Waveguide Generator checkout."
fi

CHECK_PYTHON=""
for candidate in python3.13 python3
do
  if command -v "$candidate" >/dev/null 2>&1; then
    CHECK_PYTHON="$(command -v "$candidate")"
    break
  fi
done
if [[ -z "$CHECK_PYTHON" ]]; then
  fail "Python 3 is required to check whether the local frontend build is current."
fi

# Finder and non-interactive shells do not necessarily inherit nvm's PATH.
# Prefer the caller's npm, then use the newest installed nvm runtime by mtime.
NPM=""
if command -v npm >/dev/null 2>&1; then
  NPM="$(command -v npm)"
else
  for candidate in "$HOME"/.nvm/versions/node/*/bin/npm
  do
    if [[ -x "$candidate" ]] && { [[ -z "$NPM" ]] || [[ "$candidate" -nt "$NPM" ]]; }; then
      NPM="$candidate"
    fi
  done
fi

"$CHECK_PYTHON" "$CHECKER" --check --quiet
FRESHNESS_RESULT="$?"
if [[ "$FRESHNESS_RESULT" -eq 1 ]]; then
  if [[ -z "$NPM" ]]; then
    fail "The frontend needs rebuilding, but npm was not found. Install Node.js, then run this launcher again."
  fi
  if [[ ! -x "$REPO_DIR/frontend/node_modules/.bin/vite" ]]; then
    fail "The frontend needs rebuilding. Run 'cd frontend && npm ci' once, then run this launcher again."
  fi

  echo "Frontend sources changed; building the current local interface..."
  NPM_DIR="$(dirname "$NPM")"
  if ! (cd "$REPO_DIR/frontend" && PATH="$NPM_DIR:$PATH" "$NPM" run build); then
    fail "The frontend build failed. The existing frontend/dist was not marked as current."
  fi
  if ! "$CHECK_PYTHON" "$CHECKER" --mark --quiet; then
    fail "The frontend built, but its source stamp could not be recorded."
  fi
elif [[ "$FRESHNESS_RESULT" -ne 0 ]]; then
  fail "The frontend freshness check failed."
else
  echo "Frontend build already matches the current local sources."
fi

exec "$LAUNCHER" "$@"
