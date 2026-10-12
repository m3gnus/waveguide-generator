"""Post-solve planes joining bounded P1 air to the official BEM potential."""
from __future__ import annotations

import base64
import time

import numpy as np
from scipy.spatial import cKDTree

from server.jobs.fem_field import FemFieldCorrupt, FemFieldInvalidSelection, _encode
from server.solver.field_plane import field_plane_points, FieldPlaneTimedOut


def _decode(value, dtype, width=1):
    return np.frombuffer(base64.b64decode(value), dtype=dtype).reshape(-1, width)


def sample_interior(xyz, tetrahedra, pressure, points, frequency, density, check=lambda: None):
    """Barycentric P1 pressure and constant velocity in exp(-i omega t)."""
    points = np.asarray(points, dtype=float)
    values = np.zeros(len(points), complex)
    velocity = np.zeros((len(points), 3), complex)
    inside = np.zeros(len(points), bool)
    tree = cKDTree(points)
    for indices in tetrahedra:
        check()
        corners = xyz[indices]
        center = corners.mean(axis=0)
        candidates = np.asarray(tree.query_ball_point(center, np.linalg.norm(corners - center, axis=1).max() + 1e-9), dtype=int)
        if not len(candidates):
            continue
        edges = corners[1:] - corners[0]
        try:
            inverse = np.linalg.inv(edges)
        except np.linalg.LinAlgError as exc:
            raise FemFieldCorrupt("Degenerate retained FEM tetrahedron") from exc
        bary = (points[candidates] - corners[0]) @ inverse
        keep = (bary >= -1e-7).all(axis=1) & (bary.sum(axis=1) <= 1 + 1e-7) & ~inside[candidates]
        selected = candidates[keep]
        delta = pressure[indices[1:]] - pressure[indices[0]]
        values[selected] = pressure[indices[0]] + bary[keep] @ delta
        # Euler: -i omega rho v = -grad(p) for the retained solver convention.
        velocity[selected] = inverse @ delta / (1j * 2 * np.pi * frequency * density)
        inside[selected] = True
    return values, velocity, inside


def inside_bodies(mesh, points, symmetry, check=lambda: None):
    """Closed-skin winding, including correctly oriented symmetry images.

    Coupled skins also enclose FEM air. The caller gives FEM regions priority,
    so the remainder of the skin interior is solid and cannot be evaluated.
    """
    signs = [np.ones(3)]
    for axis in ((0,) if symmetry == "x" else (0, 1) if symmetry == "xy" else ()):
        reflected = []
        for sign in signs:
            image = sign.copy()
            image[axis] *= -1
            reflected.append(image)
        signs.extend(reflected)
    points = np.asarray(points, dtype=float)
    inside = np.zeros(len(points), bool)
    lo, hi = mesh.points_m.min(axis=0), mesh.points_m.max(axis=0)
    for axis in ((0,) if symmetry == "x" else (0, 1) if symmetry == "xy" else ()):
        lo[axis] = -hi[axis]
    candidates = np.flatnonzero(((points >= lo - 1e-9) & (points <= hi + 1e-9)).all(axis=1))
    for start in range(0, len(candidates), 128):
        check()
        indices = candidates[start:start + 128]
        block = points[indices]
        angle = np.zeros(len(block))
        on_surface = np.zeros(len(block), bool)
        for sign in signs:
            for offset in range(0, len(mesh.faces), 512):
                check()
                triangles = mesh.points_m[mesh.faces[offset:offset + 512]] * sign
                if np.prod(sign) < 0:
                    triangles = triangles[:, [0, 2, 1]]
                vectors = triangles[None] - block[:, None, None]
                a, b, c = vectors[:, :, 0], vectors[:, :, 1], vectors[:, :, 2]
                la, lb, lc = (np.linalg.norm(v, axis=2) for v in (a, b, c))
                numerator = np.einsum("...i,...i->...", a, np.cross(b, c))
                denominator = la * lb * lc + np.einsum("...i,...i->...", a, b) * lc + np.einsum("...i,...i->...", b, c) * la + np.einsum("...i,...i->...", c, a) * lb
                angle += (2 * np.arctan2(numerator, denominator)).sum(axis=1)
                # Points exactly on a face must not reach a singular BEM potential.
                normals = np.cross(b - a, c - a)
                area = np.linalg.norm(normals, axis=2)
                distance = np.abs(np.einsum("...i,...i->...", a, normals)) / np.maximum(area, 1e-30)
                edge_sum = (np.linalg.norm(np.cross(a, b), axis=2)
                            + np.linalg.norm(np.cross(b, c), axis=2)
                            + np.linalg.norm(np.cross(c, a), axis=2))
                on_surface |= ((distance < 1e-9) & (edge_sum <= area * (1 + 1e-7))).any(axis=1)
        inside[indices] = (np.abs(angle) > 2 * np.pi) | on_surface
    return inside


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
    valid = interior.copy() if request.scope != "exterior" else np.zeros(len(points), bool)
    if request.scope != "interior":
        retained = store.load_bem_traces(job_id, frequency, payload["channel_id"])
        if retained is None:
            raise FemFieldInvalidSelection("No retained BEM traces; re-solve to enable exterior planes")
        mesh, p, q, speed, density, symmetry, rotation = retained
        exterior = ~interior
        exterior[exterior] &= ~inside_bodies(mesh, points[exterior] @ rotation.T, symmetry, check)
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
            step = min(1e-4, speed / frequency * 1e-4)
            evaluation_points = selected
            if request.quantity == "velocity_normal":
                stencil = np.concatenate([selected + step * image_normal, selected - step * image_normal])
                blocked = inside_bodies(mesh, stencil, symmetry, check)
                safe = ~(blocked[:len(selected)] | blocked[len(selected):])
                indices = np.flatnonzero(exterior)[safe]
                exterior[:] = False
                exterior[indices] = True
                selected = selected[safe]
                evaluation_points = np.concatenate([selected + step * image_normal, selected - step * image_normal])
            result = (np.asarray(evaluator(mesh, frequency, 2 * np.pi * frequency / speed, p, q,
                                          evaluation_points, symmetry_plane={"off": None, "x": "yz", "xy": "yz+xz"}[symmetry],
                                          cancellation_callback=check)) if len(evaluation_points) else np.empty(0, complex))
            if result.shape != (len(evaluation_points),) or not np.isfinite(result).all():
                raise FemFieldCorrupt("Invalid exterior field evaluation")
            if request.quantity == "pressure":
                values[exterior] = result
            else:
                velocity[exterior] = ((result[:len(selected)] - result[len(selected):]) / (2 * step * 1j * 2 * np.pi * frequency * density))[:, None] * normal
            valid |= exterior
    if request.quantity == "velocity_normal":
        values = velocity @ np.cross(request.plane.axis_u, request.plane.axis_v)
    values[~valid] = 0
    return {"frequency_hz": frequency, "channel_id": payload["channel_id"], "scope": request.scope,
            "quantity": request.quantity, "unit": "Pa" if request.quantity == "pressure" else "m/s",
            "phasor_convention": payload["phasor_convention"], "nx": request.plane.nx, "ny": request.plane.ny,
            "points_mm": _encode(points * 1000, "<f4"), "pressure_real": _encode(values.real, "<f4"),
            "pressure_imag": _encode(values.imag, "<f4"),
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
