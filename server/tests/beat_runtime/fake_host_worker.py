"""Public engine stand-in for fast detached-host tests; never starts Julia."""

from __future__ import annotations

import json
import os
import copy
from pathlib import Path
import threading
import time


class EngineWorker:
    def __init__(self, **kwargs):
        self.events = Path(kwargs["environment"]["TEST_EVENTS"])
        self.environment = kwargs["environment"]
        self._info = None
        self._stream = None
        if kwargs["environment"].get("TEST_FAIL"):
            raise RuntimeError("fixture constructor failure")
        time.sleep(float(kwargs["environment"].get("TEST_DELAY", "0")))
        # Fixture logs must never persist the test runner's ambient secrets.
        logged_kwargs = dict(kwargs)
        logged_kwargs["environment"] = {
            name: value for name, value in kwargs["environment"].items()
            if name.startswith(("JULIA_", "BLAB_", "TEST_")) or name == "PATH"
        }
        with self.events.open("a") as stream:
            stream.write(json.dumps({"type": "built", "pid": os.getpid(), "cwd": os.getcwd(),
                                     "kwargs": {k: str(v) if isinstance(v, Path) else v
                                                for k, v in logged_kwargs.items()}}) + "\n")

    def terminate(self):
        time.sleep(float(self.environment.get("TEST_TERMINATE_DELAY", "0")))
        if self.environment.get("TEST_TERMINATE_HANG"):
            self.log("termination_hung")
            threading.Event().wait()
        if self._stream is not None:
            self._stream.closed.set()
        self._info = None
        with self.events.open("a") as stream:
            stream.write(json.dumps({"type": "terminated", "pid": os.getpid()}) + "\n")

    def log(self, kind, **kwargs):
        with self.events.open("a") as stream:
            stream.write(json.dumps({"type": kind, **kwargs}) + "\n")

    @property
    def worker_info(self):
        return copy.deepcopy(self._info)

    def ensure_started(self, **kwargs):
        callback = kwargs.get("status_callback")
        if callback:
            callback("fixture startup")
        gate = self.environment.get("TEST_START_GATE")
        marker = self.environment.get("TEST_START_ONCE_MARKER")
        skip = marker and Path(marker).exists()
        if marker and not skip:
            Path(marker).touch()
        if gate and not skip:
            wait_until(lambda: Path(gate).exists(), timeout=20)
            if callback:
                callback("fixture startup finished")
        if self._info is None:
            self._info = {"type": "ready", "protocol": {"name": "beat-worker", "version": 1},
                          "engine": {"name": "BEAT Engine", "version": "fixture"},
                          "operations": ["solve", "bem_field"],
                          "request_transports": ["file", "inline_json"]}
            if self.environment.get("TEST_COMPILED"):
                from server.tests.beat_runtime.test_probe import WORKER_INFO
                self._info = copy.deepcopy(WORKER_INFO)
            if self.environment.get("TEST_COUPLED"):
                from server.tests.beat_runtime.test_probe import WORKER_INFO
                from server.contracts.conventions import SOLVER_TIME_CONVENTION
                self._info = copy.deepcopy(WORKER_INFO)
                self._info.update(contracts={"system_request": [1], "compiled_system": [2], "system_result": [2],
                                            "field_array": [1], "mesh_data": [1]},
                                  precisions=["float32", "float64"], solve_kinds=["coupled_fem_bem_lem", "interior_fem"],
                                  optional_output_quantities=["interface_average_normal_velocity", "interface_radiated_pressure"],
                                  phasor_conventions=[SOLVER_TIME_CONVENTION], cancellation="marker_file")
            self.log("started")

    def submit(self, request, **kwargs):
        request = json.loads(request.read_text()) if isinstance(request, Path) else request
        if "compiled_system" in request:
            request = dict(request, name="compiled probe")
            if self.environment.get("TEST_PROBE_GATE"):
                request["release_path"] = self.environment["TEST_PROBE_GATE"]
        self.ensure_started(**kwargs)
        self.log("submitted", name=request["name"], operation=kwargs.get("operation", "solve"), request=request)
        self._stream = _Stream(self, request)
        return self._stream


