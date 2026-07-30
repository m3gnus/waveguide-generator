"""Dense Galerkin assembly of the standard-Neumann exterior Helmholtz BIE.

Formulation (identical to both production backends)::

    (K - 1/2 M) p = V q

* ``p`` is the surface pressure in the continuous P1 (vertex) space.
* ``q`` is the Neumann datum dp/dn in the DP0 (per-element constant) space.
* ``K`` is the Galerkin double-layer operator, P1 test x P1 trial.
* ``M`` is the exact P1 x P1 mass matrix.
* ``V`` is the Galerkin single-layer operator, P1 test x DP0 trial.

Derived from:
  * ``hornlab-bempp-bem/hornlab_bempp_bem/bie.py:389-393`` -- ``lhs = dlp - 0.5 *
    identity``; ``rhs = slp * neumann_fun``, with ``dlp = double_layer(p1, p1,
    p1, k)``, ``slp = single_layer(dp0, p1, p1, k)`` and ``identity =
    sparse.identity(p1, p1, p1)``.
  * ``hornlab-metal-bem`` native helper ``main.swift:1723-1838`` -- the same
    ``A = D - 1/2 M`` / ``rhs = S q`` scatter, including the exact element mass
    matrix ``area/6`` on the diagonal and ``area/12`` off-diagonal
    (``main.swift:1818-1828``).

Green's function is ``G(x, y) = exp(+i k r) / (4 pi r)`` with ``r = |y - x|``
(the ``e^{-i omega t}`` time convention), matching
``bempp_cl/core/numba_kernels.py:314-315`` and
``hornlab-metal-bem main.swift:1510-1525``. The double-layer kernel
differentiates with respect to the **trial** normal::

    dG/dn_y = G * (i k - 1/r) * ((y - x) . n_y) / r

(``main.swift:1539-1565``).

Quadrature
----------
Regular pairs use the 6-point degree-4 symmetric triangle rule tensored 6x6 =
36 kernel evaluations per pair. This is bempp's ``quadrature.regular = 4``
(``hornlab_bempp_bem/config.py:75``, which is also bempp's own default) and the
literal table in ``main.swift:1469-1486``; the values below are the exact
doubles returned by ``bempp_cl.api.integration.triangle_gauss.rule(4)``.

Touching pairs get a **delta correction**: the regular-quadrature value is
computed for every pair (with an ``r == 0`` guard), and for each touching pair
the difference ``duffy_value - regular_value`` is added on top. This is the
pattern used by the Metal reference (``main.swift:3017-3038``). The Duffy rules
are the Sauter-Schwab transformations at order 4 -- 6/5/2 subdomains giving
1536/1280/512 points for coincident / edge-adjacent / vertex-adjacent pairs,
matching ``bempp_cl/api/integration/duffy_galerkin.py`` (bempp's
``quadrature.singular`` default of 4 is never overridden by hornlab-bempp-bem).

Determinism
-----------
The **output rows** of ``A`` are partitioned across threads, so every matrix
entry has exactly one writer -- no atomics, no order-dependent accumulation.
The Metal ``pair_atomic`` scheme is deliberately *not* ported:
``hornlab-metal-bem/docs/architecture.md:166-173`` documents that its atomic
float adds make it nondeterministic at rounding level.

Bitwise reproducibility across thread counts holds because the per-entry
accumulation order is fixed by module-level constants rather than by the
partition: test triangles are grouped into a *global* fixed chunking
(``TEST_CHUNK``), chunks and triangles within a chunk are visited in ascending
order, and a row slab merely skips the contributions it does not own. Changing
``TEST_CHUNK`` or ``TRIAL_CHUNK`` changes the summation order and therefore the
last bits; changing the thread count does not.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csr_matrix

from .geometry import SurfaceMesh

__all__ = [
    "AIR_DENSITY",
    "REFERENCE_PRESSURE",
    "SPEED_OF_SOUND",
    "TEST_CHUNK",
    "TRIAL_CHUNK",
    "assemble_system",
    "default_thread_count",
    "neumann_from_tags",
    "regular_triangle_rule",
    "wavenumber",
]

# hornlab_bempp_bem/_constants.py:3-5 and hornlab_metal_bem/_constants.py:3-5.
SPEED_OF_SOUND = 343.0
AIR_DENSITY = 1.2041
REFERENCE_PRESSURE = 20e-6

_INV_4PI = 0.25 / np.pi

# Blocking constants. These fix the floating-point summation order, so they are
# module constants rather than parameters: see the Determinism note above.
TEST_CHUNK = 16
TRIAL_CHUNK = 128

# Batch size for the singular delta pass (pairs per vectorised batch). Singular
# corrections are applied per pair, so this does not affect the result.
_SINGULAR_BATCH = 64


def default_thread_count() -> int:
    return min(12, os.cpu_count() or 1)


def wavenumber(frequency_hz: float, *, speed_of_sound: float = SPEED_OF_SOUND) -> float:
    """k = omega / c (``hornlab_bempp_bem/bie.py:550-551``)."""
    return 2.0 * np.pi * float(frequency_hz) / float(speed_of_sound)


# --------------------------------------------------------------------------
# Quadrature rules
# --------------------------------------------------------------------------

# 6-point degree-4 symmetric triangle rule; weights sum to 1/2 (the reference
# triangle's area), so the surface Jacobian is 2 * area. Exactly the doubles
# from bempp_cl.api.integration.triangle_gauss.rule(4).
_TRI_X = np.array(
    [
        0.4459484909159651,
        0.091576213509771,
        0.10810301816807,
        0.4459484909159651,
        0.816847572980459,
        0.091576213509771,
    ],
    dtype=np.float64,
)
_TRI_Y = np.array(
    [
        0.4459484909159651,
        0.09157621350977,
        0.4459484909159651,
        0.10810301816807,
        0.09157621350977,
        0.816847572980458,
    ],
    dtype=np.float64,
)
_TRI_W = np.array(
    [
        0.1116907948390055,
        0.054975871827661,
        0.1116907948390055,
        0.1116907948390055,
        0.054975871827661,
        0.054975871827661,
    ],
    dtype=np.float64,
)


def regular_triangle_rule() -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Return ``(points (nq, 2), weights (nq,))`` for the regular rule."""
    return np.stack([_TRI_X, _TRI_Y], axis=1), _TRI_W.copy()


