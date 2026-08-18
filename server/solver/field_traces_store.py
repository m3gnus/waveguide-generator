"""Versioned disk sidecars for post-solve exterior-field surface traces."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Literal, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

from server.mesh.artifact import mesh_text_sha256
from server.platform.paths import data_paths


FIELD_TRACES_VERSION = 2
FIELD_TRACES_DIRECTORY = "field-traces"
FIELD_TRACES_MAX_BYTES_ENV = "WG2_FIELD_TRACES_MAX_BYTES"
DEFAULT_FIELD_TRACES_MAX_BYTES = 256 * 1024 * 1024
PHASE_CONVENTION = "solver_exp_plus_ikr"
METAL_FIELD_TRACE_BACKEND = "metal-native"
BEMPP_FIELD_TRACE_BACKEND = "bempp"
FieldTraceBackend = Literal["metal-native", "bempp"]
_COMPLEX64_LE = np.dtype("<c8")
_SYMMETRY_PLANES = frozenset({None, "yz", "xz", "xy", "yz+xz"})
_FIELD_TRACE_BACKENDS = frozenset(
    {METAL_FIELD_TRACE_BACKEND, BEMPP_FIELD_TRACE_BACKEND}
)


class ArtifactMissing(FileNotFoundError):
    """The requested field-trace sidecar no longer exists."""


class ArtifactCorrupt(RuntimeError):
    """A field-trace sidecar exists but fails its integrity contract."""


@dataclass(frozen=True, slots=True)
class FieldTraceChannel:
    """Complete surface traces for one unsynthesized solve channel."""

    channel_id: str
    pressure_p1: NDArray[np.complex128]
    neumann_dp0: NDArray[np.complex128]


@dataclass(frozen=True, slots=True)
class FieldTraceArtifact:
    """In-memory trace bundle handed from a qualified solve to persistence."""

    mesh_text: str
    frequencies_hz: NDArray[np.float64]
    k_real: NDArray[np.float64]
    k_imag: NDArray[np.float64]
    symmetry_plane: str | None
    solve_path: str
    channels: tuple[FieldTraceChannel, ...]
    backend: FieldTraceBackend


@dataclass(frozen=True, slots=True)
class StoredFieldTraceArtifact:
    """Metadata returned after an artifact directory is durably published."""

    path: Path
    version: int
    bytes: int
    metadata: dict[str, Any]


def field_traces_root(data_dir: str | os.PathLike[str] | None = None) -> Path:
    """Return the field-trace sidecar root without creating it."""

    return data_paths(data_dir).root / FIELD_TRACES_DIRECTORY


def field_trace_artifact_dir(
    job_id: str,
    *,
    data_dir: str | os.PathLike[str] | None = None,
    artifact_root: str | os.PathLike[str] | None = None,
) -> Path:
    """Return the deterministic artifact directory for one job."""

    normalized = _path_component(job_id, "job_id")
    root = Path(artifact_root) if artifact_root is not None else field_traces_root(data_dir)
    return root / normalized


def field_trace_retention_cap_bytes(
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve the configurable in-memory retention ceiling."""

    env = os.environ if environ is None else environ
    raw = str(env.get(FIELD_TRACES_MAX_BYTES_ENV, "")).strip()
    if not raw:
        return DEFAULT_FIELD_TRACES_MAX_BYTES
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{FIELD_TRACES_MAX_BYTES_ENV} must be an integer byte count") from exc
    if value < 0:
        raise ValueError(f"{FIELD_TRACES_MAX_BYTES_ENV} must be non-negative")
    return value


def estimate_field_trace_retention_bytes(
    frequency_count: int,
    n_p1_dofs: int,
    n_dp0_dofs: int,
    channel_count: int,
) -> int:
    """Estimate retained complex128 trace memory before starting a solve."""

    values = (frequency_count, n_p1_dofs, n_dp0_dofs, channel_count)
    if any(isinstance(value, bool) or int(value) <= 0 for value in values):
        raise ValueError("trace retention dimensions must be positive integers")
    return int(frequency_count) * (int(n_p1_dofs) + int(n_dp0_dofs)) * 16 * int(
        channel_count
    )


