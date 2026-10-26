"""Generated file surfaces exercise the shared import owners, without local assets."""

import numpy as np
import pytest

from server.cadlink.store import CadLinkStore
from server.cadlink.wglink_protocol import source_physical_name
from server.mesh.imported import parse_source_physical_name
from server.solver.imported_channels import channel_source_identity
from server.mesh.file_import import FileMesh, group_role_guess, mesh_text, parse_file_mesh
from test_mesh_import import registration, register_mesh, request


@pytest.mark.parametrize("role", ["HF", "MF", "LF", "PASSIVE_CARDIOID", "PORT_EXIT"])
@pytest.mark.parametrize("instance", [None, "driver-1"])
def test_descriptor_parser_round_trips_its_writer(role, instance):
    assert parse_source_physical_name(source_physical_name(101, "source-1", instance, role)) == {
        "tag": 101, "source_id": "source-1", "instance_id": instance,
        "role": "PASSIVE_CARDIOID" if role == "PORT_EXIT" else role,
    }
    assert parse_source_physical_name("wg-import-v1|rigid") == {"role": "RIGID"}


@pytest.mark.parametrize("name", [
    "wg-import-v1|rigid|role=HF", "WG-import-v1|rigid",
    "wg-import-v1|tag=0|source_id=s|instance_id=null|role=HF",
    "wg-import-v1|tag=101|source_id=s|instance_id=null|role=unknown",
    "wg-import-v1|tag=101|source_id=s|instance_id=null|role=SOURCE",
    "wg-import-v1|tag=101|source_id=s|role=HF",
    "wg-import-v1|tag=101|source_id=s|instance_id=null|role=HF|extra=x",
])
def test_descriptor_parser_requires_the_exact_supported_descriptor(name):
    assert parse_source_physical_name(name) is None


