#!/usr/bin/env bash
# Public Linux installer entry. The shared installer machinery stays in scripts/.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec bash "$ROOT/scripts/install.sh" "$@"
