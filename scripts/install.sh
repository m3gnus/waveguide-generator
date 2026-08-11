#!/usr/bin/env bash
# Waveguide Generator -- installer / updater for macOS and Linux.
#
# Public entries:
#   installers/macos/install-wg.command
#   bash installers/linux/install.sh
#
# Smaller than v1's installer by design: v2 needs no Node runtime and no
# `npm ci`, because the interface ships prebuilt on the release. What is left is
# get the code, get that version's interface, build the Python environment,
# prove a solve could run, and start.
#
# Every path is quoted. Users install under parent folders with spaces in them
# ("Waveguide Generator", "Hornlab - Workspace") and v1 has live bugs from
# exactly that; the repository directory itself having no spaces proves nothing.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Kept so the post-pull restart repeats exactly what was asked for. Inside a
# function "$@" is the function's own arguments, not the script's, and the
# parsing loop below consumes them anyway.
ORIGINAL_ARGS=("$@")

AFTER_PULL=0
LAUNCH=1
FORCE=0
SKIP_SPA=0
SPA_ARCHIVE=""
SPA_VERSION=""
SPA_BASE_URL=""
TAG=""
GIT_MIN_MAJOR=2
GIT_MIN_MINOR=20
PYTHON_SERIES="3.13"

BOOTSTRAP_PYTHON=""
BOOTSTRAP_PYTHON_VERSION=""
DETECTED_PYTHON=""
DETECTED_PYTHON_VERSION=""
SPA_SUMMARY=""
METAL_SUMMARY=""

usage() {
    cat <<'USAGE'
Waveguide Generator installer.

  --tag vX.Y.Z        check the repository out at a release tag first
  --version X.Y.Z     install the SPA for this version (default: shared/version.json)
  --spa-archive PATH  install a downloaded SPA archive instead of fetching one
  --spa-base-url URL  directory holding the SPA archive and its .sha256
  --skip-spa          leave frontend/dist alone (for interface development)
  --force             rebuild the environment and reinstall the SPA
  --no-launch         finish without starting the application
  --help              this message

Uninstall with installers/macos/uninstall.sh or installers/linux/uninstall.sh.
USAGE
}

say() { printf '%s\n' "$*"; }
step() { printf '\n%s\n' "$*"; }

fail() {
    printf '\nERROR: %s\n' "$1" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --after-pull) AFTER_PULL=1 ;;
        --no-launch) LAUNCH=0 ;;
        --force) FORCE=1 ;;
        --skip-spa) SKIP_SPA=1 ;;
        --spa-archive) SPA_ARCHIVE="${2:-}"; shift ;;
        --spa-base-url) SPA_BASE_URL="${2:-}"; shift ;;
        --version) SPA_VERSION="${2:-}"; shift ;;
        --tag) TAG="${2:-}"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "Unknown option: $1. Run with --help." ;;
    esac
    shift
done

say "==============================================================="
say " Waveguide Generator -- install / update"
say "==============================================================="

# ── The folder has to be the project ──────────────────────────────────────────
step "Verifying the project folder..."
MISSING=0
for required in \
    launch/serve.py \
    launchers/statusapp/__main__.py \
    launchers/macos/launch-wg.command \
    launchers/linux/launch-wg.sh \
    scripts/bootstrap.py \
    scripts/fetch_spa.py \
    shared/version.json \
    server/requirements-lock.txt \
    server/requirements-pins.txt
do
    if [[ ! -f "$ROOT/$required" ]]; then
        say "  - Missing: $required"
        MISSING=1
    fi
done
if [[ "$MISSING" -ne 0 ]]; then
    fail "This does not look like a complete Waveguide Generator checkout.
       Current folder: $ROOT

       Clone it rather than downloading a ZIP -- the installer updates itself
       with Git, and the pinned HornLab modules are installed from Git too:
         git clone https://github.com/m3gnus/waveguide-generator.git"
fi
say "  Looks good: $ROOT"

# ── Prerequisites ─────────────────────────────────────────────────────────────
step "Checking prerequisites..."

if ! command -v git >/dev/null 2>&1; then
    fail "Git is required, and not just to update this checkout: the four pinned
       HornLab modules are installed straight from Git.
       macOS:  xcode-select --install
       Debian: sudo apt install git
       Fedora: sudo dnf install git
       Or download it from https://git-scm.com/"
