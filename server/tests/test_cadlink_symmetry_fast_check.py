"""Stage 4b in WG: the mesher's fast mirror check, its small-feature check and
the stage-4 replay rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from server.mesh.gmsh_worker import _run_in_gmsh_session
from test_cadlink_ingest_symmetry import _SIZES, _horn_bundle


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
    )
    assert calls and all(call.get("fast_check") is False for call in calls)
    assert built["symmetry"]["symmetry_rules"] == STAGE4_SYMMETRY_RULES
    assert "reused_verdict_planes" not in built["symmetry"]
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
