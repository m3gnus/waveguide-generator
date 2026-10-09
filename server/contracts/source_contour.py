"""Authoritative native contour request/excitation admission contract."""

import hashlib
import json

FEATURE = "native-source-contour-v1"


def validate_contour_request(geometry, record):
    native = record.get("native_source")
    required = getattr(geometry, "required_features", [])
    if native is None:
        if required or any(
            c.patch_weights is not None or c.physical_source_id is not None
            for c in geometry.drive_channels
        ):
            raise ValueError("native source features require a native ingestion record")
        return
    if required != [FEATURE]:
        raise ValueError("native source requires explicit supported feature negotiation")
    expected = native["channel"]
    excitation = {
        "channel_id": expected["id"],
        "weights": {k: float(v) for k, v in expected["patch_weights"].items()},
        "motion": expected["motion"],
    }
    encoded = json.dumps(
        excitation, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if "sha256:" + hashlib.sha256(encoded).hexdigest() != native["excitation_sha256"]:
        raise ValueError("native ingestion excitation identity is inconsistent")
    actual = [
        {
            "id": c.id,
            "source_ids": c.source_ids,
            "motion": c.motion,
            "physical_source_id": c.physical_source_id,
            "patch_weights": c.patch_weights,
        }
        for c in geometry.drive_channels
    ]
    if actual != [expected] or any(c.driver is not None for c in geometry.drive_channels):
        raise ValueError("native source request contradicts authoritative ingestion excitation")
    if geometry.skipped_source_ids:
        raise ValueError("native source cannot skip moving patches")
