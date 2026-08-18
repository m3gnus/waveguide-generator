"""Portable, self-describing archives of a solved job's equivalent source.

A qualified solve retains complete surface traces: complex boundary pressure
``p`` and its normal derivative ``q`` for every retained frequency and every
*raw* drive channel, on the mesh the solver actually used. Those traces plus
that mesh are an equivalent source -- the representation formula evaluates the
exterior field at any point from them, which is exactly what
``server/solver/field_plane.py`` does for one plane per drag frame.

This module packages that source so it survives the application: one ``.zip``
carrying the mesh, the arrays, and a manifest that states the frame, units,
phasor, symmetry, and array layout a later consumer needs. It deliberately
carries *no* combine state. Crossovers, gains, and delays are a post-solve
synthesis the user re-picks freely; baking one choice into a re-simulatable
source would silently make it the only choice. Consumers weight the raw
channels themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence
import zipfile

import numpy as np

from server.design.conventions import artifact_conventions
from server.jobs.store import JobStore
from server.solver.field_traces_store import (
    ArtifactCorrupt,
    ArtifactMissing,
    FieldTraceBundle,
    PHASE_CONVENTION,
)


RADIATION_PACKAGE_SCHEMA = "waveguide-generator/radiation-package"
RADIATION_PACKAGE_VERSION = 1
MANIFEST_MEMBER = "manifest.json"
MESH_MEMBER = "geometry/mesh.msh"
TRACES_MEMBER = "data/traces.npz"

#: Fixed member order. A package is content-addressed by its per-member
#: digests, so the archive itself must not vary with dict iteration or
#: filesystem order.
PACKAGE_MEMBER_ORDER = (MANIFEST_MEMBER, MESH_MEMBER, TRACES_MEMBER)

#: The zip epoch. Every entry's timestamp is derived from the job's own
#: provenance instant, never from the wall clock, so two builds of one job
#: produce byte-identical archives. Zip cannot represent anything earlier.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

#: POSIX ``0644`` under a pinned create_system, so the archive does not differ
#: between the machine that built it and the machine that rebuilt it.
_ZIP_CREATE_SYSTEM = 3
_ZIP_EXTERNAL_ATTR = 0o100644 << 16

#: Retained and reported frequencies both originate in the same solve, but the
#: results grid makes a JSON round trip on the way to the row. Match on a
#: relative tolerance rather than on identical float text.
_FREQUENCY_TOLERANCE = 1e-9


class RadiationPackageError(RuntimeError):
    """The package could not be written for a reason that is not a refusal."""


@dataclass(frozen=True, slots=True)
class RadiationPackageIssue:
    """One machine-readable reason a package cannot be built or trusted."""

    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class RadiationPackageResult:
    """Either a published package or the complete set of refusals."""

    path: Path | None = None
    bytes: int = 0
    manifest: dict[str, Any] | None = None
    issues: tuple[RadiationPackageIssue, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.issues


def build_radiation_package(
    store: JobStore,
    job_id: str,
    destination_path: str | os.PathLike[str],
    *,
    created_at: str | None = None,
) -> RadiationPackageResult:
    """Publish one job's raw equivalent source as a portable ``.zip``.

    Every readiness condition is proven before a single byte is written, and
    the archive is staged beside its destination and moved into place with one
    ``os.replace``, so an interrupted export can never leave a half-written
    package where a whole one belongs.
    """

    destination = Path(destination_path)
    issues: list[RadiationPackageIssue] = []
    if destination.exists():
        issues.append(
            RadiationPackageIssue(
                "destination_exists",
                f"destination already exists: {destination}",
            )
        )
    if not destination.parent.is_dir():
        issues.append(
            RadiationPackageIssue(
                "destination_directory_missing",
                f"destination directory does not exist: {destination.parent}",
            )
        )

    row = store.get_job_row(job_id)
    if row is None:
        issues.append(
            RadiationPackageIssue("job_not_found", f"no such job: {job_id}")
        )
        return RadiationPackageResult(issues=tuple(issues))
    if row.get("status") != "complete":
        issues.append(
            RadiationPackageIssue(
                "job_not_complete",
                f"job is not complete. Current status: {row.get('status')}",
            )
        )
        return RadiationPackageResult(issues=tuple(issues))

    metadata = row.get("task_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    if metadata.get("field_plane_available") is not True:
        reason = str(metadata.get("unavailable_reason") or "solve_predates_traces")
        issues.append(
            RadiationPackageIssue(
                "traces_not_retained",
                f"this solve retained no field traces: {reason}",
            )
        )
        return RadiationPackageResult(issues=tuple(issues))

    try:
        bundle = store.load_field_trace_bundle(job_id)
    except ArtifactMissing as exc:
        issues.append(
            RadiationPackageIssue(
                "traces_not_retained",
                f"this solve retained no field traces: {exc}",
            )
        )
        return RadiationPackageResult(issues=tuple(issues))
    except (ArtifactCorrupt, OSError, ValueError) as exc:
        issues.append(
            RadiationPackageIssue("traces_unreadable", f"retained traces: {exc}")
        )
        return RadiationPackageResult(issues=tuple(issues))

    results = store.get_results(job_id)
    job_frequencies = _job_frequencies(results)
    available = _availability_mask(job_frequencies, bundle.frequencies_hz)
    missing = [
        frequency
        for frequency, present in zip(job_frequencies, available, strict=True)
        if not present
    ]
    if missing:
        issues.append(
            RadiationPackageIssue(
                "traces_incomplete",
                f"{len(missing)} of {len(job_frequencies)} solved frequencies "
                "have no retained trace; the earliest is "
                f"{missing[0]:g} Hz",
            )
        )
    if issues:
        return RadiationPackageResult(issues=tuple(issues))

    provenance_instant = str(
        created_at
        or row.get("completed_at")
        or row.get("created_at")
        or ""
    )
    payload = {
        MESH_MEMBER: bundle.mesh_text.encode("utf-8"),
        TRACES_MEMBER: _encode_traces_npz(bundle),
    }
    manifest = _build_manifest(
        row,
        bundle,
        results=results,
        job_frequencies=job_frequencies,
        available=available,
        created_at=provenance_instant,
        payload=payload,
    )
    manifest_bytes = _encode_json(manifest)
    archive = _zip_bytes(
        [
            (MANIFEST_MEMBER, manifest_bytes),
            (MESH_MEMBER, payload[MESH_MEMBER]),
            (TRACES_MEMBER, payload[TRACES_MEMBER]),
        ],
        date_time=_zip_date_time(provenance_instant),
    )
    _publish(destination, archive)
    return RadiationPackageResult(
        path=destination,
        bytes=len(archive),
        manifest=manifest,
    )


def validate_radiation_package(
    path: str | os.PathLike[str],
) -> RadiationPackageResult:
    """Re-verify one archive's manifest schema and every member digest."""

    package = Path(path)
    try:
        raw = package.read_bytes()
    except OSError as exc:
        return RadiationPackageResult(
            path=package,
            issues=(
                RadiationPackageIssue(
                    "package_unreadable", f"could not read package: {exc}"
                ),
            ),
        )

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if MANIFEST_MEMBER not in names:
                return RadiationPackageResult(
                    path=package,
                    issues=(
                        RadiationPackageIssue(
                            "manifest_missing",
                            f"package has no {MANIFEST_MEMBER}",
                        ),
                    ),
                )
            members = {name: archive.read(name) for name in names}
    except (OSError, zipfile.BadZipFile) as exc:
        return RadiationPackageResult(
            path=package,
            issues=(
                RadiationPackageIssue(
                    "package_unreadable", f"could not read package: {exc}"
                ),
            ),
        )

    try:
        manifest = json.loads(members[MANIFEST_MEMBER].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        return RadiationPackageResult(
            path=package,
            issues=(
                RadiationPackageIssue(
                    "manifest_invalid", f"{MANIFEST_MEMBER} is not valid JSON: {exc}"
                ),
            ),
        )
    if not isinstance(manifest, dict):
        return RadiationPackageResult(
            path=package,
            issues=(
                RadiationPackageIssue(
                    "manifest_invalid", f"{MANIFEST_MEMBER} must contain an object"
                ),
            ),
        )

    issues: list[RadiationPackageIssue] = []
    if manifest.get("schema") != RADIATION_PACKAGE_SCHEMA or manifest.get(
        "version"
    ) != RADIATION_PACKAGE_VERSION:
        issues.append(
            RadiationPackageIssue(
                "schema_unsupported",
                "package declares schema "
                f"{manifest.get('schema')!r} version {manifest.get('version')!r}; "
                f"expected {RADIATION_PACKAGE_SCHEMA!r} version "
                f"{RADIATION_PACKAGE_VERSION}",
            )
        )
        return RadiationPackageResult(
            path=package, manifest=manifest, issues=tuple(issues)
        )

    files = manifest.get("files")
    if not isinstance(files, Mapping) or not files:
        issues.append(
            RadiationPackageIssue(
                "manifest_invalid", "manifest 'files' must be a non-empty object"
            )
        )
        return RadiationPackageResult(
            path=package, manifest=manifest, issues=tuple(issues)
        )

    for name in sorted(str(key) for key in files):
        entry = files[name]
        expected = entry.get("sha256") if isinstance(entry, Mapping) else None
        if not isinstance(expected, str):
            issues.append(
                RadiationPackageIssue(
                    "manifest_invalid",
                    f"manifest entry for {name} has no sha256 digest",
                )
            )
            continue
        if name not in members:
            issues.append(
                RadiationPackageIssue(
                    "member_missing", f"package is missing member {name}"
                )
            )
            continue
        actual = _sha256(members[name])
        if actual != expected:
            issues.append(
                RadiationPackageIssue(
                    "checksum_mismatch",
                    f"{name} digest {actual} does not match manifest {expected}",
                )
            )

    for name in sorted(set(members) - set(map(str, files)) - {MANIFEST_MEMBER}):
        issues.append(
            RadiationPackageIssue(
                "member_unexpected", f"package member {name} is not in the manifest"
            )
        )

    return RadiationPackageResult(
        path=package,
        bytes=len(raw),
        manifest=manifest,
        issues=tuple(issues),
    )


def _build_manifest(
    row: Mapping[str, Any],
    bundle: FieldTraceBundle,
    *,
    results: Mapping[str, Any] | None,
    job_frequencies: list[float],
    available: list[bool],
    created_at: str,
    payload: Mapping[str, bytes],
) -> dict[str, Any]:
    frequency_count = len(bundle.frequencies_hz)
    channel_count = len(bundle.channel_ids)
    manifest: dict[str, Any] = {
        "schema": RADIATION_PACKAGE_SCHEMA,
        "version": RADIATION_PACKAGE_VERSION,
        "job_id": str(row["id"]),
        "engine": _engine(row, results),
        "backend": bundle.backend,
        "geometry_sha256": bundle.geometry_sha256,
        "symmetry": {
            "plane": bundle.symmetry_plane,
            "traces_domain": "reduced-mesh",
            # v1 ships exactly what the solver held. Materializing the mirrored
            # halves here would double every array and invent node ordering the
            # solver never used; image expansion is the consumer's, and is
            # exact for the rigid symmetry planes the solver applies.
            "consumer_rule": (
                "geometry/mesh.msh and every trace array cover only the reduced "
                "domain. When 'plane' is not null the consumer must image-expand "
                "across it (mirror the mesh and reuse the same p and q on the "
                "image) before evaluating. No mirrored geometry is materialized "
                "in this package."
            ),
        },
        "frequencies": {
            "hz": list(bundle.frequencies_hz),
            "k_real": list(bundle.k_real),
            "k_imag": list(bundle.k_imag),
            "job_frequencies_hz": job_frequencies,
            "available": available,
            "note": (
                "'available' marks, per solved frequency, whether this package "
                "carries its traces. A package is refused unless every entry is "
                "true, so a valid v1 package is always complete."
            ),
        },
        "channels": {
            "ids": list(bundle.channel_ids),
            "kind": "raw",
            "combine_included": False,
            "note": (
                "Raw per-drive-channel traces only. No crossover, gain, delay, "
                "or level-match synthesis is applied or recorded; weight and sum "
                "the channels to reproduce a combined response."
            ),
        },
        "dof_counts": {"p1": bundle.n_p1, "dp0": bundle.n_dp0},
        "conventions": artifact_conventions(),
        "phase_convention": PHASE_CONVENTION,
        "pressure_unit": "Pa",
        "evaluation": {
            "method": "helmholtz-representation-formula",
            "note": (
                "p is the P1 nodal boundary pressure and q its DP0 outward "
                "normal derivative on the same mesh, per frequency and raw "
                "channel. The exterior field at any point follows from the "
                "single- and double-layer potentials of q and p at that "
                "frequency's k."
            ),
        },
        "arrays": {
            TRACES_MEMBER: {
                "format": "npz",
                "members": {
                    "frequencies_hz": _array_spec(
                        "float64", ["frequency"], [frequency_count]
                    ),
                    "k_real": _array_spec("float64", ["frequency"], [frequency_count]),
                    "k_imag": _array_spec("float64", ["frequency"], [frequency_count]),
                    "channel_ids": _array_spec("unicode", ["channel"], [channel_count]),
                    "pressure_p1": _array_spec(
                        "complex64",
                        ["frequency", "channel", "p1_node"],
                        [frequency_count, channel_count, bundle.n_p1],
                    ),
                    "neumann_dp0": _array_spec(
                        "complex64",
                        ["frequency", "channel", "dp0_element"],
                        [frequency_count, channel_count, bundle.n_dp0],
                    ),
                },
                "note": (
                    "The channel axis follows 'channels.ids', which equals the "
                    "channel_ids array in this npz."
                ),
            },
            MESH_MEMBER: {
                "format": "gmsh-2.2-ascii",
                "note": "The exact mesh text the solve used; digested above.",
            },
        },
        "member_order": list(PACKAGE_MEMBER_ORDER),
        "provenance": {
            "app": "waveguide-generator",
            "app_version": _app_version(),
            "created_at": created_at,
            "job_created_at": row.get("created_at"),
            "job_completed_at": row.get("completed_at"),
            "label": row.get("label"),
        },
        "files": {
            name: {"sha256": _sha256(payload[name]), "bytes": len(payload[name])}
            for name in sorted(payload)
        },
    }
    if row.get("run_number") is not None:
        manifest["run_number"] = int(row["run_number"])
    return manifest


def _array_spec(dtype: str, dimensions: Sequence[str], shape: Sequence[int]) -> dict[str, Any]:
    return {
        "dtype": dtype,
        "dimensions": list(dimensions),
        "shape": [int(value) for value in shape],
    }


def _encode_traces_npz(bundle: FieldTraceBundle) -> bytes:
    """Build the npz by hand; ``numpy.savez`` stamps the wall clock into it."""

    arrays: list[tuple[str, np.ndarray]] = [
        ("frequencies_hz", np.asarray(bundle.frequencies_hz, dtype=np.float64)),
        ("k_real", np.asarray(bundle.k_real, dtype=np.float64)),
        ("k_imag", np.asarray(bundle.k_imag, dtype=np.float64)),
        ("channel_ids", np.asarray(bundle.channel_ids)),
        ("pressure_p1", np.ascontiguousarray(bundle.pressure_p1, dtype=np.complex64)),
        ("neumann_dp0", np.ascontiguousarray(bundle.neumann_dp0, dtype=np.complex64)),
    ]
    members: list[tuple[str, bytes]] = []
    for name, array in arrays:
        buffer = io.BytesIO()
        np.lib.format.write_array(buffer, array, allow_pickle=False)
        members.append((f"{name}.npy", buffer.getvalue()))
    return _zip_bytes(
        members,
        date_time=_ZIP_EPOCH,
        compression=zipfile.ZIP_STORED,
    )


def _zip_bytes(
    members: Sequence[tuple[str, bytes]],
    *,
    date_time: tuple[int, int, int, int, int, int],
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, content in members:
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = compression
            info.create_system = _ZIP_CREATE_SYSTEM
            info.external_attr = _ZIP_EXTERNAL_ATTR
            archive.writestr(info, content)
    return buffer.getvalue()


def _zip_date_time(created_at: str) -> tuple[int, int, int, int, int, int]:
    """Derive entry timestamps from the job's own instant, never from now."""

    try:
        moment = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return _ZIP_EPOCH
    if moment.year < 1980:
        return _ZIP_EPOCH
    return (
        moment.year,
        moment.month,
        moment.day,
        moment.hour,
        moment.minute,
        moment.second,
    )


def _publish(destination: Path, archive: bytes) -> None:
    descriptor, staged_name = tempfile.mkstemp(
        prefix=f".{destination.name}.tmp-", dir=destination.parent
    )
    staged = Path(staged_name)
    published = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(archive)
            stream.flush()
            os.fsync(stream.fileno())
        # mkstemp opens at 0600. This is a file the user hands to other tools.
        os.chmod(staged, 0o644)
        if destination.exists():
            raise RadiationPackageError(
                f"destination appeared while writing: {destination}"
            )
        os.replace(staged, destination)
        published = True
    finally:
        if not published:
            staged.unlink(missing_ok=True)


def _job_frequencies(results: Mapping[str, Any] | None) -> list[float]:
    """Read the solve's own frequency grid from stored results.

    Single-channel results carry it at the top level; multi-channel imported
    results carry it once per channel payload instead.
    """

    if not isinstance(results, Mapping):
        return []
    direct = _float_list(results.get("frequencies"))
    if direct:
        return direct
    channels = results.get("channels")
    if isinstance(channels, Mapping):
        for key in sorted(str(name) for name in channels):
            channel = channels[key]
            if not isinstance(channel, Mapping):
                continue
            values = _float_list(channel.get("frequencies"))
            if values:
                return values
    return []


def _float_list(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    result: list[float] = []
    for entry in value:
        if isinstance(entry, bool) or not isinstance(entry, (int, float)):
            return []
        result.append(float(entry))
    return result


def _availability_mask(
    job_frequencies: Sequence[float], retained: Sequence[float]
) -> list[bool]:
    return [
        any(
            abs(frequency - candidate)
            <= _FREQUENCY_TOLERANCE * max(abs(frequency), 1.0)
            for candidate in retained
        )
        for frequency in job_frequencies
    ]


def _engine(row: Mapping[str, Any], results: Mapping[str, Any] | None) -> str | None:
    if isinstance(results, Mapping):
        metadata = results.get("metadata")
        if isinstance(metadata, Mapping) and isinstance(metadata.get("engine"), str):
            return str(metadata["engine"])
    config = row.get("config_json")
    config = config if isinstance(config, Mapping) else {}
    options = config.get("options")
    options = options if isinstance(options, Mapping) else {}
    engine = options.get("engine")
    return str(engine) if isinstance(engine, str) else None


def _app_version() -> str:
    version_path = Path(__file__).resolve().parents[2] / "shared" / "version.json"
    try:
        return str(json.loads(version_path.read_text(encoding="utf-8"))["version"])
    except (OSError, KeyError, ValueError):
        return "unknown"


def _encode_json(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(dict(payload), indent=2, sort_keys=True, allow_nan=False).encode(
            "utf-8"
        )
        + b"\n"
    )


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


__all__ = [
    "MANIFEST_MEMBER",
    "MESH_MEMBER",
    "PACKAGE_MEMBER_ORDER",
    "RADIATION_PACKAGE_SCHEMA",
    "RADIATION_PACKAGE_VERSION",
    "RadiationPackageError",
    "RadiationPackageIssue",
    "RadiationPackageResult",
    "TRACES_MEMBER",
    "build_radiation_package",
    "validate_radiation_package",
]