def mesh_trace_dof_counts(
    msh_text: str,
    mesh_stats: Mapping[str, Any] | None = None,
) -> tuple[int, int]:
    """Estimate P1 and DP0 counts from a Gmsh 2.2 mesh, with stats fallback."""

    try:
        lines = msh_text.splitlines()
        start = lines.index("$Elements")
        element_count = int(lines[start + 1].strip())
        element_lines = lines[start + 2 : start + 2 + element_count]
        if len(element_lines) != element_count:
            raise ValueError("truncated element section")
        used_vertices: set[int] = set()
        triangle_count = 0
        for line in element_lines:
            fields = line.split()
            if len(fields) < 4 or int(fields[1]) != 2:
                continue
            tag_count = int(fields[2])
            node_start = 3 + tag_count
            nodes = fields[node_start:]
            if len(nodes) != 3:
                raise ValueError("invalid first-order triangle")
            used_vertices.update(int(value) for value in nodes)
            triangle_count += 1
        if used_vertices and triangle_count:
            return len(used_vertices), triangle_count
    except (IndexError, TypeError, ValueError):
        pass

    stats = mesh_stats if isinstance(mesh_stats, Mapping) else {}
    try:
        vertex_count = int(stats["vertex_count"])
        triangle_count = int(stats["triangle_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("mesh does not expose P1/DP0 trace dimensions") from exc
    if vertex_count <= 0 or triangle_count <= 0:
        raise ValueError("mesh trace dimensions must be positive")
    return vertex_count, triangle_count


def field_trace_retention_plan(
    msh_text: str,
    *,
    mesh_stats: Mapping[str, Any] | None,
    frequency_count: int,
    channel_count: int,
    supported: bool,
    cap_bytes: int | None,
) -> tuple[bool, str | None, int | None, int]:
    """Resolve whether a solve may retain complete surface traces."""

    cap = field_trace_retention_cap_bytes() if cap_bytes is None else int(cap_bytes)
    if cap < 0:
        raise ValueError("field trace retention cap must be non-negative")
    if not supported:
        return False, "unsupported_solve_mode", None, cap
    try:
        n_p1, n_dp0 = mesh_trace_dof_counts(msh_text, mesh_stats)
    except ValueError:
        return False, "trace_size_estimate_unavailable", None, cap
    estimated = estimate_field_trace_retention_bytes(
        frequency_count,
        n_p1,
        n_dp0,
        channel_count,
    )
    if estimated > cap:
        return False, "size_cap_exceeded", estimated, cap
    return True, None, estimated, cap


def build_field_trace_artifact(
    msh_text: str,
    channel_results: Sequence[tuple[str, Any]],
    config: Any,
    *,
    backend: FieldTraceBackend,
    sound_speed_m_per_s: float,
) -> FieldTraceArtifact | None:
    """Build one backend-tagged artifact from native solver result traces."""

    channels: list[FieldTraceChannel] = []
    frequencies: NDArray[np.float64] | None = None
    for channel_id, result in channel_results:
        pressure = getattr(result, "surface_pressure_complex", None)
        neumann = getattr(result, "surface_neumann_complex", None)
        if pressure is None or neumann is None:
            return None
        result_frequencies = np.asarray(
            result.frequencies_hz, dtype=np.float64
        ).reshape(-1)
        if frequencies is None:
            frequencies = result_frequencies
        elif not np.array_equal(result_frequencies, frequencies):
            raise ValueError("retained field-trace channel frequency grids differ")
        channels.append(
            FieldTraceChannel(
                channel_id=str(channel_id),
                pressure_p1=np.asarray(pressure, dtype=np.complex128),
                neumann_dp0=np.asarray(neumann, dtype=np.complex128),
            )
        )
    if frequencies is None:
        return None

    k_real_f32 = (
        2.0 * np.pi * frequencies / float(sound_speed_m_per_s)
    ).astype(np.float32)
    formulation = getattr(config, "formulation", "standard")
    formulation_value = getattr(formulation, "value", formulation)
    if formulation_value == "complex_k":
        k_imag_f32 = (
            k_real_f32.astype(np.float64)
            * float(getattr(config, "complex_k_shift", 0.0))
        ).astype(np.float32)
    else:
        k_imag_f32 = np.zeros_like(k_real_f32)
    return FieldTraceArtifact(
        mesh_text=msh_text,
        frequencies_hz=frequencies,
        k_real=k_real_f32.astype(np.float64),
        k_imag=k_imag_f32.astype(np.float64),
        symmetry_plane=getattr(config, "native_symmetry_plane", None),
        solve_path="full-3d",
        channels=tuple(channels),
        backend=backend,
    )


def write_field_traces(
    job_id: str,
    artifact: FieldTraceArtifact,
    *,
    data_dir: str | os.PathLike[str] | None = None,
    artifact_root: str | os.PathLike[str] | None = None,
) -> StoredFieldTraceArtifact:
    """Write and atomically publish one complete field-trace sidecar."""

    target = field_trace_artifact_dir(
        job_id,
        data_dir=data_dir,
        artifact_root=artifact_root,
    )
    root = target.parent
    root.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"field-trace artifact already exists for job {job_id}")

    validated = _validated_artifact(job_id, artifact)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=root))
    published = False
    try:
        mesh_path = temporary / "mesh.msh"
        _write_fsynced(mesh_path, artifact.mesh_text.encode("utf-8"))

        channel_metadata: list[dict[str, Any]] = []
        frequency_slices = _frequency_slices(
            validated["frequency_count"],
            validated["n_p1"],
            validated["n_dp0"],
        )
        bytes_per_channel = frequency_slices[-1]["end_offset"]
        for channel_index, channel in enumerate(artifact.channels):
            file_name = f"channel-{channel_index:04d}.bin"
            channel_path = temporary / file_name
            with channel_path.open("wb") as stream:
                for frequency_index in range(validated["frequency_count"]):
                    pressure = np.ascontiguousarray(
                        channel.pressure_p1[frequency_index], dtype=_COMPLEX64_LE
                    )
                    neumann = np.ascontiguousarray(
                        channel.neumann_dp0[frequency_index], dtype=_COMPLEX64_LE
                    )
                    stream.write(pressure.tobytes(order="C"))
                    stream.write(neumann.tobytes(order="C"))
                stream.flush()
                os.fsync(stream.fileno())
            if channel_path.stat().st_size != bytes_per_channel:
                raise OSError(f"short field-trace write for channel {channel.channel_id!r}")
            channel_metadata.append(
                {
                    "id": channel.channel_id,
                    "file": file_name,
                    "bytes": bytes_per_channel,
                }
            )

        total_bytes = bytes_per_channel * len(artifact.channels)
        metadata = {
            "version": FIELD_TRACES_VERSION,
            "backend": artifact.backend,
            "job_id": job_id,
            "geometry_sha256": mesh_text_sha256(artifact.mesh_text),
            "mesh_file": mesh_path.name,
            "dof_counts": {
                "p1": validated["n_p1"],
                "dp0": validated["n_dp0"],
            },
            "frequencies_hz": validated["frequencies"].tolist(),
            "k_real": validated["k_real"].tolist(),
            "k_imag": validated["k_imag"].tolist(),
            "phase_convention": PHASE_CONVENTION,
            "symmetry_plane": artifact.symmetry_plane,
            "solve_path": artifact.solve_path,
            "channel_ids": [channel.channel_id for channel in artifact.channels],
            "channels": channel_metadata,
            "frequency_slices": frequency_slices,
            "total_bytes": total_bytes,
        }
        meta_bytes = (
            json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False).encode("utf-8")
            + b"\n"
        )
        _write_fsynced(temporary / "meta.json", meta_bytes)
        _fsync_directory(temporary)
        os.replace(temporary, target)
        published = True
        _fsync_directory(root)
        return StoredFieldTraceArtifact(
            path=target,
            version=FIELD_TRACES_VERSION,
            bytes=total_bytes,
            metadata=metadata,
        )
    finally:
        if not published:
            shutil.rmtree(temporary, ignore_errors=True)


