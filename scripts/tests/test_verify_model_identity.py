from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import verify_model_identity


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    ROOT
    / "docs"
    / "validation"
    / "2026-08"
    / "wall-clearance-model-identity.json"
)
EVIDENCE_DIR = MANIFEST_PATH.parent / "evidence"
ARCHIVED_SNAPSHOTS = {
    "run-98": (
        EVIDENCE_DIR / "wall-clearance-run-98-design-snapshot.json",
        "2036d543c19af030d1cde41b1acf8bd5ab26e5d152aaf1b47daa87c75d583bcb",
    ),
    "run-101": (
        EVIDENCE_DIR / "wall-clearance-run-101-design-snapshot.json",
        "9cc792b802699bb8868c2882e138c889b20cba4308bfbb905e732d1956b26de5",
    ),
}


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _snapshot(name: str) -> dict:
    path, _expected_digest = ARCHIVED_SNAPSHOTS[name]
    return json.loads(path.read_text(encoding="utf-8"))


def test_unsound_v1_digest_cannot_verify_as_v2_identity() -> None:
    manifest = _manifest()
    manifest["sha256"] = (
        "2667d10f72e3f5cf209d9fe1e8ac77ea9aa75c9cd5b4ad74e883abe5e474ccb8"
    )

    with pytest.raises(verify_model_identity.IdentityError, match="SHA-256 mismatch"):
        verify_model_identity.verify_manifest(manifest)


def test_archived_snapshots_are_the_reviewed_inputs_not_manifest_derivatives() -> None:
    payload = verify_model_identity.verify_manifest(_manifest())
    snapshots = [_snapshot(name) for name in ARCHIVED_SNAPSHOTS]

    assert [
        snapshot["design"]["mesh"]["mouth_resolution"]["value"]
        for snapshot in snapshots
    ] == [15.0, 25.0]
    assert [
        verify_model_identity.canonical_sha256(snapshot) for snapshot in snapshots
    ] == [expected_digest for _path, expected_digest in ARCHIVED_SNAPSHOTS.values()]
    assert [
        verify_model_identity.normalize_design_snapshot(snapshot)
        for snapshot in snapshots
    ] == [payload, payload]


def test_parameterized_expressions_with_one_cached_value_have_distinct_identity() -> None:
    first = _snapshot("run-101")
    second = copy.deepcopy(first)
    first["design"]["R"] = {"value": 600.0, "raw": "600 + 10*cos(p)"}
    second["design"]["R"] = {"value": 600.0, "raw": "600 + 20*cos(p)"}

    first_identity = verify_model_identity.normalize_design_snapshot(first)
    second_identity = verify_model_identity.normalize_design_snapshot(second)

    assert first_identity != second_identity
    assert first_identity["profile"]["R"] != second_identity["profile"]["R"]


def test_constant_expression_uses_its_schema_checked_numeric_value() -> None:
    snapshot = _snapshot("run-101")
    snapshot["design"]["R"] = {"value": 600.0, "raw": "300 * 2"}

    assert verify_model_identity.normalize_design_snapshot(snapshot) == _manifest()[
        "payload"
    ]


def test_snapshot_with_disagreeing_constant_raw_and_value_is_rejected() -> None:
    snapshot = _snapshot("run-101")
    snapshot["design"]["R"] = {"value": 600.0, "raw": "599"}

    with pytest.raises(verify_model_identity.IdentityError, match="design schema"):
        verify_model_identity.normalize_design_snapshot(snapshot)


def test_manifest_verification_rejects_payload_tampering() -> None:
    manifest = _manifest()
    manifest["payload"]["profile"]["R"] = 601.0

    with pytest.raises(verify_model_identity.IdentityError, match="SHA-256 mismatch"):
        verify_model_identity.verify_manifest(manifest)
