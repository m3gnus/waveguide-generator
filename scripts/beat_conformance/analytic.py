"""HBB's absolute breathing-sphere control in negative-time phasors."""

from __future__ import annotations

import numpy as np

SPHERE_RADIUS_M = 0.1
SPHERE_SUBDIVISIONS = 3
SPHERE_FREQUENCY_HZ = 1000.
SPHERE_OBSERVATION_M = 3.
SPHERE_LEVEL_TOLERANCE_DB = 0.05
SPHERE_PHASE_TOLERANCE_DEG = 0.6
CONTROL_MIN_PHASE_ERROR_DEG = 30.
AIR_DENSITY = 1.2041
SOUND_SPEED = 343.


def icosphere(radius: float, subdivisions: int) -> tuple[np.ndarray, np.ndarray]:
    if radius <= 0 or not np.isfinite(radius) or type(subdivisions) is not int or subdivisions < 0:
        raise ValueError("Sphere requires positive radius and non-negative subdivision count")
    phi = (1 + np.sqrt(5.)) / 2
    points = [np.array(p, dtype=float) for p in
              [(-1, phi, 0), (1, phi, 0), (-1, -phi, 0), (1, -phi, 0),
               (0, -1, phi), (0, 1, phi), (0, -1, -phi), (0, 1, -phi),
               (phi, 0, -1), (phi, 0, 1), (-phi, 0, -1), (-phi, 0, 1)]]
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
             (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
             (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
             (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    for _ in range(subdivisions):
        mids: dict[tuple[int, int], int] = {}

        def midpoint(a: int, b: int) -> int:
            key = tuple(sorted((a, b)))
            if key not in mids:
                mids[key] = len(points)
                points.append((points[a] + points[b]) / 2)
            return mids[key]

        refined = []
        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            refined.extend([(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)])
        faces = refined
    vertices = np.array([radius * p / np.linalg.norm(p) for p in points])
    triangles = np.array(faces)
    a, b, c = (vertices[triangles[:, i]] for i in range(3))
    reverse = np.sum(np.cross(b - a, c - a) * (a + b + c), axis=1) < 0
    triangles[reverse] = triangles[reverse][:, [0, 2, 1]]
    return vertices, triangles


def gmsh_surface(vertices: np.ndarray, faces: np.ndarray, tag: int = 2) -> str:
    nodes = [f"{i} {x:.17e} {y:.17e} {z:.17e}"
             for i, (x, y, z) in enumerate(vertices, 1)]
    rows = [f"{i} 2 2 {tag} {tag} {a + 1} {b + 1} {c + 1}"
            for i, (a, b, c) in enumerate(faces, 1)]
    return "\n".join(["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes", str(len(nodes)),
                      *nodes, "$EndNodes", "$Elements", str(len(rows)), *rows, "$EndElements", ""])


def sphere_reference_pressure(frequency_hz: float = SPHERE_FREQUENCY_HZ,
                              distance_m: float = SPHERE_OBSERVATION_M) -> complex:
    """Pressure per unit acceleration: rho*a²/r * exp(ik(r-a))/(1-ika)."""
    k = 2 * np.pi * frequency_hz / SOUND_SPEED
    a = SPHERE_RADIUS_M
    return complex(AIR_DENSITY * a**2 / distance_m * np.exp(1j * k * (distance_m - a)) / (1 - 1j * k * a))


def level_phase_error(measured: np.ndarray, reference: complex | np.ndarray) -> dict[str, float]:
    measured, reference = np.broadcast_arrays(np.asarray(measured, complex), np.asarray(reference, complex))
    if (not measured.size or not np.isfinite(measured).all() or not np.isfinite(reference).all()
            or np.any(np.abs(measured) == 0) or np.any(np.abs(reference) == 0)):
        raise ValueError("Analytic scoring requires finite nonzero pressure samples")
    ratio = measured / reference
    return {"level_db": float(np.max(np.abs(20 * np.log10(np.abs(ratio))))),
            "phase_deg": float(np.max(np.abs(np.degrees(np.angle(ratio)))))}


def score_sphere(measured: np.ndarray) -> dict:
    measured = np.asarray(measured)
    if measured.shape != (1, 2, 5):
        raise ValueError("Sphere pressure requires shape (1, 2, 5), ten observation samples")
    reference = sphere_reference_pressure()
    correct = level_phase_error(measured, reference)
    controls = {"conjugated": level_phase_error(np.conjugate(measured), reference),
                "sign_flipped": level_phase_error(-measured, reference)}
    isotropy = float(20 * np.log10(np.max(np.abs(measured)) / np.min(np.abs(measured))))
    failures = []
    if correct["level_db"] > SPHERE_LEVEL_TOLERANCE_DB:
        failures.append("sphere magnitude exceeds 0.05 dB")
    if correct["phase_deg"] > SPHERE_PHASE_TOLERANCE_DEG:
        failures.append("sphere phase exceeds 0.6 degrees")
    if isotropy > SPHERE_LEVEL_TOLERANCE_DB:
        failures.append("sphere cuts are not isotropic")
    if any(value["phase_deg"] < CONTROL_MIN_PHASE_ERROR_DEG for value in controls.values()):
        failures.append("phase control does not fail by the required wide margin")
    return {"passed": not failures, "failures": failures, "against_closed_form": correct,
            "controls": controls, "isotropy_db": isotropy,
            "reference_pressure": [reference.real, reference.imag]}