def load_field_traces(
    job_id: str,
    frequency_index: int,
    channel_id: str,
    *,
    data_dir: str | os.PathLike[str] | None = None,
    artifact_root: str | os.PathLike[str] | None = None,
    artifact_dir: str | os.PathLike[str] | None = None,
) -> tuple[
    str,
    float,
    float,
    str | None,
    NDArray[np.complex64],
    NDArray[np.complex64],
    FieldTraceBackend,
]:
    """Load one frequency and raw channel without reading other trace slices."""

    directory = (
        Path(artifact_dir)
        if artifact_dir is not None
        else field_trace_artifact_dir(
            job_id,
            data_dir=data_dir,
            artifact_root=artifact_root,
        )
    )
    if not directory.is_dir():
        raise ArtifactMissing(f"field-trace artifact is missing for job {job_id}")

    metadata = _load_metadata(directory)
    try:
        validated = _validate_metadata(metadata, job_id)
        mesh_path = directory / validated["mesh_file"]
        mesh_text = mesh_path.read_text(encoding="utf-8")
    except ArtifactCorrupt:
        raise
    except (OSError, UnicodeError) as exc:
        raise ArtifactCorrupt("field-trace mesh is missing or unreadable") from exc
    if mesh_text_sha256(mesh_text) != validated["geometry_sha256"]:
        raise ArtifactCorrupt("field-trace geometry sha256 does not match mesh.msh")

    for entry in validated["channels"]:
        try:
            actual_size = (directory / entry["file"]).stat().st_size
        except OSError as exc:
            raise ArtifactCorrupt(
                f"field-trace channel file is missing: {entry['file']}"
            ) from exc
        if actual_size != entry["bytes"]:
            raise ArtifactCorrupt(
                f"field-trace channel byte count mismatch for {entry['id']!r}"
            )

    if isinstance(frequency_index, bool) or not isinstance(frequency_index, int):
        raise TypeError("frequency_index must be an integer")
    if frequency_index < 0 or frequency_index >= validated["frequency_count"]:
        raise IndexError("frequency_index is outside the retained sweep")
    normalized_channel = str(channel_id)
    channel = next(
        (entry for entry in validated["channels"] if entry["id"] == normalized_channel),
        None,
    )
    if channel is None:
        raise KeyError(f"unknown retained channel {normalized_channel!r}")

    binary_path = directory / channel["file"]
    slice_metadata = validated["frequency_slices"][frequency_index]
    try:
        with binary_path.open("rb") as stream:
            stream.seek(slice_metadata["p_offset"])
            pressure_bytes = stream.read(slice_metadata["p_bytes"])
            stream.seek(slice_metadata["q_offset"])
            neumann_bytes = stream.read(slice_metadata["q_bytes"])
    except OSError as exc:
        raise ArtifactCorrupt("field-trace channel slice is unreadable") from exc
    if len(pressure_bytes) != slice_metadata["p_bytes"] or len(neumann_bytes) != slice_metadata[
        "q_bytes"
    ]:
        raise ArtifactCorrupt("field-trace channel slice is truncated")

    pressure = np.frombuffer(pressure_bytes, dtype=_COMPLEX64_LE).astype(
        np.complex64, copy=True
    )
    neumann = np.frombuffer(neumann_bytes, dtype=_COMPLEX64_LE).astype(
        np.complex64, copy=True
    )
    if pressure.shape != (validated["n_p1"],) or neumann.shape != (
        validated["n_dp0"],
    ):
        raise ArtifactCorrupt("field-trace slice dof count is inconsistent")
    return (
        mesh_text,
        validated["frequencies"][frequency_index],
        validated["k_real"][frequency_index],
        validated["symmetry_plane"],
        pressure,
        neumann,
        validated["backend"],
    )


