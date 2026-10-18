"""Immutable WGLink publication and recovery across filesystem/registry commits.

The add-in counterpart resolves the design locator for manual Update only.
Handoffs always carry the exact export path. Registered and unknown bundles are
retained indefinitely: neither heartbeat absence nor age proves them unused.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Mapping
import uuid

from server.cadlink.operations import CANCELLED, RECEIVED
from server.cadlink.store import CadLinkStore

BUNDLE_LAYOUT = "immutable-v1"
_LOCK = threading.RLock()
_WINDOWS = os.name == "nt"
logger = logging.getLogger(__name__)
_COMPONENT = re.compile(r"[A-Za-z0-9_-]+\Z")


class PublicationConflict(ValueError):
    """Original artifact identity or content is unavailable; do not rebuild."""


def _component(value: object) -> str:
    text = str(value)
    if not _COMPONENT.fullmatch(text):
        raise PublicationConflict("Invalid CAD-link publication identity")
    return text


def _sync_directory(folder: Path) -> None:
    if not _WINDOWS:
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    """Replace one file without ever removing the old name first."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                if not _WINDOWS or getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 9:
                    raise
                time.sleep(0.02)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def bundle_products(path: Path, facts: Mapping[str, object]) -> dict[str, str]:
    """Validate identity and every checksum before reusing published bytes."""
    try:
        if path.is_symlink() or not path.is_dir():
            raise ValueError("bundle is unavailable")
        manifest_path = path / "wglink.json"
        if manifest_path.is_symlink():
            raise ValueError("manifest is a symlink")
        text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(text)
        for section, key in (
            ("bundle", "bundleId"),
            ("export", "exportId"),
            ("design", "designId"),
        ):
            if manifest[section]["id"] != facts[key]:
                raise ValueError("bundle identity mismatch")
        if manifest["export"]["sequence"] != facts["sequence"]:
            raise ValueError("bundle sequence mismatch")
        for section, field, key in (
            ("design", "design_hash", "designHash"),
            ("design", "edit_version", "editVersion"),
            ("export", "geometry_hash", "geometryHash"),
        ):
            if key in facts and manifest[section][field] != facts[key]:
                raise ValueError("bundle provenance mismatch")
        if "manifest_json" in facts and manifest != json.loads(str(facts["manifest_json"])):
            raise ValueError("registered manifest mismatch")
        files = manifest["files"]
        if "waveguide.step" not in files:
            raise ValueError("STEP is missing")
        for name, entry in files.items():
            # Bundle file names are relative, with no symlink traversal.
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                raise ValueError("invalid bundle file path")
            artifact = path / relative
            if artifact.is_symlink() or any(
                (path / parent).is_symlink() for parent in relative.parents
            ):
                raise ValueError("bundle file is a symlink")
            digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
            if digest != entry["sha256"]:
                raise ValueError("bundle checksum mismatch")
        result = {
            "manifest_json": text,
            "geometry_hash": manifest["export"]["geometry_hash"],
            "artifact_sha256": files["waveguide.step"]["sha256"],
            "bundle_path": str(path),
        }
        if facts.get("artifact_sha256") not in (None, result["artifact_sha256"]):
            raise ValueError("registered artifact checksum mismatch")
        return result
    except (OSError, AttributeError, ValueError, KeyError, TypeError) as exc:
        raise PublicationConflict(
            f"The original CAD-link bundle is missing or corrupt: {exc}"
        ) from exc


def row_facts(row: Mapping[str, Any]) -> dict[str, object]:
    return {
        "exportId": row["export_id"],
        "bundleId": row["bundle_id"],
        "designId": row["design_id"],
        "sequence": row["sequence"],
        "artifact_sha256": row["artifact_sha256"],
        "geometryHash": row["geometry_hash"],
        "manifest_json": row["manifest_json"],
    }


def publish_locator(store: CadLinkStore, row: Mapping[str, Any]) -> None:
    """Serialize selection and replacement; old completions select the newest row.

    WG's backend ownership lock admits one server per data directory. This lock
    covers its concurrent Fusion and Onshape request threads, including retries.
    """
    with _LOCK:
        root = Path(row["bundle_path"]).parent
        candidates = [
            candidate
            for candidate in store.publication_exports()
            if candidate["design_id"] == row["design_id"]
            and candidate.get("bundle_path")
            and Path(candidate["bundle_path"]).parent == root
            and Path(candidate["bundle_path"]).name == f"{candidate['export_id']}.wglink"
        ]
        if not candidates:
            return  # Legacy stable folders have no locator.
        latest = max(candidates, key=lambda candidate: candidate["sequence"])
        store.make_durable()
        bundle_products(Path(latest["bundle_path"]), row_facts(latest))
        locator = root / f"{_component(latest['design_id'])}.latest.json"
        if locator.is_symlink():
            raise PublicationConflict("CAD-link locator is a symlink")
        payload = {
            "schemaVersion": 1,
            "designId": latest["design_id"],
            "exportId": latest["export_id"],
            "bundleId": latest["bundle_id"],
            "sequence": latest["sequence"],
            "bundlePath": Path(latest["bundle_path"]).name,
        }
        if locator.exists():
            try:
                previous = json.loads(locator.read_text(encoding="utf-8"))
                if (
                    not isinstance(previous, dict)
                    or set(previous) != set(payload)
                    or previous["schemaVersion"] != 1
                    or type(previous["sequence"]) is not int
                    or previous["designId"] != latest["design_id"]
                ):
                    raise ValueError("invalid locator")
                if previous["sequence"] > latest["sequence"]:
                    raise ValueError("locator is ahead of the registry")
                if previous["sequence"] == latest["sequence"] and previous != payload:
                    raise ValueError("locator identity conflict")
            except (ValueError, TypeError, KeyError) as exc:
                raise PublicationConflict(f"CAD-link locator is invalid: {exc}") from exc
            if previous == payload:
                # Also repairs a directory fsync interrupted after replacement.
                _sync_directory(root)
                return
        atomic_json(locator, payload)


