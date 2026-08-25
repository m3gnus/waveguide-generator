"""What this host actually imports, as opposed to what ``pins.json`` declares.

``pins.json`` is a declaration. It says nothing about the environment a solve
actually ran in, and until this module existed nothing compared the two outside
``scripts/bootstrap.py`` -- which only guards launcher-managed virtualenvs at
install time. A hand-managed developer venv never passes through it, so a
machine could report pinned SHAs on every result while running four modules at
different commits, every one of them still reporting version ``0.1.0``. That
happened twice; see ``docs/validation/2026-08/PINNED-VS-INSTALLED.md``.

pip records the resolved commit of a VCS install in the distribution's
``direct_url.json`` (PEP 610). That file is the only evidence of the installed
commit that survives into the environment, because these modules do not encode
their commit in their version string.

Nothing here may raise into the solve path. An environment this code cannot
measure is reported as *unknown*, which counts as drift, and never as an
exception: refusing to produce a result because provenance could not be
measured would be a worse failure than the one being fixed.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import distribution
import json
import re
from typing import Any, Iterable, Mapping


_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def measure_installed_commit(name: str) -> str | None:
    """Return the Git commit pip installed for ``name``, or ``None``.

    ``None`` means *unknown*, and covers every way the answer can be missing:
    the distribution is not installed, it was not installed from a Git URL (an
    editable path checkout, a wheel, a source tree), its ``direct_url.json`` is
    absent or unreadable, or the recorded document is not the shape PEP 610
    describes.
    """

    try:
        raw = distribution(name).read_text("direct_url.json")
    except Exception:  # noqa: BLE001 - unknown is the contract, never a raise
        return None
    if not raw:
        return None
    try:
        document: Any = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(document, Mapping):
        return None
    vcs_info = document.get("vcs_info")
    if not isinstance(vcs_info, Mapping) or vcs_info.get("vcs") != "git":
        return None
    commit = vcs_info.get("commit_id")
    if not isinstance(commit, str):
        return None
    commit = commit.strip().lower()
    return commit if _COMMIT_RE.fullmatch(commit) else None


@lru_cache(maxsize=8)
def _measure(names: tuple[str, ...]) -> tuple[tuple[str, str | None], ...]:
    """Measure once per process; installed distributions cannot change under us."""

    return tuple((name, measure_installed_commit(name)) for name in names)


def installed_dependency_shas(names: Iterable[str]) -> dict[str, str | None]:
    """Measured commit for each named distribution, ``None`` where unknown."""

    return dict(_measure(tuple(sorted(str(name) for name in names))))


def dependency_drift(
    pinned: Mapping[str, str], installed: Mapping[str, str | None]
) -> list[str]:
    """Sorted names whose measured commit differs from the pin or is unknown.

    An empty list is the trustworthy signal: every pinned module was measured
    and every measurement matched.
    """

    return sorted(
        name for name, sha in pinned.items() if installed.get(name) != sha
    )


def measure_installed_stack(
    pinned: Mapping[str, str],
) -> tuple[dict[str, str | None], list[str]]:
    """Return ``(installed_dependency_shas, dependency_drift)`` for ``pinned``.

    The single entry point used by the solve path. It cannot raise: if the
    measurement itself fails, every module degrades to unknown, which the drift
    list then reports honestly rather than passing off as agreement.
    """

    try:
        installed = installed_dependency_shas(pinned)
        return installed, dependency_drift(pinned, installed)
    except Exception:  # noqa: BLE001 - provenance must never fail a solve
        return {name: None for name in pinned}, sorted(pinned)


def clear_measurement_cache() -> None:
    """Drop the per-process measurement so tests are not order-dependent."""

    _measure.cache_clear()


__all__ = [
    "clear_measurement_cache",
    "dependency_drift",
    "installed_dependency_shas",
    "measure_installed_commit",
    "measure_installed_stack",
]
