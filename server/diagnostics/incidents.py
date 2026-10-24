"""Incident records in problem reports, and the versions each record carries.

The records themselves are owned by ``server.platform.incidents``; this module
only says what a record names about the build and how a report includes them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import lru_cache
from importlib import metadata
from pathlib import Path
import platform
import sys
from typing import Any

from server.integration.installed import measure_installed_stack
from server.integration.provenance import pinned_dependency_shas
from server.platform.incidents import IncidentStore, summary


#: Distributions whose versions explain pipeline behaviour.
VERSIONED_DISTRIBUTIONS = ("gmsh", "numpy", "scipy", "bempp-cl", "cadquery-ocp", "fastapi")

#: Incidents a summary lists, and a report carries in full.
SUMMARY_INCIDENTS = 10
BUNDLE_INCIDENTS = 50
#: Records whose job-log tails a report keeps: the newest, and any naming the
#: run selected in the dialog. The rest say how many tails they left out.
TAILED_INCIDENTS = 10


def _distribution_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in VERSIONED_DISTRIBUTIONS:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def incident_versions(*, version: str, build: Mapping[str, Any]) -> Callable[[], dict[str, Any]]:
    """What every incident names about the build; measured once per process."""

    frozen_build = {key: build.get(key) for key in ("label", "commit", "commit_short", "source", "dirty")}

    @lru_cache(maxsize=1)
    def measured() -> dict[str, Any]:
        installed, drift = measure_installed_stack(pinned_dependency_shas())
        return {
            "wg": version,
            "build": frozen_build,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "modules": installed,
            "moduleDrift": drift,
            "distributions": _distribution_versions(),
        }

    return lambda: dict(measured())


def incident_store(data_dir: Path | str) -> IncidentStore:
    """A reader for one data directory's records (reports and the CLI)."""

    return IncidentStore(data_dir)


def recent_incidents(data_dir: Path | str, *, limit: int = SUMMARY_INCIDENTS) -> list[dict[str, Any]]:
    """The newest incidents, one line each, for the summary."""

    try:
        return [summary(record) for record in incident_store(data_dir).list()[:limit]]
    except Exception:  # noqa: BLE001 - a report never fails over its evidence
        return []


def names_job(record: Mapping[str, Any], job_id: str | None) -> bool:
    """Whether a record is about this run: its latest occurrence, an earlier one, or a kept log."""

    if not job_id:
        return False
    occurrences = [record.get("latest") or {}, *(record.get("occurrences") or ())]
    return job_id in (record.get("jobLogs") or {}) or any(
        isinstance(item, Mapping) and str((item.get("ids") or {}).get("job_id")) == job_id
        for item in occurrences
    )


def bundle_incidents(
    data_dir: Path | str, *, limit: int = BUNDLE_INCIDENTS, job_id: str | None = None,
) -> list[dict[str, Any]]:
    """The newest incident records, for the report zip.

    Job-log tails stay in the :data:`TAILED_INCIDENTS` newest records and in
    those naming ``job_id`` (the run selected in the dialog); the others keep
    the count of the tails they left out.
    """

    try:
        records = incident_store(data_dir).list()[:limit]
    except Exception:  # noqa: BLE001 - a report never fails over its evidence
        return []
    kept = []
    for index, record in enumerate(records):
        logs = record.get("jobLogs") or {}
        if logs and index >= TAILED_INCIDENTS and not names_job(record, job_id):
            record = {**record, "jobLogs": {}, "jobLogsLeftOut": len(logs)}
        kept.append(record)
    return kept


__all__ = [
    "BUNDLE_INCIDENTS",
    "SUMMARY_INCIDENTS",
    "TAILED_INCIDENTS",
    "VERSIONED_DISTRIBUTIONS",
    "bundle_incidents",
    "incident_store",
    "incident_versions",
    "names_job",
    "recent_incidents",
]
