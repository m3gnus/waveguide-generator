#!/usr/bin/env bash
# Waveguide Generator v2 -- uninstaller for macOS and Linux.
#
# Public entries live in installers/macos and installers/linux. This shared
# implementation is kept here so both platforms enforce the same contract.
#
# It never deletes the checkout, and it never deletes anything belonging to v1.
# v2 was built to install beside v1 with its own data directory; an uninstaller
# that reached outside its own would make that promise a lie.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REMOVE_DATA=0
ASSUME_YES=0

usage() {
    cat <<'USAGE'
Waveguide Generator v2 uninstaller.

  --data   also remove the application data directory: saved designs, job
           history, meshes and logs. This cannot be undone.
  --yes    do not ask for confirmation.
  --help   this message

Without --data only the repository-local build products are removed:
.venv and frontend/dist. The checkout itself is left alone -- delete the
folder yourself when you no longer want it.
USAGE
}

say() { printf '%s\n' "$*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --data) REMOVE_DATA=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        --help|-h) usage; exit 0 ;;
        *) printf '\nERROR: Unknown option: %s. Run with --help.\n' "$1" >&2; exit 1 ;;
    esac
    shift
done

# Ask v2 itself where its data lives rather than restating the rule here. The
# answer depends on the platform and on WG2_DATA_DIR, and a second copy of that
# logic would drift from server/platform/paths.py the first time either moved.
resolve_data_dir() {
    local interpreter candidate
    for interpreter in "$ROOT/.venv/bin/python" python3.13 python3; do
        if [[ -x "$interpreter" ]] || command -v "$interpreter" >/dev/null 2>&1; then
            if IFS= read -r candidate < <("$interpreter" -c \
                'import sys; sys.path.insert(0, sys.argv[1]); from server.platform.paths import resolve_data_dir; print(resolve_data_dir())' \
                "$ROOT" 2>/dev/null)
            then
                "$interpreter" "$ROOT/scripts/validate_uninstall_target.py" \
                    --repo-root "$ROOT" "$candidate"
                return $?
            fi
        fi
    done
    return 1
}

TARGETS=()
[[ -d "$ROOT/.venv" ]] && TARGETS+=("$ROOT/.venv")
[[ -d "$ROOT/frontend/dist" ]] && TARGETS+=("$ROOT/frontend/dist")
# Leftovers from an interrupted SPA install. Harmless, but they are ours.
[[ -d "$ROOT/frontend/.wg2-spa-staging" ]] && TARGETS+=("$ROOT/frontend/.wg2-spa-staging")
[[ -d "$ROOT/frontend/.wg2-dist-previous" ]] && TARGETS+=("$ROOT/frontend/.wg2-dist-previous")

DATA_DIR=""
if [[ "$REMOVE_DATA" -eq 1 ]]; then
    if DATA_DIR="$(resolve_data_dir)" && [[ -n "$DATA_DIR" ]]; then
        [[ -d "$DATA_DIR" ]] && TARGETS+=("$DATA_DIR")
    else
        resolve_result=$?
        if [[ "$resolve_result" -eq 2 ]]; then
            exit 1
        fi
        say "WARNING: could not ask v2 where its data directory is -- no usable Python."
        say "         Remove it by hand. Unless WG2_DATA_DIR overrides it, it is:"
        say "           macOS:  ~/Library/Application Support/WaveguideGenerator2"
        say "           Linux:  \${XDG_DATA_HOME:-~/.local/share}/WaveguideGenerator2"
        say ""
    fi
fi

if [[ "${#TARGETS[@]}" -eq 0 ]]; then
    say "Nothing to remove: Waveguide Generator v2 is not installed in $ROOT."
    exit 0
fi

say "This will permanently delete:"
for target in "${TARGETS[@]}"; do
    say "  $target"
done
say ""
say "The checkout itself is kept. Delete $ROOT yourself when you are done with it."
say ""

if [[ "$ASSUME_YES" -ne 1 ]]; then
    if [[ ! -t 0 ]]; then
        printf 'ERROR: Refusing to delete without confirmation. Re-run with --yes.\n' >&2
        exit 1
    fi
    read -r -p "Type 'remove' to continue: " answer
    if [[ "$answer" != "remove" ]]; then
        say "Nothing was removed."
        exit 1
    fi
fi

for target in "${TARGETS[@]}"; do
    rm -rf "$target"
    say "  Removed $target"
done

say ""
if [[ "$(uname -s)" == "Darwin" ]]; then
    say "Done. Reinstall any time with: installers/macos/install-wg2.command"
else
    say "Done. Reinstall any time with: bash installers/linux/install.sh"
fi
