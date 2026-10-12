"""Bounded coupled P1/DP0 traces in their solved mesh and channel ordering."""
from __future__ import annotations

import base64
import zipfile
from pathlib import Path

import numpy as np

from server.contracts.conventions import SOLVER_TIME_CONVENTION
from server.jobs.fem_field import FemFieldCorrupt, FemFieldInvalidSelection, _pressure_slice
from server.solver.beat_adapter.mesh import SurfaceMesh
from server.solver.beat_adapter.trace_retention import MAX_BEM_TRACE_BYTES, check_trace_size as check_trace_size


def trace_arrays(request, sweep) -> dict | None:
    if sweep.bem_pressure is None:
        return None
    resource = next(m for m in request.wire["compiled_system"]["meshes"] if m["purpose"] == "bem_surface")
    mesh = resource["mesh_data"]
    def unpack(value):
        return np.frombuffer(base64.b64decode(value["data"]), dtype=value["dtype"]).reshape(value["shape"])
    medium = next(r for r in request.wire["compiled_system"]["regions"] if r["kind"] == "unbounded_air")
    return {"schema": np.asarray("wg-bem-traces-v1"),
            "phasor_convention": np.asarray(SOLVER_TIME_CONVENTION),
            "frequencies_hz": next(iter(sweep.channels.values())).frequencies_hz,
            "channel_ids": np.asarray(list(request.channel_ports)),
            "points_m": unpack(mesh["points"]),
            "triangles": unpack(mesh["cells"][0]["connectivity"]),
            "pressure": sweep.bem_pressure, "neumann": sweep.bem_neumann,
            "sound_speed_m_per_s": np.asarray(medium["sound_speed_m_per_s"]),
            "density_kg_per_m3": np.asarray(medium["density_kg_per_m3"]),
            "image_rotation": np.eye(3) if request.image_rotation is None else request.image_rotation,
            "symmetry": np.asarray(request.wire["solver_options"]["symmetry"])}


def load_bem_traces(path: Path, frequency: float, channel: str):
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(m.file_size for m in archive.infolist()) > MAX_BEM_TRACE_BYTES + 64 * 1024:
                raise FemFieldCorrupt("BEM trace archive exceeds the retention limit")
        with np.load(path, allow_pickle=False) as arrays:
            if (str(arrays["schema"].item()) != "wg-bem-traces-v1"
                    or str(arrays["phasor_convention"].item()) != SOLVER_TIME_CONVENTION):
                raise FemFieldCorrupt("Unsupported BEM trace conventions")
            frequencies = arrays["frequencies_hz"]
            channels = arrays["channel_ids"].tolist()
            if (frequencies.ndim != 1 or not len(frequencies) or not np.isfinite(frequencies).all()
                    or (frequencies <= 0).any() or len(np.unique(frequencies)) != len(frequencies)
                    or not isinstance(channels, list) or not channels
                    or any(not isinstance(c, str) or not c for c in channels)
                    or len(set(channels)) != len(channels)):
                raise FemFieldCorrupt("Invalid BEM trace axes")
            matches = np.flatnonzero(np.isclose(frequencies, frequency, rtol=1e-9, atol=0))
            if not len(matches) or channel not in channels:
                raise FemFieldInvalidSelection("BEM trace frequency or channel is not retained")
            xyz, faces = arrays["points_m"], arrays["triangles"]
            if (xyz.ndim != 2 or xyz.shape[1] != 3 or not len(xyz) or not np.isfinite(xyz).all()
                    or faces.ndim != 2 or faces.shape[1] != 3 or not len(faces)
                    or faces.dtype.kind not in "iu" or (faces < 0).any() or (faces >= len(xyz)).any()):
                raise FemFieldCorrupt("Invalid BEM trace mesh")
            f, c = int(matches[0]), channels.index(channel)
            pressure = _pressure_slice(arrays.zip, f, c, (len(frequencies), len(channels), len(xyz)))
            neumann = _pressure_slice(arrays.zip, f, c, (len(frequencies), len(channels), len(faces)), member="neumann")
            speed, density = (float(arrays[k].item()) for k in ("sound_speed_m_per_s", "density_kg_per_m3"))
            symmetry = str(arrays["symmetry"].item())
            if not np.isfinite([speed, density]).all() or min(speed, density) <= 0 or symmetry not in {"off", "x", "xy"}:
                raise FemFieldCorrupt("Invalid BEM trace medium or symmetry")
            rotation = arrays["image_rotation"] if "image_rotation" in arrays else np.eye(3)
            if (rotation.shape != (3, 3) or not np.isfinite(rotation).all()
                    or not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-9)
                    or not np.isclose(np.linalg.det(rotation), 1)):
                raise FemFieldCorrupt("Invalid retained BEM image rotation")
            return SurfaceMesh(xyz, faces, np.ones(len(faces), dtype=int), (), ()), pressure, neumann, speed, density, symmetry, rotation
    except (FemFieldCorrupt, FemFieldInvalidSelection, FileNotFoundError):
        raise
    except (ValueError, KeyError, TypeError, EOFError, zipfile.BadZipFile) as exc:
        raise FemFieldCorrupt("BEM trace sidecar is corrupt") from exc
