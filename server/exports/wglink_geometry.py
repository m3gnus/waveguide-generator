"""Build a staged bundle in the killable mesher child; never publish it."""

from pathlib import Path
import json
from typing import Any
from server.design.schema import DesignConfig
from server.design.conventions import artifact_conventions
from server.preview.geometry_identity import resolve_design_identity
from .cad_geometry import remember_solid_built, resolve_cad_geometry, solid_known_to_build, write_cad_step
from .core import _assert_step
from .geometry_identity import mesher_version as _mesher_version
from .wglink_publication import bundle_file_record


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

    design = DesignConfig.model_validate(design_dump)
    # The design's identity resolve: its geometry hash is what CAD-return
    # freshness recomputes (geometry_hash_for_design), and its density is the
    # design's own source resolution. Neither depends on how CAD samples it.
    mesher_version = _mesher_version()
    resolved, geometry_hash = resolve_design_identity(design, mesher_version_value=mesher_version)
    # The body Fusion lofts: the same CAD geometry File -> STEP writes.
    # Onshape (``include_step``) keeps the placed frame its existing
    # documents were built in; Fusion gets the throat at the origin.
    cad = resolve_cad_geometry(design, placed=include_step)
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
            # The grid's own mode: WGLink refuses a grid whose mode differs.
            "build_mode": cad.resolved.mode,
        },
        export={
            "id": facts["exportId"],
            "sequence": facts["sequence"],
            "parent_export_id": facts["parentExportId"],
            "geometry_hash": geometry_hash,
            "domain": "full",
            # Also part of geometry_hash's canonical input (geometry_identity);
            # it states the solid the bundle stands for.
            "open_throat": True,
        },
    )
    # The solid must be buildable, or Send would hand Fusion a part that File
    # -> STEP refuses. The check is the STEP writer's own OCC build, skipped
    # when this child already built this exact geometry (a File -> STEP or a
    # Send of the unchanged design), and when an Onshape bundle writes the
    # STEP itself below.
    verify_solid = not (include_step or solid_known_to_build(cad))
    product = write_wglink(
        cad.resolved.geometry,
        staged,
        identity=bundle_identity,
        instance_slug=instance_slug,
        open_throat=True,
        verify_solid=verify_solid,
        # The current design schema owns exactly one horn throat. Give
        # it an explicit acoustic identity at export time so the
        # return leg never guesses HF from anonymous CAD geometry.
        link_frame=not cad.placed,
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
        manifest["files"] = {
            **manifest["files"],
            ONSHAPE_STEP_FILE: bundle_file_record(step_path, "model/step"),
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
    remember_solid_built(cad)
