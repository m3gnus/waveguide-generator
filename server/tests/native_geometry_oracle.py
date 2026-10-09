"""Independent finite meridian distances for the persisted consumer mesh."""

import math

import numpy as np


def line(a, b):
    ar, az = a
    br, bz = b
    dr, dz = br - ar, bz - az

    def distance(r, z):
        t = np.clip(((r - ar) * dr + (z - az) * dz) / (dr * dr + dz * dz), 0, 1)
        return np.hypot(r - ar - t * dr, z - az - t * dz)

    return distance


def segment(model, i):
    a, b = model.points[i : i + 2]
    s = model.segments[i]
    if s.kind == "line":
        return line((a.r_mm, a.z_mm), (b.r_mm, b.z_mm))
    cr, cz = s.center_mm
    radius = math.hypot(a.r_mm - cr, a.z_mm - cz)
    start = math.atan2(a.z_mm - cz, a.r_mm - cr)
    end = math.atan2(b.z_mm - cz, b.r_mm - cr)
    angle = (end - start) % (2 * math.pi)
    sweep = angle if s.direction == "ccw" else angle - 2 * math.pi

    def distance(r, z):
        theta = np.arctan2(z - cz, r - cr)
        progress = np.mod((theta - start) * math.copysign(1, sweep), 2 * math.pi)
        progress = np.where(abs(progress - 2 * math.pi) < 1e-10, 0, progress)
        ends = np.minimum(np.hypot(r - a.r_mm, z - a.z_mm), np.hypot(r - b.r_mm, z - b.z_mm))
        return np.where(
            progress <= abs(sweep) + 1e-10, abs(np.hypot(r - cr, z - cz) - radius), ends
        )

    return distance


def certificate(xyz, distance, n=32):
    lattice = np.array([(i / n, j / n) for i in range(n + 1) for j in range(n + 1 - i)])
    peak = 0.0
    for batch in np.array_split(xyz, max(1, len(xyz) // 32 + 1)):
        samples = (
            batch[:, 0, None, :]
            + lattice[None, :, 0, None] * (batch[:, 1, None, :] - batch[:, 0, None, :])
            + lattice[None, :, 1, None] * (batch[:, 2, None, :] - batch[:, 0, None, :])
        )
        deviation = distance(np.linalg.norm(samples[:, :, :2], axis=2), samples[:, :, 2]).max(
            axis=1
        )
        diameter = np.linalg.norm(batch - np.roll(batch, 1, axis=1), axis=2).max(axis=1)
        peak = max(peak, float((deviation + diameter / n).max()))
    assert peak <= 0.15, peak
    return peak
