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


def _spy_builds(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from server.cadlink.intake import ingest

    real = ingest.build_imported_mesh_isolated
    calls: list[dict[str, Any]] = []

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(dict(kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(ingest, "build_imported_mesh_isolated", spy)
    return calls


def test_a_remesh_at_new_sizes_reuses_the_mirror_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mirror test reads the geometry, not the mesh sizes: a second
    preparation at new sizes hands the stored verdicts to the build -- and
    records exactly what a build that tested them would (Neon P1-1)."""

    from server.mesh.prepared_record import solve_model_sha256

    pytest.importorskip("gmsh")
    calls = _spy_builds(monkeypatch)
    bundle = _horn_bundle(tmp_path / "warm", "round")
    first = _ingest(tmp_path / "warm", bundle, _SIZES)
    assert first["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert "symmetry_verdict_cache" not in first
    assert "mirror_check_points" not in first["symmetry"]
    assert "known_symmetry_verdicts" not in calls[-1]
    assert len(list((tmp_path / "warm" / "data").rglob("symmetry-verdicts/*.json"))) == 1

    finer = {**_SIZES, "rigid_size_mm": 15}
    warm = _ingest(tmp_path / "warm", bundle, finer)
    assert set(calls[-1]["known_symmetry_verdicts"]["[]"]) == {"x0", "y0"}
    cold = _ingest(tmp_path / "cold", _horn_bundle(tmp_path / "cold", "round"), finer)
    assert "known_symmetry_verdicts" not in calls[-1]
    assert warm["symmetry"] == cold["symmetry"]
    assert solve_model_sha256(warm) == solve_model_sha256(cold)
    assert warm["symmetry_verification"]["verified"] is True


def test_a_replay_neither_reads_nor_writes_the_verdict_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.cadlink.intake import ingest

    pytest.importorskip("gmsh")
    touched: list[str] = []
    monkeypatch.setattr(ingest, "_read_symmetry_verdicts",
                        lambda path: touched.append("read") or {})
    monkeypatch.setattr(ingest, "_write_symmetry_verdicts",
                        lambda path, verdicts: touched.append("write"))
    _ingest(tmp_path, _horn_bundle(tmp_path, "round"), _SIZES, symmetry_verdict_store=False)
    assert touched == []
    _ingest(tmp_path, _horn_bundle(tmp_path / "again", "round"), _SIZES)
    assert touched == ["read", "write"]


@pytest.mark.parametrize("stored", [
    {"contract": "wg-symmetry-verdicts-v1", "verdicts": {"not json": {}}},
    {"contract": "wg-symmetry-verdicts-v1", "verdicts": {"[]": {"x0": {"accepted": True}}}},
    {"contract": "wg-symmetry-verdicts-v1", "verdicts": {"[]": {"x0": "yes"}}},
    {"contract": "wg-symmetry-verdicts-v1", "verdicts": {"[1]": {}}},
    {"contract": "wg-symmetry-verdicts-v1", "verdicts": []},
])
def test_a_malformed_verdict_store_is_a_miss(tmp_path: Path, stored: Any) -> None:
    from server.cadlink.intake.ingest import _read_symmetry_verdicts

    path = tmp_path / "store.json"
    path.write_text(json.dumps(stored), encoding="utf-8")
    assert _read_symmetry_verdicts(path) == {}


def test_sizing_only_options_do_not_move_the_verdict_key() -> None:
    from types import SimpleNamespace

    from server.cadlink.intake.ingest import _symmetry_verdict_key

    bundle = SimpleNamespace(artifact_sha256="sha256:a", manifest_sha256="sha256:m")
    manifest = {"sources": [{"id": "s"}], "instances": [{"id": "i"}]}
    group = {"group_id": "g", "selectors": {"a": 1}, "observed": {}}
    base = {"symmetry_mode": "auto", "surface_groups": [{**group, "size_mm": 10}]}
    key = _symmetry_verdict_key(bundle, manifest, [], base)
    for change in ({"surface_groups": [{**group, "size_mm": 4}]}, {"surface_deviation_mm": 0.1},
                   {"fem_rejected_symmetry_planes": ["y0"]}, {"rigid_face_sizes_mm": {"1": 5}}):
        assert _symmetry_verdict_key(bundle, manifest, [], {**base, **change}) == key
    assert _symmetry_verdict_key(
        bundle, manifest, [], {**base, "surface_groups": [{**group, "selectors": {"a": 2}}]}) != key


def test_the_rules_and_semantics_follow_the_solid_cut_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.cadlink.intake import ingest
    from server.mesh import imported

    monkeypatch.setattr(imported, "CUT_SOLIDS_AS_SOLIDS", False)
    assert imported.symmetry_rules() == imported.FACE_CUT_SYMMETRY_RULES
    assert imported.FACE_CUT_SYMMETRY_RULES not in imported.replay_symmetry_rules()
    assert "cut_route" not in ingest.meshing_semantics()
    monkeypatch.setattr(imported, "CUT_SOLIDS_AS_SOLIDS", True)
    assert imported.symmetry_rules() == imported.SOLID_CUT_SYMMETRY_RULES
    assert imported.FACE_CUT_SYMMETRY_RULES in imported.replay_symmetry_rules()
    assert ingest.meshing_semantics()["cut_route"] == "solids-as-solids-v1"


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


@pytest.mark.parametrize("cut_solids", [False, True])
def test_wg_asks_for_the_cut_its_switch_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cut_solids: bool
) -> None:
    """The solid cut renumbers every face, so its meshes are not the face
    cut's: WG asks for the cut ``CUT_SOLIDS_AS_SOLIDS`` names and records the
    rules that go with it."""

    import hornlab_mesher

    from server.mesh import imported
    from server.mesh.imported import build_imported_mesh

    pytest.importorskip("gmsh")
    monkeypatch.setattr(imported, "CUT_SOLIDS_AS_SOLIDS", cut_solids)
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
        options={"symmetry_mode": "auto"}, include_viewport_mesh=False,
    )
    assert calls and all(call.get("solid_cut") is cut_solids for call in calls)
    assert built["symmetry"]["cut"]["route"] == ("solids" if cut_solids else "faces")
    assert built["symmetry"]["symmetry_rules"] == imported.symmetry_rules()


def test_once_solids_are_cut_as_solids_a_v2_package_replays_with_the_face_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hornlab_mesher

    from server.mesh import imported
    from server.mesh.imported import FACE_CUT_SYMMETRY_RULES, build_imported_mesh

    pytest.importorskip("gmsh")
    monkeypatch.setattr(imported, "CUT_SOLIDS_AS_SOLIDS", True)
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
        options={"symmetry_mode": "auto", "replay_symmetry_rules": FACE_CUT_SYMMETRY_RULES},
        include_viewport_mesh=False,
    )
    assert calls and all(call.get("solid_cut") is False and call.get("fast_check", True)
                         for call in calls)
    assert built["symmetry"]["symmetry_rules"] == FACE_CUT_SYMMETRY_RULES
    assert built["symmetry"]["cut"]["route"] == "faces"