class _Stream:
    def __init__(self, worker, request):
        self.worker, self.request = worker, request
        self.closed = threading.Event()
        self.first = True
        self.terminal = False
        self.solved = 0

    def __next__(self):
        if self.closed.is_set():
            raise StopIteration
        if self.worker.environment.get("TEST_COUPLED"):
            frequencies = self.request["frequencies_hz"]
            if self.solved == len(frequencies):
                self.terminal = True
                return {"type": "completed", "solved_count": self.solved}
            if self.solved == 1 and self.worker.environment.get("TEST_COUPLED_PAUSE_AFTER_FIRST"):
                wait_until(lambda: self.closed.is_set() or Path(self.request["cancel_path"]).exists())
            if Path(self.request["cancel_path"]).exists():
                self.terminal = True
                return {"type": "cancelled", "solved_count": self.solved}
            raw = coupled_result(self.request, frequencies[self.solved])
            self.solved += 1
            return {"type": "result", "result": raw}
        if self.first:
            self.first = False
            if "compiled_system" in self.request:
                from server.tests.beat_runtime.test_probe import result
                return result(backend=self.request["solver_options"]["bem_backend"])
            return {"type": "result", "name": self.request["name"],
                    "payload": "x" * self.request.get("payload_size", 0)}
        gate = self.request.get("release_path")
        if gate:
            self.worker.log("reading", name=self.request["name"])
            wait_until(lambda: self.closed.is_set() or Path(gate).exists(), timeout=20)
        if self.closed.is_set():
            raise RuntimeError("fixture worker terminated during blocked read")
        if self.request.get("read_error"):
            raise RuntimeError("fixture reader failed")
        self.terminal = True
        self.worker.log("completed", name=self.request["name"])
        return {"type": "completed", "solved_count": 1}

    def close(self):
        if self.closed.is_set():
            return
        self.worker.log("closed", name=self.request["name"], terminal=self.terminal)
        if not self.terminal and self.request.get("close_error"):
            raise RuntimeError("fixture retirement failed")
        if not self.terminal and self.request.get("close_unexpected_error"):
            raise LookupError("fixture unexpected retirement failure")
        if not self.terminal and self.request.get("close_hang"):
            threading.Event().wait()
        if not self.terminal and "compiled_system" in self.request:
            # Model the official closeable stream retiring abandoned Julia.
            self.worker.terminate()
        self.closed.set()


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


