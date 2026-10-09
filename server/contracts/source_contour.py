"""Authoritative native contour request/excitation admission contract."""

import hashlib
import json

FEATURE = "native-source-contour-v1"
BAFFLE_FEATURE = "native-front-baffle-woofer-v1"
ASSEMBLY_FEATURE = "native-shared-horn-woofer-v1"
PLUG_FEATURE = "native-phase-plug-passages-v1"


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
    if (
        required
        not in (
            [FEATURE],
            [FEATURE, BAFFLE_FEATURE],
            [FEATURE, ASSEMBLY_FEATURE],
            [FEATURE, ASSEMBLY_FEATURE, PLUG_FEATURE],
        )
        or required != native["required_features"]
    ):
        raise ValueError("native source requires explicit supported feature negotiation")
    assembly = ASSEMBLY_FEATURE in required
    expected = native["channels"] if assembly else [native["channel"]]
    excitation = (
        expected
        if assembly
        else {
            "channel_id": expected[0]["id"],
            "weights": {k: float(v) for k, v in expected[0]["patch_weights"].items()},
            "motion": expected[0]["motion"],
        }
    )
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
    if actual != expected or any(c.driver is not None for c in geometry.drive_channels):
        raise ValueError("native source request contradicts authoritative ingestion excitation")
    if geometry.skipped_source_ids:
        raise ValueError("native source cannot skip moving patches")
    if assembly:
        from hornlab_mesher.source_assembly import SourceAssembly
        from hornlab_mesher.phase_plug import passage_contract

        model = SourceAssembly.from_dict(native["recipe"])
        if bool(model.phase_plugs) != (PLUG_FEATURE in required) or native.get(
            "passage_contract"
        ) != passage_contract(model):
            raise ValueError("native phase-plug topology identity is inconsistent")
        if model.geometry_sha256 != native["geometry_sha256"]:
            raise ValueError("native assembly geometry identity is inconsistent")
        origin = [0, 0, model.front_z_mm * 0.001]
        frame = {
            "axis": [0, 0, 1],
            "u": [1, 0, 0],
            "v": [0, 1, 0],
            "origin_m": origin,
            "source_center_m": origin,
            "mouth_center_m": origin,
        }
        if (
            native["observation_frame"] != frame
            or record["normalisation"].get("source_frame") != frame
        ):
            raise ValueError("native assembly common observation frame is inconsistent")
        frames = {
            c.physical_source_id: {"origin_m": [x * 0.001 for x in position], "axis": [0, 0, 1]}
            for c, position, _ in model.parts
        }
        if native["source_frames"] != frames:
            raise ValueError("native assembly physical source frames are inconsistent")
        if record.get("anchor") or record["normalisation"].get("matrix") != [
            [1, 0, 0, 0],
            [0, 1, 0, 0],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ]:
            raise ValueError(
                "native assembly common frame cannot carry another anchor or transform"
            )
