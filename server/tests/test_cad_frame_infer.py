"""The automatic solver frame (M1e): generated fixtures with fixed expectations.

Each fixture states, before any tuning, either the axis it must be given or
that WG must ask. The meshes are generated in OCC (``cad_frame_fixtures``),
wound outward, and surveyed through the production function
``frame_infer.infer_frame``; the reduced cases go through
``infer_record_frame``, the same entry point preparation uses, so the
mirror-back and the frame inverse are the production ones.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from server.cadlink import frame_infer
from server.cadlink.frame_infer import (
    ALGORITHM_VERSION,
    STATUS_ASK,
    STATUS_AUTOMATIC,
    STATUS_UNAVAILABLE,
    SurveySource,
    infer_frame,
    infer_record_frame,
)
from server.mesh.gmsh_worker import _run_in_gmsh_session

import cad_frame_fixtures as fx

pytest.importorskip("gmsh")

ASK = None  # an expected axis of None means WG must ask

_CACHE: dict[tuple, fx.FixtureMesh] = {}


def _mesh(builder, size_mm: float, **kwargs) -> fx.FixtureMesh:
    key = (builder.__name__, size_mm, repr(sorted(kwargs.items())))
    if key not in _CACHE:
        _CACHE[key] = _run_in_gmsh_session(fx.mesh_fixture, builder, size_mm=size_mm, **kwargs)
    return _CACHE[key]


def _party(scale: float = 0.25, size: float = 9.0) -> fx.FixtureMesh:
    return _mesh(_party_builder(scale), size)


_PARTY_BUILDERS: dict[float, object] = {}


def _party_builder(scale: float):
    if scale not in _PARTY_BUILDERS:
        def party_meh_like() -> list:
            return fx.party_meh_like(scale)
        party_meh_like.__name__ = f"party_meh_like_{scale}"
        _PARTY_BUILDERS[scale] = party_meh_like
    return _PARTY_BUILDERS[scale]


def _builder(function, **kwargs):
    def build() -> list:
        return function(**kwargs)
    build.__name__ = function.__name__ + "".join(f"_{k}{v}" for k, v in sorted(kwargs.items()))
    return build


# -- the fixture table: expectations fixed before tuning ------------------------

FIXTURES = {
    # name: (builder, mesh size mm, expected axis or ASK, expected reason code when asking)
    "rear-facing source": (_builder(fx.horn_box, rear_lf=True), 5.0, "+z", None),
    "opposed MEH midrange": (_builder(fx.horn_box, mf_taps=True), 5.0, "+z", None),
    "side-firing taps": (fx.side_firing_taps, 10.0, "+z", None),
    "two-way box": (fx.two_way_box, 6.0, "+z", None),
    "conventional ported box": (fx.ported_box, 12.0, "+z", None),
    "coaxial": (fx.coaxial, 10.0, "+z", None),
    "folded horn": (fx.folded_horn, 10.0, ASK, "sources-not-visible"),
    "dipole baffle": (fx.dipole_baffle, 12.0, ASK, None),
    "cardioid": (fx.cardioid, 10.0, "+z", None),
    "equal-area sources": (fx.equal_area_front_back, 8.0, "+z", None),
    "MF-only opposing taps": (fx.mf_only_opposing_taps, 10.0, ASK, None),
    "missing roles (LF only)": (fx.lf_only_box, 12.0, ASK, "no-directional-source"),
    "occluded HF": (fx.occluded_hf, 10.0, ASK, "sources-not-visible"),
}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixture_expectation(name: str) -> None:
    builder, size, expected, code = FIXTURES[name]
    result = infer_frame(_mesh(builder, size).survey())
    assert result.algorithm == ALGORITHM_VERSION
    if expected is ASK:
        assert result.status == STATUS_ASK, (name, result.axis, result.reason)
        assert result.axis is None
        assert result.reason and "Pick the front" in result.reason
        if code is not None:
            assert result.reason_code == code, (name, result.reason_code, result.reason)
    else:
        assert result.status == STATUS_AUTOMATIC, (name, result.reason_code, result.reason, result.evidence)
        assert result.axis == expected
        assert result.vote_share >= frame_infer.MIN_VOTE_SHARE
        assert result.vote_lead >= frame_infer.MIN_LEAD
        assert len(result.supporting_evidence) >= 2
        assert set(result.supporting_evidence) & frame_infer.GEOMETRIC_EVIDENCE


@pytest.mark.parametrize("axis", sorted(fx.POSES))
def test_party_meh_like_is_found_in_every_orientation(axis: str) -> None:
    result = infer_frame(_party().survey(fx.POSES[axis]))
    assert result.status == STATUS_AUTOMATIC, (axis, result.reason, result.evidence)
    assert result.axis == axis
    assert set(result.supporting_evidence) == {"normals", "visibility", "aperture"}


@pytest.mark.parametrize("axis", ["-x", "+y"])
def test_every_fixture_turns_with_its_model(axis: str) -> None:
    """The answer follows a rotated model, and an abstention stays one."""

    for name, (builder, size, expected, _) in sorted(FIXTURES.items()):
        rotated = infer_frame(_mesh(builder, size).survey(fx.POSES[axis]))
        if expected is ASK:
            assert rotated.status == STATUS_ASK, name
        else:
            turned = fx.POSES[axis] @ {"+z": np.array((0.0, 0.0, 1.0))}[expected]
            assert rotated.axis == _axis_name(turned), name


def _axis_name(vector: np.ndarray) -> str:
    index = int(np.argmax(np.abs(vector)))
    return ("+" if vector[index] > 0 else "-") + "xyz"[index]


def test_the_rear_facing_source_is_outvoted_by_role_not_by_area() -> None:
    """The LF faces backwards with seven times the HF's area; the front still wins."""

    mesh = _mesh(_builder(fx.horn_box, rear_lf=True), 5.0)
    result = infer_frame(mesh.survey())
    assert result.axis == "+z"
    normals = result.evidence["normals"]["roleMeanNormals"]
    assert normals["LF"][2] < -0.9 and normals["HF"][2] > 0.9
    roles = result.evidence["roles"]
    assert roles["LF"]["areaMm2"] > 4 * roles["HF"]["areaMm2"]