def remove_field_trace_artifact(path: str | os.PathLike[str]) -> None:
    """Remove one already-resolved artifact directory, tolerating absence."""

    directory = Path(path)
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        return


def _validated_artifact(job_id: str, artifact: FieldTraceArtifact) -> dict[str, Any]:
    if not isinstance(artifact, FieldTraceArtifact):
        raise TypeError("artifact must be a FieldTraceArtifact")
    _path_component(job_id, "job_id")
    if not isinstance(artifact.mesh_text, str) or not artifact.mesh_text:
        raise ValueError("field-trace mesh_text must be non-empty")
    if artifact.symmetry_plane not in _SYMMETRY_PLANES:
        raise ValueError("unsupported field-trace symmetry plane")
    if artifact.backend not in _FIELD_TRACE_BACKENDS:
        raise ValueError("unsupported field-trace backend")
    if artifact.solve_path != "full-3d":
        raise ValueError("field traces are supported only for solve_path='full-3d'")

    frequencies = _finite_vector(artifact.frequencies_hz, "frequencies_hz", positive=True)
    k_real = _finite_vector(artifact.k_real, "k_real", positive=True)
    k_imag = _finite_vector(artifact.k_imag, "k_imag", positive=False)
    if k_real.shape != frequencies.shape or k_imag.shape != frequencies.shape:
        raise ValueError("field-trace frequency and wavenumber counts differ")
    if not artifact.channels:
        raise ValueError("field-trace artifact must contain at least one channel")

    channel_ids: set[str] = set()
    n_p1: int | None = None
    n_dp0: int | None = None
    for channel in artifact.channels:
        channel_name = str(channel.channel_id)
        if not channel_name:
            raise ValueError("field-trace channel ids must be non-empty")
        if channel_name in channel_ids:
            raise ValueError(f"duplicate field-trace channel id {channel_name!r}")
        channel_ids.add(channel_name)
        pressure = np.asarray(channel.pressure_p1)
        neumann = np.asarray(channel.neumann_dp0)
        if pressure.ndim != 2 or neumann.ndim != 2:
            raise ValueError("field-trace channels must have two-dimensional p and q arrays")
        if pressure.shape[0] != frequencies.size or neumann.shape[0] != frequencies.size:
            raise ValueError("field-trace channel frequency counts differ")
        if pressure.shape[1] <= 0 or neumann.shape[1] <= 0:
            raise ValueError("field-trace channel dof counts must be positive")
        if n_p1 is None:
            n_p1, n_dp0 = pressure.shape[1], neumann.shape[1]
        elif pressure.shape[1] != n_p1 or neumann.shape[1] != n_dp0:
            raise ValueError("field-trace channel dof counts differ")
        if not np.all(np.isfinite(pressure)) or not np.all(np.isfinite(neumann)):
            raise ValueError("field-trace arrays must contain only finite values")
    assert n_p1 is not None and n_dp0 is not None
    return {
        "frequencies": frequencies,
        "k_real": k_real,
        "k_imag": k_imag,
        "frequency_count": int(frequencies.size),
        "n_p1": int(n_p1),
        "n_dp0": int(n_dp0),
    }


