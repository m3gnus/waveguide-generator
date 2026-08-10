#!/usr/bin/env bash
# Public Linux uninstaller entry. The shared uninstaller machinery stays in scripts/.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec bash "$ROOT/scripts/uninstall.sh" "$@"