def test_equal_area_sources_are_told_apart_by_their_tags() -> None:
    """Areas cannot separate the two sources; the production tags do."""

    mesh = _mesh(fx.equal_area_front_back, 8.0)
    roles = infer_frame(mesh.survey()).evidence["roles"]
    assert roles["HF"]["areaMm2"] == pytest.approx(roles["LF"]["areaMm2"], rel=0.02)
    swapped = tuple(
        SurveySource(tag=s.tag, source_id=s.source_id, role={"HF": "LF", "LF": "HF"}[s.role])
        for s in mesh.sources
    )
    assert infer_frame(mesh.survey()).axis == "+z"
    assert infer_frame(mesh.survey(sources=swapped)).axis == "-z"


def test_opposed_taps_cancel_in_the_normals_but_not_in_the_visibility() -> None:
    """Discrimination control: the fixture would catch a plain normal average."""

    result = infer_frame(_mesh(fx.mf_only_opposing_taps, 10.0).survey())
    assert result.status == STATUS_ASK
    assert np.linalg.norm(result.evidence["normals"]["roleMeanNormals"]["MF"]) < 0.05
    assert result.evidence["normals"]["axis"] is None
    assert "face different ways" in result.reason


def test_an_inverted_winding_asks_instead_of_answering_backwards() -> None:
    mesh = _party()
    inverted = infer_frame(mesh.survey(triangles=mesh.triangles[:, [0, 2, 1]]))
    assert inverted.status == STATUS_ASK
    assert inverted.reason_code == "orientation-unreliable"
    assert "face inwards" in inverted.reason


def test_unvalidated_source_identity_asks() -> None:
    result = infer_frame(_party().survey(identity_problem="the painted and resolved faces disagree for hf"))
    assert result.status == STATUS_ASK
    assert result.reason_code == "source-identity-unvalidated"
    assert "hf" in result.reason


def test_mesh_density_does_not_change_the_answer() -> None:
    coarse = infer_frame(_party(size=14.0).survey())
    fine = infer_frame(_party(size=6.0).survey())
    assert coarse.status == fine.status == STATUS_AUTOMATIC
    assert coarse.axis == fine.axis == "+z"
    assert coarse.confidence == pytest.approx(fine.confidence, abs=0.1)
    for name in ("normals", "visibility"):
        assert coarse.evidence[name]["strength"] == pytest.approx(fine.evidence[name]["strength"], abs=0.1)


def test_an_unsupported_winner_is_asked_about_never_replaced() -> None:
    """A declared half allows only +z; a model facing -y is not promoted to +z."""

    result = infer_frame(_party().survey(fx.POSES["-y"]), supported_axes=("+z",))
    assert result.status == STATUS_ASK
    assert result.reason_code == "unsupported-axis"
    assert result.unrestricted_axis == "-y"
    assert result.axis is None
    assert "-y" in result.reason and "+z" in result.reason
    supported = infer_frame(_party().survey(), supported_axes=("+z",))
    assert supported.status == STATUS_AUTOMATIC and supported.axis == "+z"


def test_inference_unavailable_asks_with_the_reason() -> None:
    record = {"normalisation": {"matrix": np.eye(4).tolist()}, "tag_map": {}}
    for text in ("", "not a mesh", "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n"):
        result = infer_record_frame(record, text)
        assert result.status == STATUS_UNAVAILABLE
        assert result.axis is None
        assert "Pick the front" in result.reason


def test_the_survey_is_deterministic() -> None:
    first = infer_frame(_party().survey(fx.POSES["+x"])).to_json()
    second = infer_frame(_party().survey(fx.POSES["+x"])).to_json()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# -- reduced records: mirror-back through the production adapter ----------------


