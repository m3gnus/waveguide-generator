"""Dense complex LU solve and exterior field evaluation.

The surface system is solved with ``scipy.linalg.lu_factor`` /
``lu_solve`` in complex128 (the production bempp path uses
``bempp_api.linalg.lu`` for meshes below its GMRES threshold,
``hornlab_bempp_bem/bie.py:399-400``).

Exterior pressure uses the direct Kirchhoff-Helmholtz representation

    p(x) = integral_Gamma [ dG/dn_y (x, y) p(y) - G(x, y) q(y) ] dGamma(y)

matching ``hornlab_bempp_bem/bie.py:429-444`` (``p = DLP_pot[p_s] - SLP_pot[q]``)
and ``hornlab-metal-bem main.swift:1863-1872``. As in both references the field
is evaluated at the **real** wavenumber even when the surface solve used a
complex-shifted k (``bie.py:441``, ``docs/architecture.md:75-78``).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.linalg import lu_factor, lu_solve

from .assembly import (
    AIR_DENSITY,
    REFERENCE_PRESSURE,
    SPEED_OF_SOUND,
    _INV_4PI,
    _TRI_BASIS,
    _TRI_WTS,
    assemble_system,
    default_thread_count,
    neumann_from_tags,
    wavenumber,
)
from .geometry import SurfaceMesh

__all__ = [
    "SurfaceSolution",
    "evaluate_field",
    "solve_dense",
    "solve_neumann",
    "sound_pressure_level",
]


@dataclass(frozen=True)
class SurfaceSolution:
    """Result of a single-frequency standard-Neumann solve."""

    frequency_hz: float
    wavenumber: float
    surface_pressure: NDArray[np.complex128]   # (n_p1,) P1 vertex pressures
    neumann_dp0: NDArray[np.complex128]        # (n_tri,) DP0 Neumann data
    operator: NDArray[np.complex128]           # (n_p1, n_p1) assembled A
    rhs: NDArray[np.complex128]                # (n_p1,)


def solve_dense(
    a: NDArray[np.complex128], b: NDArray[np.complex128]
) -> NDArray[np.complex128]:
    """Dense complex LU solve of ``A x = b``."""
    lu, piv = lu_factor(np.asarray(a, dtype=np.complex128), overwrite_a=False)
    return lu_solve((lu, piv), np.asarray(b, dtype=np.complex128))


def solve_neumann(
    mesh: SurfaceMesh,
    frequency_hz: float,
    *,
    threads: int | None = None,
    velocity_sources: dict[int, float] | None = None,
    velocity_mode: str = "acceleration",
    air_density: float = AIR_DENSITY,
    speed_of_sound: float = SPEED_OF_SOUND,
    complex_k_shift: float = 0.0,
    singular_order: int = 4,
) -> SurfaceSolution:
    """Assemble and solve the standard-Neumann exterior problem at one frequency.

    ``complex_k_shift`` reproduces the COMPLEX_K formulation
    (``k * (1 + 1j * shift)``, ``hornlab_bempp_bem/bie.py:554-555``); leave it at
    0.0 for the plain STANDARD formulation.
    """
    k_real = wavenumber(frequency_hz, speed_of_sound=speed_of_sound)
    k: complex = k_real * (1.0 + 1j * complex_k_shift) if complex_k_shift else k_real

    q = neumann_from_tags(
        mesh,
        frequency_hz,
        velocity_sources=velocity_sources,
        velocity_mode=velocity_mode,
        air_density=air_density,
    )
    a, b = assemble_system(
        mesh, k, q, threads=threads, singular_order=singular_order
    )
    p = solve_dense(a, b)
    return SurfaceSolution(
        frequency_hz=float(frequency_hz),
        wavenumber=k_real,
        surface_pressure=p,
        neumann_dp0=q,
        operator=a,
        rhs=b,
    )


def evaluate_field(
    mesh: SurfaceMesh,
    surface_pressure: NDArray[np.complex128],
    neumann_dp0: NDArray[np.complex128],
    k_real: float,
    points: NDArray[np.float64],
    *,
    threads: int | None = None,
    obs_chunk: int = 64,
) -> NDArray[np.complex128]:
    """Exterior pressure at observation ``points``.

    ``points`` is ``(3, n_obs)`` (the native-ipc convention) or ``(n_obs, 3)``;
    both are accepted. Returns ``(n_obs,)`` complex128.
    """
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2:
        raise ValueError("points must be 2-D")
    if pts.shape[0] == 3 and pts.shape[1] != 3:
        obs = np.ascontiguousarray(pts.T)
    elif pts.shape[1] == 3:
        obs = np.ascontiguousarray(pts)
    else:
        raise ValueError(f"points must be (3, n) or (n, 3); got {pts.shape}")

    p_surf = np.asarray(surface_pressure, dtype=np.complex128)
    q = np.asarray(neumann_dp0, dtype=np.complex128)
    l2g = mesh.p1_local2global.astype(np.int64)
    tri_verts = mesh.triangle_vertices()
    areas = mesh.areas
    normals = np.ascontiguousarray(mesh.normals.T)

    # P1 pressure sampled at the 6 quadrature points of each triangle.
    p_local = p_surf[l2g]                                      # (nt, 3)
    p_quad = p_local @ _TRI_BASIS.T                            # (nt, 6)
    y = np.einsum("qi,tid->tqd", _TRI_BASIS, tri_verts, optimize=True)  # (nt,6,3)
    jac_w = (2.0 * areas)[:, None] * _TRI_WTS[None, :]         # (nt, 6)

    n_obs = obs.shape[0]
    out = np.zeros(n_obs, dtype=np.complex128)

    def run(bounds: tuple[int, int]) -> None:
        o0, o1 = bounds
        d = y[None, :, :, :] - obs[o0:o1, None, None, :]       # (nb,nt,6,3)
        r = np.sqrt(np.einsum("otqd,otqd->otq", d, d, optimize=True))
        g = np.exp(1j * k_real * r) * (_INV_4PI / r)
        proj = np.einsum("otqd,td->otq", d, normals, optimize=True) / r
        dgdn = g * (1j * k_real - 1.0 / r) * proj
        integrand = dgdn * p_quad[None, :, :] - g * q[None, :, None]
        out[o0:o1] = np.einsum(
            "otq,tq->o", integrand, jac_w, optimize=True
        )

    bounds = [
        (s, min(s + obs_chunk, n_obs)) for s in range(0, n_obs, obs_chunk)
    ]
    n_threads = int(threads) if threads is not None else default_thread_count()
    if n_threads > 1 and len(bounds) > 1:
        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            list(pool.map(run, bounds))
    else:
        for bound in bounds:
            run(bound)
    return out


def sound_pressure_level(
    pressure: NDArray[np.complex128],
    *,
    reference: float = REFERENCE_PRESSURE,
    floor_db: float = -120.0,
) -> NDArray[np.float64]:
    """``20 log10(|p| / p_ref)``, peak amplitude, no RMS factor.

    Matches ``hornlab_bempp_bem/sweep.py:50-61`` (which then normalises the
    on-axis value to 0 dB; that normalisation is left to the caller here).
    """
    amp = np.abs(np.asarray(pressure))
    spl = np.full(amp.shape, float(floor_db), dtype=np.float64)
    audible = amp > 1e-15
    spl[audible] = 20.0 * np.log10(amp[audible] / reference)
    return spl