def _p1_basis(points: NDArray[np.float64]) -> NDArray[np.float64]:
    """P1 barycentric basis ``(1 - x - y, x, y)`` at ``points (n, 2)``."""
    x = points[:, 0]
    y = points[:, 1]
    return np.stack([1.0 - x - y, x, y], axis=1)


_TRI_PTS, _TRI_WTS = regular_triangle_rule()
_TRI_BASIS = _p1_basis(_TRI_PTS)                       # (6, 3)
_TRI_W2 = _TRI_WTS[:, None] * _TRI_WTS[None, :]        # (6, 6)


def _gauss_legendre_01(order: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Gauss-Legendre rule mapped to [0, 1] (matches main.swift:2623-2632)."""
    x, w = np.polynomial.legendre.leggauss(int(order))
    return 0.5 * (x + 1.0), 0.5 * w


def duffy_rule(
    order: int, adjacency: str
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Sauter-Schwab Duffy rule for a singular Galerkin triangle pair.

    Returns ``(test_points (n, 2), trial_points (n, 2), weights (n,))`` on the
    reference triangle ``(0,0)-(1,0)-(0,1)``. Region formulas transcribed from
    ``bempp_cl/api/integration/duffy_galerkin.py:53-224``.
    """
    if adjacency not in {"coincident", "edge_adjacent", "vertex_adjacent"}:
        raise ValueError(f"Unknown adjacency {adjacency!r}")

    x1d, w1d = _gauss_legendre_01(order)
    n = x1d.size
    # Tensor points, mirroring duffy_galerkin.py:45-49.
    tx = np.tile(x1d, n)
    ty = np.repeat(x1d, n)
    tw = np.repeat(w1d, n) * np.tile(w1d, n)

    ti = np.repeat(np.arange(n * n), n * n)
    ri = np.tile(np.arange(n * n), n * n)
    xsi, eta1 = tx[ti], ty[ti]
    eta2, eta3 = tx[ri], ty[ri]
    base = tw[ti] * tw[ri]

    eta12 = eta1 * eta2
    eta123 = eta12 * eta3

    if adjacency == "coincident":
        w = base * xsi**3 * eta1**2 * eta2
        regions = [
            ((xsi, xsi * (1.0 - eta1 + eta12)),
             (xsi * (1.0 - eta123), xsi * (1.0 - eta1)), w),
            ((xsi * (1.0 - eta123), xsi * (1.0 - eta1)),
             (xsi, xsi * (1.0 - eta1 + eta12)), w),
            ((xsi, xsi * (eta1 - eta12 + eta123)),
             (xsi * (1.0 - eta12), xsi * (eta1 - eta12)), w),
            ((xsi * (1.0 - eta12), xsi * (eta1 - eta12)),
             (xsi, xsi * (eta1 - eta12 + eta123)), w),
            ((xsi * (1.0 - eta123), xsi * (eta1 - eta123)),
             (xsi, xsi * (eta1 - eta12)), w),
            ((xsi, xsi * (eta1 - eta12)),
             (xsi * (1.0 - eta123), xsi * (eta1 - eta123)), w),
        ]
    elif adjacency == "edge_adjacent":
        w = base * xsi**3 * eta1**2
        we = w * eta2
        regions = [
            ((xsi, xsi * eta1 * eta3),
             (xsi * (1.0 - eta12), xsi * eta1 * (1.0 - eta2)), w),
            ((xsi, xsi * eta1),
             (xsi * (1.0 - eta123), xsi * eta12 * (1.0 - eta3)), we),
            ((xsi * (1.0 - eta12), xsi * eta1 * (1.0 - eta2)),
             (xsi, xsi * eta123), we),
            ((xsi * (1.0 - eta123), xsi * eta12 * (1.0 - eta3)),
             (xsi, xsi * eta1), we),
            ((xsi * (1.0 - eta123), xsi * eta1 * (1.0 - eta2 * eta3)),
             (xsi, xsi * eta12), we),
        ]
    else:  # vertex_adjacent
        w = base * xsi**3 * eta2
        regions = [
            ((xsi, xsi * eta1), (xsi * eta2, xsi * eta2 * eta3), w),
            ((xsi * eta2, xsi * eta2 * eta3), (xsi, xsi * eta1), w),
        ]

    test = np.concatenate([np.stack(r[0], axis=1) for r in regions], axis=0)
    trial = np.concatenate([np.stack(r[1], axis=1) for r in regions], axis=0)
    weights = np.concatenate([r[2] for r in regions], axis=0)

    # duffy_galerkin.py:220-224 -- shift onto bempp's reference triangle.
    test[:, 0] -= test[:, 1]
    trial[:, 0] -= trial[:, 1]
    return test, trial, weights


def _remap_shared_vertex(points: NDArray[np.float64], vertex_id: int) -> NDArray[np.float64]:
    """duffy_galerkin.py:229-250 -- move the shared vertex from local 0 to ``vertex_id``."""
    if vertex_id == 0:
        return points
    out = np.zeros_like(points)
    if vertex_id == 1:
        out[:, 0] = 1.0 - points[:, 0] - points[:, 1]
        out[:, 1] = points[:, 1]
    elif vertex_id == 2:
        out[:, 0] = points[:, 0]
        out[:, 1] = 1.0 - points[:, 0] - points[:, 1]
    else:
        raise ValueError(f"vertex_id must be 0, 1 or 2; got {vertex_id}")
    return out


def _remap_shared_edge(
    points: NDArray[np.float64], v0: int, v1: int
) -> NDArray[np.float64]:
    """duffy_galerkin.py:253-285 -- move the shared edge (0, 1) onto (v0, v1)."""
    ref = np.zeros((2, 3), dtype=np.float64)
    ref[0, 1] = 1.0
    ref[1, 2] = 1.0
    new = np.zeros((2, 3), dtype=np.float64)
    new[:, 0] = ref[:, v0]
    new[:, 1] = ref[:, v1]
    new[:, 2] = ref[:, 3 - v0 - v1]
    a = np.zeros((2, 2), dtype=np.float64)
    a[:, 0] = new[:, 1] - new[:, 0]
    a[:, 1] = new[:, 2] - new[:, 0]
    return (a @ points.T + new[:, 0:1]).T


# --------------------------------------------------------------------------
# Kernels
# --------------------------------------------------------------------------


def _green(r: NDArray[np.float64], k: complex) -> NDArray[np.complex128]:
    """G = exp(i k r) / (4 pi r), guarded so r == 0 contributes nothing."""
    ok = r > 0.0
    r_safe = np.where(ok, r, 1.0)
    g = np.exp(1j * k * r_safe) * (_INV_4PI / r_safe)
    return np.where(ok, g, 0.0)


def _pair_blocks_outer(
    vt: NDArray[np.float64],
    at: NDArray[np.float64],
    vs: NDArray[np.float64],
    as_: NDArray[np.float64],
    ns: NDArray[np.float64],
    k: complex,
) -> tuple[NDArray[np.complex128], NDArray[np.complex128]]:
    """Regular 6x6 Galerkin blocks for every (test, trial) pair in the outer product.

    ``vt`` is ``(m, 3, 3)`` test-triangle vertices, ``vs`` ``(nb, 3, 3)`` trial.
    Returns ``dlp (m, nb, 3, 3)`` and ``slp (m, nb, 3)``.
    """
    # Quadrature points: (m, 6, 3) and (nb, 6, 3).
    xt = np.einsum("qi,pid->pqd", _TRI_BASIS, vt, optimize=True)
    ys = np.einsum("qi,pid->pqd", _TRI_BASIS, vs, optimize=True)

    d = ys[None, None, :, :, :] - xt[:, :, None, None, :]      # (m,6,nb,6,3)
    r = np.sqrt(np.einsum("aqbpd,aqbpd->aqbp", d, d, optimize=True))
    g = _green(r, k)
    ok = r > 0.0
    r_safe = np.where(ok, r, 1.0)
    proj = np.einsum("aqbpd,bd->aqbp", d, ns, optimize=True) / r_safe
    dgdn = g * (1j * k - 1.0 / r_safe) * proj

    jac = (2.0 * at)[:, None] * (2.0 * as_)[None, :]           # (m, nb)

    gw = g * _TRI_W2[None, :, None, :]
    dw = dgdn * _TRI_W2[None, :, None, :]

    slp = np.einsum("aqbp,qi->abi", gw, _TRI_BASIS, optimize=True)
    step = np.einsum("aqbp,qi->abpi", dw, _TRI_BASIS, optimize=True)
    dlp = np.einsum("abpi,pj->abij", step, _TRI_BASIS, optimize=True)

    return dlp * jac[:, :, None, None], slp * jac[:, :, None]


def _pair_blocks_zipped(
    vt: NDArray[np.float64],
    at: NDArray[np.float64],
    vs: NDArray[np.float64],
    as_: NDArray[np.float64],
    ns: NDArray[np.float64],
    k: complex,
    test_basis: NDArray[np.float64],
    trial_basis: NDArray[np.float64],
    test_pts_3d: NDArray[np.float64],
    trial_pts_3d: NDArray[np.float64],
    weights: NDArray[np.float64],
) -> tuple[NDArray[np.complex128], NDArray[np.complex128]]:
    """Galerkin blocks for element-wise zipped pairs on an arbitrary rule.

    ``test_pts_3d`` / ``trial_pts_3d`` are ``(np, nq, 3)`` physical quadrature
    points; ``weights`` is ``(nq,)``. Returns ``dlp (np, 3, 3)``, ``slp (np, 3)``.
    """
    d = trial_pts_3d - test_pts_3d                              # (np, nq, 3)
    r = np.sqrt(np.einsum("pqd,pqd->pq", d, d, optimize=True))
    g = _green(r, k)
    ok = r > 0.0
    r_safe = np.where(ok, r, 1.0)
    proj = np.einsum("pqd,pd->pq", d, ns, optimize=True) / r_safe
    dgdn = g * (1j * k - 1.0 / r_safe) * proj

    jac = (2.0 * at) * (2.0 * as_)                              # (np,)
    gw = g * weights[None, :]
    dw = dgdn * weights[None, :]

    slp = np.einsum("pq,qi->pi", gw, test_basis, optimize=True)
    dlp = np.einsum("pq,qi,qj->pij", dw, test_basis, trial_basis, optimize=True)
    return dlp * jac[:, None, None], slp * jac[:, None]


# --------------------------------------------------------------------------
# Touching-pair discovery
# --------------------------------------------------------------------------


def _touching_pairs(mesh: SurfaceMesh) -> dict[str, NDArray[np.int64]]:
    """Ordered (test, trial) triangle pairs that share 1 or 2 vertices.

    Coincident pairs are the diagonal and are handled separately.
    """
    l2g = mesh.p1_local2global.astype(np.int64)
    n_tri = mesh.n_triangles

    order = np.argsort(l2g.ravel(), kind="stable")
    verts_sorted = l2g.ravel()[order]
    tris_sorted = np.repeat(np.arange(n_tri, dtype=np.int64), 3)[order]
    boundaries = np.flatnonzero(np.diff(verts_sorted)) + 1
    groups = np.split(tris_sorted, boundaries)

    left: list[NDArray[np.int64]] = []
    right: list[NDArray[np.int64]] = []
    for grp in groups:
        if grp.size < 2:
            continue
        a, b = np.meshgrid(grp, grp, indexing="ij")
        mask = a != b
        left.append(a[mask])
        right.append(b[mask])

    if not left:
        return {"edge_adjacent": np.empty((0, 2), np.int64),
                "vertex_adjacent": np.empty((0, 2), np.int64)}

    a = np.concatenate(left)
    b = np.concatenate(right)
    keys, counts = np.unique(a * n_tri + b, return_counts=True)
    test = keys // n_tri
    trial = keys % n_tri

    edge = np.stack([test[counts >= 2], trial[counts >= 2]], axis=1)
    vertex = np.stack([test[counts == 1], trial[counts == 1]], axis=1)
    return {"edge_adjacent": edge, "vertex_adjacent": vertex}


def _shared_locals(
    mesh: SurfaceMesh, pairs: NDArray[np.int64], n_shared: int
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Local vertex indices of the shared vertices, aligned test<->trial."""
    l2g = mesh.p1_local2global.astype(np.int64)
    eq = l2g[pairs[:, 0]][:, :, None] == l2g[pairs[:, 1]][:, None, :]
    idx = np.argwhere(eq)                    # rows [pair, test_local, trial_local]
    if idx.shape[0] != pairs.shape[0] * n_shared:
        raise RuntimeError(
            "Inconsistent shared-vertex count while classifying singular pairs"
        )
    idx = idx.reshape(pairs.shape[0], n_shared, 3)
    if n_shared == 2:
        # bempp orders the shared-edge pair by *trial*-local index ascending
        # (grid.py:1036-1042, "Ensure that order of indices is the same as
        # Bempp 3"). Both orderings are valid Sauter-Schwab parametrisations,
        # but they are different order-4 approximations of the same integral,
        # so matching bempp's choice is what makes the two assemblies agree to
        # round-off rather than to quadrature error.
        swap = idx[:, 1, 2] < idx[:, 0, 2]
        idx[swap] = idx[swap][:, ::-1]
    return idx[:, :, 1], idx[:, :, 2]


# --------------------------------------------------------------------------
# Singular corrections
# --------------------------------------------------------------------------


def _singular_corrections(
    mesh: SurfaceMesh,
    k: complex,
    singular_order: int,
    executor: ThreadPoolExecutor | None,
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.complex128], NDArray[np.complex128]]:
    """Compute ``duffy - regular`` deltas for every touching pair.

    Returns ``(test_idx, trial_idx, ddlp (P,3,3), dslp (P,3))``.
    """
    tri_verts = mesh.triangle_vertices()
    areas = mesh.areas
    normals = np.ascontiguousarray(mesh.normals.T)
    n_tri = mesh.n_triangles

    adj = _touching_pairs(mesh)
    coincident = np.stack(
        [np.arange(n_tri, dtype=np.int64), np.arange(n_tri, dtype=np.int64)], axis=1
    )

    jobs: list[tuple[str, NDArray[np.int64], NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]] = []

    # Coincident: the rule is used unremapped.
    tp, rp, wq = duffy_rule(singular_order, "coincident")
    jobs.append(("coincident", coincident, tp, rp, wq))

    # Edge-adjacent: group by (test edge, trial edge) remap pair.
    edge = adj["edge_adjacent"]
    if edge.size:
        t_loc, s_loc = _shared_locals(mesh, edge, 2)
        tp, rp, wq = duffy_rule(singular_order, "edge_adjacent")
        key = (t_loc[:, 0] * 3 + t_loc[:, 1]) * 9 + (s_loc[:, 0] * 3 + s_loc[:, 1])
        for kv in np.unique(key):
            sel = key == kv
            t0, t1 = int(t_loc[sel][0, 0]), int(t_loc[sel][0, 1])
            s0, s1 = int(s_loc[sel][0, 0]), int(s_loc[sel][0, 1])
            jobs.append((
                "edge_adjacent",
                edge[sel],
                _remap_shared_edge(tp, t0, t1),
                _remap_shared_edge(rp, s0, s1),
                wq,
            ))

    # Vertex-adjacent: group by (test vertex, trial vertex) remap pair.
    vertex = adj["vertex_adjacent"]
    if vertex.size:
        t_loc, s_loc = _shared_locals(mesh, vertex, 1)
        tp, rp, wq = duffy_rule(singular_order, "vertex_adjacent")
        key = t_loc[:, 0] * 3 + s_loc[:, 0]
        for kv in np.unique(key):
            sel = key == kv
            tv = int(t_loc[sel][0, 0])
            sv = int(s_loc[sel][0, 0])
            jobs.append((
                "vertex_adjacent",
                vertex[sel],
                _remap_shared_vertex(tp, tv),
                _remap_shared_vertex(rp, sv),
                wq,
            ))

    total = int(sum(j[1].shape[0] for j in jobs))
    test_idx = np.empty(total, dtype=np.int64)
    trial_idx = np.empty(total, dtype=np.int64)
    ddlp = np.empty((total, 3, 3), dtype=np.complex128)
    dslp = np.empty((total, 3), dtype=np.complex128)

    # Flatten the jobs into fixed-size batches so each batch writes a disjoint
    # slice -- the pass is order-free, so threading it is safe.
    batches: list[tuple[int, NDArray[np.int64], NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]] = []
    offset = 0
    for _kind, pairs, tpts, rpts, wq in jobs:
        for start in range(0, pairs.shape[0], _SINGULAR_BATCH):
            chunk = pairs[start : start + _SINGULAR_BATCH]
            batches.append((offset, chunk, tpts, rpts, wq))
            offset += chunk.shape[0]

    def run(job) -> None:
        off, pairs, tpts, rpts, wq = job
        nb = pairs.shape[0]
        ti, si = pairs[:, 0], pairs[:, 1]
        vt, vs = tri_verts[ti], tri_verts[si]
        at, as_, ns = areas[ti], areas[si], normals[si]

        tb_t = _p1_basis(tpts)
        tb_s = _p1_basis(rpts)
        x3 = np.einsum("qi,pid->pqd", tb_t, vt, optimize=True)
        y3 = np.einsum("qi,pid->pqd", tb_s, vs, optimize=True)
        sing_d, sing_s = _pair_blocks_zipped(
            vt, at, vs, as_, ns, k, tb_t, tb_s, x3, y3, wq
        )

        xr = np.einsum("qi,pid->pqd", _TRI_BASIS, vt, optimize=True)   # (nb,6,3)
        yr = np.einsum("qi,pid->pqd", _TRI_BASIS, vs, optimize=True)
        xr_t = np.repeat(xr, 6, axis=1)                                # (nb,36,3)
        yr_t = np.tile(yr, (1, 6, 1))
        w36 = _TRI_W2.ravel()
        tb36_t = np.repeat(_TRI_BASIS, 6, axis=0)
        tb36_s = np.tile(_TRI_BASIS, (6, 1))
        reg_d, reg_s = _pair_blocks_zipped(
            vt, at, vs, as_, ns, k, tb36_t, tb36_s, xr_t, yr_t, w36
        )

        test_idx[off : off + nb] = ti
        trial_idx[off : off + nb] = si
        ddlp[off : off + nb] = sing_d - reg_d
        dslp[off : off + nb] = sing_s - reg_s

    if executor is None:
        for job in batches:
            run(job)
    else:
        list(executor.map(run, batches))

    return test_idx, trial_idx, ddlp, dslp


# --------------------------------------------------------------------------
# Boundary condition
# --------------------------------------------------------------------------


def neumann_from_tags(
    mesh: SurfaceMesh,
    frequency_hz: float,
    *,
    velocity_sources: dict[int, float] | None = None,
    velocity_mode: str = "acceleration",
    air_density: float = AIR_DENSITY,
) -> NDArray[np.complex128]:
    """DP0 Neumann coefficients ``q = i rho omega v_n``.

    Mirrors ``hornlab_bempp_bem/bie.py:168-189``. In the default acceleration
    mode ``v = a / (-i omega)`` under ``e^{-i omega t}``, so ``q = -rho * a`` --
    frequency independent. Non-source tags stay zero (rigid wall).
    """
    if velocity_sources is None:
        velocity_sources = {2: 1.0}
    mode = str(velocity_mode).lower()
    if mode not in {"velocity", "acceleration"}:
        raise ValueError("velocity_mode must be 'velocity' or 'acceleration'")

    omega = 2.0 * np.pi * float(frequency_hz)
    coeffs = np.zeros(mesh.dp0_dof_count, dtype=np.complex128)
    for tag, weight in velocity_sources.items():
        mask = mesh.tag_mask(int(tag))
        if not np.any(mask):
            continue
        v_n: complex = complex(weight)
        if mode == "acceleration":
            v_n = weight / (-1j * omega) if omega > 0 else 0.0
        coeffs[mask] = 1j * air_density * omega * v_n
    return coeffs


# --------------------------------------------------------------------------
# Dense assembly
# --------------------------------------------------------------------------


def _row_slabs(n_rows: int, n_slabs: int) -> list[tuple[int, int]]:
    bounds = np.linspace(0, n_rows, n_slabs + 1).astype(np.int64)
    return [
        (int(bounds[i]), int(bounds[i + 1]))
        for i in range(n_slabs)
        if bounds[i + 1] > bounds[i]
    ]


def assemble_system(
    mesh: SurfaceMesh,
    k: complex,
    neumann_dp0: NDArray[np.complex128],
    *,
    threads: int | None = None,
    singular_order: int = 4,
) -> tuple[NDArray[np.complex128], NDArray[np.complex128]]:
    """Assemble ``A = K - 1/2 M`` and ``b = V q`` in complex128.

    Returns ``(A (n_p1, n_p1), b (n_p1,))``. The result is bitwise independent
    of ``threads``.
    """
    n_threads = int(threads) if threads is not None else default_thread_count()
    if n_threads < 1:
        raise ValueError("threads must be >= 1")

    q = np.ascontiguousarray(neumann_dp0, dtype=np.complex128)
    if q.shape != (mesh.dp0_dof_count,):
        raise ValueError(
            f"neumann_dp0 must have shape ({mesh.dp0_dof_count},); got {q.shape}"
        )

    n_p1 = mesh.p1_dof_count
    n_tri = mesh.n_triangles
    l2g = mesh.p1_local2global.astype(np.int64)
    tri_verts = mesh.triangle_vertices()
    areas = mesh.areas
    normals = np.ascontiguousarray(mesh.normals.T)

    executor = ThreadPoolExecutor(max_workers=n_threads) if n_threads > 1 else None
    try:
        s_test, s_trial, s_ddlp, s_dslp = _singular_corrections(
            mesh, k, singular_order, executor
        )
        # Index the corrections by test triangle for fast lookup per chunk.
        sort = np.argsort(s_test, kind="stable")
        s_test, s_trial = s_test[sort], s_trial[sort]
        s_ddlp, s_dslp = s_ddlp[sort], s_dslp[sort]
        starts = np.searchsorted(s_test, np.arange(n_tri + 1))

        # Column scatter matrices: Qt[j] maps trial triangle -> P1 column j.
        col_scatter = [
            csr_matrix(
                (
                    np.ones(n_tri, dtype=np.complex128),
                    (l2g[:, j], np.arange(n_tri)),
                ),
                shape=(n_p1, n_tri),
            )
            for j in range(3)
        ]

        # Exact P1 element mass matrix: area/6 on the diagonal, area/12 off
        # (main.swift:1818-1828).
        mass_unit = (np.ones((3, 3)) + np.eye(3)) / 12.0

        a = np.zeros((n_p1, n_p1), dtype=np.complex128)
        b = np.zeros(n_p1, dtype=np.complex128)

        chunks = [
            (s, min(s + TEST_CHUNK, n_tri)) for s in range(0, n_tri, TEST_CHUNK)
        ]

        def run_slab(slab: tuple[int, int]) -> None:
            r0, r1 = slab
            for c0, c1 in chunks:
                ids = np.arange(c0, c1, dtype=np.int64)
                rows3 = l2g[ids]
                owned = (rows3 >= r0) & (rows3 < r1)
                sel = np.flatnonzero(owned.any(axis=1))
                if sel.size == 0:
                    continue
                tris = ids[sel]

                m = tris.size
                dlp = np.empty((m, n_tri, 3, 3), dtype=np.complex128)
                slp = np.empty((m, n_tri, 3), dtype=np.complex128)
                vt, at = tri_verts[tris], areas[tris]
                for t0 in range(0, n_tri, TRIAL_CHUNK):
                    t1 = min(t0 + TRIAL_CHUNK, n_tri)
                    d_blk, s_blk = _pair_blocks_outer(
                        vt, at,
                        tri_verts[t0:t1], areas[t0:t1], normals[t0:t1],
                        k,
                    )
                    dlp[:, t0:t1] = d_blk
                    slp[:, t0:t1] = s_blk

                # -1/2 * exact element mass on the coincident block.
                for local, tri in enumerate(tris):
                    dlp[local, tri] -= 0.5 * areas[tri] * mass_unit
                    lo, hi = starts[tri], starts[tri + 1]
                    if hi > lo:
                        cols = s_trial[lo:hi]
                        dlp[local, cols] += s_ddlp[lo:hi]
                        slp[local, cols] += s_dslp[lo:hi]

                for i in range(3):
                    rows = l2g[tris, i]
                    own = (rows >= r0) & (rows < r1)
                    if not own.any():
                        continue
                    ridx = rows[own]
                    acc = np.zeros((int(own.sum()), n_p1), dtype=np.complex128)
                    for j in range(3):
                        acc += col_scatter[j].dot(
                            np.ascontiguousarray(dlp[own, :, i, j].T)
                        ).T
                    np.add.at(a, ridx, acc)
                    # Reduce with an explicit elementwise product + sum rather
                    # than a matmul: BLAS picks its kernel from the operand
                    # shape, and the row count here depends on the slab, which
                    # would make the RHS thread-count dependent at ~1e-23.
                    # np.sum's pairwise blocking depends only on the reduction
                    # length, which is fixed at n_tri.
                    np.add.at(b, ridx, (slp[own, :, i] * q[None, :]).sum(axis=1))

        slabs = _row_slabs(n_p1, n_threads)
        if executor is None:
            for slab in slabs:
                run_slab(slab)
        else:
            list(executor.map(run_slab, slabs))
    finally:
        if executor is not None:
            executor.shutdown(wait=True)

    return a, b
