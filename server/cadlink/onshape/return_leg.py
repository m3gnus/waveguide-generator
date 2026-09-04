"""Build an immutable Onshape-authored ``wgreturn`` and ingest it.

The Onshape adapter is server-side, so the CAD evidence is observed from the
exact STEP bytes returned by Onshape.  Identity and source contracts come only
from the registry's exact outbound export manifest; no parameter or role is
reverse-engineered from returned geometry.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from typing import Any

from server.cadlink.identity import mint_id
from server.cadlink.ingest import ingest_bundle
from server.cadlink.isolated import inspect_returned_step
from server.cadlink.isolation import ChildRefusal
from server.cadlink.step_evidence import ReturnedStepError
from server.cadlink.store import CadLinkStore
from server.platform.staging import publish_staging_directory


RETURN_SUBDIRECTORY = Path("cadlink") / "onshape" / "wgreturn"
SOURCE_INTERFACE_FEATURE = "source-interface-v1"


class OnshapeReturnError(ReturnedStepError):
    """The linked document cannot honestly satisfy the wgreturn contract.

    A subclass of the shared returned-STEP error so that code catching either
    the general case or the Onshape case sees the same failures. The general
    one is raised by ``server/cadlink/step_evidence.py``, which no longer knows
    or cares which CAD tool the bytes came from.
    """


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OnshapeReturnError(f"Stored outbound evidence is missing {label}.")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise OnshapeReturnError(f"Stored outbound evidence is missing {label}.")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _source_policy_from_export(manifest: Mapping[str, Any]) -> dict[str, Any]:
    features = manifest.get("required_features")
    interface = manifest.get("interface")
    sources = interface.get("sources") if isinstance(interface, Mapping) else None
    if (
        not isinstance(features, list)
        or SOURCE_INTERFACE_FEATURE not in features
        or not isinstance(sources, list)
        or not sources
    ):
        raise OnshapeReturnError(
            "The linked outbound bundle has no WG-authored return-source interface. "
            "Its throat geometry is known, but source role, source id, and drive-channel "
            "identity are not; WG will not default them to HF."
        )
    if len(sources) != 1 or not isinstance(sources[0], Mapping):
        raise OnshapeReturnError(
            "The Onshape linked-throat return currently requires exactly one "
            "wglink.interface.sources[] record."
        )
    raw = sources[0]
    policy = dict(raw)
    for key in ("id", "role", "default_drive_channel_id", "patch_policy"):
        _string(policy.get(key), f"wglink.interface.sources[0].{key}")
    if not isinstance(policy.get("required"), bool):
        raise OnshapeReturnError(
            "Stored outbound evidence is missing wglink.interface.sources[0].required."
        )
    components = policy.get("expected_connected_components")
    if isinstance(components, bool) or not isinstance(components, int) or components < 1:
        raise OnshapeReturnError(
            "Stored outbound evidence has an invalid return-source component count."
        )
    if policy["patch_policy"] != "single-connected":
        raise OnshapeReturnError(
            "The managed linked-throat return source must be single-connected."
        )
    if components != 1:
        raise OnshapeReturnError(
            "A single-connected return source must declare exactly one component."
        )
    resolution = policy.get("suggested_resolution_mm")
    try:
        resolution = float(resolution)
    except (TypeError, ValueError) as exc:
        raise OnshapeReturnError(
            "Stored outbound return-source resolution is missing or invalid."
        ) from exc
    if not math.isfinite(resolution) or resolution <= 0:
        raise OnshapeReturnError(
            "Stored outbound return-source resolution is missing or invalid."
        )
    policy["suggested_resolution_mm"] = resolution
    return policy


def source_contract_from_export(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the linked throat geometry only from stored WG-authored evidence."""

    source_policy = _source_policy_from_export(manifest)

    datums = _mapping(manifest.get("datums"), "wglink.datums")
    throat = _mapping(datums.get("WG_THROAT_PLANE"), "wglink.datums.WG_THROAT_PLANE")
    axis = _mapping(datums.get("WG_AXIS"), "wglink.datums.WG_AXIS")
    parameters = manifest.get("parameters")
    if not isinstance(parameters, list):
        raise OnshapeReturnError("Stored outbound evidence is missing wglink.parameters.")
    throat_parameter = next(
        (
            item
            for item in parameters
            if isinstance(item, Mapping)
            and isinstance(item.get("name"), str)
            and str(item["name"]).endswith("_throat_dia")
        ),
        None,
    )
    if throat_parameter is None:
        raise OnshapeReturnError(
            "The linked outbound bundle has no managed throat-diameter parameter, "
            "so WG cannot author a source contract for this return."
        )
    try:
        diameter = float(throat_parameter["value"])
        origin = [float(value) for value in throat["origin_mm"]]
        normal = [float(value) for value in throat["normal"]]
        axis_origin = [float(value) for value in axis["origin_mm"]]
        axis_direction = [float(value) for value in axis["direction"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise OnshapeReturnError(
            "The linked outbound bundle's throat datums are incomplete."
        ) from exc
    if diameter <= 0 or len(origin) != 3 or len(normal) != 3 or len(axis_origin) != 3 or len(axis_direction) != 3:
        raise OnshapeReturnError("The linked outbound bundle's throat contract is invalid.")
    return {
        "role": source_policy["role"],
        "throat_z_mm": origin[2],
        "throat_plane_link": {"origin_mm": origin, "normal": normal},
        "axis_link": {"origin_mm": axis_origin, "direction": axis_direction},
        "throat_diameter_mm": diameter,
        "expected_disc_area_mm2": math.pi * diameter * diameter / 4.0,
    }


# The OCC observation itself lives in ``server/cadlink/step_evidence.py`` so the
# inspect child can import it without the store, the registry, or the ingest
# pipeline coming along.


def _baseline_fingerprint(outbound: Mapping[str, Any]) -> dict[str, Any] | None:
    body = outbound.get("body")
    if not isinstance(body, Mapping):
        return None
    bbox = body.get("bbox_mm")
    if not (
        isinstance(bbox, list)
        and len(bbox) == 2
        and all(isinstance(row, list) and len(row) == 3 for row in bbox)
    ):
        return None
    try:
        return {
            "is_solid": str(body.get("kind")) == "solid",
            "volume_mm3": float(body["volume_mm3"]),
            "bbox_mm": [float(value) for row in bbox for value in row],
        }
    except (KeyError, TypeError, ValueError):
        return None


def write_return_bundle(
    step_bytes: bytes,
    *,
    link: Mapping[str, Any],
    export_row: Mapping[str, Any],
    data_dir: str | Path,
) -> Path:
    """Publish one checksummed immutable bundle under WG's Onshape data area."""

    if not step_bytes.startswith(b"ISO-10303") and b"ISO-10303" not in step_bytes[:4096]:
        raise OnshapeReturnError(
            "Onshape's completed translation did not return a STEP Part 21 file."
        )
    try:
        outbound = json.loads(str(export_row["manifest_json"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise OnshapeReturnError("The linked export's stored manifest is unreadable.") from exc
    if not isinstance(outbound, Mapping):
        raise OnshapeReturnError("The linked export's stored manifest is not an object.")
    source_policy = _source_policy_from_export(outbound)
    contract = source_contract_from_export(outbound)
    return_id = mint_id("wgr_")
    created_at = _utc_now()
    root = Path(data_dir).resolve() / RETURN_SUBDIRECTORY
    root.mkdir(parents=True, exist_ok=True)
    temporary = publish_staging_directory(root, ".onshape-return-")
    target = root / f"{return_id}.wgreturn"
    # API-created links always carry WG's durable opaque identity. Direct unit
    # callers and pre-v8 fixtures retain the old single-Part-Studio fallback;
    # the registry migration assigns a durable id before any real API action.
    instance_id = str(link.get("instance_id") or "") or (
        f"onshape-{_string(link.get('part_studio_element_id'), 'Part Studio identity')}"
    )
    try:
        assembly = temporary / "assembly.step"
        assembly.write_bytes(step_bytes)
        baseline = _baseline_fingerprint(outbound)
        # These bytes came back from a cloud CAD tool. They are inspected in a
        # disposable child process that has no credentials, no data directory,
        # and a deadline (``docs/plans/STEP-PARSER-ISOLATION.md``); a crash,
        # hang, or over-budget parse is a refusal here, never a wedged server.
        try:
            observed = inspect_returned_step(assembly, contract, baseline)
        except ChildRefusal as exc:
            raise OnshapeReturnError(exc.detail) from exc
        for item in observed["included"]:
            if item.get("wglink_instance_id") == "__LINKED_INSTANCE__":
                item["wglink_instance_id"] = instance_id
        design = _mapping(outbound.get("design"), "wglink.design")
        exported = _mapping(outbound.get("export"), "wglink.export")
        bundle = _mapping(outbound.get("bundle"), "wglink.bundle")
        parameter = next(
            item for item in outbound["parameters"]
            if isinstance(item, Mapping) and str(item.get("name", "")).endswith("_throat_dia")
        )
        parameter_prefix = str(parameter["name"])[: -len("throat_dia")]
        manifest = {
            "wgreturn_version": "1.1",
            "required_features": [
                "checksummed-files-v1",
                "assembly-frame-v1",
                "instance-records-v1",
            ],
            "return": {"id": return_id, "created_at": created_at},
            "generator": {
                "adapter": "waveguide-generator/Onshape",
                "adapter_version": "1.0",
                "cad_app": "onshape",
                "cad_version": "cloud-api",
            },
            "document": {
                "name": _string(link.get("document_name"), "Onshape document name"),
                "native_id": _string(link.get("document_id"), "Onshape document identity"),
            },
            "coordinate_system": {
                "length_unit": "mm",
                "handedness": "right",
                "matrix_convention": "row-major-local-to-parent",
                "solver_anchor_instance_id": instance_id,
            },
            "assembly": {
                "file": "assembly.step",
                "n_bodies_expected": observed["n_bodies"],
                "bbox_mm": observed["bbox_mm"],
                "signature_hash": observed["signature_hash"],
            },
            "files": {
                "assembly.step": {
                    "sha256": _sha256(step_bytes),
                    "size_bytes": len(step_bytes),
                    "media_type": "model/step",
                    "purpose": "exterior-assembly",
                }
            },
            "scope": {
                "selection": "linked-onshape-part-studio",
                "included": observed["included"],
                "skipped": [],
                "status": "clean",
                "fem_air_volumes": [],
            },
            "instances": [
                {
                    "instance_id": instance_id,
                    "design_id": _string(design.get("id"), "wglink.design.id"),
                    "lineage_id": design.get("lineage_id"),
                    "edit_version": design.get("edit_version"),
                    "design_hash": design.get("design_hash"),
                    "formula": design.get("formula"),
                    "config": design.get("config"),
                    "export_id": _string(exported.get("id"), "wglink.export.id"),
                    "export_sequence": int(exported["sequence"]),
                    "geometry_hash": exported.get("geometry_hash"),
                    "origin_bundle_id": _string(bundle.get("id"), "wglink.bundle.id"),
                    "occurrence_path": f"Part Studio/{link['part_studio_element_id']}",
                    "build_mode": _string(design.get("build_mode"), "wglink.design.build_mode"),
                    "parameter_prefix": parameter_prefix,
                    "assembly_from_link": [
                        [1, 0, 0, 0],
                        [0, 1, 0, 0],
                        [0, 0, 1, 0],
                        [0, 0, 0, 1],
                    ],
                    "chirality": "original",
                    "body_evidence": {
                        # O3 did not capture a post-translation Onshape baseline.
                        # The exact returned observation is retained, but it is
                        # not promoted to an unmodified verdict.
                        "local_body_state": "unknown",
                        "baseline_fingerprint": baseline,
                        "observed_fingerprint": observed["observed_fingerprint"],
                        "observed_at": created_at,
                    },
                    "source_contract": contract,
                }
            ],
            "sources": [
                {
                    "id": source_policy["id"],
                    "role": source_policy["role"],
                    "instance_id": instance_id,
                    "required": source_policy["required"],
                    "default_drive_channel_id": source_policy[
                        "default_drive_channel_id"
                    ],
                    "patch_policy": source_policy["patch_policy"],
                    "expected_connected_components": source_policy[
                        "expected_connected_components"
                    ],
                    "selectors": {"linked_throat": {"instance_id": instance_id}},
                    "observed": observed["source_observed"],
                    "suggested_resolution_mm": source_policy[
                        "suggested_resolution_mm"
                    ],
                }
            ],
            "acoustics": None,
        }
        (temporary / "wgreturn.json").write_bytes(
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
        )
        if target.exists():
            raise OnshapeReturnError(f"Return id collision at {target.name}.")
        os.replace(temporary, target)
        return target
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def write_and_ingest_return(
    step_bytes: bytes,
    *,
    link: Mapping[str, Any],
    export_row: Mapping[str, Any],
    store: CadLinkStore,
    data_dir: str | Path,
) -> tuple[Path, dict[str, Any]]:
    try:
        outbound = json.loads(str(export_row["manifest_json"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise OnshapeReturnError("The linked export's stored manifest is unreadable.") from exc
    if not isinstance(outbound, Mapping):
        raise OnshapeReturnError("The linked export's stored manifest is not an object.")
    source_policy = _source_policy_from_export(outbound)
    bundle_path = write_return_bundle(
        step_bytes, link=link, export_row=export_row, data_dir=data_dir
    )
    mesh = {
        "rigid_size_mm": float(source_policy["suggested_resolution_mm"]),
        "transition_mm": float(source_policy["suggested_resolution_mm"]),
        "source_size_mm": {
            str(source_policy["id"]): float(source_policy["suggested_resolution_mm"])
        },
    }
    design = _mapping(outbound.get("design"), "wglink.design")
    instance_id = str(link.get("instance_id") or "") or None
    record = ingest_bundle(
        bundle_path,
        mesh,
        [],
        store,
        data_dir,
        expected_design_id=_string(design.get("id"), "wglink.design.id"),
        expected_instance_id=instance_id,
    )
    return bundle_path, record


__all__ = [
    "OnshapeReturnError",
    "RETURN_SUBDIRECTORY",
    "source_contract_from_export",
    "write_and_ingest_return",
    "write_return_bundle",
]
