"""Build a staged bundle in the killable mesher child; never publish it."""

from pathlib import Path
import hashlib
import json
from typing import Any
from server.design.schema import DesignConfig
from server.design.conventions import artifact_conventions
from server.preview.translate import design_to_mesher_config
from .cad_geometry import resolve_cad_geometry, write_cad_step
from .core import _assert_step
from .geometry_identity import geometry_hash as _geometry_hash, mesher_version as _mesher_version


#: The solid STEP an Onshape bundle carries (``build_bundle(include_step=True)``).
ONSHAPE_STEP_FILE = "waveguide.step"


def build_bundle(
    design_dump: dict[str, Any],
    staged_path: str,
    facts: dict[str, Any],
    lineage_id: str,
    design_name: str,
    instance_slug: str,
    app_version: str,
    include_step: bool = False,
    *, cancel_cb: Any = None,
) -> None:
    """Write the bundle: the CAD geometry's point grid, plus the solid STEP
    only when ``include_step`` (the Onshape import, which uploads it).

    Fusion lofts the grid and never reads a STEP, so a Fusion send writes none
    (bundle format 1.2). The STEP an Onshape bundle carries is the File -> STEP
    solid of the same geometry, recorded in the manifest's ``step`` section
    with the body evidence the Onshape return leg identifies its body by.
    """
    if cancel_cb is not None:
        cancel_cb()
    from hornlab_mesher import WgLinkIdentity, WgLinkSourceInterface, write_wglink
    from hornlab_mesher.config_builder import resolve_geometry

    design = DesignConfig.model_validate(design_dump)
    # The design's identity resolve: its geometry hash is what CAD-return
    # freshness recomputes (geometry_hash_for_design), and its density is the
    # design's own source resolution. Neither depends on how CAD samples it.
    resolved = resolve_geometry(design_to_mesher_config(design))
    mesher_version = _mesher_version()
    geometry_hash = _geometry_hash(resolved.geometry, mesher_version)
    # The body Fusion lofts: the same CAD geometry File -> STEP writes.
    cad = resolve_cad_geometry(design)
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
            # The point grid is the realized geometry; this is the exact
            # WG config that produced it. WGLink persists the
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
        cad.resolved.geometry,
        staged,
        identity=bundle_identity,
        instance_slug=instance_slug,
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
        # How the grid was sampled, and any compromise the planner reports;
        # wglink_service returns the warning with the send.
        "cad_sampling": cad.sampling_record(),
    }
    if include_step:
        step_path = staged / ONSHAPE_STEP_FILE
        info = write_cad_step(cad, step_path)
        _assert_step(step_path.read_text(encoding="utf-8", errors="replace"))
        data = step_path.read_bytes()
        manifest["files"] = {
            **manifest["files"],
            ONSHAPE_STEP_FILE: {
                "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "media_type": "model/step",
            },
        }
        manifest["step"] = {
            "file": ONSHAPE_STEP_FILE,
            "kind": info.body,
            "n_faces": info.n_faces,
            "volume_mm3": info.volume_mm3,
            "bbox_mm": [list(corner) for corner in info.bounding_box_mm],
            "throat_opened": info.throat_opened,
        }
    product.manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
