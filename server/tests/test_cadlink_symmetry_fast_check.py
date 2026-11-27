"""Stage 4b in WG: the mesher's fast mirror check, its small-feature check, the
stage-4 replay rules, and mirror verdicts kept per geometry across mesh sizes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from server.cadlink.intake.ingest import ingest_bundle
from server.cadlink.store import CadLinkStore
from server.mesh.gmsh_worker import _run_in_gmsh_session
from test_cadlink_ingest_symmetry import _SIZES, _horn_bundle


def _ingest(tmp_path: Path, bundle: Path, sizes: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    data_dir = tmp_path / "data"
    return _run_in_gmsh_session(
        ingest_bundle, bundle, sizes, [], CadLinkStore(data_dir / "cadlink.db"), data_dir,
        prep_options={"symmetry_mode": "auto"},
        expected_design_id="wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M", defer_viewport=True, **kwargs,
    )


def test_a_remesh_at_new_sizes_reuses_the_mirror_verdicts(tmp_path: Path) -> None:
    """The mirror test reads the geometry, not the mesh sizes: a second
    preparation at new sizes finds its verdicts and does not test again."""

    pytest.importorskip("gmsh")
    bundle = _horn_bundle(tmp_path, "round")
    first = _ingest(tmp_path, bundle, _SIZES)
    assert first["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert "reused_verdict_planes" not in first["symmetry"]
    assert "symmetry_verdict_cache" not in first
    stored = list((tmp_path / "data").rglob("symmetry-verdicts/*.json"))
    assert len(stored) == 1

    finer = {**_SIZES, "rigid_size_mm": 15}
    second = _ingest(tmp_path, bundle, finer)
    assert second["symmetry"]["reused_verdict_planes"] == ["x0", "y0"]
    assert second["symmetry"]["planes"] == first["symmetry"]["planes"]
    assert second["symmetry"]["cut_planes"] == first["symmetry"]["cut_planes"]
    assert second["symmetry_verification"]["verified"] is True
    assert second["mesh_store_path"] != first["mesh_store_path"]


def test_a_corrupt_verdict_store_is_only_a_miss(tmp_path: Path) -> None:
    pytest.importorskip("gmsh")
    bundle = _horn_bundle(tmp_path, "round")
    first = _ingest(tmp_path, bundle, _SIZES)
    for path in (tmp_path / "data").rglob("symmetry-verdicts/*.json"):
        path.write_text("{not json", encoding="utf-8")
    again = _ingest(tmp_path, bundle, {**_SIZES, "rigid_size_mm": 15})
    assert "reused_verdict_planes" not in again["symmetry"]
    assert again["symmetry"]["planes"] == first["symmetry"]["planes"]


def test_the_verdict_key_names_the_geometry_and_not_the_sizes() -> None:
    """Only inputs of the mirror test address its verdicts."""

    from types import SimpleNamespace

    from server.cadlink.intake.ingest import _symmetry_verdict_key

    bundle = SimpleNamespace(artifact_sha256="sha256:a", manifest_sha256="sha256:m")
    manifest = {"sources": [{"id": "s"}], "instances": [{"id": "i"}]}
    key = _symmetry_verdict_key(bundle, manifest, [], {"symmetry_mode": "auto"})
    assert key == _symmetry_verdict_key(bundle, manifest, [], {"symmetry_mode": "auto"})
    other_step = SimpleNamespace(artifact_sha256="sha256:b", manifest_sha256="sha256:m")
    assert key != _symmetry_verdict_key(other_step, manifest, [], {"symmetry_mode": "auto"})
    assert key != _symmetry_verdict_key(bundle, manifest, ["s"], {"symmetry_mode": "auto"})
    assert key != _symmetry_verdict_key(
        bundle, manifest, [], {"symmetry_mode": "auto", "surface_groups": [{"group_id": "g"}]})


def test_a_stage_4_replay_rebuilds_with_every_sample_projected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hornlab_mesher

    from server.mesh.imported import STAGE4_SYMMETRY_RULES, build_imported_mesh

    pytest.importorskip("gmsh")
    real = hornlab_mesher.auto_cut_occ_geometry
    calls: list[dict[str, Any]] = []

    def spy(groups: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real(groups, **kwargs)

    monkeypatch.setattr(hornlab_mesher, "auto_cut_occ_geometry", spy)
    bundle = _horn_bundle(tmp_path, "round")
    manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
    built = _run_in_gmsh_session(
        build_imported_mesh, bundle / "assembly.step", manifest, _SIZES,
        options={"symmetry_mode": "auto", "replay_symmetry_rules": STAGE4_SYMMETRY_RULES},
        include_viewport_mesh=False,
        known_symmetry_verdicts={"[]": {"x0": {"accepted": False}}},
    )
    assert calls and all(call.get("fast_check") is False for call in calls)
    assert built["symmetry"]["symmetry_rules"] == STAGE4_SYMMETRY_RULES
    # A replay tests every plane itself, and keeps no verdict for the cache.
    assert "reused_verdict_planes" not in built["symmetry"]
    assert "symmetry_verdict_cache" not in built
    assert "feature_mismatches" not in built["symmetry"]["planes"]["x0"]


def test_an_unknown_replay_rule_is_refused(tmp_path: Path) -> None:
    from server.mesh.imported import ImportedMeshError, build_imported_mesh

    pytest.importorskip("gmsh")
    bundle = _horn_bundle(tmp_path, "round")
    manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
    with pytest.raises(ImportedMeshError, match="unknown replay symmetry rules"):
        _run_in_gmsh_session(
            build_imported_mesh, bundle / "assembly.step", manifest, _SIZES,
            options={"symmetry_mode": "auto", "replay_symmetry_rules": "made-up"},
            include_viewport_mesh=False,
        )


def test_a_plane_lost_to_a_one_sided_small_feature_says_so() -> None:
    from server.solver.domain_record import _plane_loss, cad_plane_entries

    verdict = {
        "plane": "x0", "accepted": False, "reason": "2 sampled points ...",
        "tolerance_step_units": 0.3, "points_tested": 100, "points_off_model": 0,
        "max_residual_step_units": 0.01, "worst_off_model_distance_step_units": None,
        "role_mismatches": 0, "feature_points_tested": 140, "feature_mismatches": 2,
    }
    entries = cad_plane_entries(
        {"planes": {"x0": verdict}, "candidate_planes": ["x0"], "cut_planes": []},
        domain_planes=[],
    )
    assert entries["x0"]["feature_mismatches"] == 2
    sentence, _fix = _plane_loss("x0", entries["x0"], producer=None, expected_words="a half")
    assert "small hole, boss or edge" in sentence
