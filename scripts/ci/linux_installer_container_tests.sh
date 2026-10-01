#!/bin/bash
# Run tests on real GNU userland (python:3.13-slim) as an ordinary user, as on
# hosted runners: the installers refuse root.
# Usage: linux_installer_container_tests.sh <worktree> <pytest args...>
set -e
wt=$1; shift
docker run --rm -e WG_STRESS -v "$wt":/w:ro python:3.13-slim bash -c '
  pip -q install --disable-pip-version-check pytest pytest-xdist pyyaml >/dev/null 2>&1 &&
  useradd -m runner &&
  mkdir /home/runner/w && tar -C /w --exclude=./frontend/node_modules --exclude=./frontend/dist -cf - . | tar -C /home/runner/w -xf - && chown -R runner:runner /home/runner/w &&
  su runner -c "cd /home/runner/w && WG_STRESS=$WG_STRESS HOME=/home/runner python -m pytest $(printf "%q " "$@")"' _ "$@"
