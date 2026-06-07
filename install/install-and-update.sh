#!/bin/bash
# Clearer entry point for users. The implementation lives in install.sh.

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$DIR/install.sh" "$@"