fi
GIT_VERSION="$(git --version 2>/dev/null | awk '{print $3}')"
GIT_MAJOR="${GIT_VERSION%%.*}"
GIT_REST="${GIT_VERSION#*.}"
GIT_MINOR="${GIT_REST%%.*}"
if [[ "$GIT_MAJOR" =~ ^[0-9]+$ ]] && [[ "$GIT_MINOR" =~ ^[0-9]+$ ]]; then
    if (( GIT_MAJOR < GIT_MIN_MAJOR )) ||
       { (( GIT_MAJOR == GIT_MIN_MAJOR )) && (( GIT_MINOR < GIT_MIN_MINOR )); }; then
        fail "Git ${GIT_MIN_MAJOR}.${GIT_MIN_MINOR} or newer is required; found $GIT_VERSION.
       Older versions cannot fetch the pinned module commits reliably.
       Update Git and run this script again."
    fi
fi
say "  Git: $GIT_VERSION"

# CPython 3.13 exactly. scripts/bootstrap.py refuses any other series, so
# accepting one here would only move the failure later and make it stranger.
for candidate in "python${PYTHON_SERIES}" python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        found_version="$("$candidate" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)"
        if [[ -z "$DETECTED_PYTHON" ]] && [[ -n "$found_version" ]]; then
            DETECTED_PYTHON="$(command -v "$candidate")"
            DETECTED_PYTHON_VERSION="$found_version"
        fi
        if "$candidate" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 13))' >/dev/null 2>&1; then
            BOOTSTRAP_PYTHON="$(command -v "$candidate")"
            BOOTSTRAP_PYTHON_VERSION="$found_version"
            break
        fi
    fi
done
if [[ -z "$BOOTSTRAP_PYTHON" ]]; then
    detail="No Python interpreter was found on PATH."
    if [[ -n "$DETECTED_PYTHON" ]]; then
        detail="Found $DETECTED_PYTHON (Python $DETECTED_PYTHON_VERSION), which is the wrong series."
    fi
    fail "CPython $PYTHON_SERIES is required. $detail
       Waveguide Generator locks its dependency set against one interpreter series on purpose,
       so a near miss is rejected rather than half-installed.
       macOS:  brew install python@$PYTHON_SERIES
       Debian: sudo apt install python$PYTHON_SERIES python$PYTHON_SERIES-venv
       Or download it from https://www.python.org/downloads/"
fi
say "  Python: $BOOTSTRAP_PYTHON ($BOOTSTRAP_PYTHON_VERSION)"

# Hold the same data-directory lock as the server across every mutable update
# step. The guarded child suppresses its own final launch; the wrapper releases
# the lock first and then starts the application.
if [[ "${WG_UPDATE_LOCK_HELD:-0}" != "1" ]]; then
    guard_args=()
    if [[ "$LAUNCH" -eq 1 ]]; then
        if [[ "$(uname -s)" == "Darwin" ]]; then
            guard_args+=(--launch "$ROOT/launchers/macos/launch-wg.command")
        else
            guard_args+=(--launch "$ROOT/launchers/linux/launch-wg.sh")
        fi
    fi
    exec "$BOOTSTRAP_PYTHON" "$ROOT/scripts/run_update_guard.py" \
        "${guard_args[@]+"${guard_args[@]}"}" -- bash "$ROOT/scripts/install.sh" \
        "${ORIGINAL_ARGS[@]+"${ORIGINAL_ARGS[@]}"}"
fi

# macOS: the Metal solve path is a Swift package the pinned module builds on
# first use, which needs the Command Line Tools. Missing tools are not fatal --
# bempp is the cross-platform fallback and does run -- but the machine then
# quietly loses its fastest solver, so say so rather than let it be discovered
# during a slow solve.
METAL_SUMMARY=""
if [[ "$(uname -s)" == "Darwin" ]]; then
    if xcode-select -p >/dev/null 2>&1; then
        say "  Xcode Command Line Tools: $(xcode-select -p)"
    elif [[ "$(uname -m)" == "arm64" ]]; then
        say "  WARNING: Xcode Command Line Tools are not installed."
        say "           The Apple Silicon Metal solver is a Swift package that cannot be"
        say "           built without them, so solves will fall back to the slower bempp"
        say "           backend. Install them with:  xcode-select --install"
        METAL_SUMMARY="Metal unavailable: Xcode Command Line Tools are missing (xcode-select --install)."
    fi