def _frequency_slices(frequency_count: int, n_p1: int, n_dp0: int) -> list[dict[str, int]]:
    p_bytes = n_p1 * _COMPLEX64_LE.itemsize
    q_bytes = n_dp0 * _COMPLEX64_LE.itemsize
    stride = p_bytes + q_bytes
    return [
        {
            "index": index,
            "p_offset": index * stride,
            "p_bytes": p_bytes,
            "q_offset": index * stride + p_bytes,
            "q_bytes": q_bytes,
            "end_offset": (index + 1) * stride,
        }
        for index in range(frequency_count)
    ]


def _load_metadata(directory: Path) -> dict[str, Any]:
    try:
        value = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise ArtifactCorrupt("field-trace meta.json is missing or invalid") from exc
    if not isinstance(value, dict):
        raise ArtifactCorrupt("field-trace meta.json must contain an object")
    return value


def _validate_metadata(metadata: Mapping[str, Any], job_id: str) -> dict[str, Any]:
    try:
        version = _metadata_int(metadata, "version", minimum=1)
        if version not in {1, FIELD_TRACES_VERSION}:
            raise ArtifactCorrupt(f"unsupported field-trace artifact version {version}")
        if version == 1:
            backend = METAL_FIELD_TRACE_BACKEND
        else:
            backend = metadata.get("backend")
            if backend not in _FIELD_TRACE_BACKENDS:
                raise ArtifactCorrupt("field-trace backend is invalid")
        if metadata.get("job_id") != job_id:
            raise ArtifactCorrupt("field-trace job id does not match its directory")
        geometry_sha256 = str(metadata["geometry_sha256"])
        digest = geometry_sha256.removeprefix("sha256:")
        if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
            raise ArtifactCorrupt("field-trace geometry sha256 is invalid")
        mesh_file = _metadata_file(metadata, "mesh_file")
        if metadata.get("phase_convention") != PHASE_CONVENTION:
            raise ArtifactCorrupt("field-trace phase convention is unsupported")
        symmetry_plane = metadata.get("symmetry_plane")
        if symmetry_plane not in _SYMMETRY_PLANES:
            raise ArtifactCorrupt("field-trace symmetry plane is invalid")
        if metadata.get("solve_path") != "full-3d":
            raise ArtifactCorrupt("field-trace solve path is unsupported")

        dof_counts = metadata["dof_counts"]
        if not isinstance(dof_counts, Mapping):
            raise ArtifactCorrupt("field-trace dof_counts must be an object")
        n_p1 = _metadata_int(dof_counts, "p1", minimum=1)
        n_dp0 = _metadata_int(dof_counts, "dp0", minimum=1)
        frequencies = _metadata_float_list(metadata, "frequencies_hz", positive=True)
        k_real = _metadata_float_list(metadata, "k_real", positive=True)
        k_imag = _metadata_float_list(metadata, "k_imag", positive=False)
        if not frequencies or len(k_real) != len(frequencies) or len(k_imag) != len(
            frequencies
        ):
            raise ArtifactCorrupt("field-trace frequency and wavenumber counts differ")

        expected_slices = _frequency_slices(len(frequencies), n_p1, n_dp0)
        raw_slices = metadata["frequency_slices"]
        if raw_slices != expected_slices:
            raise ArtifactCorrupt("field-trace frequency offsets or byte counts are invalid")
        expected_channel_bytes = expected_slices[-1]["end_offset"]
        raw_channels = metadata["channels"]
        channel_ids = metadata["channel_ids"]
        if not isinstance(raw_channels, list) or not raw_channels:
            raise ArtifactCorrupt("field-trace channels must be a non-empty list")
        if not isinstance(channel_ids, list) or not all(
            isinstance(value, str) and value for value in channel_ids
        ):
            raise ArtifactCorrupt("field-trace channel_ids are invalid")
        channels: list[dict[str, Any]] = []
        for raw_channel in raw_channels:
            if not isinstance(raw_channel, Mapping):
                raise ArtifactCorrupt("field-trace channel metadata is invalid")
            channel_name = raw_channel.get("id")
            if not isinstance(channel_name, str) or not channel_name:
                raise ArtifactCorrupt("field-trace channel id is invalid")
            file_name = _metadata_file(raw_channel, "file")
            byte_count = _metadata_int(raw_channel, "bytes", minimum=1)
            if byte_count != expected_channel_bytes:
                raise ArtifactCorrupt("field-trace channel byte count is invalid")
            channels.append({"id": channel_name, "file": file_name, "bytes": byte_count})
        if [channel["id"] for channel in channels] != channel_ids or len(set(channel_ids)) != len(
            channel_ids
        ):
            raise ArtifactCorrupt("field-trace channel id order is inconsistent")
        total_bytes = _metadata_int(metadata, "total_bytes", minimum=1)
        if total_bytes != expected_channel_bytes * len(channels):
            raise ArtifactCorrupt("field-trace total byte count is inconsistent")
    except KeyError as exc:
        raise ArtifactCorrupt(f"field-trace metadata is missing {exc.args[0]!r}") from exc
    return {
        "version": version,
        "backend": backend,
        "geometry_sha256": geometry_sha256,
        "mesh_file": mesh_file,
        "symmetry_plane": symmetry_plane,
        "n_p1": n_p1,
        "n_dp0": n_dp0,
        "frequencies": frequencies,
        "k_real": k_real,
        "frequency_count": len(frequencies),
        "frequency_slices": expected_slices,
        "channels": channels,
        "total_bytes": total_bytes,
    }


