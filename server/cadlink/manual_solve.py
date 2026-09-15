"""Create backend-owned solve operations from retained ingestion records."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from .ingest import read_snapshot, retained_snapshot_path
from .operations import PREPARE_AND_SOLVE, prepare_and_solve_request, request_digest
from .project_setup import snapshot_project
from .store import CadLinkStore


class UnknownIngest(ValueError):
    """The requested immutable ingestion record does not exist."""


class SnapshotNotRetained(ValueError):
    """The ingestion record's content-addressed return is no longer present."""


class OperationConflict(ValueError):
    """The operation id already names another request."""


def _record(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(str(row["record_json"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SnapshotNotRetained("The ingestion record cannot name its retained snapshot.") from exc
    if not isinstance(value, dict):
        raise SnapshotNotRetained("The ingestion record cannot name its retained snapshot.")
    return value


def create_manual_solve(
    store: CadLinkStore, data_dir: str | Path, operation_id: str, ingest_id: str
) -> tuple[dict[str, Any], str]:
    """Accept or recover one solve bound to an existing retained ingest."""

    ingest = store.get_ingest(ingest_id)
    if ingest is None:
        raise UnknownIngest(f"Unknown CAD ingest {ingest_id}")
    record = _record(ingest)
    manifest_sha256 = str(ingest.get("manifest_sha256") or "")
    artifact_sha256 = str(ingest.get("artifact_sha256") or "")
    return_id = str(record.get("return_id") or "")
    target, inputs = prepare_and_solve_request(
        return_id=return_id,
        bundle_path=f"ingest/{ingest_id}",
        manifest_sha256=manifest_sha256,
    )
    digest = request_digest(PREPARE_AND_SOLVE, target, inputs)
    existing = store.get_operation(operation_id)
    if existing is not None:
        if (
            existing.get("kind") != PREPARE_AND_SOLVE
            or int(existing.get("legacy") or 0) == 1
            or existing.get("request_digest") != digest
        ):
            raise OperationConflict(f"CAD operation {operation_id} already names another request")
        # Replay recovers the durable operation itself. Its retained artifact may
        # have been pruned or damaged since acceptance; preparation reports that
        # separately, but it cannot turn an identical request into a new refusal.
        return existing, "recovered"

    try:
        retained = retained_snapshot_path(data_dir, manifest_sha256)
        bundle = read_snapshot(retained, retained=True)
    except (OSError, ValueError) as exc:
        raise SnapshotNotRetained(
            "The selected CAD import no longer has a retained snapshot. Import it again."
        ) from exc
    if bundle.manifest_sha256 != manifest_sha256 or bundle.artifact_sha256 != artifact_sha256:
        raise SnapshotNotRetained(
            "The selected CAD import's retained snapshot no longer matches its ingestion record."
        )

    document = record.get("document") if isinstance(record.get("document"), Mapping) else {}
    project = record.get("project") if isinstance(record.get("project"), Mapping) else {}
    snapshot = {
        "manifest_sha256": manifest_sha256,
        "artifact_sha256": artifact_sha256,
        "document_name": str(document.get("name") or ""),
        "project_lineage_id": project.get("lineage_id") or snapshot_project(store, bundle.manifest),
    }
    row, outcome = store.accept_operation(
        operation_id, PREPARE_AND_SOLVE, digest, target, inputs, snapshot=snapshot
    )
    if outcome == "conflict" or int(row.get("legacy") or 0) == 1 or row.get("request_digest") != digest:
        raise OperationConflict(f"CAD operation {operation_id} already names another request")
    return row, outcome


__all__ = ["OperationConflict", "SnapshotNotRetained", "UnknownIngest", "create_manual_solve"]