fi

# ── Code update ───────────────────────────────────────────────────────────────
# Kept inside a function on purpose. Bash parses a function definition as one
# unit, so the whole body is in memory before `git pull` can rewrite this file
# underneath it; the `exec` then restarts cleanly from the updated copy.
update_from_git() {
    if [[ "$AFTER_PULL" -eq 1 ]]; then
        say "  Code already updated; continuing with the updated installer."
        return 0
    fi
    if [[ ! -d "$ROOT/.git" ]]; then
        say "  Skipped: this folder is not a Git clone, so there is nothing to update."
        return 0
    fi

    if [[ -n "$TAG" ]]; then
        if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
            fail "Cannot check out $TAG: this checkout has uncommitted changes.
       Commit or stash them first:  git status
       Or install without switching tags by omitting --tag."
        fi
        git fetch --tags --quiet || fail "Could not fetch tags from the remote."
        if ! git rev-parse --verify --quiet "refs/tags/$TAG" >/dev/null; then
            fail "No such tag: $TAG. List what exists with:  git tag --list 'v*'"
        fi
        git checkout --quiet "$TAG"
        say "  Checked out $TAG (detached HEAD; fast-forward updates are off until you"
        say "  switch back to a branch)."
        say "  Restarting with the installer from $TAG."
        exec bash "$ROOT/scripts/install.sh" --after-pull \
            "${ORIGINAL_ARGS[@]+"${ORIGINAL_ARGS[@]}"}"
    fi

    if ! git symbolic-ref --quiet HEAD >/dev/null; then
        say "  Skipped: HEAD is detached, which is what --tag leaves behind."
        say "  Switch to a branch to resume updates:  git checkout main"
        return 0
    fi
    if ! git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
        say "  Skipped: branch $(git rev-parse --abbrev-ref HEAD) has no upstream configured,"
        say "  so there is nothing to fast-forward from. Everything below still runs."
        return 0
    fi
    # Report a dirty tree directly. Letting `git pull --ff-only` discover it
    # produces a message about merging that sends people to the wrong fix.
    if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
        say "  Skipped: this checkout has uncommitted changes, and only fast-forward"
        say "  updates are safe here. Commit or stash them, then run this again:"
        say "    git status"
        say "  Everything below still runs against the code you have."
        return 0
    fi

    local before after
    before="$(git rev-parse HEAD)"
    if ! git pull --ff-only; then
        fail "Code update failed. This installer only fast-forwards.
       The branch has diverged from its upstream and cannot fast-forward.
       Reconcile it by hand, then run this again. Diagnose with:
         git status
         git log --oneline -5"
    fi
    after="$(git rev-parse HEAD)"
    if [[ "$before" != "$after" ]]; then
        say "  Updated ${before:0:7} -> ${after:0:7}. Restarting with the updated installer."
        exec bash "$ROOT/scripts/install.sh" --after-pull \
            "${ORIGINAL_ARGS[@]+"${ORIGINAL_ARGS[@]}"}"
    fi
    say "  Already up to date at ${after:0:7}."
}

step "Checking for code updates..."
update_from_git

# ── The prebuilt interface ────────────────────────────────────────────────────
# Before the environment, not after: fetch_spa.py is standard library only so it
# can run on the interpreter found above, and a missing or corrupted release
# asset then fails in seconds rather than after a multi-minute pip install.
step "Installing the prebuilt interface..."
if [[ "$SKIP_SPA" -eq 1 ]]; then
    SPA_SUMMARY="Interface: left untouched (--skip-spa)."
    say "  Skipped by request. frontend/dist is whatever you built there."
