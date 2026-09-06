"""Pure imported-mesh tag, sizing, transform, and role decisions."""

from __future__ import annotations

import math

import numpy as np
import pytest

from server.mesh.imported import (
    IMPORTED_SURFACE_DEVIATION_MM,
    ImportedMeshError,
    RoleResolutionError,
    declared_disc_reduction,
    geometry_candidate_matches,
    allocate_imported_tags,
    imported_tessellation_settings,
    imported_viewport_tessellation_settings,
    recorded_vertical_offset_mm,
    resolve_instance_source,
    resolve_user_source,
    resolve_vertical_recentre,
    rigid_inverse,
    validate_imported_sizes,
    verify_symmetry_cut,
    _advanced_face_identifier_surfaces,
    _assert_disjoint_source_claims,
    _lookup_faces,
    _reconcile_source_paint,
    _post_cut_source_area_record,
)


def test_imported_tags_are_deterministic_complete_and_do_not_use_parametric_values() -> None:
    sources = [{"id": "source-mf", "role": "MF", "instance_id": None}, {"id": "source-hf", "role": "HF", "instance_id": "i"}]
    result = allocate_imported_tags(sources)
    assert result["source_tags"] == {"source-mf": 101, "source-hf": 102}
    assert result["tag_map"] == {
        "1": {"source_id": None, "instance_id": None, "role": "rigid"},
        "101": {"source_id": "source-mf", "instance_id": None, "role": "MF"},
        "102": {"source_id": "source-hf", "instance_id": "i", "role": "HF"},
    }
    assert not ({3, 4} & set(result["source_tags"].values()))


def test_imported_tags_reject_duplicate_normalized_source_ids() -> None:
    with pytest.raises(ImportedMeshError, match="duplicate normalized source ids.*'1'"):
        allocate_imported_tags(
            [
                {"id": 1, "role": "MF", "instance_id": None},
                {"id": "1", "role": "HF", "instance_id": None},
            ]
        )


def test_sizes_cover_exactly_non_skipped_sources() -> None:
    sources = [{"id": "a"}, {"id": "b"}]
    assert validate_imported_sizes(sources, {"rigid_size_mm": 10, "transition_mm": 20, "source_size_mm": {"a": 2}}, skipped_source_ids=["b"])["source_size_mm"] == {"a": 2.0}
    with pytest.raises(ImportedMeshError, match="missing"):
        validate_imported_sizes(sources, {"rigid_size_mm": 10, "transition_mm": 20, "source_size_mm": {"a": 2}})


def test_imported_tessellation_is_deviation_driven_but_bounded() -> None:
    settings = imported_tessellation_settings(
        {
            "rigid_size_mm": 12.0,
            "transition_mm": 20.0,
            "source_size_mm": {"hf": 4.0, "mf": 8.0},
        }
    )
    assert settings == {
        "surface_deviation_mm": IMPORTED_SURFACE_DEVIATION_MM,
        "mesh_size_min_mm": 2.0,
        "mesh_size_max_mm": 12.0,
        "algorithm": 6,
    }


def test_imported_viewport_tessellation_is_scale_based_and_coarsens() -> None:
    first = imported_viewport_tessellation_settings([0, 0, 0, 420, 400, 300])
    second = imported_viewport_tessellation_settings(
        [0, 0, 0, 420, 400, 300], retry=1
    )
    assert 1.0 <= first["mesh_size_max_mm"] <= 6.0
    assert first["curvature_segments_per_2pi"] == 48
    assert second["mesh_size_max_mm"] > first["mesh_size_max_mm"]
    assert second["curvature_segments_per_2pi"] < first["curvature_segments_per_2pi"]


