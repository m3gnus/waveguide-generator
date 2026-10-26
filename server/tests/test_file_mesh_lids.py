"""Closed symmetry faces use the ordinary file preparation and CAD checks."""

import asyncio

import numpy as np
import pytest

from server.cadlink.store import CadLinkStore
from server.cadlink.wglink_protocol import SOURCE_ROLES
from server.mesh.file_import import inspect_file_mesh, mesh_text, prepare_file_mesh
from server.mesh.imported import SYMMETRY_SNAP_TOLERANCE_MM
from test_file_mesh_roles_symmetry import box_mesh
from test_mesh_import import register_mesh, registration
from test_models_api import app_for, call


@pytest.mark.parametrize("planes", [["x0"], ["y0"], ["x0", "y0"]])
@pytest.mark.parametrize("negative", [False, True])
def test_closed_faces_are_reported_removed_and_verified(tmp_path, planes, negative):
    original = box_mesh(planes=planes, capped=planes, negative=planes if negative else ())
    # A lid can span more than one group; count each without dropping the group.
    lid = np.all(original.points[original.triangles, {"x0": 0, "y0": 1}[planes[0]]] == 0, axis=1)
    original.tags[np.flatnonzero(lid)[0]] = 103
    original.tags[np.flatnonzero(~lid)[0]] = 103
    original.names[103] = "Shared wall"
    summary = inspect_file_mesh(original, "m", "+z", "+y")
    assert summary["detected_planes"] == []
    for plane in planes:
        assert summary["lids"][plane]["candidate"]
        assert summary["lids"][plane]["triangle_count"] == 8
    assert summary["lids"][planes[0]]["groups"] == [
        {"tag": 1, "triangle_count": 7}, {"tag": 103, "triangle_count": 1},
    ]
    with pytest.raises(ValueError, match="Remove it in the symmetry settings, or export the mesh open"):
        prepare_file_mesh(original, "m", "+z", "+y", domain_planes=planes)
    prepared, transform = prepare_file_mesh(original, "m", "+z", "+y", domain_planes=planes, remove_lid_planes=planes)
    assert len(prepared.triangles) == len(original.triangles) - 8 * len(planes)
    assert len(prepared.points) < len(original.points)
    np.testing.assert_array_equal(np.unique(prepared.triangles), np.arange(len(prepared.points)))
    for plane in planes:
        assert transform["removed_lids"][plane] == {
            key: summary["lids"][plane][key] for key in ("triangle_count", "groups")
        }
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        body = registration(msh_text=mesh_text(original), units="m", source_tags=[],
                            roles={101: "HF", 102: "MF"}, domain_planes=planes, remove_lid_planes=planes)
        record = register_mesh(store, tmp_path, body)
        assert record["source_file"]["remove_lid_planes"] == planes
        assert record["source_file"]["transform"]["removed_lids"] == transform["removed_lids"]
        assert record["symmetry"]["domain_planes"] == planes
        assert record["mesh"]["integrity"]["off_plane_open_edge_count"] == 0
        assert register_mesh(store, tmp_path, body)["ingest_id"] == record["ingest_id"]
    finally:
        store.close()


def test_lid_detection_and_removal_follow_the_transformed_frame():
    original = box_mesh()
    original.points *= 1000
    summary = inspect_file_mesh(original, "mm", "+z", "+x")
    assert summary["lids"]["y0"]["candidate"]
    prepared, transform = prepare_file_mesh(original, "mm", "+z", "+x", domain_planes=["y0"], remove_lid_planes=["y0"])
    assert len(prepared.triangles) == 40
    assert transform["removed_lids"]["y0"]["triangle_count"] == 8


@pytest.mark.parametrize("fraction,has_lid", [(0.5, True), (1.5, False)])
def test_lids_and_open_rims_use_the_detector_tolerance(fraction, has_lid):
    original = box_mesh()
    original.points[original.points[:, 0] == 0, 0] = fraction * SYMMETRY_SNAP_TOLERANCE_MM * 1e-3
    summary = inspect_file_mesh(original, "m", "+z", "+y")
    assert ("x0" in summary["lids"]) is has_lid
    if has_lid:
        prepared, _ = prepare_file_mesh(original, "m", "+z", "+y", domain_planes=["x0"], remove_lid_planes=["x0"])
        assert inspect_file_mesh(prepared, "m", "+z", "+y")["detected_planes"] == ["x0"]
    else:
        with pytest.raises(ValueError, match="no closed face to remove on x = 0"):
            prepare_file_mesh(original, "m", "+z", "+y", domain_planes=["x0"], remove_lid_planes=["x0"])


def test_removal_without_a_lid_is_an_error():
    with pytest.raises(ValueError, match="no closed face to remove on x = 0"):
        prepare_file_mesh(box_mesh(planes=["x0"]), "m", "+z", "+y", domain_planes=["x0"], remove_lid_planes=["x0"])


