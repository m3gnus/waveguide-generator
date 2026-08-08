#!/bin/bash
# Double-click in Finder to install or update Waveguide Generator v2.
#
# The counterpart of launch-wg2.command, and the counterpart of Windows'
# scripts\install-and-update.bat. All it does is find the real installer, keep a
# transcript, and hold the window open long enough to read a failure -- a
# double-clicked script's window closes the instant it exits, which is precisely
# when the message mattered.

set -u

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR" || exit 1

if [[ ! -f "scripts/install.sh" ]]; then
    echo
    echo "ERROR: install-wg2.command must remain in the waveguide-generator-v2 folder."
    echo "       Current folder: $REPO_DIR"
    echo
    read -r -p "Press Return to close..." _unused
    exit 1
fi

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
    echo
    echo "Starting Waveguide Generator v2..."
    exec "$REPO_DIR/launch-wg2.command"
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
