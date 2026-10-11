"""The coupled FEM/BEM compiled system, checked by BEAT's own contract."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from server.mesh.fem_regions import read_fem_region_domain
from server.solver.coupled_system import (
    CoupledSystemError,
    ExteriorSurface,
    FemRegionInput,
    build_coupled_system,
    open_edges,
    solve_kind,
)
from server.tests.test_fem_regions import _mesh, _region, _write_step

contract = pytest.importorskip("beat_engine.beat_contract")

CONE = "wg-fem-v1|region=rear-chamber|group=cone|kind=moving"
PORT = "wg-fem-v1|region=rear-chamber|group=port|kind=interface"
WALLS = "wg-fem-v1|region=rear-chamber|group=walls|kind=rigid"


@pytest.fixture(scope="module")
def chamber(tmp_path_factory):
    step = _write_step(tmp_path_factory.mktemp("fem") / "air.step")
    region = _region()
    built = _mesh(step, region)
    return region, read_fem_region_domain(built["msh_text"], "rear-chamber")


def _exterior_from(domain, *, cone_kind="moving"):
    """An exterior that is the chamber's own skin, re-indexed like a BEM mesh.

    The port patch keeps the FEM interface's physical name; the cone is the
    other face of the same diaphragm; the rest is rigid.
    """

    used = np.unique(np.concatenate(list(domain.surfaces.values())))
    remap = -np.ones(len(domain.points_mm), dtype=np.int64)
    order = used[::-1]  # a different vertex order, as a separate mesh would have
    remap[order] = np.arange(len(order))
    groups = {name: remap[block] for name, block in domain.surfaces.items()}
    kinds = {
        PORT: {"kind": "interface"},
        CONE: {"kind": cone_kind, "drive_channel_id": "drive-LF"} if cone_kind == "moving" else {"kind": cone_kind},
        WALLS: {"kind": "rigid"},
    }
    return ExteriorSurface(points_mm=domain.points_mm[order], groups=groups, kinds=kinds)


def _request(system):
    return {
        "schema_version": 1,
        "compiled_system": system,
        "frequencies_hz": [100.0, 1000.0],
        "excitation_port_ids": [port["id"] for port in system["excitation_ports"]],
        "outputs": [{"id": "fem", "quantity": "fem_nodal_pressure", "target_ids": [], "options": {}}],
        "solver_options": {"phasor_convention": "exp(+i omega t)"},
    }


def test_a_coupled_chamber_passes_beats_contract(chamber):
    region, domain = chamber
    system = build_coupled_system(_exterior_from(domain), [FemRegionInput(region, domain)])

    contract.validate_compiled_system(system)
    contract.validate_solve_request(_request(system))
    assert solve_kind(system) == "coupled_fem_bem_lem"
    assert [item["kind"] for item in system["regions"]] == ["unbounded_air", "bounded_air"]
    assert system["regions"][1]["loss_model"] == {"bulk_loss_factor": 0.0}
    # The cone's two faces are one source, driven by one port.
    (component,) = system["components"]
    assert component["boundary_ids"] == ["boundary:exterior:0", "boundary:fem:rear-chamber:cone"]
    assert system["excitation_ports"] == [{
        "id": "port:drive-LF", "name": "drive-LF",
        "component_id": "component:drive-LF", "kind": "normal_velocity",
    }]
    (interface,) = system["interfaces"]
    topology = interface["topology"]
    assert topology["bem_boundary_edges"] == 0
    assert topology["max_coordinate_error"] == 0.0
    assert len(topology["fem_face_indices"]) == len(domain.surfaces[PORT])
    assert topology["fem_facets_on_tetra_boundary"] == len(domain.surfaces[PORT])
    assert set(topology["normal_sign"]) == {1}
    # Coordinates leave in metres.
    points = np.frombuffer(
        __import__("base64").b64decode(system["meshes"][1]["mesh_data"]["points"]["data"]), "<f8"
    ).reshape(-1, 3)
    assert points.max() == pytest.approx(0.040)


def test_an_interior_only_driver_compiles_without_an_exterior(chamber):
    region, domain = chamber
    region = copy.deepcopy(region)
    region["surface_groups"][1]["kind"] = "plane-wave-termination"
    surfaces = {
        name.replace("group=port|kind=interface", "group=port|kind=plane-wave-termination"): block
        for name, block in domain.surfaces.items()
    }
    interior = type(domain)(domain.region_id, domain.points_mm, domain.tetrahedra, surfaces)
    system = build_coupled_system(None, [FemRegionInput(region, interior)])
    contract.validate_compiled_system(system)
    assert solve_kind(system) == "interior_fem"
    kinds = sorted(boundary["kind"] for boundary in system["boundaries"])
    assert kinds == ["moving", "plane_wave_tube_termination", "rigid"]


def test_refusals_name_what_to_fix(chamber):
    region, domain = chamber
    exterior = _exterior_from(domain)
    open_groups = dict(exterior.groups)
    del open_groups[WALLS]
    with pytest.raises(CoupledSystemError, match="open edges"):
        build_coupled_system(
            ExteriorSurface(exterior.points_mm, open_groups, exterior.kinds),
            [FemRegionInput(region, domain)],
        )
    with pytest.raises(CoupledSystemError, match="no matching exterior patch"):
        build_coupled_system(None, [FemRegionInput(region, domain)])
    shifted = ExteriorSurface(exterior.points_mm + [0.0, 0.0, 0.5], exterior.groups, exterior.kinds)
    with pytest.raises(CoupledSystemError, match="does not conform"):
        build_coupled_system(shifted, [FemRegionInput(region, domain)])
    with pytest.raises(CoupledSystemError, match="at least one FEM region"):
        build_coupled_system(exterior, [])


def test_open_edges_counts_a_missing_face():
    triangles = np.array([[0, 1, 2], [0, 2, 3]])
    assert open_edges(triangles) == 4