def _journal_folder(store: CadLinkStore) -> Path:
    return store.db_path.with_suffix(".publications")


def publish_bundle(
    store: CadLinkStore,
    root: Path,
    facts: Mapping[str, object],
    build: Callable[[Path], None],
    *,
    handoff: Mapping[str, object] | None = None,
) -> Mapping[str, str]:
    """Persist intent, stage/validate/fsync, then rename to a unique export name."""
    # Reservations and snapshots must survive power loss before their bytes
    # become visible. The store otherwise uses WAL synchronous=NORMAL.
    store.make_durable()
    destination = root / f"{_component(facts['exportId'])}.wglink"
    folder = _journal_folder(store)
    folder.mkdir(parents=True, exist_ok=True)
    _sync_directory(folder.parent)
    journal = folder / f"{_component(facts['exportId'])}.json"
    with _LOCK:
        if not journal.exists():
            atomic_json(
                journal,
                {
                    "facts": dict(facts),
                    "root": str(root),
                    "handoff": dict(handoff) if handoff else None,
                    "requestId": str(uuid.uuid4()),
                },
            )
    if destination.exists() or destination.is_symlink():
        return bundle_products(destination, facts)
    from server.platform.staging import publish_staging_directory
    import shutil

    staging = publish_staging_directory(root, ".wg2-wglink-")
    try:
        staged = staging / "bundle.wglink"
        build(staged)
        bundle_products(staged, facts)
        for artifact in staged.rglob("*"):
            if artifact.is_file():
                with artifact.open("rb+") as handle:
                    os.fsync(handle.fileno())
        for directory in sorted((p for p in staged.rglob("*") if p.is_dir()), reverse=True):
            _sync_directory(directory)
        _sync_directory(staged)
        with _LOCK:
            if destination.exists() or destination.is_symlink():
                return bundle_products(destination, facts)
            os.rename(staged, destination)
            _sync_directory(root)
        return bundle_products(destination, facts)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _never_published(existing: Mapping[str, Any], data_dir: str) -> bool:
    """Whether an accepted handoff row has no request file and no claim.

    A crash between accepting the operation and renaming its request file into
    place leaves such a row. A live same-key retry re-issues it with a fresh id,
    as every retry did before immutable bundles; startup never decides this.
    """
    from server.cadlink.fusion.fusion_delivery import HANDOFFS
    from server.cadlink.ipc import ipc_folder

    if existing.get("state") != RECEIVED or existing.get("claim_json") is not None:
        return False
    operation_id = str(existing["operation_id"])
    directory = ipc_folder(Path(data_dir)) / HANDOFFS.directory
    try:
        names = {path.name for path in directory.iterdir()}
    except FileNotFoundError:
        names = set()
    except OSError:
        return False  # Unreadable evidence is not an absent request.
    return f"{operation_id}.json" not in names and not any(
        name.startswith(f".wglink-claim-{operation_id}-") and name.endswith(".json")
        for name in names
    )