def _metadata_int(values: Mapping[str, Any], key: str, *, minimum: int) -> int:
    value = values[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ArtifactCorrupt(f"field-trace metadata {key!r} must be an integer >= {minimum}")
    return value


def _metadata_float_list(
    values: Mapping[str, Any], key: str, *, positive: bool
) -> list[float]:
    raw = values[key]
    if not isinstance(raw, list):
        raise ArtifactCorrupt(f"field-trace metadata {key!r} must be a list")
    result: list[float] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ArtifactCorrupt(f"field-trace metadata {key!r} contains a non-number")
        number = float(value)
        if not math.isfinite(number) or (positive and number <= 0.0):
            raise ArtifactCorrupt(f"field-trace metadata {key!r} contains an invalid value")
        result.append(number)
    return result


def _metadata_file(values: Mapping[str, Any], key: str) -> str:
    value = values[key]
    if not isinstance(value, str):
        raise ArtifactCorrupt(f"field-trace metadata {key!r} must be a file name")
    try:
        return _path_component(value, key)
    except ValueError as exc:
        raise ArtifactCorrupt(f"field-trace metadata {key!r} is not a safe file name") from exc


def _finite_vector(value: Any, name: str, *, positive: bool) -> NDArray[np.float64]:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 1 or array.size == 0:
        raise ValueError(f"{name} must be a non-empty one-dimensional array")
    if not np.all(np.isfinite(array)) or (positive and np.any(array <= 0.0)):
        raise ValueError(f"{name} must contain valid finite values")
    return np.ascontiguousarray(array)


def _path_component(value: str, name: str) -> str:
    normalized = str(value)
    if not normalized or normalized in {".", ".."} or Path(normalized).name != normalized:
        raise ValueError(f"{name} must be one safe path component")
    return normalized


def _write_fsynced(path: Path, payload: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        # Windows cannot open a directory with os.open(..., O_RDONLY).
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "ArtifactCorrupt",
    "ArtifactMissing",
    "BEMPP_FIELD_TRACE_BACKEND",
    "DEFAULT_FIELD_TRACES_MAX_BYTES",
    "FIELD_TRACES_MAX_BYTES_ENV",
    "FIELD_TRACES_VERSION",
    "FieldTraceBackend",
    "FieldTraceArtifact",
    "FieldTraceChannel",
    "METAL_FIELD_TRACE_BACKEND",
    "StoredFieldTraceArtifact",
    "build_field_trace_artifact",
    "estimate_field_trace_retention_bytes",
    "field_trace_artifact_dir",
    "field_trace_retention_cap_bytes",
    "field_trace_retention_plan",
    "field_traces_root",
    "load_field_traces",
    "mesh_trace_dof_counts",
    "remove_field_trace_artifact",
    "write_field_traces",
]