def box_mesh(*, planes=(), negative=(), capped=(), hole=False, descriptors=True):
    """A small box with two source patches, also representing case-00101's domain."""
    points = np.asarray([
        [0, 0, 0], [.2, 0, 0], [.2, .2, 0], [0, .2, 0],
        [0, 0, .3], [.2, 0, .3], [.2, .2, .3], [0, .2, .3],
    ])
    faces = np.asarray([
        [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
        [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
        [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
    ])
    tags = np.asarray([1, 1, 101, 102, 1, 1, 1, 1, 1, 1, 1, 1])
    keep = np.ones(len(faces), dtype=bool)
    for plane in planes:
        axis = {"x0": 0, "y0": 1}[plane]
        if plane not in capped:
            keep &= ~np.all(points[faces, axis] == 0, axis=1)
    if hole:
        keep[6] = False
    faces, tags = faces[keep], tags[keep]
    # Subdivide once so each open plane has several exclusive rim edges, as
    # required by the mesher's detector (a lone leak is never a mirror plane).
    vertices = points.tolist()
    midpoints = {}
    def midpoint(a, b):
        key = tuple(sorted((int(a), int(b))))
        if key not in midpoints:
            midpoints[key] = len(vertices)
            vertices.append(((points[a] + points[b]) / 2).tolist())
        return midpoints[key]
    refined = []
    for a, b, c in faces:
        ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
        refined.extend([[a, ab, ca], [ab, b, bc], [ca, bc, c], [ab, bc, ca]])
    points, faces, tags = np.asarray(vertices), np.asarray(refined), np.repeat(tags, 4)
    for plane in negative:
        points[:, {"x0": 0, "y0": 1}[plane]] *= -1
    if len(negative) % 2:
        faces = faces[:, [0, 2, 1]]
    names = {1: "wg-import-v1|rigid", 101: source_physical_name(101, "hf", None, "HF"),
             102: source_physical_name(102, "mf", None, "MF")} if descriptors else {1: "Walls", 101: "Tweeter", 102: "Mid"}
    return FileMesh(points, faces, tags, names)


@pytest.mark.parametrize("name,role,origin", [
    (source_physical_name(101, "mid", None, "HF"), "HF", "file"),
    (source_physical_name(102, "hf", None, "PORT_EXIT"), "PASSIVE_CARDIOID", "file"),
    ("wg-import-v1|rigid", "RIGID", "file"),
    ("HF left", "HF", "name"), ("tweeter1", "HF", "name"),
    ("midrange", "MF", "name"), ("bass cone", "LF", "name"),
    ("PORT_EXIT", "PASSIVE_CARDIOID", "name"), ("radiating", "HF", "name"),
    ("shell", "RIGID", "default"), ("shelf", "RIGID", "default"),
])
def test_role_guess(name, role, origin):
    guess = group_role_guess(name)
    assert (guess["suggested_role"], guess["role_from"]) == (role, origin)


def test_descriptor_groups_have_readable_names_and_si_units():
    mesh = parse_file_mesh(mesh_text(box_mesh()))
    groups = {g["tag"]: g for g in mesh.summary()["groups"]}
    assert groups[101]["display_name"] == "HF source (101)"
    assert groups[102]["display_name"] == "MF source (102)"
    assert groups[1]["display_name"] == "Walls (1)"
    assert all(g["role_from"] == "file" for g in groups.values())
    mesh.points *= 1000  # Descriptors take priority over the size heuristic.
    assert mesh.summary()["suggested_units"] == "m"


@pytest.mark.parametrize("extent,units", [(5, "m"), (5.1, None), (19.9, None), (20, "mm")])
def test_units_guess(extent, units):
    mesh = box_mesh(descriptors=False)
    mesh.points *= extent / .3
    assert mesh.summary()["suggested_units"] == units


def test_multiple_generic_sources_need_a_choice():
    mesh = box_mesh(descriptors=False)
    mesh.names.update({101: "source one", 102: "driver two"})
    groups = [g for g in mesh.summary()["groups"] if g["tag"] != 1]
    assert all(g["suggested_role"] == "RIGID" and g["needs_role_choice"] for g in groups)


def test_roles_are_canonical_and_part_of_preparation_identity(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        body = registration(msh_text=mesh_text(box_mesh()), units="m", source_tags=[],
                            roles={1: "RIGID", 101: "HF", 102: "MF"})
        first = register_mesh(store, tmp_path, body)
        assert register_mesh(store, tmp_path, body)["ingest_id"] == first["ingest_id"]
        assert [(s["id"], s["role"]) for s in first["sources"]] == [("group_101", "HF"), ("group_102", "MF")]
        assert first["sources"][0]["name"] == box_mesh().names[101]
        geometry = request(first, drive_channels=[
            {"id": "hf", "source_ids": ["group_101"]},
            {"id": "mf", "source_ids": ["group_102"]},
        ]).geometry
        channels = channel_source_identity(geometry, first)
        assert channels["hf"]["role"] == "HF"
        assert channels["mf"]["role"] == "MF"
        body.roles[102] = "LF"
        second = register_mesh(store, tmp_path, body)
        assert second["manifest_sha256"] != first["manifest_sha256"]
        assert second["ingest_id"] != first["ingest_id"]
        alias = registration(source_tags=[], roles={2: "PORT_EXIT"})
        assert alias.roles == {2: "PASSIVE_CARDIOID"}
        for changes in ({"roles": {999: "HF"}}, {"source_tags": [], "roles": {2: "RIGID"}}):
            with pytest.raises(ValueError):
                register_mesh(store, tmp_path, registration(**changes))
        with pytest.raises(ValueError):
            registration(roles={2: "invented"})
    finally:
        store.close()


def test_legacy_preparation_keeps_its_pinned_digest_and_id(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        record = register_mesh(store, tmp_path, registration())
        assert record["manifest_sha256"] == "sha256:d37a7e7dca517e718516b5f73e6e04068286cabf934d32c9a12c0b56a161b9ff"
        assert record["mesh_content_sha256"] == "sha256:31c8fa5660d3a2ae3aeb97011ba256e9c8997ba5a34178fbd234a9bb6f00670b"
        assert "roles" not in record["source_file"]
        assert record["sources"][0]["role"] == "HF"
        assert register_mesh(store, tmp_path, registration())["ingest_id"] == record["ingest_id"]
    finally:
        store.close()
