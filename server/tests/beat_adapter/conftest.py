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
