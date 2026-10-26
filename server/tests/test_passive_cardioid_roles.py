"""File and CAD records share one role-first passive-cardioid lookup."""

import pytest

from server.cadlink.store import CadLinkStore
from server.jobs.models import passive_cardioid_apertures
from server.jobs.runtime import ImportedSolveRefusal
from server.jobs.runtime.cad_lane import _validate_passive_cardioid_topology
from server.mesh.file_import import mesh_text
from server.solver import metal
from test_file_mesh_roles_symmetry import box_mesh
from test_mesh_import import register_mesh, registration, request


@pytest.mark.parametrize("coupled", [False, True])
def test_file_record_roles_work_at_admission_and_in_the_solver(tmp_path, coupled):
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        record = register_mesh(store, tmp_path, registration(
            msh_text=mesh_text(box_mesh()), units="m", source_tags=[],
            roles={101: "MF", 102: "PASSIVE_CARDIOID"},
        ))
        geometry = request(
            record, drive_channels=[
                {"id": "mid", "source_ids": ["group_101"], "driver": {
                    "sd_cm2": 210.0, "bl_t_m": 10.5, "re_ohm": 5.3, "le_mh": 0.5,
                    "mmd_g": 12.0, "cms_m_per_n": 4.0e-4, "rms_kg_per_s": 1.2, "xmax_mm": 5.0,
                }},
                {"id": "port", "source_ids": ["group_102"]},
            ],
            passive_cardioid_rear_volume_l=6.0, passive_cardioid_port_length_mm=25.0,
            model_port_area_m2=0.05, bem_port_area_m2=0.01, port_area_source="user",
            passive_cardioid_foam_resistance_pa_s_m3=10_000.0, passive_cardioid_coupled=coupled,
        ).geometry
        _validate_passive_cardioid_topology(geometry, record)
        assert metal._passive_cardioid_apertures(record["source_tags"], record) == (
            {"group_102": [102], "MF": [101]}, ["group_102"], "group_101",
        )
        if coupled:
            geometry.drive_channels[0].driver = None
            with pytest.raises(ImportedSolveRefusal, match="driver model"):
                _validate_passive_cardioid_topology(geometry, record)
    finally:
        store.close()


@pytest.mark.parametrize("role", ["PASSIVE_CARDIOID", "PORT_EXIT"])
@pytest.mark.parametrize("port_id,mf_id", [("group_1", "group_2"), ("f2-passive_cardioid", "f2-mf")])
def test_explicit_file_and_cad_roles_win_over_legacy_names_for_both_surfaces(role, port_id, mf_id):
    tags = {port_id: 11, mf_id: 12, "PORT_EXIT": 13, "MF": 14}
    record = {"sources": [{"id": port_id, "role": role}, {"id": mf_id, "role": "MF"}]}
    assert passive_cardioid_apertures(tags, record) == ({port_id: [11], "MF": [12]}, [port_id], mf_id)


@pytest.mark.parametrize("first_role", ["PASSIVE_CARDIOID", "PORT_EXIT"])
def test_multiple_port_role_surfaces_are_kept_together(first_role):
    tags = {"group_1": 11, "group_2": 12, "group_3": 13}
    record = {"sources": [{"id": "group_1", "role": first_role},
                          {"id": "group_2", "role": "PASSIVE_CARDIOID"}, {"id": "group_3", "role": "MF"}]}
    assert passive_cardioid_apertures(tags, record) == (
        {"group_1": [11], "group_2": [12], "MF": [13]}, ["group_1", "group_2"], "group_3",
    )


@pytest.mark.parametrize("roles", [[], [{"id": "unknown", "role": "PASSIVE_CARDIOID"}]])
def test_name_fallback_remains_available_when_no_role_has_a_tag(roles):
    assert passive_cardioid_apertures({"port_exit_l": 1, "PORT_EXIT_R": 2, "mf": 3}, {"sources": roles}) == (
        {"port_exit_l": [1], "PORT_EXIT_R": [2], "mf": [3]}, ["port_exit_l", "PORT_EXIT_R"], "mf",
    )


@pytest.mark.parametrize("mf_ids", [[], ["group_2", "group_3"]])
def test_missing_or_ambiguous_mf_role_is_rejected(mf_ids):
    record = {"sources": [{"id": "group_1", "role": "PASSIVE_CARDIOID"},
                          *[{"id": source_id, "role": "MF"} for source_id in mf_ids]]}
    with pytest.raises(ValueError, match="exactly one MF diaphragm"):
        passive_cardioid_apertures({"group_1": 1, "group_2": 2, "group_3": 3}, record)


def test_port_source_id_mf_does_not_overwrite_the_diaphragm():
    record = {"sources": [{"id": "MF", "role": "PASSIVE_CARDIOID"}, {"id": "group_2", "role": "MF"}]}
    assert passive_cardioid_apertures({"MF": 1, "group_2": 2}, record) == (
        {"MF": [1], "group_2": [2]}, ["MF"], "group_2",
    )


def test_role_based_apertures_cannot_share_a_physical_tag():
    record = {"sources": [{"id": "group_1", "role": "PASSIVE_CARDIOID"}, {"id": "group_2", "role": "MF"}]}
    with pytest.raises(ValueError, match="duplicate faces"):
        passive_cardioid_apertures({"group_1": 1, "group_2": 1}, record)
