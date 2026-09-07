"""Make this suite self-contained about BEAT CPU provisioning.

Until 2026-09-07 ``scripts/tests`` and ``server/tests`` always ran in one
pytest process, and ``server/tests/conftest.py`` sets
``WG2_SKIP_BEAT_CPU_PROVISION`` on import. That is a process-wide environment
mutation, so this suite silently inherited it and nobody had to notice that
its own tests depend on it.

Splitting the CI job so each suite runs alone removed the donor, and
``test_two_bootstraps_never_run_pip_mutations_concurrently`` began failing on
ubuntu and windows with ``PermissionError`` on the ``.venv/bin/python`` stub
it touches into place: with provisioning switched on, ``bootstrap`` really
reaches ``_provision_beat_cpu_runtime`` -> ``_beat_provision_facts`` ->
``_capture``, and tries to execute a zero-byte file. macOS never saw it,
because ``_provision_beat_cpu_runtime`` excludes macOS by design.

So the opt-out is declared here too, with the same reasoning as the server
suite's copy: a test run is not an install, and the suite opts out through the
same switch an operator would use. Tests that are *about* provisioning must
set their own environment explicitly rather than lean on this default -- which
is exactly the mistake this file exists to stop repeating.

``setdefault``, not assignment: an operator debugging the provisioning path
with the variable already set keeps their value.
"""

import os

os.environ.setdefault("WG2_SKIP_BEAT_CPU_PROVISION", "1")