def _record(fixture: fx.FixtureMesh, solver_from_assembly: np.ndarray, planes: list[str]) -> tuple[dict, str]:
    names = {1: "wg-import-v1|rigid"}
    tag_map: dict[str, dict] = {"1": {"source_id": None, "instance_id": None, "role": "rigid"}}
    for source in fixture.sources:
        names[source.tag] = (
            f"wg-import-v1|tag={source.tag}|source_id={source.source_id}|instance_id=null|role={source.role}"
        )
        tag_map[str(source.tag)] = {"source_id": source.source_id, "instance_id": None, "role": source.role}
    record = {
        "normalisation": {"matrix": solver_from_assembly.tolist()},
        "symmetry": {"domain_planes": planes, "cut_planes": planes},
        "tag_map": tag_map,
        "findings": [],
    }
    return record, fx.msh22_text(fixture.points_mm, fixture.triangles, fixture.tags, names)


def _solver_matrix(axis: str) -> np.ndarray:
    from server.cadlink.solver_frame import frame_spec, spec_matrix

    return spec_matrix(frame_spec(axis, {}))


def _reduced(builder, size: float, axis: str, symmetric: tuple[int, ...]):
    """The open reduced solver mesh WG would hold for a model facing ``axis``.

    The model is placed facing ``axis`` in CAD and meshed in that frame, so the
    solver frame is ``Q`` times the canonical pose, with ``Q`` a turn about z.
    ``symmetric`` names the canonical mirror planes (0 = x, 1 = y); each is a
    solver plane x0 or y0, and WG keeps its positive side, which is a
    canonical half-space through ``Q^T``. Returns the reduced mesh, the
    record's matrix and the solver planes.
    """

    matrix = _solver_matrix(axis)
    q = matrix[:3, :3] @ fx.POSES[axis]
    assert np.allclose(q @ [0, 0, 1], [0, 0, 1])
    planes = []
    for canonical in symmetric:
        image = q @ np.eye(3)[canonical]
        planes.append("x0" if abs(image[0]) > 0.5 else "y0")
    planes = [plane for plane in ("x0", "y0") if plane in planes]
    keep = [q.T @ np.eye(3)[{"x0": 0, "y0": 1}[plane]] for plane in planes]
    drop = sorted({int(np.argmax(np.abs(normal))) for normal in keep})
    canonical = _mesh(builder, size, keep=tuple(tuple(np.round(k, 12)) for k in keep), drop_planes=tuple(drop))
    solver_points = canonical.points_mm @ q.T
    reduced = fx.FixtureMesh(solver_points, canonical.triangles, canonical.tags, canonical.sources)
    return reduced, matrix, planes


@pytest.mark.parametrize(
    ("axis", "symmetric"),
    [("+z", (0,)), ("+x", (0,)), ("-y", (0,)), ("-x", (0, 1)), ("+y", (0, 1)), ("-z", (0, 1))],
)
def test_a_reduced_record_is_mirrored_back_and_judged_as_the_full_model(axis: str, symmetric) -> None:
    builder = _party_builder(0.25) if symmetric == (0,) else _builder(fx.horn_box, mf_taps=True)
    size = 9.0 if symmetric == (0,) else 5.0
    reduced, matrix, planes = _reduced(builder, size, axis, symmetric)
    assert len(planes) == len(symmetric)
    record, text = _record(reduced, matrix, planes)
    result = infer_record_frame(record, text)
    full = infer_frame(_mesh(builder, size).survey(fx.POSES[axis]))
    assert result.status == full.status == STATUS_AUTOMATIC, (result.reason, result.evidence)
    assert result.axis == full.axis == axis
    assert result.confidence == pytest.approx(full.confidence, abs=0.1)

    # Mirror-back against the full geometry, in CAD coordinates.
    mirrored = frame_infer.survey_mesh_from_record(record, text)
    whole = _mesh(builder, size).survey(fx.POSES[axis])

    def area(mesh, tags=None):
        corners = mesh.points_mm[mesh.triangles]
        cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        areas = 0.5 * np.linalg.norm(cross, axis=1)
        return areas.sum() if tags is None else areas[np.isin(mesh.tags, tags)].sum()

    def signed_volume(mesh):
        corners = mesh.points_mm[mesh.triangles]
        return np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum() / 6.0

    assert np.allclose(mirrored.points_mm.min(axis=0), whole.points_mm.min(axis=0), atol=0.05)
    assert np.allclose(mirrored.points_mm.max(axis=0), whole.points_mm.max(axis=0), atol=0.05)
    assert area(mirrored) == pytest.approx(area(whole), rel=0.01)
    assert signed_volume(mirrored) == pytest.approx(signed_volume(whole), rel=0.01)
    assert signed_volume(mirrored) > 0.0  # still wound outward
    tags = {source.source_id: source.tag for source in mirrored.sources}
    assert set(tags) == {source.source_id for source in whole.sources}
    for source in whole.sources:
        assert area(mirrored, [tags[source.source_id]]) == pytest.approx(
            area(whole, [source.tag]), rel=0.02
        )


def test_a_reduced_record_is_not_judged_without_its_mirror_image() -> None:
    """Positive control for the mirror-back: the open half alone is not the model."""

    reduced, matrix, planes = _reduced(_party_builder(0.25), 9.0, "+x", (0,))
    assert planes == ["y0"]
    record, text = _record(reduced, matrix, [])
    assert infer_record_frame(record, text).status != STATUS_AUTOMATIC
