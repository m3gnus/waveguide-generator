"""Onshape return evidence and immutable bundle conformance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hornlab_mesher import WgLinkIdentity, WgLinkSourceInterface, write_wglink
from hornlab_mesher.config_builder import resolve_geometry
from server.cadlink.onshape.return_leg import (
    _fingerprints_match,
    write_and_ingest_return,
    write_return_bundle,
)
from server.cadlink.store import CadLinkStore
from server.cadlink.wgreturn import WgReturnIntegrityError, read_wgreturn
from server.design.schema import DesignConfig
from server.exports.core import design_to_mesher_config
from server.mesh.gmsh_worker import _run_in_gmsh_session


def _outbound(tmp_path: Path) -> tuple[dict, bytes]:
    design = DesignConfig.model_validate({"formula": "OSSE", "L": 70, "a": 45})
    resolved = resolve_geometry(design_to_mesher_config(design))
    product = write_wglink(
        resolved.geometry,
        tmp_path / "outbound.wglink",
        identity=WgLinkIdentity(
            bundle={"id": "wgb_01K00000000000000000000000", "created_at": "2026-08-13T10:00:00Z"},
            generator={"app": "waveguide-generator", "app_version": "test", "mesher_version": "test", "datum_schema": 1},
            design={
                "id": "wgd_01K00000000000000000000000",
                "lineage_id": "wgl_01K00000000000000000000000",
                "edit_version": 1,
                "design_hash": "sha256:" + "a" * 64,
                "name": "demo",
                "formula": "osse",
                "config": design.model_dump(mode="json", by_alias=True),
                "build_mode": resolved.mode,
            },
            export={
                "id": "wge_01K00000000000000000000000",
                "sequence": 1,
                "parent_export_id": None,
                "geometry_hash": "sha256:" + "b" * 64,
                "domain": "full",
                "open_throat": True,
            },
        ),
        instance_slug="demo",
        open_throat=True,
        interface_sources=[
            WgLinkSourceInterface(
                id="source-hf",
                role="HF",
                required=True,
                default_drive_channel_id="drive-hf",
                patch_policy="single-connected",
                expected_connected_components=1,
                suggested_resolution_mm=4.0,
            )
        ],
    )
    def source_bearing_step(path: Path) -> bytes:
        import gmsh

        gmsh.clear()
        gmsh.model.add("source-bearing-return")
        gmsh.model.occ.importShapes(str(product.step_path), highestDimOnly=True)
        throat = product.manifest["datums"]["WG_THROAT_PLANE"]["origin_mm"]
        diameter = next(
            item["value"]
            for item in product.manifest["parameters"]
            if item["name"].endswith("_throat_dia")
        )
        gmsh.model.occ.addDisk(*throat, diameter / 2.0, diameter / 2.0)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        return path.read_bytes()

    # The O3 import intentionally exports an open throat and therefore does
    # not itself pass the source-evidence gate. This synthetic edited Part
    # Studio adds the required membrane face and proves the success contract;
    # a separate test below pins the honest refusal for the shipping O3 body.
    step = _run_in_gmsh_session(source_bearing_step, tmp_path / "source-bearing.step")
    return product.manifest, step


def test_fingerprint_match_allows_measured_onshape_translation_noise() -> None:
    baseline = {
        "is_solid": True,
        "volume_mm3": 128665.85329932554,
        "bbox_mm": [
            -77.72616306984958,
            -77.72616306984958,
            -5.005582703529123,
            77.72616306984958,
            77.72616306984958,
            70.00558270352913,
        ],
    }
    onshape = {
        "is_solid": True,
        "volume_mm3": 128668.85601470468,
        "bbox_mm": [
            -77.7279079509932,
            -77.7279079509932,
            -5.007327584673205,
            77.7279079509932,
            77.7279079509932,
            70.0073275846732,
        ],
    }

    assert _fingerprints_match(baseline, onshape)


def test_fingerprint_match_still_rejects_material_volume_or_bounds_changes() -> None:
    baseline = {
        "is_solid": True,
        "volume_mm3": 128665.0,
        "bbox_mm": [-77.7, -77.7, -5.0, 77.7, 77.7, 70.0],
    }

    assert not _fingerprints_match(
        baseline,
        {**baseline, "volume_mm3": 128675.0},
    )
    assert not _fingerprints_match(
        baseline,
        {**baseline, "bbox_mm": [-77.72, -77.7, -5.0, 77.7, 77.7, 70.0]},
    )


def test_shipping_wglink_refuses_missing_source_policy(tmp_path: Path) -> None:
    outbound, step = _outbound(tmp_path)
    outbound["required_features"].remove("source-interface-v1")
    outbound["interface"] = {"sources": []}
    with pytest.raises(ValueError, match="no WG-authored return-source interface"):
        _run_in_gmsh_session(
            write_return_bundle,
            step,
            link={
                "document_id": "DID",
                "workspace_id": "WID",
                "part_studio_element_id": "PART",
                "document_name": "Demo Horn",
            },
            export_row={"manifest_json": json.dumps(outbound)},
            data_dir=tmp_path / "blocked-policy-data",
        )


def test_onshape_return_preserves_identity_source_evidence_and_checksums(tmp_path: Path) -> None:
    outbound, step = _outbound(tmp_path)
    link = {
        "document_id": "DID",
        "workspace_id": "WID",
        "part_studio_element_id": "PART",
        "document_name": "Demo Horn",
    }
    bundle_path = _run_in_gmsh_session(
        write_return_bundle,
        step,
        link=link,
        export_row={"manifest_json": json.dumps(outbound)},
        data_dir=tmp_path / "data",
    )
    bundle = read_wgreturn(bundle_path)

    manifest = bundle.manifest
    instance = manifest["instances"][0]
    source = manifest["sources"][0]
    assert instance["instance_id"] == "onshape-PART"
    assert instance["design_id"] == outbound["design"]["id"]
    assert instance["export_id"] == outbound["export"]["id"]
    assert instance["origin_bundle_id"] == outbound["bundle"]["id"]
    assert instance["body_evidence"]["local_body_state"] == "unknown"
    assert manifest["assembly"]["n_bodies_expected"] == 2
    assert {item["body_kind"] for item in manifest["scope"]["included"]} == {
        "solid",
        "surface",
    }
    assert source["selectors"]["linked_throat"]["instance_id"] == "onshape-PART"
    assert source["observed"]["face_count"] == 1
    assert source["observed"]["total_area_mm2"] == pytest.approx(
        instance["source_contract"]["expected_disc_area_mm2"], rel=0.01
    )
    assert bundle.artifact_sha256 == manifest["files"]["assembly.step"]["sha256"]

    (bundle_path / "assembly.step").write_bytes(step + b"\nTAMPER")
    with pytest.raises(WgReturnIntegrityError, match="size mismatch|checksum mismatch"):
        read_wgreturn(bundle_path)


def test_source_bearing_return_passes_the_existing_ingest_pipeline(tmp_path: Path) -> None:
    outbound, step = _outbound(tmp_path)
    bundle_path, record = _run_in_gmsh_session(
        write_and_ingest_return,
        step,
        link={
            "document_id": "DID",
            "workspace_id": "WID",
            "part_studio_element_id": "PART",
            "document_name": "Demo Horn",
        },
        export_row={"manifest_json": json.dumps(outbound)},
        store=CadLinkStore(tmp_path / "cadlink.db"),
        data_dir=tmp_path / "data",
    )

    assert bundle_path.is_dir()
    assert record["return_id"] == read_wgreturn(bundle_path).manifest["return"]["id"]
    assert record["sources"][0]["id"] == "source-hf"
    assert record["mesh_sizes"]["source_size_mm"] == {"source-hf": 4.0}


def test_shipping_o3_open_throat_refuses_instead_of_inventing_source_evidence(tmp_path: Path) -> None:
    outbound, _source_bearing_step = _outbound(tmp_path)
    open_step = (tmp_path / "outbound.wglink" / "waveguide.step").read_bytes()
    with pytest.raises(ValueError, match="resolved the required linked throat to 0 faces"):
        _run_in_gmsh_session(
            write_return_bundle,
            open_step,
            link={
                "document_id": "DID",
                "workspace_id": "WID",
                "part_studio_element_id": "PART",
                "document_name": "Demo Horn",
            },
            export_row={"manifest_json": json.dumps(outbound)},
            data_dir=tmp_path / "blocked-data",
        )
