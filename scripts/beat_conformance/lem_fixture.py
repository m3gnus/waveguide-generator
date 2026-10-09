"""Reference-only coupled request, not a general PreparedModel compiler.

Uses the public packed mesh transport. Topology indices refer to its source
vertex/triangle arrays (zero-based); both meshes receive the same placement.
The inner FEM boundary normal points into the sphere: dot(n,+z) is -cos(theta).
BEAT projects the transducer axis against that normal itself; no extra sign,
area factor, RMS conversion, or duplicate exterior source is applied here.
"""

from copy import deepcopy
from beat_engine.beat_contract import validate_solve_request
from .reference_support import POSITIVE_TIME, NEGATIVE_TIME

DRIVER_PARAMETERS = {
    "re_ohm": 6.0,
    "le_h": 0.0005,
    "bl_n_per_a": 7.0,
    "mmd_kg": 0.02,
    "cms_m_per_n": 0.001,
    "rms_n_s_per_m": 1.5,
    "motion_profile": "rigid_translation",
    "motion_axis": [0.0, 0.0, 1.0],
}
REFERENCE_VOLTAGE_V = 2.83
COMPONENT_ID = "component:sphere-driver"
PORT_ID = "excitation:sphere-driver"


def build_lem_reference_request(
    fem_data, bem_data, topology, frequencies_hz, points_m, *, centre_m, phasor_convention
):
    """Lossless same-air FEM shell -> BEM exterior; explicit 2.83 V basis."""
    if phasor_convention not in (POSITIVE_TIME, NEGATIVE_TIME):
        raise ValueError("Unknown phasor convention.")

    def group(mesh, dimension, tag):
        return {"mesh_id": mesh, "dimension": dimension, "tag": tag}

    def boundary(identifier, region, kind, mesh, tag):
        return {
            "id": identifier,
            "name": identifier,
            "region_id": region,
            "kind": kind,
            "group": group(mesh, 2, tag),
            "parameters": {},
        }

    system = {
        "id": "system:lem-sphere",
        "name": "LEM translating sphere reference",
        "contract_version": 2,
        "meshes": [
            {
                "id": identifier,
                "name": identifier,
                "purpose": purpose,
                "file": "",
                "mesh_data": deepcopy(data),
                "scale_to_m": 1.0,
                "translation_m": list(centre_m),
            }
            for identifier, purpose, data in (
                ("mesh:shell", "fem_volume", fem_data),
                ("mesh:exterior", "bem_surface", bem_data),
            )
        ],
        "regions": [
            {
                "id": identifier,
                "name": identifier,
                "kind": kind,
                "mesh_ids": [mesh],
                "volume_groups": [group(mesh, 3, 3)] if kind == "bounded_air" else [],
                "sound_speed_m_per_s": 343.0,
                "density_kg_per_m3": 1.21,
                "loss_model": {},
            }
            for identifier, kind, mesh in (
                ("region:shell", "bounded_air", "mesh:shell"),
                ("region:exterior", "unbounded_air", "mesh:exterior"),
            )
        ],
        "boundaries": [
            boundary("boundary:diaphragm", "region:shell", "moving", "mesh:shell", 1),
            boundary("boundary:fem-interface", "region:shell", "interface", "mesh:shell", 2),
            boundary("boundary:bem-interface", "region:exterior", "interface", "mesh:exterior", 2),
        ],
        "interfaces": [
            {
                "id": "interface:shell",
                "name": "Shell to exterior",
                "bounded_boundary_id": "boundary:fem-interface",
                "unbounded_boundary_id": "boundary:bem-interface",
                "topology": deepcopy(topology),
            }
        ],
        "components": [
            {
                "id": COMPONENT_ID,
                "name": "Sphere driver",
                "kind": "electrodynamic_transducer",
                "boundary_ids": ["boundary:diaphragm"],
                "parameters": deepcopy(DRIVER_PARAMETERS),
            }
        ],
        "excitation_ports": [
            {"id": PORT_ID, "name": "2.83 V", "component_id": COMPONENT_ID, "kind": "voltage"}
        ],
    }
    outputs = [
        {"id": name, "quantity": name, "target_ids": [], "options": {}}
        for name in ("diaphragm_velocity", "voice_coil_current")
    ]
    outputs.append(
        {
            "id": "pressure",
            "quantity": "exterior_pressure",
            "target_ids": [],
            "options": {"points_m": [list(p) for p in points_m]},
        }
    )
    request = {
        "schema_version": 1,
        "compiled_system": system,
        "frequencies_hz": list(frequencies_hz),
        "excitation_port_ids": [PORT_ID],
        "outputs": outputs,
        "solver_options": {
            "bem_backend": "cpu",
            "precision": "float64",
            "symmetry": "off",
            "phasor_convention": phasor_convention,
            "transducer_reference_voltage_v": REFERENCE_VOLTAGE_V,
            "regular_quadrature_mode": "fixed",
            "quadrature_order": 4,
            "singular_order": 4,
        },
    }
    validate_solve_request(request)
    return request
