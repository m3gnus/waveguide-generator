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


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _snapshot(payload: dict, *, mouth_resolution: float) -> dict:
    profile = copy.deepcopy(payload["profile"])
    geometry = copy.deepcopy(payload["geometry"])
    design = {
        **profile,
        "mesh": {
            "vertical_offset": {
                "value": geometry.pop("vertical_offset"),
                "raw": "presentation-only",
            },
            "wall_thickness": geometry.pop("wall_thickness"),
            "mouth_resolution": mouth_resolution,
            "rear_resolution": 40.0,
        },
        "enclosure": {
            **geometry["enclosure"],
            "front_resolution": mouth_resolution,
            "back_resolution": 40.0,
        },
        "source": copy.deepcopy(payload["source"]),
        "extra_blocks": {"WG.Solve": {"items": {"Engine": "ignored"}}},
    }
    return {"version": 1, "design": design}


def test_committed_model_identity_manifest_is_self_verifying() -> None:
    manifest = _manifest()

    payload = verify_model_identity.verify_manifest(manifest)

    assert verify_model_identity.leaf_count(payload) == 40
    assert (
        verify_model_identity.payload_sha256(payload)
        == "2667d10f72e3f5cf209d9fe1e8ac77ea9aa75c9cd5b4ad74e883abe5e474ccb8"
    )


def test_normalization_excludes_solver_mesh_resolution_and_ui_raw_strings() -> None:
    payload = _manifest()["payload"]
    coarse = _snapshot(payload, mouth_resolution=25.0)
    fine = _snapshot(payload, mouth_resolution=15.0)

    assert verify_model_identity.normalize_design_snapshot(coarse) == payload
    assert verify_model_identity.normalize_design_snapshot(fine) == payload


def test_manifest_verification_rejects_payload_tampering() -> None:
    manifest = _manifest()
    manifest["payload"]["profile"]["R"] = 601.0

    with pytest.raises(verify_model_identity.IdentityError, match="SHA-256 mismatch"):
        verify_model_identity.verify_manifest(manifest)