def test_rigid_transform_refuses_scale_and_mirror() -> None:
    inverse = rigid_inverse([[1, 0, 0, 10], [0, 1, 0, 20], [0, 0, 1, 30], [0, 0, 0, 1]])
    assert np.allclose(inverse[:3, 3], [-10, -20, -30])
    with pytest.raises(ImportedMeshError, match="orthonormal"):
        rigid_inverse([[2, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    with pytest.raises(ImportedMeshError, match="mirrored"):
        rigid_inverse([[-1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])


def test_instance_role_resolution_five_rows() -> None:
    source = {"id": "hf"}
    candidate = {"face_id": 7, "matches": True}
    assert resolve_instance_source(source, [candidate], [7], local_body_state="unmodified")["method"] == "geometry+paint-corroborated"
    assert resolve_instance_source(source, [candidate], [], local_body_state="unmodified")["method"] == "geometry"
    overrode = resolve_instance_source(source, [candidate], [7, 9], local_body_state="unmodified")
    assert overrode["demoted_painted_surfaces"] == [9]
    with pytest.raises(RoleResolutionError, match="local_body_state='modified'"):
        resolve_instance_source(source, [], [7], local_body_state="modified")
    with pytest.raises(RoleResolutionError, match="2 geometric"):
        resolve_instance_source(source, [candidate, {"face_id": 8}], [7], local_body_state="unmodified")


def test_user_selector_agreement_and_drift_rules() -> None:
    source = {"id": "lf", "required": True, "patch_policy": "explicit-disconnected", "expected_connected_components": 2, "observed": {"face_count": 2, "total_area_mm2": 100}}
    result = resolve_user_source(source, {"appearance_labels": [4, 5], "shell_names": [5, 4]}, face_areas_mm2={4: 50, 5: 50}, connected_components=2)
    assert result["surfaces"] == [4, 5]
    with pytest.raises(RoleResolutionError, match="disagree"):
        resolve_user_source(source, {"appearance_labels": [4], "shell_names": [5]}, face_areas_mm2={4: 50, 5: 50}, connected_components=2)
    with pytest.raises(RoleResolutionError, match="area drift") as drift:
        resolve_user_source(source, {"appearance_labels": [4, 5]}, face_areas_mm2={4: 51, 5: 51}, connected_components=2)
    assert drift.value.area_drift_sources == ("lf",)

    resolving = resolve_user_source(
        source,
        {"appearance_labels": [4, 5], "shell_names": []},
        face_areas_mm2={4: 50, 5: 50},
        connected_components=2,
    )
    assert resolving["surfaces"] == [4, 5]

    overridden = resolve_user_source(
        source,
        {"appearance_labels": [4, 5]},
        face_areas_mm2={4: 51, 5: 51},
        connected_components=2,
        allow_area_drift=True,
    )
    assert overridden["area_drift_overridden"] is True


def test_advanced_face_selectors_are_identifiers_not_positions() -> None:
    assert _advanced_face_identifier_surfaces([38], [12, 38], [101, 205]) == {205}
    with pytest.raises(RoleResolutionError, match="identifier 1.*12, 38"):
        _advanced_face_identifier_surfaces([1], [12, 38], [101, 205])


def test_source_claims_must_be_disjoint() -> None:
    with pytest.raises(RoleResolutionError, match="'a'.*'b'.*face 9"):
        _assert_disjoint_source_claims(
            {"a": {"surfaces": [9]}, "b": {"surfaces": [9]}}
        )


def test_port_exit_alias_is_guarded() -> None:
    groups = {"PORT_EXIT": [8], "PORT_EXIT_L": [9]}
    assert _lookup_faces(["port_exit_l"], groups) == {9}
    assert _lookup_faces(["PORT_EXIT_R"], groups) == {8}
    assert _lookup_faces(["PORT_EXIT", "PORT_EXIT_R"], groups) == {8}
    assert (
        _lookup_faces(
            ["PORT_EXIT_R"],
            groups,
            requested_labels=["PORT_EXIT", "PORT_EXIT_R"],
        )
        == set()
    )


def test_casefolded_step_label_collisions_require_identical_face_sets() -> None:
    with pytest.raises(RoleResolutionError, match="PORT.*port"):
        _lookup_faces(["port"], {"PORT": [7], "port": [9]})

    assert _lookup_faces(["port"], {"PORT": [7, 9], "port": [9, 7]}) == {
        7,
        9,
    }


def test_shared_instance_paint_is_reconciled_across_sources_and_r16_stays_live() -> None:
    sources = [
        {
            "id": "left",
            "selectors": {
                "linked_throat": {"instance_id": "left-instance"},
                "appearance_labels": ["HF"],
            },
        },
        {
            "id": "right",
            "selectors": {
                "linked_throat": {"instance_id": "right-instance"},
                "appearance_labels": ["HF"],
            },
        },
    ]
    resolutions = {
        "left": {"surfaces": [11], "skipped": False},
        "right": {"surfaces": [12], "skipped": False},
    }
    claimed, findings, unclaimed = _reconcile_source_paint(
        sources,
        resolutions,
        {"left": {11, 12}, "right": {11, 12}},
    )
    assert claimed == {11, 12}
    assert findings == []
    assert unclaimed == []
    assert {item["method"] for item in resolutions.values()} == {
        "geometry+paint-corroborated"
    }

    _, _, unclaimed = _reconcile_source_paint(
        sources,
        resolutions,
        {"left": {11, 12, 13}, "right": {11, 12, 13}},
    )
    assert unclaimed == [13]


def test_explicitly_skipped_painted_source_is_explained() -> None:
    sources = [
        {
            "id": "optional",
            "selectors": {
                "appearance_labels": ["HF"],
            },
        }
    ]
    resolutions = {
        "optional": {"source_id": "optional", "skipped": True, "surfaces": []}
    }
    claimed, findings, unclaimed = _reconcile_source_paint(
        sources,
        resolutions,
        {},
        skipped_source_ids={"optional"},
        skipped_paint={21},
    )
    assert claimed == set()
    assert findings == []
    assert unclaimed == []


def test_post_cut_source_area_refuses_a_silently_dead_channel() -> None:
    with pytest.raises(ImportedMeshError, match="source 'right'.*retained fraction 0"):
        _post_cut_source_area_record(
            "right",
            parent_area_mm2=100.0,
            retained_child_area_mm2=0.0,
            predicted_retained_fraction=0.5,
        )
    accepted = _post_cut_source_area_record(
        "right",
        parent_area_mm2=100.0,
        retained_child_area_mm2=50.0,
        predicted_retained_fraction=0.5,
    )
    assert accepted["retained_fraction"] == 0.5


def test_recorded_vertical_offset_reads_the_echoed_design_config() -> None:
    assert (
        recorded_vertical_offset_mm(
            {"config": {"root": {"mesh": {"vertical_offset": {"value": 80.0, "raw": None}}}}}
        )
        == 80.0
    )
    # A bare number is accepted because the config is an opaque echo, not a
    # schema WG can enforce on the return leg.
    assert (
        recorded_vertical_offset_mm({"config": {"root": {"mesh": {"vertical_offset": 12}}}})
        == 12.0
    )
    assert recorded_vertical_offset_mm({}) is None
    assert recorded_vertical_offset_mm({"config": {"root": {"mesh": {}}}}) is None
    assert recorded_vertical_offset_mm(None) is None
    assert (
        recorded_vertical_offset_mm(
            {"config": {"root": {"mesh": {"vertical_offset": {"value": None, "raw": "y"}}}}}
        )
        is None
    )


def test_vertical_recentre_needs_the_geometry_to_agree_with_the_config() -> None:
    placed = resolve_vertical_recentre(80.0, bounds_mm=(-100.0, -20.0, 0.0, 100.0, 180.0, 300.0))
    assert placed["applied"] is True
    assert placed["applied_offset_mm"] == 80.0

    # Same recorded offset, a body that is not centred on it: the config
    # describes some other design and must not move this one.
    wrong = resolve_vertical_recentre(80.0, bounds_mm=(-100.0, -20.0, 0.0, 100.0, 60.0, 300.0))
    assert wrong["applied"] is False
    assert wrong["applied_offset_mm"] == 0.0
    assert "y midpoint" in wrong["reason"]

    for absent in (
        resolve_vertical_recentre(None, bounds_mm=(0.0,) * 6),
        resolve_vertical_recentre(0.0, bounds_mm=(0.0,) * 6),
    ):
        assert absent["applied"] is False
        assert absent["applied_offset_mm"] == 0.0


_QUARTER_BOX_POINTS = np.asarray(
    [
        (0.0, 0.0, 1.0),
        (1.0, 0.0, 1.0),
        (1.0, 1.0, 1.0),
        (0.0, 1.0, 1.0),
        (0.0, 0.0, 2.0),
        (1.0, 0.0, 2.0),
        (1.0, 1.0, 2.0),
        (0.0, 1.0, 2.0),
    ],
    dtype=float,
)
_QUARTER_BOX_FACES = {
    "zlow": [(0, 1, 2), (0, 2, 3)],
    "zhigh": [(4, 5, 6), (4, 6, 7)],
    "y0": [(0, 1, 5), (0, 5, 4)],
    "y1": [(3, 2, 6), (3, 6, 7)],
    "x0": [(0, 3, 7), (0, 7, 4)],
    "x1": [(1, 2, 6), (1, 6, 5)],
}


def _quarter_box(*open_faces: str) -> np.ndarray:
    return np.asarray(
        [
            triangle
            for name, face in _QUARTER_BOX_FACES.items()
            if name not in open_faces
            for triangle in face
        ],
        dtype=np.int64,
    )


def test_verify_symmetry_cut_confirms_an_open_quarter_domain() -> None:
    verdict = verify_symmetry_cut(
        _QUARTER_BOX_POINTS,
        _quarter_box("x0", "y0"),
        cut_planes=("x0", "y0"),
        topology={"unexpected_free_edges": 0, "free_edges": 6},
    )
    assert verdict["verified"] is True
    assert verdict["detected_planes"] == ["x0", "y0"]
    assert verdict["capped_planes"] == []


def test_verify_symmetry_cut_refuses_a_capped_cut_plane() -> None:
    verdict = verify_symmetry_cut(
        _QUARTER_BOX_POINTS,
        _quarter_box("x0"),
        cut_planes=("x0", "y0"),
        topology={"unexpected_free_edges": 0, "free_edges": 4},
    )
    assert verdict["verified"] is False
    assert verdict["capped_planes"] == ["y0"]
    assert "capped" in verdict["reason"]


def test_verify_symmetry_cut_refuses_an_off_plane_leak() -> None:
    verdict = verify_symmetry_cut(
        _QUARTER_BOX_POINTS,
        _quarter_box("x0", "y0"),
        cut_planes=("x0", "y0"),
        topology={
            "unexpected_free_edges": 3,
            "free_edges": 9,
            "unexpected_free_edge_midpoint_samples": [[0.5, 0.5, 2.0]],
        },
    )
    assert verdict["verified"] is False
    assert verdict["capped_planes"] == []
    assert "leaks" in verdict["reason"]
    assert verdict["off_plane_free_edge_samples"] == [[0.5, 0.5, 2.0]]


def test_verify_symmetry_cut_does_not_judge_an_uncut_open_shell() -> None:
    """An imported open shell's rim is real geometry, not a leak.

    Without a cut plane there is no mirror to be wrong about, so the free
    edges of a full-domain import are left alone.
    """

    verdict = verify_symmetry_cut(
        _QUARTER_BOX_POINTS,
        _quarter_box("zhigh"),
        cut_planes=(),
        topology={"unexpected_free_edges": 4, "free_edges": 4},
    )
    assert verdict["verified"] is True
    assert verdict["detected_planes"] == []


# ------------------- what a declared pre-cut domain does to a linked throat


def _throat_contract(centre=(0.0, 0.0, 0.0), diameter=20.0, normal=(0.0, 0.0, 1.0)):
    """A throat contract as ``transformed_contracts`` builds one."""

    return {
        "throat_diameter_mm": diameter,
        "expected_disc_area_mm2": math.pi * diameter * diameter / 4.0,
        "plane_origin_mm": list(centre),
        "plane_normal": list(normal),
        "axis_origin_mm": list(centre),
        "axis_direction": list(normal),
    }


def test_a_source_the_cut_never_reached_keeps_its_whole_disc() -> None:
    """A pair of drivers mirrored about y = 0 is a real reduced-domain model.

    The retained one sits wholly on the retained side and its twin is the
    solver's mirror, so its own disc is untouched. Expecting half of it would
    refuse a legitimate return, and accepting a half-area face there would take
    the wrong face.
    """

    contract = _throat_contract(centre=(0.0, 60.0, 0.0))
    reduction = declared_disc_reduction(contract, ["y0"])

    assert reduction.retained_fraction == 1.0
    assert reduction.cut_directions == ()
    assert reduction.clear_axes == (1,)

    whole = {
        "planar": True,
        "plane_distance_mm": 0.0,
        "normal_angle_deg": 0.0,
        "centroid_axis_distance_mm": 0.0,
        "area_mm2": contract["expected_disc_area_mm2"],
        "retained_area_fraction": 1.0,
    }
    assert geometry_candidate_matches(whole, contract) is True
    # The position gate is intact for it: an off-axis face of the right area is
    # still not this throat.
    assert (
        geometry_candidate_matches(
            {**whole, "centroid_axis_distance_mm": 4.0}, contract
        )
        is False
    )
    # And a half-area face is not it either.
    assert (
        geometry_candidate_matches(
            {**whole, "area_mm2": contract["expected_disc_area_mm2"] / 2.0}, contract
        )
        is False
    )


def test_a_cut_through_a_disc_centre_halves_it_and_fixes_where_its_centroid_must_be() -> None:
    """A half disc's centroid is 4r/3pi from the centre. Not "somewhere positive"."""

    contract = _throat_contract()
    reduction = declared_disc_reduction(contract, ["y0"])
    expected = 4.0 * 10.0 / (3.0 * math.pi)

    assert reduction.retained_fraction == 0.5
    assert len(reduction.cut_directions) == 1
    assert np.allclose(reduction.cut_directions[0], (0.0, 1.0, 0.0))
    assert np.allclose(reduction.centroid_offset_mm, (0.0, expected, 0.0))

    def candidate(centroid_error_mm: float) -> dict:
        return {
            "planar": True,
            "plane_distance_mm": 0.0,
            "normal_angle_deg": 0.0,
            # What the caller computes: |measured centroid - expected centroid|.
            "centroid_axis_distance_mm": abs(centroid_error_mm),
            "area_mm2": contract["expected_disc_area_mm2"] / 2.0,
            "retained_area_fraction": 0.5,
        }

    axis_limit = max(0.10, 0.005 * contract["throat_diameter_mm"])
    assert geometry_candidate_matches(candidate(0.0), contract) is True
    assert geometry_candidate_matches(candidate(0.9 * axis_limit), contract) is True
    # The hole this closes: a same-area half translated FAR along its own cut
    # direction used to pass, because the whole displacement was subtracted.
    assert geometry_candidate_matches(candidate(40.0), contract) is False
    # And the cases that were already caught stay caught: centred on the axis
    # (error = the full expected offset) and displaced the wrong way.
    assert geometry_candidate_matches(candidate(expected), contract) is False
    assert geometry_candidate_matches(candidate(2.0 * expected), contract) is False
    # Nothing may sit on the removed side of the plane.
    assert (
        geometry_candidate_matches(
            {**candidate(0.0), "crosses_declared_plane": True}, contract
        )
        is False
    )
    # The full disc is not a match once half of it was declared away.
    assert (
        geometry_candidate_matches(
            {**candidate(0.0), "area_mm2": contract["expected_disc_area_mm2"]},
            contract,
        )
        is False
    )


def test_a_far_displaced_same_area_half_is_rejected_by_position() -> None:
    """The decoy the previous sign-only check let through, measured end to end.

    A genuine retained half and a decoy of identical area, plane and normal,
    the decoy translated 40 mm further along the very direction the cut was
    taken from. Both are on the positive side; only one is in the right place.
    """

    contract = _throat_contract()
    reduction = declared_disc_reduction(contract, ["y0"])
    axis_direction = np.asarray(contract["axis_direction"], dtype=float)
    axis_origin = np.asarray(contract["axis_origin_mm"], dtype=float)
    expected_offset = np.asarray(reduction.centroid_offset_mm, dtype=float)

    def matches(centroid: tuple[float, float, float]) -> bool:
        delta = np.asarray(centroid, dtype=float) - axis_origin
        lateral = delta - float(np.dot(delta, axis_direction)) * axis_direction
        return geometry_candidate_matches(
            {
                "planar": True,
                "plane_distance_mm": 0.0,
                "normal_angle_deg": 0.0,
                "centroid_axis_distance_mm": float(
                    np.linalg.norm(lateral - expected_offset)
                ),
                "area_mm2": contract["expected_disc_area_mm2"] / 2.0,
                "retained_area_fraction": reduction.retained_fraction,
            },
            contract,
        )

    correct_y = float(expected_offset[1])
    assert matches((0.0, correct_y, 0.0)) is True
    assert matches((0.0, correct_y + 40.0, 0.0)) is False
    assert matches((40.0, correct_y, 0.0)) is False


def test_a_quarter_pins_both_directions_and_rejects_a_positive_decoy() -> None:
    contract = _throat_contract()
    reduction = declared_disc_reduction(contract, ["x0", "y0"])
    expected = 4.0 * 10.0 / (3.0 * math.pi)

    assert reduction.retained_fraction == 0.25
    assert len(reduction.cut_directions) == 2
    assert np.allclose(reduction.centroid_offset_mm, (expected, expected, 0.0))

    axis_origin = np.asarray(contract["axis_origin_mm"], dtype=float)
    axis_direction = np.asarray(contract["axis_direction"], dtype=float)
    offset = np.asarray(reduction.centroid_offset_mm, dtype=float)

    def matches(centroid: tuple[float, float, float]) -> bool:
        delta = np.asarray(centroid, dtype=float) - axis_origin
        lateral = delta - float(np.dot(delta, axis_direction)) * axis_direction
        return geometry_candidate_matches(
            {
                "planar": True,
                "plane_distance_mm": 0.0,
                "normal_angle_deg": 0.0,
                "centroid_axis_distance_mm": float(np.linalg.norm(lateral - offset)),
                "area_mm2": contract["expected_disc_area_mm2"] / 4.0,
                "retained_area_fraction": 0.25,
            },
            contract,
        )

    assert matches((expected, expected, 0.0)) is True
    # Positive in both directions and the right area, but not this throat.
    assert matches((expected + 30.0, expected, 0.0)) is False
    assert matches((expected, expected + 30.0, 0.0)) is False
    assert matches((2.0 * expected, 2.0 * expected, 0.0)) is False
    assert matches((0.0, 0.0, 0.0)) is False


def test_a_throat_whose_axis_is_not_its_plane_normal_is_refused_not_approximated() -> None:
    """The disc's centroid direction is only defined when the axis is its normal."""

    contract = _throat_contract()
    contract["axis_direction"] = [0.05, 0.0, 1.0]  # ~2.9 degrees off
    with pytest.raises(
        RoleResolutionError, match="not perpendicular to its own throat plane"
    ):
        declared_disc_reduction(contract, ["y0"])
    # It is only refused where it matters: with no declared cut there is no
    # retained centroid to locate, so this contract still resolves.
    assert declared_disc_reduction(contract, []).retained_fraction == 1.0


def test_a_cut_that_clips_a_disc_off_centre_is_refused_with_the_measurement() -> None:
    """There is no honest fraction for a circular segment, so none is invented."""

    contract = _throat_contract(centre=(0.0, 4.0, 0.0))  # disc reaches 10 mm
    with pytest.raises(RoleResolutionError, match=r"crosses this throat's disc \+4 mm off its centre"):
        declared_disc_reduction(contract, ["y0"])

    removed = _throat_contract(centre=(0.0, -60.0, 0.0))
    with pytest.raises(RoleResolutionError, match="off its centre"):
        declared_disc_reduction(removed, ["y0"])


def test_a_degenerate_throat_contract_is_named_rather_than_guessed_at() -> None:
    flat = _throat_contract(normal=(0.0, 0.0, 0.0))
    with pytest.raises(RoleResolutionError, match="degenerate throat plane"):
        declared_disc_reduction(flat, ["y0"])

    contract = _throat_contract()
    contract["axis_direction"] = [1.0, 0.0, 0.0]  # axis lies in the throat plane
    with pytest.raises(
        RoleResolutionError, match="not perpendicular to its own throat plane"
    ):
        declared_disc_reduction(contract, ["y0"])

    with pytest.raises(RoleResolutionError, match="not one this throat matching understands"):
        declared_disc_reduction(_throat_contract(), ["z0"])


def test_no_declared_plane_leaves_the_contract_judged_exactly_as_before() -> None:
    contract = _throat_contract()
    reduction = declared_disc_reduction(contract, [])

    assert reduction == declared_disc_reduction(contract, ())
    assert reduction.retained_fraction == 1.0
    assert reduction.cut_directions == ()
