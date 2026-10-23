"""Synthetic retained-manifest evidence shared by CAD archive tests."""

import hashlib
import json

from server.cadlink.intake.ingest import retained_snapshot_path


def retained_document_record(app, content: bytes, digest: str, lineage: str) -> dict:
    manifest = {
        "assembly": {"signature_hash": digest},
        "files": {"document.f3d": {
            "purpose": "cad-document",
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }},
    }
    raw = json.dumps(manifest).encode()
    manifest_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    retained = retained_snapshot_path(app.state.data_dir, manifest_hash)
    retained.mkdir(parents=True, exist_ok=True)
    (retained / "wgreturn.json").write_bytes(raw)
    row = app.state.cadlink_store.allocate_ingest(
        manifest_sha256=manifest_hash, artifact_sha256="sha256:unused",
        record_builder=lambda ingest_id, created_at: json.dumps({
            "ingest_id": ingest_id, "created_at": created_at,
            "manifest_sha256": manifest_hash,
            "project": {"lineage_id": lineage},
            "document": {"return_state_hash": digest},
        }),
    )
    return json.loads(row["record_json"])


def identify_document(app, document, lineage: str, digest: str) -> None:
    record = retained_document_record(app, document.read_bytes(), digest, lineage)
    ordinary = document.with_suffix(".json")
    sidecar = ordinary if ordinary.exists() else document.with_suffix(".cad.json")
    payload = json.loads(sidecar.read_text())
    payload["ingestId"] = record["ingest_id"]
    sidecar.write_text(json.dumps(payload))
