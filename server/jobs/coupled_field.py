"""Post-solve planes joining bounded P1 air to the official BEM potential."""
from __future__ import annotations

import base64
import time

import numpy as np
from scipy.spatial import cKDTree

from server.jobs.fem_field import FemFieldCorrupt, FemFieldInvalidSelection, _encode
from server.solver.field_plane import field_plane_points, FieldPlaneTimedOut, FieldPlaneEvaluationFailed


def _decode(value, dtype, width=1):
    return np.frombuffer(base64.b64decode(value), dtype=dtype).reshape(-1, width)


def sample_interior(xyz, tetrahedra, pressure, points, frequency, density, check=lambda: None):
    """Barycentric P1 pressure and constant velocity in exp(-i omega t)."""
    points = np.asarray(points, dtype=float)
    values = np.zeros(len(points), complex)
    velocity = np.zeros((len(points), 3), complex)
    inside = np.zeros(len(points), bool)
    tree = cKDTree(points)
    tetrahedra = np.asarray(tetrahedra)
    for start in range(0, len(tetrahedra), 512):
        check()
        indices = tetrahedra[start:start + 512]
        corners = xyz[indices]
        centers = corners.mean(axis=1)
        candidates = tree.query_ball_point(centers, np.linalg.norm(corners - centers[:, None], axis=2).max(axis=1) + 1e-9)
        counts = np.array([len(c) for c in candidates])
        if not counts.sum():
            continue
        elements = np.repeat(np.arange(len(indices)), counts)
        selected = np.concatenate([np.asarray(c, dtype=int) for c in candidates])
        try:
            inverse = np.linalg.inv(corners[:, 1:] - corners[:, :1])
        except np.linalg.LinAlgError as exc:
            raise FemFieldCorrupt("Degenerate retained FEM tetrahedron") from exc
        bary = np.einsum("ni,nij->nj", points[selected] - corners[elements, 0], inverse[elements])
        keep = (bary >= -1e-7).all(axis=1) & (bary.sum(axis=1) <= 1 + 1e-7) & ~inside[selected]
        # Preserve first-element priority on shared faces without Python/tet loops.
        hits = np.flatnonzero(keep)
        _, first = np.unique(selected[hits], return_index=True)
        hits = hits[first]
        selected, elements, bary = selected[hits], elements[hits], bary[hits]
        delta = pressure[indices[:, 1:]] - pressure[indices[:, :1]]
        values[selected] = pressure[indices[elements, 0]] + np.einsum("ni,ni->n", bary, delta[elements])
        velocity[selected] = np.einsum("nij,nj->ni", inverse[elements], delta[elements]) / (1j * 2 * np.pi * frequency * density)
        inside[selected] = True
    return values, velocity, inside


def inside_bodies(mesh, points, symmetry, check=lambda: None):
    """Indexed ray parity, including the closed symmetry images."""
    from server.jobs.body_index import SkinRayIndex, skin_triangles

    index = SkinRayIndex(skin_triangles(mesh, symmetry), [0.371, 0.529, 0.763], check)
    return index.query(points, check)[0]