def finish_publication(
    store: CadLinkStore, row: Mapping[str, Any], *, deliver_handoff: bool = True,
) -> dict[str, Any]:
    """Repair durable boundaries; only a live request may deliver its handoff."""
    store.make_durable()
    publish_locator(store, row)
    journal = _journal_folder(store) / f"{_component(row['export_id'])}.json"
    with _LOCK:
        if not journal.exists():
            return {"cadHandoff": "failed", "cadHandoffReason": "not_published"}
        intent = json.loads(journal.read_text(encoding="utf-8"))
        handoff = intent["handoff"]
        if not deliver_handoff:
            if not intent.get("done"):
                atomic_json(journal, {**intent, "handoffState": "not_delivered"})
            return {}
        if any(
            candidate["design_id"] == row["design_id"]
            and candidate["sequence"] > row["sequence"]
            for candidate in store.publication_exports()
        ):
            atomic_json(journal, {**intent, "handoffState": "superseded"})
            return {"cadHandoff": "failed", "cadHandoffReason": "superseded"}
        existing = store.get_operation(intent["requestId"]) if handoff else None
        if intent.get("done"):
            if not handoff:
                return {"cadHandoff": "failed", "cadHandoffReason": "not_published"}
            if existing is not None and existing.get("state") == "cancelled":
                return {"cadHandoff": "failed", "cadHandoffReason": existing.get("reason")}
            return {"cadHandoff": "published", "cadHandoffRequestId": intent["requestId"]}
        published = None
        if handoff:
            from .cad_handoff import publish_fusion_handoff

            if existing is not None and _never_published(existing, handoff["dataDir"]):
                store.record_outcome(
                    intent["requestId"],
                    int(existing["attempt_generation"]),
                    CANCELLED,
                    reason="publication_failed",
                    outcome={"message": "The Fusion handoff request file was never published; a retry re-issued it."},
                )
                existing = store.get_operation(intent["requestId"])
            # A consumed or terminal operation must never become runnable again.
            if existing is not None and existing.get("reason") == "publication_failed":
                intent["requestId"] = str(uuid.uuid4())
                atomic_json(journal, intent)
                existing = None
            if existing is None:
                result = {
                    "bundlePath": row["bundle_path"],
                    "bundleId": row["bundle_id"],
                    "exportId": row["export_id"],
                    "sequence": row["sequence"],
                    "bundleLayout": BUNDLE_LAYOUT,
                    "identity": {"designId": row["design_id"]},
                }
                published = publish_fusion_handoff(
                    Path(handoff["dataDir"]),
                    Path(intent["root"]).parent,
                    store,
                    result,
                    request_id=intent["requestId"],
                    expected_document_id=handoff.get("documentId"),
                    expected_instance_id=handoff.get("instanceId"),
                    expected_return_state_hash=handoff.get("returnStateHash"),
                )
            store.make_durable()
            if published is not None:
                _sync_directory(published.path.parent)
        atomic_json(journal, {**intent, "done": True,
                              "handoffState": "published" if handoff else "not_delivered"})
        if published is not None:
            return {"cadHandoff": "published", "cadHandoffRequestId": published.request_id,
                    "cadHandoffSuperseded": list(published.withdrawn)}
        if handoff and existing is not None and existing.get("reason") != "publication_failed":
            return {"cadHandoff": "published", "cadHandoffRequestId": intent["requestId"]}
        return {"cadHandoff": "failed", "cadHandoffReason": "not_published"}


def recover_publications(store: CadLinkStore) -> list[str]:
    """Recover only validated bundles with durable reservations; unknown means keep.

    No orphan GC is attempted: an unregistered path may still be named by an
    offline link, pending handoff, operation, or another registry.
    """
    failures = []
    folder = _journal_folder(store)
    for journal in sorted(folder.glob("*.json")):
        try:
            intent = json.loads(journal.read_text(encoding="utf-8"))
            if intent.get("done"):
                continue
            facts = intent["facts"]
            row = store.get_export(str(facts["exportId"]))
            if row is None:
                reservation = store.publication_reservation(str(facts["exportId"]))
                if reservation is None:
                    continue
                path = Path(intent["root"]) / f"{_component(facts['exportId'])}.wglink"
                if not path.exists():
                    continue  # Build did not finish; retry can rebuild its staging.
                row = store.allocate_export(
                    design_id=reservation["design_id"],
                    idempotency_key=reservation["idempotency_key"],
                    export_builder=lambda reserved: bundle_products(path, reserved),
                )
            finish_publication(store, row, deliver_handoff=False)
        except Exception as exc:
            failures.append(journal.name)
            logger.warning("Could not recover WGLink publication %s: %s", journal.name, exc)
    # Also repairs a locator lost after a completed publication. Never fabricate
    # delivery intent for historical exports.
    latest = {}
    for row in store.publication_exports():
        if row.get("bundle_path") and Path(row["bundle_path"]).name == f"{row['export_id']}.wglink":
            key = (str(Path(row["bundle_path"]).parent), row["design_id"])
            if key not in latest or latest[key]["sequence"] < row["sequence"]:
                latest[key] = row
    for row in latest.values():
        try:
            publish_locator(store, row)
        except Exception as exc:
            failures.append(str(row["export_id"]))
            logger.warning("Could not recover WGLink locator: %s", exc)
    return failures


def recover_reserved_export(store: CadLinkStore, key: str) -> dict[str, Any] | None:
    reservation = store.publication_reservation_for_key(key)
    if reservation is None:
        return None
    journal = _journal_folder(store) / f"{_component(reservation['export_id'])}.json"
    if not journal.exists():
        return None
    intent = json.loads(journal.read_text(encoding="utf-8"))
    path = Path(intent["root"]) / f"{_component(reservation['export_id'])}.wglink"
    if not path.exists() and not path.is_symlink():
        return None
    return store.allocate_export(
        design_id=reservation["design_id"],
        idempotency_key=key,
        export_builder=lambda facts: bundle_products(path, facts),
    )
