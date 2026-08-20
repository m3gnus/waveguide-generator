"""Map every native backend onto the versioned v2 result contract.

The numerical semantics port v1 ``server/solver/result_mapping.py:20-391``:
20 µPa SPL, raw wrapped pressure phase, engineering-sign ``Z/(rho*c)``, one
common frequency axis, null invalid samples, partial-success diagnostics,
full-sphere DI, and the balloon four-state/beam-shape contract.
"""

from __future__ import annotations

import enum
import inspect
import math
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import numpy as np

from .acoustics import reference_air_density_kg_per_m3, reference_sound_speed_m_per_s
from .beam_shape import beam_shape_summary
from .context import SolverContext
from .contract import build_directivity_metadata, frequency_failure
from .directivity_index import calculate_di_from_spherical_grid
from .quadrants import native_symmetry_plane_for_quadrants


REFERENCE_PRESSURE_PA = 20.0e-6
# The pressures normalized below came out of a solve that used the native
# package's own air density -- WG never passes one -- so a typed 1.21 divided
# them by a rho 0.49% away from the rho that made them (~0.04 dB). Ask the
# packages instead. Only the density is shared across backends; the sound speed
# is taken per-engine at the call site in ``build_solver_response``, and this
# module-level pairing is only the default for callers that arrive without one.
REFERENCE_AIR_DENSITY_KG_PER_M3 = reference_air_density_kg_per_m3()
REFERENCE_RHO_C = REFERENCE_AIR_DENSITY_KG_PER_M3 * reference_sound_speed_m_per_s()
_BALLOON_FLOOR_AMPLITUDE = REFERENCE_PRESSURE_PA * 10.0 ** (-120.0 / 20.0)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def json_safe_native_value(value: Any) -> Any:
    """Convert native/NumPy values without permitting JSON NaN/Infinity."""

    if isinstance(value, complex):
        return {"real": _finite(value.real), "imaginary": _finite(value.imag)}
    if isinstance(value, enum.Enum):
        return json_safe_native_value(value.value)
    if isinstance(value, np.generic):
        return json_safe_native_value(value.item())
    if isinstance(value, np.ndarray):
        if value.dtype.kind in "biu":
            return value.tolist()
        if value.dtype.kind == "f":
            if value.ndim == 0:
                return json_safe_native_value(value.item())
            finite = np.isfinite(value)
            if bool(np.all(finite)):
                return value.tolist()
            output = value.astype(object)
            output[~finite] = None
            return output.tolist()
        return json_safe_native_value(value.tolist())
    if isinstance(value, dict):
        return {str(key): json_safe_native_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_native_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def response_solver_log(solver_log: Any) -> list[Any]:
    """Strip duplicated sphere pressure (v1 ``result_mapping.py:49-65``)."""

    entries: list[Any] = []
    for entry in solver_log or []:
        if isinstance(entry, dict) and "observation_sphere_pressure_complex" in entry:
            entry = {
                key: value
                for key, value in entry.items()
                if key != "observation_sphere_pressure_complex"
            }
        entries.append(entry)
    return entries


def native_symmetry_plane(context: SolverContext) -> str | None:
    return native_symmetry_plane_for_quadrants(context.quadrants)


def _project_to_symmetry(vector: np.ndarray, plane: str | None) -> np.ndarray:
    projected = np.asarray(vector, dtype=float).copy()
    if plane == "yz":
        projected[0] = 0.0
    elif plane == "xz":
        projected[1] = 0.0
    elif plane == "yz+xz":
        projected[:2] = 0.0
    return projected


@dataclass(frozen=True)
class _ObservationFrameParts:
    axis: np.ndarray
    mouth_center: np.ndarray
    source_center: np.ndarray
    horizontal: np.ndarray
    vertical: np.ndarray


def _gmsh22_observation_frame_parts(
    msh_text: str,
    *,
    symmetry_plane: str | None,
    aperture_tag: int | None = None,
) -> _ObservationFrameParts:
    """Read the authoritative observation basis and centres from a Gmsh artifact."""

    lines = msh_text.splitlines()
    try:
        nodes_start = lines.index("$Nodes")
        elements_start = lines.index("$Elements")
        node_count = int(lines[nodes_start + 1])
        element_count = int(lines[elements_start + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError(
            "observation framing requires an ASCII Gmsh 2.2 solver artifact"
        ) from exc
    node_rows = lines[nodes_start + 2 : nodes_start + 2 + node_count]
    nodes: dict[int, np.ndarray] = {}
    try:
        for row in node_rows:
            parts = row.split()
            if len(parts) >= 4:
                nodes[int(parts[0])] = np.asarray(parts[1:4], dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("solver artifact contains invalid Gmsh nodes") from exc

    tagged_triangles: dict[int, list[list[np.ndarray]]] = {}
    try:
        for row in lines[elements_start + 2 : elements_start + 2 + element_count]:
            parts = row.split()
            if len(parts) < 3 or int(parts[1]) != 2:
                continue
            tag_count = int(parts[2])
            first_node = 3 + tag_count
            if tag_count < 1 or len(parts) < first_node + 3:
                continue
            physical_tag = int(parts[3])
            node_ids = [int(value) for value in parts[first_node : first_node + 3]]
            if all(node_id in nodes for node_id in node_ids):
                tagged_triangles.setdefault(physical_tag, []).append(
                    [nodes[node_id] for node_id in node_ids]
                )
    except (TypeError, ValueError) as exc:
        raise ValueError("solver artifact contains invalid Gmsh elements") from exc

    source_triangles = tagged_triangles.get(2, [])
    if not nodes or not source_triangles:
        raise ValueError(
            "observation framing requires source-tagged triangles (physical tag 2)"
        )
    vertices = np.stack(list(nodes.values()))
    source = np.asarray(source_triangles, dtype=float)
    normals = np.cross(source[:, 1] - source[:, 0], source[:, 2] - source[:, 0])
    areas = np.linalg.norm(normals, axis=1)
    valid = areas > 1.0e-15
    if not np.any(valid):
        raise ValueError("source-tagged triangles cannot define an observation axis")
    normals, areas, source = normals[valid], areas[valid], source[valid]
    reference = normals[int(np.argmax(areas))]
    signs = np.sign(normals @ reference)
    signs[signs == 0.0] = 1.0
    axis = np.sum(normals * signs[:, None], axis=0)
    axis /= np.linalg.norm(axis)
    projected_axis = _project_to_symmetry(axis, symmetry_plane)
    if np.linalg.norm(projected_axis) > 1.0e-12:
        axis = projected_axis / np.linalg.norm(projected_axis)
    centroids = np.mean(source, axis=1)
    source_center = np.average(centroids, weights=areas, axis=0)

    if aperture_tag is None:
        along_axis = vertices @ axis
        threshold = float(np.max(along_axis) - 0.02 * np.ptp(along_axis))
        mouth_center = np.mean(vertices[along_axis >= threshold], axis=0)
    else:
        aperture = np.asarray(tagged_triangles.get(int(aperture_tag), []), dtype=float)
        if not aperture.size:
            raise ValueError(
                f"solver artifact has no aperture triangles with physical tag {aperture_tag}"
            )
        aperture_normals = np.cross(
            aperture[:, 1] - aperture[:, 0], aperture[:, 2] - aperture[:, 0]
        )
        aperture_areas = np.linalg.norm(aperture_normals, axis=1)
        aperture_valid = aperture_areas > 1.0e-15
        if not np.any(aperture_valid):
            raise ValueError(
                f"aperture triangles with physical tag {aperture_tag} cannot define an origin"
            )
        aperture_centroids = np.mean(aperture[aperture_valid], axis=1)
        mouth_center = np.average(
            aperture_centroids,
            weights=aperture_areas[aperture_valid],
            axis=0,
        )

    reference_axis = np.asarray([1.0, 0.0, 0.0])
    if abs(float(axis @ reference_axis)) >= 0.9:
        reference_axis = np.asarray([0.0, 1.0, 0.0])
    horizontal = reference_axis - float(reference_axis @ axis) * axis
    horizontal /= np.linalg.norm(horizontal)
    vertical = np.cross(axis, horizontal)
    return _ObservationFrameParts(
        axis=axis,
        mouth_center=mouth_center,
        source_center=source_center,
        horizontal=horizontal,
        vertical=vertical,
    )


def _gmsh22_observation_frame(
    msh_text: str,
    *,
    origin_at: str,
    symmetry_plane: str | None,
    aperture_tag: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Infer the native observation frame from the authoritative Gmsh 2.2 artifact."""

    parts = _gmsh22_observation_frame_parts(
        msh_text,
        symmetry_plane=symmetry_plane,
        aperture_tag=aperture_tag,
    )
    # The source cap's outward normal already points from the throat into the
    # fluid, which is forward by construction: the mesher only emits artifacts
    # whose winding it has verified (``require_positive_volume``), and the
    # builder re-checks it as ``semantic_orientation.source_normal_projection``.
    #
    # The native backends additionally second-guess that normal against the
    # mesh extents, flipping the axis when the source sits nearer the far end
    # than a quarter of the span.  That test assumes the mesh is a bare horn,
    # whose throat is the rearmost thing in it.  Put the same horn in a cabinet
    # roughly four times its own depth and the throat lands inside the front
    # quarter, the test "corrects" a perfectly good +z axis to -z, and every
    # polar is then measured two metres behind the closed back panel -- a
    # near-silent, frequency-collapsing response that looks like a leaking box.
    # Nothing here needs the guess, so nothing here makes it.
    origin = parts.mouth_center if origin_at == "mouth" else parts.source_center
    origin = _project_to_symmetry(origin, symmetry_plane)
    return parts.axis, origin, parts.horizontal, parts.vertical


def native_observation_frame(
    context: SolverContext,
    msh_text: str,
    observation_frame_cls: Any,
    *,
    aperture_tag: int | None = None,
) -> Any | None:
    """Build the pinned helper's explicit frame, retaining native centre semantics."""

    if observation_frame_cls is None:
        return None
    try:
        parts = _gmsh22_observation_frame_parts(
            msh_text,
            symmetry_plane=native_symmetry_plane(context),
            aperture_tag=aperture_tag,
        )
    except ValueError:
        if aperture_tag is not None:
            # Coupled infinite-baffle evaluation is defined by that physical
            # aperture. Falling back to an extent-derived mouth would silently
            # move the observation origin when metadata and artifact disagree.
            raise
        # Direct adapter callers may still supply a legacy artifact the helper
        # itself knows how to read. Authoritative v2 builds always have tag 2,
        # and coupled infinite-baffle builds additionally have aperture_tag.
        return None
    origin_at = str(context.polar_config.get("observation_origin") or "mouth")
    origin = parts.mouth_center if origin_at == "mouth" else parts.source_center
    origin = _project_to_symmetry(origin, native_symmetry_plane(context))
    mouth_center = (
        _project_to_symmetry(parts.mouth_center, native_symmetry_plane(context))
        if aperture_tag is not None
        else parts.mouth_center
    )
    return observation_frame_cls(
        axis=parts.axis,
        origin=origin,
        u=parts.horizontal,
        v=parts.vertical,
        mouth_center=mouth_center,
        source_center=parts.source_center,
    )


def _custom_observation_points(
    context: SolverContext,
    msh_text: str,
    *,
    aperture_tag: int | None = None,
) -> dict[str, np.ndarray]:
    polar = context.polar_config
    start, end, count = polar["angle_range"]
    angles = np.deg2rad(np.linspace(float(start), float(end), int(count)))
    axis, origin, horizontal, vertical = _gmsh22_observation_frame(
        msh_text,
        origin_at=str(polar["observation_origin"]),
        symmetry_plane=native_symmetry_plane(context),
        aperture_tag=aperture_tag,
    )
    inclination = math.radians(float(polar["inclination"]))
    transverse = {
        "horizontal": horizontal,
        "vertical": vertical,
        "diagonal": math.cos(inclination) * horizontal + math.sin(inclination) * vertical,
    }
    distance = float(polar["distance"])
    return {
        plane: (
            origin[None, :]
            + distance * np.cos(angles)[:, None] * axis[None, :]
            + distance * np.sin(angles)[:, None] * transverse[plane][None, :]
        )
        for plane in polar["enabled_axes"]
    }


def observation_config(
    context: SolverContext,
    observation_config_cls: Any,
    unavailable_error: type[Exception],
    package_name: str,
    *,
    msh_text: str | None = None,
    aperture_tag: int | None = None,
) -> Any:
    """Build native observation config, requesting a DI sphere when supported."""

    if observation_config_cls is None:
        raise unavailable_error(f"{package_name} is not installed.")
    polar = context.polar_config
    angle_range = polar.get("angle_range") or [0.0, 180.0, 37]
    kwargs: dict[str, Any] = {
        "planes": list(polar.get("enabled_axes") or ["horizontal", "vertical"]),
        "distance_m": float(polar.get("distance", 2.0)),
        "angle_min_deg": float(angle_range[0]),
        "angle_max_deg": float(angle_range[1]),
        "angle_count": int(angle_range[2]),
        "origin": str(polar.get("observation_origin") or "mouth"),
    }
    try:
        supported = inspect.signature(observation_config_cls).parameters
    except (TypeError, ValueError):
        supported = {}
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in supported.values()
    )

    def supports(name: str) -> bool:
        return name in supported or accepts_kwargs

    native_inclination = False
    for field_name in ("inclination_deg", "diagonal_inclination_deg"):
        if supports(field_name):
            kwargs[field_name] = float(polar.get("inclination", 45.0))
            native_inclination = True
            break
    if supports("normalization_angle_deg"):
        kwargs["normalization_angle_deg"] = float(polar.get("norm_angle", 10.0))
    needs_custom_inclination = (
        not native_inclination
        and "diagonal" in kwargs["planes"]
        and not math.isclose(float(polar.get("inclination", 45.0)), 45.0)
    )
    # The adapters supply the authoritative frame through SolveConfig. Explicit
    # points remain necessary only for helpers that lack a native diagonal
    # inclination control; using them for every solve would leave sphere grids
    # and axial source logic on the helper's separately inferred (and possibly
    # backwards) frame.
    if needs_custom_inclination:
        if msh_text is None or not supports("custom_points"):
            raise unavailable_error(
                f"Installed {package_name} cannot represent a custom diagonal inclination."
            )
        kwargs["custom_points"] = _custom_observation_points(
            context,
            msh_text,
            aperture_tag=aperture_tag,
        )
    # DI is a spherical quantity, so its field grid is part of every solve and
    # is independent of the H/V/D display cuts. ``spherical_sampling`` only
    # controls whether the full grid is retained for the 3D balloon result.
    if supports("sphere_grid"):
        kwargs["sphere_grid"] = (
            int(polar.get("spherical_theta_count") or 37),
            int(polar.get("spherical_phi_count") or 72),
        )
        if context.sim_type == 1 and supports("sphere_theta_max_deg"):
            kwargs["sphere_theta_max_deg"] = 90.0
    try:
        return observation_config_cls(**kwargs)
    except TypeError as exc:
        if "sphere" not in str(exc) or "sphere_grid" not in kwargs:
            raise
        kwargs.pop("sphere_grid", None)
        kwargs.pop("sphere_theta_max_deg", None)
        return observation_config_cls(**kwargs)


def directivity(result: Any) -> dict[str, list[list[list[float | None]]]]:
    """Map engine dB patterns, preserving unavailable cells as null."""

    try:
        angles = np.asarray(result.observation_angles_deg, dtype=float)
        values = np.asarray(result.directivity_db, dtype=float)
        frequency_count = len(np.asarray(result.frequencies_hz).reshape(-1))
    except (TypeError, ValueError):
        return {}
    if angles.ndim != 1 or values.ndim != 3:
        return {}
    finite_angle_indices = np.flatnonzero(np.isfinite(angles))
    finite_angle_indices = finite_angle_indices[finite_angle_indices < values.shape[2]]
    angle_values = angles[finite_angle_indices]
    point_count = int(finite_angle_indices.size)
    output: dict[str, list[list[list[float | None]]]] = {}
    for plane_index, plane_name in enumerate(result.observation_planes):
        if plane_index >= values.shape[1]:
            break
        points = np.empty((frequency_count, point_count, 2), dtype=object)
        points[:, :, 0] = angle_values[None, :]
        points[:, :, 1] = None
        usable_frequency_count = min(frequency_count, values.shape[0])
        if usable_frequency_count and point_count:
            levels = values[
                :usable_frequency_count, plane_index, finite_angle_indices
            ].astype(object)
            levels[~np.isfinite(values[:usable_frequency_count, plane_index])[
                :, finite_angle_indices
            ]] = None
            points[:usable_frequency_count, :, 1] = levels
        output[str(plane_name)] = points.tolist()
    return output


def _wrapped_phase_degrees(value: complex) -> float | None:
    """Map one pressure sample using the result contract's phase convention."""

    amplitude = _finite(np.abs(value))
    if amplitude is None or amplitude <= 0.0:
        return None
    phase = float(np.angle(value, deg=True))
    return phase if math.isfinite(phase) else None


def directivity_phase(result: Any) -> dict[str, list[list[list[float | None]]]]:
    """Map raw wrapped pressure phase in the same shape as ``directivity``."""

    pressure_source = getattr(result, "pressure_complex", None)
    if pressure_source is None:
        return {}
    try:
        angles = np.asarray(result.observation_angles_deg, dtype=float)
        pressure = np.asarray(pressure_source, dtype=np.complex128)
        frequency_count = len(np.asarray(result.frequencies_hz).reshape(-1))
        planes = list(result.observation_planes)
    except (AttributeError, TypeError, ValueError):
        return {}
    if angles.ndim != 1 or pressure.ndim != 3:
        return {}
    finite_angle_indices = np.flatnonzero(np.isfinite(angles))
    finite_angle_indices = finite_angle_indices[finite_angle_indices < pressure.shape[2]]
    angle_values = angles[finite_angle_indices]
    point_count = int(finite_angle_indices.size)
    output: dict[str, list[list[list[float | None]]]] = {}
    for plane_index, plane_name in enumerate(planes):
        if plane_index >= pressure.shape[1]:
            break
        points = np.empty((frequency_count, point_count, 2), dtype=object)
        points[:, :, 0] = angle_values[None, :]
        points[:, :, 1] = None
        usable_frequency_count = min(frequency_count, pressure.shape[0])
        for frequency_index in range(usable_frequency_count):
            for point_index, angle_index in enumerate(finite_angle_indices):
                points[frequency_index, point_index, 1] = _wrapped_phase_degrees(
                    pressure[frequency_index, plane_index, angle_index]
                )
        output[str(plane_name)] = points.tolist()
    return output


def _renormalize_directivity(
    patterns: dict[str, list[list[list[float | None]]]], norm_angle: float
) -> None:
    """Shift each native polar row so the requested normalization angle is 0 dB."""

    for rows in patterns.values():
        for row in rows:
            finite = [
                (float(angle), float(level))
                for angle, level in row
                if level is not None and math.isfinite(float(angle)) and math.isfinite(float(level))
            ]
            if not finite:
                continue
            finite.sort(key=lambda point: point[0])
            reference = float(
                np.interp(
                    float(norm_angle),
                    [point[0] for point in finite],
                    [point[1] for point in finite],
                )
            )
            for point in row:
                if point[1] is not None:
                    point[1] = float(point[1]) - reference


def _on_axis_pressure(result: Any) -> np.ndarray | None:
    pressure_source = getattr(result, "pressure_complex", None)
    if pressure_source is None:
        return None
    try:
        angles = np.asarray(result.observation_angles_deg, dtype=float)
        pressure = np.asarray(pressure_source, dtype=np.complex128)
    except (AttributeError, TypeError, ValueError):
        return None
    if angles.ndim != 1 or angles.size == 0 or pressure.ndim != 3 or pressure.shape[1] == 0:
        return None
    reference = _on_axis_reference(angles, pressure.shape[2])
    if reference is None:
        return None
    angle_index, _angle_deg = reference
    if angle_index >= pressure.shape[2]:
        return None
    return pressure[:, 0, angle_index]


def _on_axis_reference(
    angles: np.ndarray, point_count: int
) -> tuple[int, float] | None:
    """Return the finite sample nearest zero and the angle it actually represents."""

    usable = np.flatnonzero(np.isfinite(angles))
    usable = usable[usable < point_count]
    if usable.size == 0:
        return None
    angle_index = int(usable[np.argmin(np.abs(angles[usable]))])
    return angle_index, float(angles[angle_index])


def _on_axis_reference_angle(result: Any) -> float | None:
    try:
        angles = np.asarray(result.observation_angles_deg, dtype=float)
        pressure = np.asarray(result.pressure_complex, dtype=np.complex128)
    except (AttributeError, TypeError, ValueError):
        return None
    if angles.ndim != 1 or pressure.ndim != 3 or pressure.shape[1] == 0:
        return None
    reference = _on_axis_reference(angles, pressure.shape[2])
    return reference[1] if reference is not None else None


def spl_on_axis(result: Any) -> list[float | None]:
    values = _on_axis_pressure(result)
    count = len(np.asarray(result.frequencies_hz))
    if values is None:
        return [None] * count
    output: list[float | None] = []
    for value in values:
        amplitude = _finite(np.abs(value))
        if amplitude is None or amplitude <= 0.0:
            output.append(None)
            continue
        spl = 20.0 * (math.log10(amplitude) - math.log10(REFERENCE_PRESSURE_PA))
        output.append(spl if math.isfinite(spl) else None)
    count = len(np.asarray(result.frequencies_hz).reshape(-1))
    return (output + [None] * count)[:count]


def phase_on_axis(result: Any) -> list[float | None]:
    """Raw wrapped pressure phase in degrees (v1 lines 167-182)."""

    values = _on_axis_pressure(result)
    count = len(np.asarray(result.frequencies_hz))
    if values is None:
        return [None] * count
    output: list[float | None] = []
    for value in values:
        output.append(_wrapped_phase_degrees(value))
    count = len(np.asarray(result.frequencies_hz).reshape(-1))
    return (output + [None] * count)[:count]


def specific_impedance_z_over_rho_c(
    result: Any, *, rho_c: float = REFERENCE_RHO_C
) -> list[complex | None]:
    """Convert unit-acceleration pressure using v=a/(-iω), then conjugate.

    This is the sign/drive convention from v1
    ``server/solver/result_mapping.py:185-200``.  ``rho_c`` names the medium
    the result is reported against; it defaults to the shared reference so a
    direct caller holding only a result still gets the packages' constants
    rather than a typed pair.
    """

    frequencies = np.asarray(result.frequencies_hz, dtype=float)
    raw_pressure = np.asarray(result.impedance, dtype=np.complex128).reshape(-1)
    output: list[complex | None] = []
    for index, frequency in enumerate(frequencies.reshape(-1)):
        if index >= raw_pressure.size:
            output.append(None)
            continue
        mapped = np.conjugate(-1j * 2.0 * np.pi * frequency * raw_pressure[index]) / rho_c
        output.append(
            complex(mapped)
            if math.isfinite(float(mapped.real)) and math.isfinite(float(mapped.imag))
            else None
        )
    return output


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _mesh_resolution_warning(
    *,
    suspect_frequencies: list[float],
    diagnosed_count: int,
    limit_hz: float | None,
    max_edge_m: float | None,
    min_elements_per_wavelength: float | None,
) -> str:
    """One aggregated line naming the mesh's validity ceiling and the band above it."""

    finite = sorted(f for f in suspect_frequencies if math.isfinite(f))
    count = len(suspect_frequencies)
    scope = (
        f"{count} of {diagnosed_count}" if diagnosed_count >= count else f"{count}"
    )
    frequency_noun = "frequency" if diagnosed_count == 1 else "frequencies"
    band = f" ({finite[0]:.0f}-{finite[-1]:.0f} Hz)" if finite else ""
    if limit_hz is not None:
        rule = ""
        if max_edge_m is not None and min_elements_per_wavelength is not None:
            rule = (
                f" ({min_elements_per_wavelength:g} elements per wavelength on the "
                f"worst {max_edge_m * 1000.0:.1f} mm edge)"
            )
        head = (
            "Mesh resolution supports this solve only up to about "
            f"{limit_hz:.0f} Hz{rule}"
        )
    else:
        head = "Mesh resolution is insufficient for part of this sweep"
    return (
        f"{head}; {scope} solved {frequency_noun}{band} exceed the limit and their "
        "SPL/DI are unreliable. Refine the mesh's mm resolutions or lower the "
        "sweep's upper frequency."
    )


def _apply_solver_log_warnings(metadata: dict[str, Any]) -> None:
    """Surface GMRES/LAPACK failures without blanking usable arrays.

    Per-frequency accuracy diagnostics are surfaced too. Mesh-resolution
    suspects aggregate into a single warning: a wide sweep can exceed the
    mesh's validity ceiling at dozens of frequencies, and one line per
    frequency would bury that single finding in repetition.
    """

    solver_log = None
    for backend_key in ("metal", "bempp"):
        backend = metadata.get(backend_key)
        if isinstance(backend, dict) and isinstance(backend.get("solver_log"), list):
            solver_log = backend["solver_log"]
            break
    if solver_log is None:
        return
    warnings = metadata.setdefault("warnings", [])
    unreliable = 0
    resolution_diagnosed = 0
    resolution_suspects: list[float] = []
    resolution_limit_hz: float | None = None
    resolution_edge_m: float | None = None
    resolution_min_epw: float | None = None
    for entry in solver_log:
        if not isinstance(entry, dict):
            continue
        frequency = entry.get("frequency_hz")
        label = f"{float(frequency):.1f} Hz" if isinstance(frequency, (int, float)) else "unknown frequency"
        if entry.get("converged") is False:
            warnings.append(f"GMRES did not converge at {label}; SPL/DI at this frequency is unreliable.")
            unreliable += 1
        lapack_info = entry.get("lapack_info")
        if isinstance(lapack_info, (int, float)) and int(lapack_info) != 0:
            warnings.append(
                f"Dense LU solve failed (LAPACK info={int(lapack_info)}) at {label}; "
                "results at this frequency are unreliable."
            )
            unreliable += 1
        diagnostics = entry.get("native_diagnostics")
        if not isinstance(diagnostics, dict):
            continue
        if diagnostics.get("dense_solve_suspect") is True:
            warnings.append(
                f"Dense-solve conditioning is suspect at {label} (near a fictitious resonance); "
                "compare against neighbouring frequencies."
            )
        if "mesh_resolution_suspect" in diagnostics:
            resolution_diagnosed += 1
        if diagnostics.get("mesh_resolution_suspect") is True:
            suspect_frequency = _finite_number(frequency)
            resolution_suspects.append(
                suspect_frequency if suspect_frequency is not None else math.nan
            )
            limit = _finite_number(diagnostics.get("mesh_max_valid_frequency_hz"))
            if limit is not None:
                resolution_limit_hz = (
                    limit
                    if resolution_limit_hz is None
                    else min(resolution_limit_hz, limit)
                )
            edge = _finite_number(diagnostics.get("mesh_max_edge_m"))
            if edge is not None:
                resolution_edge_m = max(resolution_edge_m or 0.0, edge)
            min_epw = _finite_number(
                diagnostics.get("mesh_elements_per_wavelength_min")
            )
            if min_epw is not None:
                resolution_min_epw = min_epw
    if resolution_suspects:
        warnings.append(
            _mesh_resolution_warning(
                suspect_frequencies=resolution_suspects,
                diagnosed_count=resolution_diagnosed,
                limit_hz=resolution_limit_hz,
                max_edge_m=resolution_edge_m,
                min_elements_per_wavelength=resolution_min_epw,
            )
        )
    metadata["warning_count"] = len(warnings)
    if unreliable:
        metadata["partial_success"] = True


def _balloon_grid_from_result(result: Any) -> dict[str, Any] | None:
    """Apply v1 theta-major validation, -120 dB floor, and pole normalization."""

    pressure = getattr(result, "sphere_pressure_complex", None)
    theta_deg = getattr(result, "sphere_theta_deg", None)
    phi_deg = getattr(result, "sphere_phi_deg", None)
    if pressure is None or theta_deg is None or phi_deg is None:
        return None
    try:
        pressure = np.asarray(pressure, dtype=np.complex128)
        theta_flat = np.asarray(theta_deg, dtype=float)
        phi_flat = np.asarray(phi_deg, dtype=float)
        frequency_count = len(np.asarray(result.frequencies_hz).reshape(-1))
    except (TypeError, ValueError):
        return None
    if (
        pressure.ndim != 2
        or pressure.shape[0] != frequency_count
        or theta_flat.ndim != 1
        or phi_flat.ndim != 1
        or theta_flat.size != pressure.shape[1]
        or phi_flat.size != pressure.shape[1]
        or not np.isfinite(theta_flat).all()
        or not np.isfinite(phi_flat).all()
    ):
        return None
    theta_axis = np.unique(theta_flat)
    if theta_axis.size < 2 or theta_flat.size % theta_axis.size != 0:
        return None
    phi_count = theta_flat.size // theta_axis.size
    if phi_count < 3:
        return None
    theta_grid = theta_flat.reshape(theta_axis.size, phi_count)
    phi_grid = phi_flat.reshape(theta_axis.size, phi_count)
    phi_axis = phi_grid[0]
    if (
        not np.all(np.diff(theta_axis) > 0.0)
        or not np.all(np.diff(phi_axis) > 0.0)
        or not np.allclose(theta_grid, theta_axis[:, None])
        or not np.allclose(phi_grid, phi_axis[None, :])
    ):
        return None
    finite_rows = np.isfinite(pressure.real).all(axis=1) & np.isfinite(
        pressure.imag
    ).all(axis=1)
    normalized = np.full(pressure.shape, np.nan, dtype=np.float64)
    if np.any(finite_rows):
        amplitude = np.maximum(
            np.abs(pressure[finite_rows]), _BALLOON_FLOOR_AMPLITUDE
        )
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            spl = 20.0 * (
                np.log10(amplitude) - math.log10(REFERENCE_PRESSURE_PA)
            )
        normalized[finite_rows] = spl - spl[:, 0][:, None]
    return {
        "theta_deg": theta_axis,
        "phi_deg": phi_axis,
        "spl_norm_db": normalized.reshape(pressure.shape[0], theta_axis.size, phi_count),
        "hemisphere": bool(theta_axis[-1] <= 90.0 + 1.0e-9),
        "invalid_frequency_indices": np.flatnonzero(~finite_rows).tolist(),
    }


def build_solver_response(
    *,
    result: Any,
    config: Any,
    context: SolverContext,
    start_time: float,
    metadata: dict[str, Any],
    sound_speed_m_per_s: float,
) -> dict[str, Any]:
    """Build the common JSON shape; dry-run remains a strict subset of it."""

    frequencies = [_finite(value) for value in np.asarray(result.frequencies_hz).tolist()]
    if any(value is None for value in frequencies):
        raise ValueError("native solver returned a non-finite frequency axis")
    frequency_values = [float(value) for value in frequencies if value is not None]
    # The engine that ran the solve is known here through the c it was given,
    # so normalize against that c rather than against whichever package
    # happened to be installed when this module was imported.
    impedance = specific_impedance_z_over_rho_c(
        result, rho_c=REFERENCE_AIR_DENSITY_KG_PER_M3 * float(sound_speed_m_per_s)
    )
    observation = {
        "requested_distance_m": float(config.observation.distance_m),
        "effective_distance_m": float(config.observation.distance_m),
        "adjusted": False,
        "observation_origin": str(config.observation.origin),
        "sound_speed_m_per_s": float(sound_speed_m_per_s),
    }
    patterns = directivity(result)
    phase_patterns = directivity_phase(result)
    on_axis_angle_deg = _on_axis_reference_angle(result)
    balloon = _balloon_grid_from_result(result)
    spherical_di_available = balloon is not None and (
        not bool(balloon["hemisphere"]) or context.sim_type == 1
    )
    if spherical_di_available:
        assert balloon is not None
        di_values = calculate_di_from_spherical_grid(
            balloon["theta_deg"],
            balloon["phi_deg"],
            balloon["spl_norm_db"],
            hemisphere=bool(balloon["hemisphere"]),
        )
        di_method = "spherical_grid"
    else:
        di_values = [None] * len(frequency_values)
        di_method = "unavailable"
    # This is strictly a level/display normalization. Raw wrapped phase is an
    # independent payload and must never pass through this mutating function.
    _renormalize_directivity(
        patterns, float(context.polar_config.get("norm_angle", 10.0))
    )

    metadata.setdefault("warnings", [])
    metadata.setdefault("warning_count", 0)
    metadata.setdefault("failures", [])
    metadata.setdefault("failure_count", len(metadata["failures"]))
    metadata.setdefault("partial_success", False)
    failures = metadata["failures"]
    expected = len(frequency_values)
    raw_counts = {
        "pressure": (
            int(np.asarray(getattr(result, "pressure_complex", [])).shape[0])
            if np.asarray(getattr(result, "pressure_complex", [])).ndim >= 1
            else 0
        ),
        "directivity": (
            int(np.asarray(getattr(result, "directivity_db", [])).shape[0])
            if np.asarray(getattr(result, "directivity_db", [])).ndim >= 1
            else 0
        ),
        "impedance": int(np.asarray(getattr(result, "impedance", [])).size),
    }
    for quantity, actual in raw_counts.items():
        if actual != expected:
            failures.append(
                frequency_failure(
                    frequency_values[min(actual, expected - 1)] if expected else 0.0,
                    "postprocess",
                    "native_axis_mismatch",
                    f"{quantity} returned {actual} samples for {expected} frequencies; unavailable samples were padded with null",
                )
            )
    if balloon is not None:
        for frequency_index in balloon["invalid_frequency_indices"]:
            failures.append(
                frequency_failure(
                    frequency_values[frequency_index],
                    "postprocess",
                    "nonfinite_spherical_pressure",
                    "spherical pressure contains a non-finite sample; balloon "
                    "and DI cells at this frequency were set to null",
                )
            )
    if any(actual != expected for actual in raw_counts.values()):
        metadata["partial_success"] = True
    if balloon is not None and balloon["invalid_frequency_indices"]:
        metadata["partial_success"] = True
    metadata["spl_on_axis"] = {
        "requested_angle_degrees": 0.0,
        "sampled_angle_degrees": on_axis_angle_deg,
    }
    if on_axis_angle_deg is not None and not np.isclose(
        on_axis_angle_deg, 0.0, rtol=0.0, atol=1.0e-9
    ):
        metadata["warnings"].append(
            "0 degrees is not present in the sampled polar grid; reported "
            "on-axis SPL and phase use the nearest available angle, "
            f"{on_axis_angle_deg:g} degrees"
        )
    metadata["failure_count"] = len(failures)
    _apply_solver_log_warnings(metadata)
    metadata["warning_count"] = len(metadata["warnings"])
    metadata.setdefault("performance", {})
    metadata["performance"].setdefault("total_time_seconds", time.time() - start_time)
    metadata["observation"] = observation
    metadata["directivity"] = build_directivity_metadata(
        {
            **context.polar_config,
            "distance": config.observation.distance_m,
            "observation_origin": config.observation.origin,
        },
        observation,
    )
    metadata["directivity_index"] = {
        "available": any(value is not None for value in di_values),
        "definition": "10log10(reference-axis mean-square pressure / full-sphere mean-square pressure)",
        "method": di_method,
        "domain": "full_sphere",
        "power_average": "linear_mean_square_pressure",
        "planes_used": [],
        "opposing_orbit_sides": "not_applicable",
        "rear_hemisphere": "zero_radiation" if context.sim_type == 1 else "sampled",
    }
    metadata.update(
        {
            "result_contract_version": 1,
            "phase_quantity": "raw_wrapped_pressure_phase",
            "phase_units": "degrees",
            "impedance_units": "Z/(rho*c)",
            "impedance_quantity": "specific_acoustic_impedance",
            "impedance_drive": "unit_acceleration",
            "source_motion": context.source_motion,
            "quadrants": context.quadrants,
            # "explicit" rather than log/linear: reporting a spacing the solve
            # never used would misdescribe the sweep to every downstream reader.
            "frequency_spacing": (
                "explicit" if context.frequencies_hz is not None else context.frequency_spacing
            ),
            "frequency_source": (
                "explicit_list" if context.frequencies_hz is not None else "generated_grid"
            ),
            "verbose": context.verbose,
        }
    )

    response: dict[str, Any] = {
        "result_kind": "parametric",
        "result_contract_version": 1,
        "frequencies": frequency_values,
        "directivity": patterns,
        "directivity_phase": phase_patterns,
        "spl_on_axis": {
            "frequencies": frequency_values,
            "spl": spl_on_axis(result),
            "phase_degrees": phase_on_axis(result),
        },
        "impedance": {
            "frequencies": frequency_values,
            "real": [_finite(value.real) if value is not None else None for value in impedance],
            "imaginary": [_finite(value.imag) if value is not None else None for value in impedance],
        },
        "di": {
            "frequencies": frequency_values,
            "di": di_values,
        },
        "metadata": metadata,
    }

    requested = bool(context.polar_config.get("spherical_sampling"))
    configured = requested and getattr(config.observation, "sphere_grid", None) is not None
    balloon_available = requested and balloon is not None
    metadata["balloon_sampling"] = {
        "requested": requested,
        "configured": configured,
        "available": balloon_available,
        "status": (
            "available"
            if balloon_available
            else "backend_unsupported"
            if requested and not configured
            else "missing_result"
            if requested
            else "disabled"
        ),
    }
    if balloon_available:
        assert balloon is not None
        response["balloon"] = {
            "frequencies": frequency_values,
            "theta_deg": [round(float(value), 3) for value in balloon["theta_deg"]],
            "phi_deg": [round(float(value), 3) for value in balloon["phi_deg"]],
            "spl_norm_db": json_safe_native_value(np.round(balloon["spl_norm_db"], 2)),
            "distance_m": float(config.observation.distance_m),
            "hemisphere": balloon["hemisphere"],
        }
        summary = beam_shape_summary(
            balloon["theta_deg"],
            balloon["phi_deg"],
            balloon["spl_norm_db"],
            frequency_values,
            hemisphere=balloon["hemisphere"],
        )
        if summary is not None:
            response["beam_shape"] = summary
    return response


def build_provisional_frequency_response(
    *,
    index: int,
    frequency_hz: float,
    entry: dict[str, Any],
    config: Any,
    context: SolverContext,
    backend: str,
    sound_speed_m_per_s: float,
) -> dict[str, Any]:
    """Map one streamed native callback onto the ordinary result contract.

    The native packages deliberately stream their compact per-frequency log
    entries rather than constructing a second public result type.  Converting
    that row here keeps provisional charts on exactly the same SPL, phase,
    impedance, directivity-normalisation, and DI conventions as the durable
    result built when the sweep finishes.

    Older BEMPP callback entries expose normalised directivity but not complex
    pressure.  They still produce a useful live directivity map; their absolute
    SPL and phase cells remain null until the canonical result lands.
    """

    try:
        angles = np.asarray(entry.get("observation_angles_deg"), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("streamed result has invalid observation angles") from exc
    planes = [str(value) for value in entry.get("observation_planes") or []]
    if angles.ndim != 1 or not planes:
        raise ValueError("streamed result is missing observation angles or planes")

    directivity_source = entry.get("observation_directivity_db")
    if directivity_source is None:
        directivity_source = entry.get("observation_spl_db")
    try:
        directivity_row = np.asarray(directivity_source, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("streamed result has invalid directivity values") from exc
    expected_shape = (len(planes), angles.size)
    if directivity_row.shape != expected_shape:
        raise ValueError(
            "streamed directivity shape does not match its planes/angles: "
            f"{directivity_row.shape} != {expected_shape}"
        )

    pressure_source = entry.get("observation_pressure_complex")
    if pressure_source is None:
        # A zero complex row maps to contract nulls for absolute SPL and phase,
        # while retaining the streamed normalised directivity values above.
        pressure_row = np.zeros(expected_shape, dtype=np.complex128)
    else:
        try:
            pressure_row = np.asarray(pressure_source, dtype=np.complex128)
        except (TypeError, ValueError) as exc:
            raise ValueError("streamed result has invalid complex pressure") from exc
        if pressure_row.shape != expected_shape:
            raise ValueError(
                "streamed pressure shape does not match its planes/angles: "
                f"{pressure_row.shape} != {expected_shape}"
            )

    raw_impedance = entry.get("impedance")
    try:
        impedance = complex(raw_impedance) if raw_impedance is not None else complex(math.nan)
    except (TypeError, ValueError) as exc:
        raise ValueError("streamed result has invalid impedance") from exc

    native_result = SimpleNamespace(
        frequencies_hz=np.asarray([float(frequency_hz)], dtype=float),
        observation_angles_deg=angles,
        observation_planes=planes,
        pressure_complex=pressure_row[None, ...],
        directivity_db=directivity_row[None, ...],
        impedance=np.asarray([impedance], dtype=np.complex128),
    )
    response = build_solver_response(
        result=native_result,
        config=config,
        context=context,
        start_time=time.time(),
        metadata={"solver_backend": backend},
        sound_speed_m_per_s=sound_speed_m_per_s,
    )
    expected = (
        len(context.frequencies_hz)
        if context.frequencies_hz is not None
        else int(context.num_frequencies)
    )
    response["metadata"]["provisional"] = {
        "completed_frequency_count": int(index) + 1,
        "expected_frequency_count": expected,
    }
    return response


__all__ = [
    "REFERENCE_AIR_DENSITY_KG_PER_M3",
    "REFERENCE_PRESSURE_PA",
    "REFERENCE_RHO_C",
    "build_solver_response",
    "build_provisional_frequency_response",
    "directivity",
    "directivity_phase",
    "json_safe_native_value",
    "native_symmetry_plane",
    "observation_config",
    "phase_on_axis",
    "response_solver_log",
    "specific_impedance_z_over_rho_c",
    "spl_on_axis",
]
