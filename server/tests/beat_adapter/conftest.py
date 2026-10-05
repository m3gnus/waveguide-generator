"""In-memory official wire fixtures; no engine, Julia, worker or user paths."""

from __future__ import annotations

import base64
from typing import Any

import numpy as np
import pytest

from server.contracts.conventions import SOLVER_TIME_CONVENTION
from server.solver.beat_adapter.observations import build_observations
from server.solver.beat_adapter.results import result_outputs


def wire(values: Any, precision: str = "float64") -> dict[str, Any]:
    array = np.asarray(values, dtype="<c8" if precision == "float32" else "<c16")
    return {"encoding": "base64", "dtype": "complex64" if precision == "float32" else "complex128",
            "shape": list(array.shape), "order": "C", "byte_order": "little",
            "content_base64": base64.b64encode(array.tobytes(order="C")).decode("ascii")}


@pytest.fixture
def layout():
    return build_observations(angle_range=(-90.0, 90.0, 3),
                              planes=("vertical", "diagonal", "horizontal"),
                              sphere_grid=(3, 4), precision="float64")


@pytest.fixture
def raw_result(layout):
    def make(frequency=500.0, *, symmetry="off", precision="float64", traces=True):
        items = []
        for output in result_outputs(layout, surface_traces=traces):
            kind = output["quantity"]
            if kind == "exterior_pressure":
                name = output["id"].removeprefix("pressure:")
                size = len(layout.points_m[name])
                data = (np.arange(size) + 1.0 + 2j)[None, :]
                unit, axes = "Pa", ["excitation", "observation"]
            elif kind == "radiation_impedance":
                data = [4.0 - 5j]
                unit, axes = "N*s/m", ["radiator"]
            elif kind == "bem_boundary_pressure":
                data = [[9 + 2j, 1 - 3j, 7 + 4j, 2 + 5j]]
                unit, axes = "Pa", ["excitation", "bem_node"]
            else:
                data = [[11 - 2j, 3 + 7j]]
                unit, axes = "Pa/m", ["excitation", "bem_face"]
            items.append({"id": output["id"], "quantity": kind, "unit": unit,
                          "axes": axes, "target_id": None, "metadata": {},
                          "values": wire(data, precision)})
        return {"schema_version": 2, "freq_hz": frequency, "excitation_port_ids": ["source"],
                "quantities": items,
                "diagnostics": {"phasor_convention": SOLVER_TIME_CONVENTION,
                                "symmetry": symmetry, "precision": precision, "bem_backend": "cpu"}}
    return make


class EventStream:
    def __init__(self, events):
        self.events = events
        self.closed = 0

    def __iter__(self):
        yield from self.events

    def close(self):
        self.closed += 1


@pytest.fixture
def run_sweep(layout):
    from server.solver.beat_adapter.results import map_sweep

    def run(stream, frequencies=(500.0, 1000.0), **kwargs):
        arguments = {"layout": layout, "source_area_m2": 0.004, "excitation_port_id": "source",
                     "precision": "float64", "trace_counts": (4, 2)}
        arguments.update(kwargs)
        return map_sweep(stream, frequencies, **arguments)
    return run


@pytest.fixture(scope="session")
def official_contract():
    """Load the checkout's schema validator without importing the engine package."""
    import importlib.util
    import json
    from pathlib import Path

    roots = [Path("/Users/magnus/Code/hornlab-workspace/BEAT_Engine/src/beat_engine/beat_contract"),
             Path("/private/tmp/beat-batch1-candidate/src/beat_engine/beat_contract")]
    for root in roots:
        schema_path = root / "system-v1.schema.json"
        if not schema_path.is_file():
            continue
        schema = json.loads(schema_path.read_text())
        if 2 not in schema["$defs"]["compiled_system"]["properties"]["contract_version"].get("enum", []):
            continue  # Main can predate PR #18; use the read-only batch-1 candidate.
        spec = importlib.util.spec_from_file_location("wg_test_official_contract", root / "__init__.py")
        contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contract)

        def validate(request):
            # JSON round-trip proves requests contain ordinary durable wire values.
            wire_request = json.loads(json.dumps(request.wire, allow_nan=False))
            contract.validate_solve_request(wire_request)
            return request
        return validate
    pytest.skip("Official BEAT system-v1 JSON schema with PR #18 contract v2 is absent "
                "from the BEAT checkout and /private/tmp/beat-batch1-candidate")


@pytest.fixture
def make_mesh():
    def make(points, faces, tags, *, node_ids=None):
        node_ids = node_ids or list(range(1, len(points) + 1))
        nodes = [" ".join(map(str, [node, *point])) for node, point in zip(node_ids, points)]
        elements = [" ".join(map(str, [index + 51, 2, 2, tag, tag,
                                      *(node_ids[n] for n in face)]))
                    for index, (face, tag) in enumerate(zip(faces, tags))]
        return "\n".join(["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes", str(len(points)),
                          *nodes, "$EndNodes", "$Elements", str(len(faces)), *elements,
                          "$EndElements", ""])
    return make


@pytest.fixture
def compiled_result():
    def make(request, *, boundary_pressure=None, forces=None, frequency=None):
        ports = request.wire["excitation_port_ids"]
        options = request.wire["solver_options"]
        precision = options["precision"]
        count = len(ports)
        if boundary_pressure is None:
            boundary_pressure = np.tile(np.arange(len(request.mesh.points_m)) + 2 + 3j, (count, 1))
        if forces is None:
            forces = np.arange(count) + 5 - 2j
        quantities = []
        for output in request.wire["outputs"]:
            kind = output["quantity"]
            if kind == "exterior_pressure":
                size = len(output["options"]["points_m"])
                data = np.repeat((np.arange(count) + 1 + 2j)[:, None], size, axis=1)
                unit, axes = "Pa", ["excitation", "observation"]
            elif kind == "radiation_impedance":
                data, unit, axes = forces, "N*s/m", ["radiator"]
            elif kind == "bem_boundary_pressure":
                data, unit, axes = boundary_pressure, "Pa", ["excitation", "bem_node"]
            else:
                data = np.tile(np.arange(len(request.mesh.faces)) + 11 + 2j, (count, 1))
                unit, axes = "Pa/m", ["excitation", "bem_face"]
            quantities.append({"id": output["id"], "quantity": kind, "unit": unit, "axes": axes,
                               "target_id": None, "metadata": {}, "values": wire(data, precision)})
        return {"schema_version": 2,
                "freq_hz": frequency if frequency is not None else request.wire["frequencies_hz"][0],
                "excitation_port_ids": ports.copy(), "quantities": quantities,
                "diagnostics": {key: options[key] for key in
                                ("phasor_convention", "symmetry", "precision", "bem_backend")}}
    return make