else
    spa_args=()
    [[ -n "$SPA_VERSION" ]] && spa_args+=(--version "$SPA_VERSION")
    [[ -n "$SPA_ARCHIVE" ]] && spa_args+=(--archive "$SPA_ARCHIVE")
    [[ -n "$SPA_BASE_URL" ]] && spa_args+=(--base-url "$SPA_BASE_URL")
    [[ "$FORCE" -eq 1 ]] && spa_args+=(--force)
    if "$BOOTSTRAP_PYTHON" "$ROOT/scripts/fetch_spa.py" "${spa_args[@]+"${spa_args[@]}"}"; then
        SPA_SUMMARY="Interface: installed."
    else
        if [[ "$FORCE" -eq 1 ]] || [[ -n "$SPA_ARCHIVE" ]] || [[ -n "$SPA_BASE_URL" ]]; then
            fail "The requested SPA replacement could not be installed.
       The existing interface was left intact, but an explicit --force,
       --spa-archive, or --spa-base-url request is never treated as successful
       when its download or checksum verification fails. See the reason above."
        fi
        # A previously checksum-verified copy of this exact version is safe to
        # keep when a release host is temporarily unreachable. Unstamped local
        # builds and stale releases are accepted only through explicit --skip-spa.
        spa_check_args=(--check)
        [[ -n "$SPA_VERSION" ]] && spa_check_args+=(--version "$SPA_VERSION")
        if ! "$BOOTSTRAP_PYTHON" "$ROOT/scripts/fetch_spa.py" \
            "${spa_check_args[@]}"; then
            fail "The prebuilt interface could not be installed, and the existing
       frontend/dist is not a checksum-verified copy of the requested version.
       Waveguide Generator will not start with a stale or unverified interface. See the reasons above.

       If you are working on the interface itself, build it locally:
         cd frontend && npm ci && npm run build
       then run this script again with --skip-spa."
        fi
        say ""
        say "  WARNING: the release interface could not be installed (see above),"
        say "           but the installed interface is a checksum-verified copy of"
        say "           this version, so it is being kept."
        SPA_SUMMARY="Interface: kept the matching checksum-verified frontend/dist."
    fi
fi

# ── The Python environment ────────────────────────────────────────────────────
# Idempotent by construction: bootstrap.py fingerprints the dependency manifests
# and does not contact the package index when an environment already matches.
step "Preparing the Python environment..."
bootstrap_args=()
[[ "$FORCE" -eq 1 ]] && bootstrap_args+=(--force)
if ! "$BOOTSTRAP_PYTHON" "$ROOT/scripts/bootstrap.py" "${bootstrap_args[@]+"${bootstrap_args[@]}"}"; then
    fail "The Python environment could not be prepared; review the errors above.
       Common causes: no network access to PyPI or GitHub, or a proxy that
       blocks Git over HTTPS (the four HornLab modules install from Git).
       Retry a clean build with:
         \"$BOOTSTRAP_PYTHON\" scripts/bootstrap.py --force"
fi

VENV_PYTHON="$ROOT/.venv/bin/python"
[[ -x "$VENV_PYTHON" ]] || fail "The environment was created but $VENV_PYTHON is missing."

# ── Can a solve actually run? ─────────────────────────────────────────────────
step "Checking that a solve can run..."
if ! "$VENV_PYTHON" "$ROOT/scripts/check_backends.py"; then
    fail "No solve backend is usable, so simulations would all fail.
       Fix the reason reported above, then run this script again."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
say ""
say "==============================================================="
say " Install complete."
say "==============================================================="
say "  $SPA_SUMMARY"
[[ -n "$METAL_SUMMARY" ]] && say "  $METAL_SUMMARY"
say ""
if [[ "$(uname -s)" == "Darwin" ]]; then
    LAUNCHER="$ROOT/launchers/macos/launch-wg.command"
    say "Start it any time with launchers/macos/Waveguide Generator.app, or:"
    say "  ./launchers/macos/launch-wg.command"
    say "Remove it again with installers/macos/uninstall.sh."
else
    LAUNCHER="$ROOT/launchers/linux/launch-wg.sh"
    say "Start it any time with:"
    say "  ./launchers/linux/launch-wg.sh"
    say "Remove it again with installers/linux/uninstall.sh."
fi
say "It picks the first free port from 3100 to 3109 and shows the local URL."
say ""

if [[ "$LAUNCH" -eq 0 ]]; then
    say "Not starting it (--no-launch)."
    exit 0
fi

if [[ "${WG_UPDATE_LOCK_HELD:-0}" == "1" ]]; then
    say "Update complete; releasing the update lock before launch."
    exit 0
fi

say "Starting Waveguide Generator..."
exec "$LAUNCHER"