def events(key):
    path = Path(key["environment"]["TEST_EVENTS"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def coupled_result(request, frequency):
    """Deterministic contract-2 FEM fields without native engine imports or Julia."""
    import base64
    import numpy as np

    meshes = [m for m in request["compiled_system"]["meshes"] if m["purpose"] == "fem_volume"]
    counts = [m["mesh_data"]["points"]["shape"][0] for m in meshes]
    metadata = {"mesh_ids": [m["id"] for m in meshes],
                "region_ids": [r["id"] for r in request["compiled_system"]["regions"]
                               if r["kind"] == "bounded_air"],
                "node_counts": counts, "node_offsets": np.cumsum([0, *counts[:-1]]).tolist()}
    ports = request["excitation_port_ids"]
    system = request["compiled_system"]
    interfaces = system["interfaces"]
    interface_ids = [item["id"] for item in interfaces]
    exterior_ids = {r["id"] for r in system["regions"] if r["kind"] == "unbounded_air"}
    radiation_ids = interface_ids + (["radiation:other-exterior"] if any(
        b["kind"] == "moving" and b["region_id"] in exterior_ids for b in system["boundaries"])
        else []) + ["radiation:total"]
    # Compute the represented area from each authoritative FEM boundary.
    areas = []
    for interface in interfaces:
        boundary = next(b for b in system["boundaries"] if b["id"] == interface["bounded_boundary_id"])
        mesh = next(m for m in meshes if m["id"] == boundary["group"]["mesh_id"])
        points = np.frombuffer(base64.b64decode(mesh["mesh_data"]["points"]["data"]), dtype="<f8").reshape(-1, 3)
        cell = next(c for c in mesh["mesh_data"]["cells"] if c["type"] == "triangle")
        triangles = np.frombuffer(base64.b64decode(cell["connectivity"]["data"]), dtype="<i8").reshape(-1, 3)
        tags = np.frombuffer(base64.b64decode(cell["physical_tags"]["data"]), dtype="<i8")
        faces = points[triangles[tags == boundary["group"]["tag"]]]
        areas.append(float(np.linalg.norm(np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0]), axis=1).sum() / 2))
    quantities = []
    for output in request["outputs"]:
        fem = output["quantity"] == "fem_nodal_pressure"
        transducer = output["quantity"] in {"diaphragm_velocity", "voice_coil_current"}
        interface = output["quantity"] == "interface_average_normal_velocity"
        radiation = output["quantity"] == "interface_radiated_pressure"
        drivers = [c for c in request["compiled_system"]["components"] if c["kind"] == "electrodynamic_transducer"]
        count = sum(counts) if fem else len(drivers) if transducer else len(interfaces) if interface else len(output["options"]["points_m"])
        values = np.asarray([np.full(count, (i + 1) * (2 + 3j)) for i in range(len(ports))], dtype="<c8" if request["solver_options"]["precision"] == "float32" else "<c16")
        extra = {"metadata": metadata} if fem else {}
        if transducer:
            unit = "m/s" if output["quantity"] == "diaphragm_velocity" else "A"
            meta = {"component_ids": [c["id"] for c in drivers]}
            if any(r["kind"] == "unbounded_air" for r in request["compiled_system"]["regions"]):
                meta.update(surface_completion_factors=[1] * len(drivers), physical_driver_orbit_counts=[1] * len(drivers))
            extra = {"metadata": meta}
        else:
            unit = "Pa"
        axes = ["excitation", "fem_node" if fem else "transducer" if transducer else "observation"]
        if interface:
            unit = "m/s"
            axes = ["excitation", "interface"]
            extra = {"metadata": {"interface_ids": interface_ids, "represented_area_m2": areas,
                                  "normal_orientation": "bounded_region_outward",
                                  "spatial_average": "area_weighted_complex_normal_velocity",
                                  "amplitude_convention": "rms"}}
        if radiation:
            # Distinct contributions sum exactly to the ordinary fake total.
            values = np.stack([values * ((i + 1) / sum(range(1, len(radiation_ids))))
                               for i in range(len(radiation_ids) - 1)] + [values], axis=1)
            axes = ["excitation", "radiation_source", "observation"]
            extra = {"metadata": {"interface_ids": interface_ids, "radiation_source_ids": radiation_ids,
                                  "radiation_source_names": radiation_ids, "points_m": output["options"]["points_m"],
                                  "decomposition": "fixed_operating_flux_full_exterior_replay",
                                  "amplitude_convention": "rms"}}
        quantities.append({"id": output["id"], "quantity": output["quantity"], "unit": unit,
                           "target_id": None, "axes": axes,
                           "values": {"encoding": "base64", "dtype": "complex64" if values.dtype.itemsize == 8 else "complex128", "shape": list(values.shape),
                                      "order": "C", "byte_order": "little",
                                      "content_base64": base64.b64encode(values.tobytes()).decode()}, **extra})
    diagnostics = dict(request["solver_options"])
    scalar = np.float32 if diagnostics["precision"] == "float32" else np.float64
    # Julia serializes Float32 scalars using their shortest round-trip decimal.
    def json_scalar(value):
        return float(str(scalar(value)))
    if "transducer_reference_voltage_v" in diagnostics:
        diagnostics["transducer_reference_voltage_v"] = json_scalar(diagnostics["transducer_reference_voltage_v"])
    if interfaces:
        diagnostics.update(interface_ids=interface_ids,
                           interface_pressure_continuity_errors=[1e-8 * (i + 1) for i in range(len(interfaces))],
                           interface_flux_conservation_errors=[2e-8 * (i + 1) for i in range(len(interfaces))])
    if not any(r["kind"] == "unbounded_air" for r in request["compiled_system"]["regions"]):
        diagnostics["linear_backend"] = diagnostics.pop("bem_backend")
    return {"schema_version": 2, "freq_hz": json_scalar(frequency), "excitation_port_ids": ports,
            "quantities": quantities, "diagnostics": diagnostics}
