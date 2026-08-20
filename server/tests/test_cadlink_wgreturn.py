"""Strict CAD-return bundle reader contracts."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from server.cadlink.fusion_status import FUSION_STATUS_FILENAME, read_fusion_status
from server.cadlink.limits import MAX_STEP_INPUT_BYTES
from server.cadlink.wgreturn import WgReturnError, read_wgreturn, validate_manifest


def _manifest(step: bytes) -> dict:
    instance = "instance-1"
    return {
        "wgreturn_version": "1.0",
        "required_features": ["checksummed-files-v1", "assembly-frame-v1", "instance-records-v1"],
        "return": {"id": "wgr_01J5A8QK3M9T2XVBH0RD7NWE6C", "created_at": "2026-08-12T09:14:03Z"},
        "generator": {"adapter": "hornlab-fusion-addin/WGLink", "adapter_version": "1.0.0", "cad_app": "fusion360", "cad_version": "2704.1.53"},
        "document": {"name": "Tritonia speaker", "native_id": None},
        "coordinate_system": {"length_unit": "mm", "handedness": "right", "matrix_convention": "row-major-local-to-parent", "solver_anchor_instance_id": instance},
        "assembly": {"file": "assembly.step", "n_bodies_expected": 1, "bbox_mm": [[-172, -427, -185.23], [172, 152, 94.77]]},
        "files": {"assembly.step": {"sha256": "sha256:" + hashlib.sha256(step).hexdigest(), "size_bytes": len(step), "media_type": "model/step", "purpose": "exterior-assembly"}},
        "scope": {"selection": "root", "included": [{"object_id": "speaker", "name": "speaker", "body_kind": "surface", "visible": True, "external_reference": "local", "wglink_instance_id": instance}], "skipped": [{"object_id": "construction", "kind": "construction", "severity": "info", "reason": "not geometry"}], "fem_air_volumes": [], "status": "clean"},
        "instances": [{
            "instance_id": instance, "design_id": "wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M", "lineage_id": "wgl_01J4Y2WZQK8Z3TFD3E7V9XKQ4M", "edit_version": 19,
            "design_hash": "sha256:" + "1" * 64, "export_id": "wge_01J4Y2ZD000000000000000000", "export_sequence": 7,
            "formula": "osse", "config": {"root": {"formula": "OSSE"}, "dimensions": {"length": {"raw": "130", "value": 130}}, "extra_keys": {"Symmetry": "1234", "Tag": "preserved WG input"}},
            "geometry_hash": "sha256:" + "2" * 64, "origin_bundle_id": "wgb_01J4Y2ZF000000000000000000", "build_mode": "enclosure", "parameter_prefix": "wg_tritonia_", "occurrence_path": "Speaker/WGLink",
            "assembly_from_link": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "chirality": "original",
            "body_evidence": {"local_body_state": "unmodified", "baseline_fingerprint": {"is_solid": True, "volume_mm3": 12, "bbox_mm": [0, 0, 0, 1, 1, 1]}, "observed_fingerprint": {"is_solid": True, "volume_mm3": 12, "bbox_mm": [0, 0, 0, 1, 1, 1]}, "observed_at": "2026-08-12T09:14:03Z"},
            "source_contract": {"role": "HF", "throat_z_mm": 0, "throat_plane_link": {"origin_mm": [0, 0, 0], "normal": [0, 0, 1]}, "axis_link": {"origin_mm": [0, 0, 0], "direction": [0, 0, 1]}, "throat_diameter_mm": 25.4, "expected_disc_area_mm2": 506.707},
        }],
        "sources": [{"id": "source-hf", "role": "HF", "instance_id": instance, "required": True, "default_drive_channel_id": "drive-hf", "patch_policy": "single-connected", "expected_connected_components": 1, "selectors": {"linked_throat": {"instance_id": instance}, "appearance_labels": ["HF"]}, "observed": {"face_count": 1, "total_area_mm2": 506.696, "per_face_area_mm2": [506.696], "bodies": ["speaker"]}, "suggested_resolution_mm": 4}],
        "acoustics": None,
    }


def write_bundle(root: Path, manifest: dict | None = None, *, step: bytes = b"STEP") -> Path:
    bundle = root / "fixture.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "assembly.step").write_bytes(step)
    payload = manifest or _manifest(step)
    (bundle / "wgreturn.json").write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")
    return bundle


def test_worked_example_shape_and_real_member_table_validate(tmp_path: Path) -> None:
    result = read_wgreturn(write_bundle(tmp_path))
    assert result.manifest["sources"][0]["id"] == "source-hf"
    assert result.manifest["instances"][0]["config"]["dimensions"]["length"]["raw"] == "130"
    assert result.artifact_sha256.startswith("sha256:")
    assert result.degradations == (
        "stale detection unavailable: this returned bundle predates wgreturn 1.1 "
        "and carries no document signature",
    )


def test_reader_streams_step_hashing_and_refuses_over_limit_before_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = write_bundle(tmp_path / "streamed")
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path.name == "assembly.step":
            raise AssertionError("STEP hashing must not use Path.read_bytes()")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    assert read_wgreturn(bundle).artifact_size_bytes == 4

    oversized = write_bundle(tmp_path / "oversized")
    assembly = oversized / "assembly.step"
    with assembly.open("r+b") as handle:
        handle.truncate(MAX_STEP_INPUT_BYTES + 1)
    manifest_path = oversized / "wgreturn.json"
    manifest = json.loads(original_read_bytes(manifest_path))
    manifest["files"]["assembly.step"].update(
        size_bytes=MAX_STEP_INPUT_BYTES + 1,
        sha256="sha256:" + "0" * 64,
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(WgReturnError, match="limit for one STEP input"):
        read_wgreturn(oversized)


def test_signature_hash_contract_tracks_wgreturn_minor_version(tmp_path: Path) -> None:
    missing = _manifest(b"STEP")
    missing["wgreturn_version"] = "1.1"
    with pytest.raises(WgReturnError, match=r"\$\.assembly\.signature_hash: is required"):
        read_wgreturn(write_bundle(tmp_path / "missing", missing))

    current = _manifest(b"STEP")
    current["wgreturn_version"] = "1.1"
    current["assembly"]["signature_hash"] = "sha256:document"
    assert read_wgreturn(write_bundle(tmp_path / "current", current)).degradations == ()

    legacy = _manifest(b"STEP")
    legacy["assembly"]["signature_hash"] = "sha256:document"
    assert read_wgreturn(write_bundle(tmp_path / "legacy", legacy)).degradations == ()


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda value: value.update(wgreturn_version="2.0"), "unsupported major"),
        (lambda value: value["required_features"].append("future-physics-v1"), "unknown required feature"),
        (lambda value: value["instances"][0].update(chirality="mirrored"), "chirality"),
        (lambda value: value.update(acoustics={"driver": "x"}), "acoustics"),
        (lambda value: value["instances"][0]["assembly_from_link"].__setitem__(3, [0, 0, 1, 1]), "last row"),
        (lambda value: value["scope"].update(status="degraded"), "iff"),
        (lambda value: value["sources"][0].update(metadata={"nested": {"freshness": "current"}}), "forbidden"),
    ],
)
def test_manifest_rejection_corpus(tmp_path: Path, mutate, match: str) -> None:
    manifest = deepcopy(_manifest(b"STEP"))
    mutate(manifest)
    with pytest.raises(WgReturnError, match=match):
        read_wgreturn(write_bundle(tmp_path, manifest))


def test_member_integrity_rejects_checksum_undeclared_and_paths(tmp_path: Path) -> None:
    manifest = _manifest(b"STEP")
    manifest["files"]["assembly.step"]["sha256"] = "sha256:" + "0" * 64
    with pytest.raises(WgReturnError, match="assembly.step.*checksum"):
        read_wgreturn(write_bundle(tmp_path / "one", manifest))

    bundle = write_bundle(tmp_path / "two")
    (bundle / "extra.bin").write_bytes(b"x")
    with pytest.raises(WgReturnError, match="undeclared.*extra.bin"):
        read_wgreturn(bundle)

    manifest = _manifest(b"STEP")
    manifest["files"]["../assembly.step"] = manifest["files"].pop("assembly.step")
    manifest["assembly"]["file"] = "../assembly.step"
    with pytest.raises(WgReturnError, match="path segments"):
        read_wgreturn(write_bundle(tmp_path / "three", manifest))


def test_nonfinite_json_is_refused_at_its_path(tmp_path: Path) -> None:
    manifest = _manifest(b"STEP")
    manifest["assembly"]["bbox_mm"][0][0] = float("nan")
    with pytest.raises(WgReturnError, match="non-finite JSON number"):
        read_wgreturn(write_bundle(tmp_path, manifest))


def test_only_the_instances_own_config_is_opaque_to_evidence_validation(
    tmp_path: Path,
) -> None:
    verdict = _manifest(b"STEP")
    verdict["instances"][0]["body_evidence"]["config"] = {
        "tags": ["solver-authored"]
    }
    with pytest.raises(WgReturnError, match=r"body_evidence\.config\.tags.*forbidden"):
        read_wgreturn(write_bundle(tmp_path / "verdict", verdict))

    nonfinite = _manifest(b"STEP")
    nonfinite["instances"][0]["body_evidence"]["config"] = {
        "measurement": float("inf")
    }
    with pytest.raises(WgReturnError, match=r"body_evidence\.config\.measurement.*finite"):
        validate_manifest(nonfinite)


def test_subtree_return_disables_root_document_stale_comparisons(tmp_path: Path) -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    workspace = tmp_path / "workspace"
    status_folder = workspace / "ipc" / "wglink"
    status_folder.mkdir(parents=True)
    (status_folder / FUSION_STATUS_FILENAME).write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "cadApplication": "fusion360",
                "updatedAt": now.isoformat().replace("+00:00", "Z"),
                "document": {
                    "name": "Scoped assembly",
                    "id": "fusion:doc-a",
                    "links": [
                        {
                            "instanceId": "instance-1",
                            "designId": "wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M",
                            "designHash": "sha256:current",
                            "configPresent": True,
                            "parameterDriftCount": 0,
                            "localBodyState": "unmodified",
                            "documentSignatureHash": "sha256:root-document",
                            "documentBodyCount": 9,
                            "sourceStateHash": "sha256:root-sources",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = _manifest(b"STEP")
    manifest["scope"]["selection"] = "Speaker/Waveguide"
    manifest["wgreturn_version"] = "1.1"
    manifest["assembly"]["signature_hash"] = "sha256:subtree-document"
    returned = write_bundle(workspace / "returns", manifest)

    result = read_fusion_status(
        workspace,
        current_design_hash="sha256:current",
        current_formula="OSSE",
        design_id="wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M",
        returned_bundle=returned,
        now=now,
    )

    assert result["state"] == "current"
    assert result["fusionChangesAvailable"] is False
    assert result["documentChanged"] is False
    assert result["documentChangeDetectable"] is False
    # A scoped return is not a legacy one; saying so would send the user
    # looking for a wgreturn version problem they do not have.
    assert result["staleDetectionExplanation"] == (
        "stale detection unavailable: this return covers a selected assembly "
        "subtree, and Fusion reports its document signature for the whole root"
    )


def test_reader_rejects_symlink_missing_size_and_absolute_members(tmp_path: Path) -> None:
    symlink_bundle = write_bundle(tmp_path / "symlink")
    (symlink_bundle / "linked.bin").symlink_to(symlink_bundle / "assembly.step")
    with pytest.raises(WgReturnError, match="symlink"):
        read_wgreturn(symlink_bundle)

    missing_bundle = write_bundle(tmp_path / "missing")
    (missing_bundle / "assembly.step").unlink()
    with pytest.raises(WgReturnError, match="declared bundle member is missing"):
        read_wgreturn(missing_bundle)

    wrong_size = _manifest(b"STEP")
    wrong_size["files"]["assembly.step"]["size_bytes"] += 1
    with pytest.raises(WgReturnError, match="size mismatch"):
        read_wgreturn(write_bundle(tmp_path / "size", wrong_size))

    absolute = _manifest(b"STEP")
    absolute["files"]["/assembly.step"] = absolute["files"].pop("assembly.step")
    absolute["assembly"]["file"] = "/assembly.step"
    with pytest.raises(WgReturnError, match="relative bundle member"):
        read_wgreturn(write_bundle(tmp_path / "absolute", absolute))


def test_reader_rejects_duplicate_portable_names_and_clean_degraded_scope(tmp_path: Path) -> None:
    duplicate = _manifest(b"STEP")
    duplicate["files"]["ASSEMBLY.STEP"] = dict(duplicate["files"]["assembly.step"])
    with pytest.raises(WgReturnError, match="duplicate normalized member names"):
        read_wgreturn(write_bundle(tmp_path / "duplicate", duplicate))

    degraded = _manifest(b"STEP")
    degraded["scope"]["skipped"].append(
        {
            "object_id": "hidden-body",
            "kind": "hidden",
            "severity": "degraded",
            "reason": "hidden by user",
        }
    )
    with pytest.raises(WgReturnError, match="degraded.*iff"):
        read_wgreturn(write_bundle(tmp_path / "degraded", degraded))


def test_reader_enforces_fem_member_feature_and_purpose_coupling(tmp_path: Path) -> None:
    fem = b"FEM STEP"
    manifest = _manifest(b"STEP")
    manifest["files"]["fem/air.step"] = {
        "sha256": "sha256:" + hashlib.sha256(fem).hexdigest(),
        "size_bytes": len(fem),
        "media_type": "model/step",
        "purpose": "fem-air-volume",
    }
    bundle = write_bundle(tmp_path / "unreferenced", manifest)
    (bundle / "fem").mkdir()
    (bundle / "fem" / "air.step").write_bytes(fem)
    with pytest.raises(WgReturnError, match="unreferenced FEM"):
        read_wgreturn(bundle)

    manifest = _manifest(b"STEP")
    manifest["files"]["fem/air.step"] = {
        "sha256": "sha256:" + hashlib.sha256(fem).hexdigest(),
        "size_bytes": len(fem),
        "media_type": "model/step",
        "purpose": "fem-air-volume",
    }
    manifest["scope"]["fem_air_volumes"] = [
        {"file": "fem/air.step", "n_solids_expected": 1}
    ]
    bundle = write_bundle(tmp_path / "feature", manifest)
    (bundle / "fem").mkdir()
    (bundle / "fem" / "air.step").write_bytes(fem)
    with pytest.raises(WgReturnError, match="fem-air-volume-v1 is required"):
        read_wgreturn(bundle)


def test_reader_accepts_addin_fem_expected_body_spelling(tmp_path: Path) -> None:
    fem = b"FEM STEP"
    manifest = _manifest(b"STEP")
    manifest["required_features"].append("fem-air-volume-v1")
    manifest["files"]["fem/air.step"] = {
        "sha256": "sha256:" + hashlib.sha256(fem).hexdigest(),
        "size_bytes": len(fem),
        "media_type": "model/step",
        "purpose": "fem-air-volume",
    }
    manifest["scope"]["fem_air_volumes"] = [
        {"file": "fem/air.step", "n_bodies_expected": 1}
    ]
    bundle = write_bundle(tmp_path, manifest)
    (bundle / "fem").mkdir()
    (bundle / "fem" / "air.step").write_bytes(fem)

    result = read_wgreturn(bundle)

    assert result.manifest["scope"]["fem_air_volumes"][0]["n_bodies_expected"] == 1


def test_reader_accepts_addin_surface_body_fingerprint(tmp_path: Path) -> None:
    manifest = _manifest(b"STEP")
    surface_fingerprint = {
        "is_solid": False,
        "volume_mm3": None,
        "bbox_mm": [0, 0, 0, 1, 1, 1],
    }
    evidence = manifest["instances"][0]["body_evidence"]
    evidence["baseline_fingerprint"] = deepcopy(surface_fingerprint)
    evidence["observed_fingerprint"] = deepcopy(surface_fingerprint)

    result = read_wgreturn(write_bundle(tmp_path, manifest))

    fingerprints = result.manifest["instances"][0]["body_evidence"]
    assert fingerprints["baseline_fingerprint"]["volume_mm3"] is None
    assert fingerprints["observed_fingerprint"]["volume_mm3"] is None


def test_reader_enforces_disc_area_and_anchor_rules(tmp_path: Path) -> None:
    area = _manifest(b"STEP")
    area["instances"][0]["source_contract"]["expected_disc_area_mm2"] *= 2
    with pytest.raises(WgReturnError, match=r"pi\*throat_diameter"):
        read_wgreturn(write_bundle(tmp_path / "area", area))

    multiple = _manifest(b"STEP")
    second = deepcopy(multiple["instances"][0])
    second["instance_id"] = "instance-2"
    multiple["instances"].append(second)
    multiple["coordinate_system"]["solver_anchor_instance_id"] = None
    with pytest.raises(WgReturnError, match="required and must name"):
        read_wgreturn(write_bundle(tmp_path / "missing-anchor", multiple))

    unknown = _manifest(b"STEP")
    unknown["coordinate_system"]["solver_anchor_instance_id"] = "unknown"
    with pytest.raises(WgReturnError, match="must name the sole instance"):
        read_wgreturn(write_bundle(tmp_path / "unknown-anchor", unknown))


def test_an_empty_signature_hash_is_refused_like_a_missing_one(tmp_path: Path) -> None:
    """A blank hash compares equal to nothing and would fail open.

    ``_string`` already rejects it, so this pins existing behaviour rather than
    adding any: a later "just check the key is present" refactor would
    reintroduce exactly the silence the version bump exists to remove.
    """

    blank = _manifest(b"STEP")
    blank["wgreturn_version"] = "1.1"
    blank["assembly"]["signature_hash"] = ""
    with pytest.raises(WgReturnError, match=r"\$\.assembly\.signature_hash: must be a non-empty string"):
        read_wgreturn(write_bundle(tmp_path / "blank", blank))


def test_the_degradation_reaches_ingestion_findings_not_just_the_bundle() -> None:
    """Recording a degradation nobody reads is still a silent degradation.

    The bundle carries it, but the ingestion record is what a user sees, so
    surfacing it as a finding is the part that actually closes the gap.
    """

    from server.cadlink import ingest

    source = Path(ingest.__file__).read_text(encoding="utf-8")
    assert "stale-detection-unavailable" in source
    assert "degradations" in source


def test_a_captured_cad_document_is_a_valid_bundle_member(tmp_path: Path) -> None:
    """The strict reader refuses undeclared members, so the capture must declare.

    A Fusion archive travels beside the STEP and is not solver input; this is
    the contract that lets WG file it in the run archive without the reader
    rejecting the bundle it arrived in.
    """

    step = b"STEP"
    document = b"fusion-archive-bytes"
    manifest = _manifest(step)
    manifest["files"]["document.f3d"] = {
        "sha256": "sha256:" + hashlib.sha256(document).hexdigest(),
        "size_bytes": len(document),
        "media_type": "application/vnd.autodesk.fusion360",
        "purpose": "cad-document",
    }
    bundle = write_bundle(tmp_path, manifest, step=step)
    (bundle / "document.f3d").write_bytes(document)

    result = read_wgreturn(bundle)

    assert result.manifest["files"]["document.f3d"]["purpose"] == "cad-document"
    # It is not geometry: the assembly the mesher reads is unchanged.
    assert result.manifest["assembly"]["file"] == "assembly.step"


def test_a_captured_document_whose_bytes_changed_is_refused_like_any_member(
    tmp_path: Path,
) -> None:
    document = b"fusion-archive-bytes"
    manifest = _manifest(b"STEP")
    manifest["files"]["document.f3d"] = {
        "sha256": "sha256:" + hashlib.sha256(document).hexdigest(),
        "size_bytes": len(document),
        "media_type": "application/vnd.autodesk.fusion360",
        "purpose": "cad-document",
    }
    bundle = write_bundle(tmp_path, manifest)
    (bundle / "document.f3d").write_bytes(document + b"tampered")

    with pytest.raises(Exception, match="document.f3d"):
        read_wgreturn(bundle)


def test_an_oversized_manifest_is_refused_before_it_is_parsed(tmp_path: Path) -> None:
    """``docs/plans/STEP-PARSER-ISOLATION.md``: wgreturn.json is capped at 1 MiB.

    The manifest is CAD-authored, so its size decides an allocation in this
    process. It is checked from ``stat()`` -- the file is never read, let alone
    handed to ``json.loads`` -- which is what makes the cap a refusal rather
    than a report.
    """

    from server.cadlink.limits import MAX_WGRETURN_JSON_BYTES

    assert MAX_WGRETURN_JSON_BYTES == 1024 * 1024
    bundle = write_bundle(tmp_path)
    manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
    manifest["padding"] = "p" * (MAX_WGRETURN_JSON_BYTES + 1)
    (bundle / "wgreturn.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(WgReturnError, match="byte limit for a return manifest"):
        read_wgreturn(bundle)
