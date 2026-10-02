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
import sys

os.environ.setdefault("WG2_SKIP_BEAT_CPU_PROVISION", "1")


# The installer scripts' tests: full matrices on demand, a spread-out sample by
# default. Their full parametrisations take about 32 minutes serially, more
# than the whole CI harness budget, so by default each test function keeps at
# most INSTALLER_DEFAULT_CASES of its cases, spaced evenly through its
# parametrisation (so the first and the last, which for the platform-
# parametrised tests means both Linux and macOS). Every test function still
# runs. WG_STRESS=1 (the on-demand "Installer stress" workflow, and branch
# evidence) runs every case.
INSTALLER_TEST_FILES = frozenset({
    "test_installer_review_followups.py",
    "test_linux_bundle_install_update.py",
    "test_dmg_install_update.py",
})
INSTALLER_DEFAULT_CASES = 2
#: Hosted macOS runners run these about 3x slower than a developer Mac
#: (measured 2026-10-02: the 2-case sample took ~17.6 min there and the
#: harness job hit its 25-minute limit). There, one case per function,
#: preferring the macOS variant; the Linux script runs natively on the
#: ubuntu harness, and every case runs in installer-stress.yml.
INSTALLER_HOSTED_MACOS_CASES = 1


def _installer_cases_per_function() -> int:
    if os.environ.get("CI") == "true" and sys.platform == "darwin":
        return INSTALLER_HOSTED_MACOS_CASES
    return INSTALLER_DEFAULT_CASES


def _spread(count: int, keep: int) -> set[int]:
    if count <= keep:
        return set(range(count))
    if keep == 1:
        return {count - 1}
    return {round(index * (count - 1) / (keep - 1)) for index in range(keep)}


def _always_skipped(item) -> bool:
    """A case a skipif mark already decided to skip on this host (for example
    a macOS-only parametrisation on Linux): never worth one of the slots."""

    for mark in item.iter_markers("skipif"):
        if mark.args and mark.args[0] is True:
            return True
    return False


def pytest_collection_modifyitems(config, items):
    if os.environ.get("WG_STRESS") == "1" or os.environ.get("WG_INSTALLER_ALL_CASES") == "1":
        # WG_INSTALLER_ALL_CASES: every case at default counts (the mutation
        # runner selects specific cases and must not lose them to sampling).
        return
    keep = _installer_cases_per_function()
    groups: dict[tuple[str, str], list] = {}
    for item in items:
        if item.path.name in INSTALLER_TEST_FILES and hasattr(item, "callspec"):
            groups.setdefault((item.path.name, item.originalname), []).append(item)
    dropped = []
    for group in groups.values():
        runnable = [item for item in group if not _always_skipped(item)] or group
        if keep == 1:
            mac = [item for item in runnable if "macos" in item.callspec.id.split("-")]
            runnable = mac or runnable
        chosen = {id(runnable[index]) for index in _spread(len(runnable), keep)}
        dropped.extend(item for item in group if id(item) not in chosen)
    if dropped:
        drop = set(map(id, dropped))
        items[:] = [item for item in items if id(item) not in drop]
        config.hook.pytest_deselected(items=dropped)
