"""Retained exterior names keep their historical source-id interpretation."""

from types import SimpleNamespace

import numpy as np
import pytest

from server.solver import coupled_admission
from server.solver.beat_adapter import coupled_request
from server.mesh.imported import parse_source_physical_name


NAMES = [
    ("wg-import-v1|tag=101|source_id=target|instance_id=null|role=HF", ("target",)),
    ("source_id=target", ("target",)),
    ("unrelated|source_id=first|source_id=target", ("first", "target")),
    ("source_id=|source_id=target", ("", "target")),
    ("source_id=target=tail|other=x", ("target=tail",)),
    ("source_id==target", ("=target",)),
    ("source_id= \n|other=x", (" \n",)),
    ("source_id=target|source_id=target", ("target", "target")),
    ("wg-import-v1|tag=bad|source_id=target|role=UNKNOWN", ("target",)),
    ("prefixsource_id=target|source_id =target|SOURCE_ID=target", ()),
    ("wg-import-v1|rigid", ()),
    ("", ()),
]

SELECTABLE_NAMES = [(name, source_ids, "target") for name, source_ids in NAMES] + [
    ("source_id=|source_id=target", ("", "target"), ""),
    ("source_id=target=tail", ("target=tail",), "target=tail"),
    ("source_id=target|source_id=second", ("target", "second"), "target|source_id=second"),
]


@pytest.mark.parametrize("name,source_ids", NAMES)
def test_descriptor_legacy_mode_keeps_all_fields_in_order(name, source_ids):
    assert parse_source_physical_name(name, legacy_source_ids=True) == {"source_ids": source_ids}


@pytest.mark.parametrize("name,source_ids", NAMES)
def test_painted_sources_collect_all_source_id_fields(name, source_ids):
    record = {"source_tags": {"existing": 1}, "sources": [{"id": "declared"}],
              "coupled_exterior": {"physical_names": {name: {}}}}
    assert coupled_admission.painted_source_ids(record) == {"existing", "declared", *source_ids}


@pytest.mark.parametrize("name,source_ids,target", SELECTABLE_NAMES)
def test_moving_meshes_match_any_source_id_field(monkeypatch, name, source_ids, target):
    from server.mesh import artifact, fem_coupling
    from server.solver import coupled_system

    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    faces = np.array([[0, 1, 2]])
    monkeypatch.setattr(artifact, "read_verified_coupled_exterior_mesh", lambda _record: "mesh")
    monkeypatch.setattr(fem_coupling, "read_coupled_exterior", lambda _mesh: (points, {name: faces}))
    monkeypatch.setattr(coupled_system, "_oriented_exterior_surfaces", lambda *a, **k: [(name, faces)])
    record = {"coupled_exterior": {"physical_names": {name: {}}}}
    channel = SimpleNamespace(id="drive", source_ids=[target])
    if target in source_ids:
        result = coupled_admission._channel_moving_meshes(record, channel)
        np.testing.assert_array_equal(result["exterior"][0][0], faces)
        np.testing.assert_allclose(result["exterior"][0][1], points[faces] * .001)
    else:
        with pytest.raises(ValueError, match="has no faces in the prepared mesh"):
            coupled_admission._channel_moving_meshes(record, channel)


@pytest.mark.parametrize("name,source_ids,target", SELECTABLE_NAMES)
def test_driver_surface_uses_only_first_source_id_field(monkeypatch, name, source_ids, target):
    from server.mesh import artifact, fem_coupling
    from server.solver import component_symmetry, coupled_system

    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    faces = np.array([[0, 1, 2]])
    calls = []
    monkeypatch.setattr(artifact, "read_verified_coupled_exterior_mesh", lambda _record: "mesh")
    monkeypatch.setattr(fem_coupling, "read_coupled_exterior", lambda _mesh: (points, {name: faces}))
    monkeypatch.setattr(coupled_system, "_oriented_exterior_surfaces", lambda *a, **k: [(name, faces)])
    monkeypatch.setattr(component_symmetry, "infer_component_symmetry", lambda *a, **k: calls.append((a, k)))
    record = {"coupled_exterior": {"physical_names": {name: {}}}}
    channel = SimpleNamespace(id="drive", source_ids=[target],
                              exterior_transducer=SimpleNamespace(motion_axis=[0, 0, 1]))
    assert coupled_admission.driver_surface_problem(record, [channel]) is None
    assert bool(calls) is bool(source_ids and source_ids[0] == target)


@pytest.mark.parametrize("name,source_ids,target", SELECTABLE_NAMES)
def test_beat_exterior_uses_first_source_id_and_preserves_unmatched_kind(monkeypatch, name, source_ids, target):
    monkeypatch.setattr(coupled_admission, "fem_request_problem", lambda *a, **k: None)
    monkeypatch.setattr(coupled_request, "read_verified_fem_region_mesh", lambda *a: "region")
    monkeypatch.setattr(coupled_request, "read_fem_region_domain", lambda *a: object())
    monkeypatch.setattr(coupled_request, "read_verified_coupled_exterior_mesh", lambda *a: "exterior")
    monkeypatch.setattr(coupled_request, "read_coupled_exterior", lambda *a: (np.zeros((0, 3)), {}))
    monkeypatch.setattr(coupled_request, "imported_anchor_frame", lambda *a: {})
    monkeypatch.setattr(coupled_request, "_layout", lambda *a: object())
    captured = []
    def compile_request(exterior, *args, **kwargs):
        captured.append(exterior)
        return SimpleNamespace(channel_ports={"drive": ("port",)})
    monkeypatch.setattr(coupled_request, "build_coupled_request", compile_request)
    channel = SimpleNamespace(id="drive", source_ids=[target], patch_weights={target: .25},
                              exterior_transducer=None)
    geometry = SimpleNamespace(drive_channels=[channel], combine=None)
    record = {"fem_regions": [{"region_id": "r", "surface_groups": [{"kind": "interface"}]}],
              "coupled_exterior": {"physical_names": {name: {"kind": "moving", "drive_channel_id": "old"}}}}
    coupled_request.build_imported_coupled_request(record, geometry, SimpleNamespace(ground_plane=None),
                                                  frequencies_hz=[100.])
    kind = captured[0].kinds[name]
    if source_ids and source_ids[0] == target:
        assert kind == {"kind": "moving", "drive_channel_id": "drive", "motion_weight": .25}
    elif source_ids:
        assert kind == {"kind": "rigid"}
    else:
        assert kind == {"kind": "moving", "drive_channel_id": "old"}
