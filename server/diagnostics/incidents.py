"""Incident records in problem reports, and the versions each record carries.

The records themselves are owned by ``server.platform.incidents``; this module
only says what a record names about the build and how a report includes them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from functools import lru_cache
from importlib import metadata
import json
from pathlib import Path
import platform
import sqlite3
import sys
from typing import Any

from server.cadlink.intake.ingest import retained_snapshot_path
from server.cadlink.wgreturn import identifying_names
from server.integration.installed import measure_installed_stack
from server.integration.provenance import pinned_dependency_shas
from server.platform.incidents import IncidentStore, summary
from server.platform.paths import data_paths


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


class IncidentNames:
    """The names of the model an incident's ids lead to: what its record must redact.

    A run (its stored snapshot's document name, and the ingest and retained
    bundle it names), a CAD Link ingest (its document and sources), and WG's
    retained copy of the return (its manifest). Read only: the databases are
    opened read-only and nothing is written. Called with an occurrence's ids;
    returns ``None`` when they lead nowhere, else the names (possibly none).
    """

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        db = data_paths(self.data_dir).db
        self._jobs_db = db / "simulations.db"
        self._cadlink_db = db / "cadlink.db"

    def __call__(self, ids: Mapping[str, Any]) -> list[str] | None:
        names, sources = self.find(ids)
        return names if sources else None

    def find(self, ids: Mapping[str, Any]) -> tuple[list[str], list[str]]:
        """The names, and which sources were found (``run``, ``ingest``, ``bundle``)."""

        names: list[str] = []
        sources: list[str] = []

        def add(found: Iterable[str], source: str) -> None:
            sources.append(source)
            names.extend(name for name in found if name not in names)

        digests = [ids.get("manifest_sha256")]
        ingests = [ids.get("ingest_id")]
        job = self._job_cad(ids.get("job_id"))
        if job is not None:
            snapshot = job.get("snapshot") if isinstance(job.get("snapshot"), Mapping) else {}
            preparation = job.get("preparation") if isinstance(job.get("preparation"), Mapping) else {}
            add([str(snapshot["document_name"])] if snapshot.get("document_name") else [], "run")
            digests.append(snapshot.get("manifest_sha256"))
            ingests.append(preparation.get("ingest_id"))
        for ingest_id in dict.fromkeys(str(item) for item in ingests if item):
            record = self._ingest(ingest_id)
            if record is not None:
                add(identifying_names(record), "ingest")
                digests.append(record.get("manifest_sha256"))
        for digest in dict.fromkeys(str(item) for item in digests if item):
            manifest = self._manifest(digest)
            if manifest is not None:
                add(identifying_names(manifest), "bundle")
        return names, list(dict.fromkeys(sources))

    @staticmethod
    def _row(path: Path, query: str, key: str) -> tuple[Any, ...] | None:
        if not key or not path.is_file():
            return None
        try:
            connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1.0)
            try:
                return connection.execute(query, (key,)).fetchone()
            finally:
                connection.close()
        except (sqlite3.Error, OSError):
            return None

    @staticmethod
    def _json(raw: Any) -> Mapping[str, Any] | None:
        try:
            value = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, Mapping) else None

    def _job_cad(self, job_id: Any) -> Mapping[str, Any] | None:
        row = self._row(self._jobs_db, "SELECT task_metadata_json FROM simulation_jobs WHERE id = ?", str(job_id or ""))
        metadata = self._json(row[0]) if row else None
        cad = metadata.get("cad") if metadata is not None else None
        return cad if isinstance(cad, Mapping) else None

    def _ingest(self, ingest_id: str) -> Mapping[str, Any] | None:
        row = self._row(
            self._cadlink_db, "SELECT record_json, manifest_sha256 FROM ingests WHERE ingest_id = ?", ingest_id,
        )
        record = self._json(row[0]) if row else None
        return {**record, "manifest_sha256": row[1]} if record is not None and row else None

    def _manifest(self, digest: str) -> Mapping[str, Any] | None:
        try:
            path = retained_snapshot_path(self.data_dir, digest) / "wgreturn.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        return manifest if isinstance(manifest, Mapping) else None


def incident_store(data_dir: Path | str) -> IncidentStore:
    """A reader for one data directory's records (reports and the CLI)."""

    return IncidentStore(data_dir)


def recent_incidents(data_dir: Path | str, *, limit: int = SUMMARY_INCIDENTS) -> list[dict[str, Any]]:
    """The newest incidents, one line each, for the summary."""

    try:
        # The clipboard's and the summary's form: the model's names and the ids redacted.
        return [summary(record, public=True) for record in incident_store(data_dir).list()[:limit]]
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
    "IncidentNames",
    "SUMMARY_INCIDENTS",
    "TAILED_INCIDENTS",
    "VERSIONED_DISTRIBUTIONS",
    "bundle_incidents",
    "incident_store",
    "incident_versions",
    "names_job",
    "recent_incidents",
]
