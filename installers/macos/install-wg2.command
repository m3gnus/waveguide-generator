#!/bin/bash
# Double-click in Finder to install or update Waveguide Generator v2.
#
# The counterpart of launchers/macos/launch-wg2.command, and the counterpart of
# Windows' installers\windows\install-and-update.bat. All it does is find the
# real installer, keep a
# transcript, and hold the window open long enough to read a failure -- a
# double-clicked script's window closes the instant it exits, which is precisely
# when the message mattered.

set -u

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_DIR" || exit 1

if [[ ! -f "scripts/install.sh" ]]; then
    echo
    echo "ERROR: install-wg2.command must remain in installers/macos in the Waveguide Generator v2 checkout."
    echo "       Current folder: $REPO_DIR"
    echo
    read -r -p "Press Return to close..." _unused
    exit 1
fi

# --no-launch is handled here as well as passed through, because the launcher is
# started below rather than by install.sh: the server must not run inside the
# `tee` pipeline, where its output would be appended to the log for as long as
# it stays up.
LAUNCH_AFTER=1
for argument in "$@"; do
    if [[ "$argument" == "--no-launch" ]]; then
        LAUNCH_AFTER=0
    fi
done

LOG_DIR="$HOME/Library/Application Support/WaveguideGenerator2/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || LOG_DIR="${TMPDIR:-/tmp}"
LOG="$LOG_DIR/install.log"

echo "Logging to: $LOG"
echo

# Keep the exit status of the installer, not of tee. `set -o pipefail` alone
# would report the pipeline's worst status; PIPESTATUS names the one we want.
set -o pipefail
bash "$REPO_DIR/scripts/install.sh" --no-launch "$@" 2>&1 | tee -a "$LOG"
RESULT="${PIPESTATUS[0]}"

echo
if [[ "$RESULT" -eq 0 ]]; then
    echo "Install log: $LOG"
    if [[ "$LAUNCH_AFTER" -eq 0 ]]; then
        exit 0
    fi
    echo
    echo "Starting Waveguide Generator v2..."
    exec "$REPO_DIR/launchers/macos/launch-wg2.command"
fi

echo "==============================================================="
echo "Install failed with exit code $RESULT."
echo "Full log: $LOG"
echo "==============================================================="
echo "Include that log when reporting the problem. It contains no"
echo "credentials; it does contain the project path."
echo
if [[ -t 0 ]]; then
    read -r -p "Press Return to close..." _unused
fi
exit "$RESULT"
