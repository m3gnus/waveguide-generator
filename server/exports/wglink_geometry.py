"""Build a staged bundle in the killable mesher child; never publish it."""

from pathlib import Path
import json
from typing import Any
from server.design.schema import DesignConfig
from server.design.conventions import artifact_conventions
from server.preview.translate import design_to_mesher_config
from .geometry_identity import geometry_hash as _geometry_hash, mesher_version as _mesher_version


def build_bundle(
    design_dump: dict[str, Any],
    staged_path: str,
    facts: dict[str, Any],
    lineage_id: str,
    design_name: str,
    instance_slug: str,
    app_version: str,
) -> None:
    from hornlab_mesher import WgLinkIdentity, WgLinkSourceInterface, write_wglink
    from hornlab_mesher.config_builder import resolve_geometry

    design = DesignConfig.model_validate(design_dump)
    resolved = resolve_geometry(design_to_mesher_config(design))
    mesher_version = _mesher_version()
    geometry_hash = _geometry_hash(resolved.geometry, mesher_version)
    staged = Path(staged_path)
    bundle_identity = WgLinkIdentity(
        bundle={"id": facts["bundleId"], "created_at": facts["createdAt"]},
        generator={
            "app": "waveguide-generator",
            "app_version": app_version,
            "mesher_version": mesher_version,
            "datum_schema": 1,
        },
        design={
            "id": facts["designId"],
            "lineage_id": lineage_id,
            "edit_version": facts["editVersion"],
            "design_hash": facts["designHash"],
            "name": design_name,
            "formula": design.root.formula.lower(),
            # The STEP and point grid are the realized geometry; this is
            # the exact WG config that produced them. WGLink persists the
            # snapshot with the managed Fusion instance so future updates,
            # audits, and round trips do not have to reverse-engineer a
            # formula from the body or from the smaller CAD parameter set.
            "config": design.model_dump(mode="json", by_alias=True),
            "build_mode": resolved.mode,
        },
        export={
            "id": facts["exportId"],
            "sequence": facts["sequence"],
            "parent_export_id": facts["parentExportId"],
            "geometry_hash": geometry_hash,
            "domain": "full",
            "open_throat": True,
        },
    )
    product = write_wglink(
        resolved.geometry,
        staged,
        identity=bundle_identity,
        instance_slug=instance_slug,
        open_throat=True,
        # The current design schema owns exactly one horn throat. Give
        # it an explicit acoustic identity at export time so the
        # return leg never guesses HF from anonymous CAD geometry.
        interface_sources=[
            WgLinkSourceInterface(
                id="source-hf",
                role="HF",
                required=True,
                default_drive_channel_id="drive-hf",
                patch_policy="single-connected",
                expected_connected_components=1,
                suggested_resolution_mm=float(resolved.density.throat_res_mm),
            )
        ],
    )
    # Optional top-level metadata is additive under WGLink 1.x, so this
    # does not require a wglink_version or required_features change.
    manifest = {
        **product.manifest,
        "conventions": artifact_conventions(),
    }
    product.manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
