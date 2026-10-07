"""Prove a tiny compiled-system solve through an injected EngineWorker."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from dataclasses import InitVar, dataclass, field
import hashlib
import json
import math
from pathlib import Path
import struct
import tempfile
from typing import Protocol

from . import paths

PROBE_CONTRACT = "system-v1/result-v2"
_FIXTURE = Path(__file__).with_name("fixtures") / "probe.msh"
_PORT = "excitation:probe"
_PROOF = object()


class ProbeStream(Protocol):
    def __iter__(self) -> Iterator[dict]: ...
    def close(self) -> None: ...


class ProbeWorker(Protocol):
    @property
    def worker_info(self) -> dict | None: ...
    def submit(self, request_path: Path) -> ProbeStream: ...


@dataclass(frozen=True)
class ProbeResult:
    ready: bool
    reason: str
    fixture_identity: str = ""
    completion: dict = field(default_factory=dict)
    _proof: InitVar[object] = None

    def __post_init__(self, _proof: object) -> None:
        if self.ready and _proof is not _PROOF:
            raise ValueError("Ready verdicts must come from compiled_probe")


def fixture_identity() -> str:
    """Hash the actual compiled-probe fixture for provisioning reuse."""
    return hashlib.sha256(_FIXTURE.read_bytes()).hexdigest()


def build_request(mesh: Path, *, backend: str = "cpu") -> dict:
    """One source, frequency and observation; paths are worker-local absolutes."""
    if backend not in {"cpu", "metal"}:
        raise ValueError(f"Unsupported probe backend: {backend!r}")
    return {
        "schema_version": 1,
        "compiled_system": {
            "id": "system:probe", "name": "WG readiness probe", "contract_version": 1,
            "meshes": [{"id": "mesh:probe", "name": "Tetrahedron", "file": str(mesh.resolve()),
                        "purpose": "bem_surface", "scale_to_m": 1.0, "translation_m": [0, 0, 0]}],
            "regions": [{"id": "region:air", "name": "Air", "kind": "unbounded_air",
                         "mesh_ids": ["mesh:probe"], "volume_groups": [],
                         "sound_speed_m_per_s": 343.0, "density_kg_per_m3": 1.2041,
                         "loss_model": {}}],
            "boundaries": [{"id": "boundary:probe", "name": "Source", "kind": "moving",
                            "region_id": "region:air", "parameters": {},
                            "group": {"mesh_id": "mesh:probe", "dimension": 2, "tag": 2}}],
            "interfaces": [],
            "components": [{"id": "component:probe", "name": "Source",
                            "kind": "ideal_velocity_source", "boundary_ids": ["boundary:probe"],
                            "parameters": {}}],
            "excitation_ports": [{"id": _PORT, "name": "Source",
                                  "component_id": "component:probe", "kind": "normal_velocity"}],
        },
        "frequencies_hz": [1000.0], "excitation_port_ids": [_PORT],
        "outputs": [{"id": "pressure", "quantity": "exterior_pressure", "target_ids": [],
                     "options": {"points_m": [[0, 0, 1]]}}],
        # The same regular quadrature production solves request, so the probe
        # warms the code path they run (BEAT's CPU default would be "wavelength").
        "solver_options": {"precision": "float32", "bem_backend": backend, "symmetry": "off",
                           "phasor_convention": "exp(-i omega t)",
                           "regular_quadrature_mode": "fixed", "quadrature_order": 4,
                           "singular_order": 4},
    }


def _worker_info(info: object, options: dict) -> None:
    if not isinstance(info, dict) or info.get("type") != "ready":
        raise ValueError("Probe requires worker_info ready announcement")
    protocol = info.get("protocol")
    if (not isinstance(protocol, dict) or protocol.get("name") != "beat-worker"
            or type(protocol.get("version")) is not int or protocol["version"] != 1):
        raise ValueError("Incompatible probe worker protocol")
    engine = info.get("engine")
    if (not isinstance(engine, dict) or engine.get("name") != "BEAT Engine"
            or not isinstance(engine.get("version"), str) or not engine["version"]):
        raise ValueError("Probe worker lacks engine identity/version")
    contracts = info.get("contracts")
    if not isinstance(contracts, dict):
        raise ValueError("Probe worker lacks contract capabilities")
    for name, expected in (("system_request", 1), ("compiled_system", 1), ("system_result", 2)):
        versions = contracts.get(name)
        if not isinstance(versions, list) or not any(type(v) is int and v == expected for v in versions):
            raise ValueError(f"Probe worker lacks {name} version {expected}")
    for name, expected in (("operations", "solve"), ("precisions", options["precision"]),
                           ("solve_kinds", "exterior_bem"), ("request_transports", "file"),
                           ("phasor_conventions", options["phasor_convention"])):
        values = info.get(name)
        if not isinstance(values, list) or expected not in values:
            raise ValueError(f"Probe worker lacks {name} capability {expected}")
    backends = info.get("backends")
    backend = backends.get(options["bem_backend"]) if isinstance(backends, dict) else None
    if not isinstance(backend, dict) or backend.get("available") is not True:
        raise ValueError("Probe worker backend is unavailable")
    conventions = backend.get("phasor_conventions", info["phasor_conventions"])
    if not isinstance(conventions, list) or options["phasor_convention"] not in conventions:
        raise ValueError("Probe worker backend lacks requested phasor convention")


def _pressure(result: object, options: dict) -> None:
    if (not isinstance(result, dict) or type(result.get("schema_version")) is not int
            or result["schema_version"] != 2 or type(result.get("freq_hz")) not in {int, float}
            or result["freq_hz"] != 1000.0 or result.get("excitation_port_ids") != [_PORT]):
        raise ValueError("Malformed probe result: version, frequency or excitation")
    diagnostics = result.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise ValueError("Probe result lacks diagnostics")
    for name in ("bem_backend", "precision", "phasor_convention"):
        if diagnostics.get(name) != options[name]:
            raise ValueError(f"Probe result {name} does not match request")
    quantities = result.get("quantities")
    if not isinstance(quantities, list) or len(quantities) != 1 or not isinstance(quantities[0], dict):
        raise ValueError("Malformed probe pressure quantity")
    quantity = quantities[0]
    if (quantity.get("id"), quantity.get("quantity"), quantity.get("unit"), quantity.get("axes")) != (
        "pressure", "exterior_pressure", "Pa", ["excitation", "observation"],
    ):
        raise ValueError("Malformed probe pressure identity or axes")
    values = quantity.get("values")
    if (not isinstance(values, dict) or values.get("encoding") != "base64"
            or values.get("dtype") != "complex64"
            or values.get("order") != "C" or values.get("byte_order") != "little"
            or values.get("shape") != [1, 1]
            or any(type(size) is not int for size in values["shape"])
            or not isinstance(values.get("content_base64"), str)):
        raise ValueError("Malformed probe pressure array")
    raw = base64.b64decode(values["content_base64"], validate=True)
    layout = "<ff"
    if len(raw) != struct.calcsize(layout):
        raise ValueError("Malformed probe pressure byte count")
    real, imag = struct.unpack(layout, raw)
    if not all(math.isfinite(value) for value in (real, imag)):
        raise ValueError("Probe pressure is non-finite")
    if real == imag == 0:
        raise ValueError("Probe pressure is zero")


def compiled_probe(worker: ProbeWorker, *, directory: Path, backend: str = "cpu") -> ProbeResult:
    """Prove matching negotiated results in caller-owned, HBB-isolated staging.

    An injected fake can impersonate the engine. Readiness authenticity comes
    from the WG-owned worker launch, not from this probe's wire validation.
    Always close the stream; tolerate Windows handles during staging cleanup.
    """
    identity = ""
    try:
        directory = paths.checked_root(directory)
        fixture = _FIXTURE.read_bytes()
        identity = hashlib.sha256(fixture).hexdigest()
        with tempfile.TemporaryDirectory(prefix="beat-probe-", dir=directory,
                                         ignore_cleanup_errors=True) as staging:
            mesh = Path(staging) / "probe.msh"
            mesh.write_bytes(fixture)
            request = Path(staging) / "request.json"
            payload = build_request(mesh, backend=backend)
            options = payload["solver_options"]
            request.write_text(json.dumps(payload), encoding="utf-8")
            events = worker.submit(request)
            results = 0
            completed = False
            try:
                _worker_info(worker.worker_info, options)
                for event in events:
                    if not isinstance(event, dict) or completed:
                        raise ValueError("Malformed probe event or event after terminal")
                    kind = event.get("type")
                    if kind == "result":
                        results += 1
                        _pressure(event.get("result"), options)
                    elif kind == "completed":
                        if type(event.get("solved_count")) is not int or event["solved_count"] != 1:
                            raise ValueError("Probe terminal solved_count must be 1")
                        completed = True
                    elif kind in {"failed", "cancelled"}:
                        raise ValueError(f"Probe worker {kind}: {event.get('error', kind)}")
                    elif kind != "status":
                        raise ValueError("Unexpected probe event type")
                if results != 1 or not completed:
                    raise ValueError(f"Probe requires one result and completion: results={results}, completed={completed}")
            finally:
                events.close()
        return ProbeResult(True, "Compiled probe solved", identity,
                           {"result_count": 1, "solved_count": 1, "finite_nonzero": True,
                            "bem_backend": backend}, _proof=_PROOF)
    except Exception as exc:
        return ProbeResult(False, f"Compiled probe failed: {exc}", identity)