@pytest.mark.parametrize("changes", [
    {"remove_lid_planes": ["x0"]},
    {"domain_planes": ["x0"], "remove_lid_planes": ["y0"]},
    {"domain_planes": ["x0"], "remove_lid_planes": ["z0"]},
    {"domain_planes": ["x0"], "remove_lid_planes": ["x0", "x0"]},
])
def test_lid_removal_is_limited_to_declared_planes(changes):
    with pytest.raises(ValueError, match="only on a declared symmetry plane"):
        registration(**changes)
    with pytest.raises(ValueError, match="only on a declared symmetry plane"):
        prepare_file_mesh(box_mesh(), "m", "+z", "+y", **changes)


@pytest.mark.parametrize("explicit_role", [False, True])
@pytest.mark.parametrize("planes", [["x0"], ["x0", "y0"]])
def test_removal_drops_a_rigid_lid_group_and_records_it(tmp_path, explicit_role, planes):
    original = box_mesh()
    for plane in planes:
        original.tags[np.all(original.points[original.triangles, {"x0": 0, "y0": 1}[plane]] == 0, axis=1)] = 103
    original.names[103] = "Cut face"
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        record = register_mesh(store, tmp_path, registration(
            msh_text=mesh_text(original), units="m", source_tags=[],
            roles={101: "HF", **({103: "RIGID"} if explicit_role else {})},
            domain_planes=planes, remove_lid_planes=planes,
        ))
        assert 103 not in {group["tag"] for group in record["summary"]["groups"]}
        assert record["source_file"]["transform"]["removed_lids"] == {
            plane: {"triangle_count": 8, "groups": [{"tag": 103, "triangle_count": 8}]} for plane in planes
        }
    finally:
        store.close()


@pytest.mark.parametrize("role", SOURCE_ROLES)
@pytest.mark.parametrize("partial", [False, True])
def test_removal_cannot_remove_any_driver_or_port_triangles(tmp_path, role, partial):
    original = box_mesh()
    lid = np.all(original.points[original.triangles, 0] == 0, axis=1)
    original.tags[lid] = 103
    if partial:
        original.tags[np.flatnonzero(~lid)[0]] = 103
    original.names[103] = "Cut face driver"
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        with pytest.raises(ValueError, match="closed face on x = 0.*driver or port surface 'Cut face driver'"):
            register_mesh(store, tmp_path, registration(
                msh_text=mesh_text(original), units="m", source_tags=[], roles={101: "HF", 103: role},
                domain_planes=["x0"], remove_lid_planes=["x0"],
            ))
    finally:
        store.close()


def test_legacy_source_tags_also_protect_driver_triangles(tmp_path):
    original = box_mesh()
    original.tags[np.all(original.points[original.triangles, 0] == 0, axis=1)] = 103
    original.names[103] = "Cut face driver"
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        with pytest.raises(ValueError, match="closed face on x = 0.*driver or port surface 'Cut face driver'"):
            register_mesh(store, tmp_path, registration(
                msh_text=mesh_text(original), units="m", source_tags=[101, 103],
                domain_planes=["x0"], remove_lid_planes=["x0"],
            ))
    finally:
        store.close()


@pytest.mark.parametrize("plane,axis,label", [("x0", 0, "x = 0"), ("y0", 1, "y = 0")])
def test_lid_removal_names_a_crossed_plane(plane, axis, label):
    original = box_mesh()
    original.points[6, axis] = -.1
    with pytest.raises(ValueError, match=f"The mesh crosses {label}"):
        prepare_file_mesh(original, "m", "+z", "+y", domain_planes=[plane], remove_lid_planes=[plane])


def test_removing_a_lid_does_not_admit_an_off_plane_hole_or_inward_shell():
    for inward in (False, True):
        original = box_mesh(hole=not inward)
        if inward:
            original.triangles = original.triangles[:, [0, 2, 1]]
        with pytest.raises(ValueError, match="face outward" if inward else "opening that is not on"):
            prepare_file_mesh(original, "m", "+z", "+y", domain_planes=["x0"], remove_lid_planes=["x0"])


def test_empty_lid_choice_preserves_the_legacy_digest(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        record = register_mesh(store, tmp_path, registration(remove_lid_planes=[]))
        assert record["manifest_sha256"] == "sha256:d37a7e7dca517e718516b5f73e6e04068286cabf934d32c9a12c0b56a161b9ff"
        assert "remove_lid_planes" not in record["source_file"]
        assert "removed_lids" not in record["source_file"]["transform"]
    finally:
        store.close()


def test_inspect_register_and_reprepare_closed_half(tmp_path):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            original = {"filename": "closed-half.msh", "msh_text": mesh_text(box_mesh())}
            summary = await call(app, "POST", "/api/models/files/inspect", original)
            assert summary["lids"]["x0"]["candidate"]
            settings = {"units": "m", "forward": "+z", "up": "+y", "roles": {101: "HF", 102: "MF"},
                        "domain_planes": ["x0"], "remove_lid_planes": ["x0"]}
            registered = await call(app, "POST", "/api/models/files", {**original, **settings})
            rebuilt = await call(app, "POST", f"/api/models/{registered['model']['model_id']}/prepare", settings)
            assert rebuilt["prepared"]["ingest_id"] == registered["prepared"]["ingest_id"]
            assert rebuilt["prepared"]["source_file"]["transform"]["removed_lids"]["x0"]["triangle_count"] == 8
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())