def evaluate_coupled_plane(store, job_id, request, *, evaluator=None, timeout_seconds=90, cancellation_callback=None):
    deadline = time.monotonic() + timeout_seconds
    def check():
        if cancellation_callback is not None:
            cancellation_callback()
        if time.monotonic() >= deadline:
            raise FieldPlaneTimedOut("Coupled field plane exceeded 90 seconds")
    payload = store.load_fem_field(job_id, request.frequency, request.channel)
    if payload is None:
        return None
    points = field_plane_points(request.plane)
    xyz = _decode(payload["points_mm"], "<f4", 3).astype(float) / 1000
    tets = _decode(payload["tetrahedra"], "<u4", 4)
    pressure = _decode(payload["pressure_real"], "<f4").ravel() + 1j * _decode(payload["pressure_imag"], "<f4").ravel()
    frequency = payload["frequency_hz"]
    density = payload.get("density_kg_per_m3", 1.21)
    if not np.isfinite(density) or density <= 0:
        raise FemFieldCorrupt("Invalid retained FEM density")
    values, velocity, interior = sample_interior(xyz, tets, pressure, points, frequency, density, check)
    region_class = interior.astype("u1")
    crosses = np.zeros((request.plane.nx - 1) * (request.plane.ny - 1), bool)
    valid = interior.copy() if request.scope != "exterior" else np.zeros(len(points), bool)
    # Wire codes: 0 valid, 1 solid/outside selected domain, 2 unreliable stencil.
    mask_reason = np.ones(len(points), dtype="u1")
    if request.scope != "interior":
        retained = store.load_bem_traces(job_id, frequency, payload["channel_id"])
        if retained is None:
            raise FemFieldInvalidSelection("No retained BEM traces; re-solve to enable exterior planes")
        mesh, p, q, speed, density, symmetry, rotation = retained
        from server.jobs.body_index import SkinRayIndex, SkinDistanceIndex, skin_triangles, crossing_cells

        triangles = skin_triangles(mesh, symmetry)
        image_points = points @ rotation.T
        # One base classification per request; velocity reuses it.
        body = SkinRayIndex(triangles, [0.371, 0.529, 0.763], check).query(image_points, check)[0]
        exterior = ~interior & ~body
        region_class[exterior] = 2
        crosses = crossing_cells(triangles, request.plane, rotation, check)
        if exterior.any():
            if evaluator is None:
                from server.solver.beat_field import evaluate_exterior_from_traces
                evaluator = evaluate_exterior_from_traces
            normal = np.cross(request.plane.axis_u, request.plane.axis_v)
            selected = points[exterior] @ rotation.T
            image_normal = normal @ rotation.T
            # The official operation returns pressure. A centred difference of
            # that same potential gives normal exterior velocity; P1 interior
            # velocity above remains exact, with no averaging between elements.
            # Bound wavelength and curvature; float32 pressure noise also
            # requires a resolvable phase difference, beyond coordinate ULPs.
            step = np.full(len(selected), min(speed / frequency / 50, 0.1))
            evaluation_points = selected
            if request.quantity == "velocity_normal":
                distance, element_size = SkinDistanceIndex(triangles, check).query(selected, check, return_sizes=True)
                step = np.minimum(step, distance * 0.05)
                rounding = np.max(np.abs(np.spacing(selected.astype(np.float32))), axis=1).astype(float)
                # BEAT pressure evaluations are complex64 even for CPU solves.
                # Relative pressure noise ~1e-5 needs k*h >= 1e-2 rad.
                k = 2 * np.pi * frequency / speed
                minimum_step = max(.001, .01 / k)
                safe = ((distance >= np.maximum(.002, .25 * element_size))
                        & (step >= np.maximum(32 * rounding, minimum_step)))
                mask_reason[np.flatnonzero(exterior)[~safe]] = 2
                indices = np.flatnonzero(exterior)[safe]
                exterior[:] = False
                exterior[indices] = True
                selected, step = selected[safe], step[safe]
                evaluation_points = np.concatenate([selected + step[:, None] * image_normal,
                                                    selected - step[:, None] * image_normal])
            result = (np.asarray(evaluator(mesh, frequency, 2 * np.pi * frequency / speed, p, q,
                                          evaluation_points, symmetry_plane={"off": None, "x": "yz", "xy": "yz+xz"}[symmetry],
                                          cancellation_callback=check)) if len(evaluation_points) else np.empty(0, complex))
            if result.shape != (len(evaluation_points),) or not np.isfinite(result).all():
                raise FieldPlaneEvaluationFailed(
                    "BEAT runtime returned invalid exterior field values; retry the field evaluation")
            if request.quantity == "pressure":
                values[exterior] = result
            else:
                velocity[exterior] = ((result[:len(selected)] - result[len(selected):]) / (2 * step * 1j * 2 * np.pi * frequency * density))[:, None] * normal
            valid |= exterior
    if request.quantity == "velocity_normal":
        values = velocity @ np.cross(request.plane.axis_u, request.plane.axis_v)
    values[~valid] = 0
    mask_reason[valid] = 0
    return {"frequency_hz": frequency, "channel_id": payload["channel_id"], "scope": request.scope,
            "quantity": request.quantity, "unit": "Pa" if request.quantity == "pressure" else "m/s",
            "phasor_convention": payload["phasor_convention"], "nx": request.plane.nx, "ny": request.plane.ny,
            "points_mm": _encode(points * 1000, "<f4"), "pressure_real": _encode(values.real, "<f4"),
            "pressure_imag": _encode(values.imag, "<f4"),
            "region_class": base64.b64encode(region_class.tobytes()).decode("ascii"),
            "crosses_body": base64.b64encode(crosses.astype("u1").tobytes()).decode("ascii"),
            "mask_reason": base64.b64encode(mask_reason.tobytes()).decode("ascii"),
            "valid_mask": base64.b64encode(valid.astype("u1").tobytes()).decode("ascii")}


async def evaluate_coupled_plane_async(store, job_id, request, coordinator):
    """Use the existing latest-wins field permit and retain it until draining."""
    import asyncio
    from uuid import uuid4
    import threading

    lease = await coordinator.permit.acquire_field(
        f"coupled-{uuid4().hex}", solve_queued=bool(coordinator._solve_queued()))
    timeout = min(90, coordinator.timeout_seconds)
    stopped = threading.Event()
    def check_stopped():
        if stopped.is_set():
            raise FieldPlaneTimedOut("Coupled field plane evaluation ended")
    work = asyncio.create_task(asyncio.to_thread(evaluate_coupled_plane, store, job_id, request,
        timeout_seconds=timeout, cancellation_callback=check_stopped))
    try:
        result = await asyncio.wait_for(asyncio.shield(work), timeout=timeout)
    except asyncio.TimeoutError as exc:
        stopped.set()
        coordinator._release_when_finished(work, lease)
        raise FieldPlaneTimedOut("Coupled field plane exceeded 90 seconds") from exc
    except asyncio.CancelledError:
        stopped.set()
        coordinator._release_when_finished(work, lease)
        raise
    except BaseException:
        await lease.release()
        raise
    await lease.release()
    return result
