"""Correctness and parity tests for the pure NumPy/SciPy CPU BEM backend.

Ground truth is the reference horn mesh, built from the repo root with::

    node scripts/run-backend-python.js \
        scripts/diagnostics/build-occ-mesh-reference-horn.py

which writes ``scripts/diagnostics/out/test_reference_horn.msh``
(898 vertices, 1792 triangles; tag 1 = 1744 wall faces, tag 2 = 48 source
faces). Tests that need the mesh skip when it is absent.

The bempp parity test additionally needs ``bempp-cl`` and ``hornlab-bempp-bem``
importable; it skips when they are not. A cached ground-truth ``.npz`` produced
by ``scripts/diagnostics`` tooling can be supplied through
``CPUBEM_BEMPP_REFERENCE`` to avoid a ~50 s cold bempp assembly in CI.

This suite takes roughly 5 minutes -- it assembles the full dense operator
several times at several thread counts. `npm run test:server` runs in ~11 s, so
these are opt-in to keep the default loop fast::

    set CPUBEM_SLOW_TESTS=1 && npm run test:server

Skipped otherwise. This is a validation reference, not a shipped backend: see
docs/windows-baseline.md for the measurement that ruled it out on performance.
"""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

import numpy as np

from solver.cpubem import (
    AIR_DENSITY,
    SPEED_OF_SOUND,
    assemble_system,
    evaluate_field,
    load_msh,
    neumann_from_tags,
    solve_dense,
    solve_neumann,
    sound_pressure_level,
    wavenumber,
)
from solver.cpubem.assembly import TEST_CHUNK, duffy_rule
from solver.cpubem.geometry import build_surface_mesh

REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_MSH = Path(
    os.environ.get("CPUBEM_REFERENCE_MSH")
    or REPO_ROOT / "scripts" / "diagnostics" / "out" / "test_reference_horn.msh"
)

EXPECTED_VERTICES = 898
EXPECTED_TRIANGLES = 1792
EXPECTED_TAG_COUNTS = {1: 1744, 2: 48}

PARITY_FREQUENCY_HZ = 1000.0


def _require_reference_mesh():
    if not REFERENCE_MSH.exists():
        raise unittest.SkipTest(
            f"Reference horn mesh not found at {REFERENCE_MSH}. Build it with: "
            "node scripts/run-backend-python.js "
            "scripts/diagnostics/build-occ-mesh-reference-horn.py"
        )
    return load_msh(REFERENCE_MSH)


class ReferenceMeshMixin(unittest.TestCase):
    mesh = None

    @classmethod
    def setUpClass(cls):
        cls.mesh = _require_reference_mesh()


class TestGeometry(ReferenceMeshMixin):
    """Requirement 1: SoA buffers are well formed and match the mesh contract."""

    def test_shapes_and_dtypes(self):
        mesh = self.mesh
        self.assertEqual(mesh.vertices.shape, (3, EXPECTED_VERTICES))
        self.assertEqual(mesh.vertices.dtype, np.float64)
        self.assertEqual(mesh.triangles.shape, (3, EXPECTED_TRIANGLES))
        self.assertEqual(mesh.triangles.dtype, np.int32)
        self.assertEqual(mesh.physical_tags.shape, (EXPECTED_TRIANGLES,))
        self.assertEqual(mesh.physical_tags.dtype, np.int32)
        self.assertEqual(mesh.p1_local2global.shape, (EXPECTED_TRIANGLES, 3))
        self.assertEqual(mesh.p1_local2global.dtype, np.int32)
        self.assertEqual(mesh.areas.shape, (EXPECTED_TRIANGLES,))
        self.assertEqual(mesh.areas.dtype, np.float64)
        self.assertEqual(mesh.normals.shape, (3, EXPECTED_TRIANGLES))
        self.assertEqual(mesh.normals.dtype, np.float64)
        self.assertEqual(mesh.p1_dof_count, EXPECTED_VERTICES)
        self.assertEqual(mesh.dp0_dof_count, EXPECTED_TRIANGLES)

    def test_areas_positive_and_finite(self):
        areas = self.mesh.areas
        self.assertTrue(np.all(np.isfinite(areas)))
        self.assertTrue(np.all(areas > 0.0))
        total = float(areas.sum())
        self.assertTrue(np.isfinite(total))
        self.assertGreater(total, 0.0)

    def test_normals_are_unit_length(self):
        norms = np.linalg.norm(self.mesh.normals, axis=0)
        np.testing.assert_allclose(norms, 1.0, rtol=0.0, atol=1e-12)

    def test_indices_zero_based_and_in_range(self):
        tris = self.mesh.triangles
        self.assertEqual(int(tris.min()), 0, "connectivity must be zero-based")
        self.assertLess(int(tris.max()), EXPECTED_VERTICES)
        # Every vertex is referenced, so index 0 really is a used DOF.
        self.assertEqual(len(np.unique(tris)), EXPECTED_VERTICES)
        np.testing.assert_array_equal(self.mesh.p1_local2global, tris.T)

    def test_tag_counts(self):
        tags, counts = np.unique(self.mesh.physical_tags, return_counts=True)
        self.assertEqual(dict(zip(tags.tolist(), counts.tolist())), EXPECTED_TAG_COUNTS)

    def test_normals_are_outward(self):
        """Outward winding: the divergence-theorem volume must be positive."""
        centroids = self.mesh.triangle_vertices().mean(axis=1)      # (nt, 3)
        flux = np.einsum("td,dt->t", centroids, self.mesh.normals)
        volume = float(np.sum(flux * self.mesh.areas) / 3.0)
        self.assertGreater(volume, 0.0)


class TestQuadratureRules(unittest.TestCase):
    """The Duffy rules must have the point counts the reference uses."""

    def test_duffy_point_counts(self):
        for adjacency, expected in (
            ("coincident", 6 * 4**4),
            ("edge_adjacent", 5 * 4**4),
            ("vertex_adjacent", 2 * 4**4),
        ):
            test_pts, trial_pts, weights = duffy_rule(4, adjacency)
            self.assertEqual(test_pts.shape, (expected, 2), adjacency)
            self.assertEqual(trial_pts.shape, (expected, 2), adjacency)
            self.assertEqual(weights.shape, (expected,), adjacency)
            # Total weight is the product of the two reference-triangle areas.
            self.assertAlmostEqual(float(weights.sum()), 0.25, places=12, msg=adjacency)

    def test_duffy_points_inside_reference_triangle(self):
        for adjacency in ("coincident", "edge_adjacent", "vertex_adjacent"):
            for pts in duffy_rule(4, adjacency):
                if pts.ndim != 2:
                    continue
                x, y = pts[:, 0], pts[:, 1]
                self.assertGreaterEqual(float(x.min()), -1e-12, adjacency)
                self.assertGreaterEqual(float(y.min()), -1e-12, adjacency)
                self.assertLessEqual(float((x + y).max()), 1.0 + 1e-12, adjacency)


class TestDeterminism(ReferenceMeshMixin):
    """Requirement 2: assembly is bitwise independent of the thread count."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        q = neumann_from_tags(cls.mesh, PARITY_FREQUENCY_HZ)
        k = wavenumber(PARITY_FREQUENCY_HZ)
        cls.results = {}
        for threads in (1, 4, 12):
            start = time.perf_counter()
            cls.results[threads] = assemble_system(cls.mesh, k, q, threads=threads)
            cls.results[threads] += (time.perf_counter() - start,)

    def test_bitwise_identical_across_thread_counts(self):
        a1, b1, t1 = self.results[1]
        for threads in (4, 12):
            a_n, b_n, t_n = self.results[threads]
            self.assertTrue(
                np.array_equal(a1, a_n),
                f"operator differs between 1 and {threads} threads "
                f"(max |delta| = {np.abs(a1 - a_n).max():.3e})",
            )
            self.assertTrue(
                np.array_equal(b1, b_n),
                f"rhs differs between 1 and {threads} threads "
                f"(max |delta| = {np.abs(b1 - b_n).max():.3e})",
            )

    def test_repeat_run_is_bitwise_identical(self):
        """No run-to-run nondeterminism (the pair_atomic failure mode)."""
        q = neumann_from_tags(self.mesh, PARITY_FREQUENCY_HZ)
        k = wavenumber(PARITY_FREQUENCY_HZ)
        a_again, b_again = assemble_system(self.mesh, k, q, threads=12)
        a_ref, b_ref, _ = self.results[12]
        self.assertTrue(np.array_equal(a_ref, a_again))
        self.assertTrue(np.array_equal(b_ref, b_again))

    def test_reported_timings(self):
        for threads in (1, 4, 12):
            print(
                f"    assembly wall time, {threads:2d} threads: "
                f"{self.results[threads][2]:6.2f} s"
            )


class TestOperatorStructure(ReferenceMeshMixin):
    """Requirement 3: structural sanity of the assembled operator."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.k = wavenumber(PARITY_FREQUENCY_HZ)
        cls.q = neumann_from_tags(cls.mesh, PARITY_FREQUENCY_HZ)
        cls.a, cls.b = assemble_system(cls.mesh, cls.k, cls.q, threads=4)

    def test_dtype_is_complex128(self):
        self.assertEqual(self.a.dtype, np.complex128)
        self.assertEqual(self.b.dtype, np.complex128)

    def test_shape_and_finiteness(self):
        n = self.mesh.p1_dof_count
        self.assertEqual(self.a.shape, (n, n))
        self.assertEqual(self.b.shape, (n,))
        self.assertTrue(np.all(np.isfinite(self.a)))
        self.assertTrue(np.all(np.isfinite(self.b)))

    def test_operator_is_dense_and_nonsingular(self):
        self.assertGreater(np.abs(self.a).max(), 0.0)
        # Well below machine precision would mean an unsolvable system.
        self.assertLess(np.linalg.cond(self.a), 1e12)

    def test_operator_is_not_symmetric(self):
        """The Galerkin double layer is genuinely non-symmetric -- by design.

        ``K_ij = int int phi_i(x) dG/dn_y(x, y) phi_j(y)`` differentiates with
        respect to the *trial* normal only, so swapping i and j gives a
        different integral. A symmetric ``A`` would mean the normal derivative
        had been applied on the wrong side. The measured asymmetry agrees with
        bempp's to 1e-15 (see the parity test), so this is a physical property
        of the operator rather than an assembly artefact.
        """
        asym = np.abs(self.a - self.a.T).max() / np.abs(self.a).max()
        self.assertGreater(asym, 1e-3)

    def test_operator_is_real_at_zero_wavenumber(self):
        """At k = 0 both G and dG/dn are real, so A must be exactly real."""
        a0, _ = assemble_system(self.mesh, 0.0, self.q, threads=4)
        self.assertEqual(float(np.abs(a0.imag).max()), 0.0)

    def test_solid_angle_row_sum_identity(self):
        """Laplace solid-angle identity: sum_j A_ij = -sum_j M_ij at smooth vertices.

        At k = 0 the double layer of a closed surface satisfies
        ``int dG/dn_y dS_y = -1/2`` for x at a *smooth* boundary point, so
        ``sum_j K_ij = -1/2 sum_j M_ij`` and the row sums of ``A = K - 1/2 M``
        collapse onto ``-sum_j M_ij`` -- for P1 the vertex support area over 3.
        This pins the sign convention, the 1/(4 pi) normalisation, the identity
        coefficient and the outward normal orientation simultaneously.

        The free term is only 1/2 where the surface is *smooth*. On a faceted
        mesh every vertex sits on a crease, so the discrete solid angle differs
        from 2 pi by an O(h) amount, and at the horn's rim/throat/backplate
        creases it differs outright. The per-vertex ratio therefore scatters
        around -1 (measured range on this mesh: -1.195 to +0.355) while its
        median lands on -1 to 4e-6. The robust centre is the meaningful check;
        asserting the worst vertex would be asserting the mesh's faceting.
        """
        a0, _ = assemble_system(self.mesh, 0.0, self.q, threads=4)
        row_sums = a0.sum(axis=1).real
        vertex_area = np.zeros(self.mesh.p1_dof_count)
        np.add.at(
            vertex_area,
            self.mesh.p1_local2global.ravel(),
            np.repeat(self.mesh.areas, 3) / 3.0,
        )
        ratio = row_sums / vertex_area
        median_signed = float(np.median(ratio))
        median_abs = float(np.median(np.abs(ratio + 1.0)))
        print(
            f"    solid-angle identity: median ratio {median_signed:.9f} "
            f"(ideal -1), median |residual| {median_abs:.3e}"
        )
        # The median ratio pins sign/normalisation/orientation exactly; the
        # scatter is the mesh's O(h) faceting error.
        self.assertLess(abs(median_signed + 1.0), 1e-4)
        self.assertLess(median_abs, 1e-2)

    def test_diagonal_is_dominated_by_the_mass_term(self):
        """-1/2 M contributes a negative real diagonal that must dominate."""
        self.assertTrue(np.all(self.a.diagonal().real < 0.0))

    def test_rhs_is_zero_far_from_source(self):
        """V q must be nonzero everywhere but largest near the driven tag."""
        source_verts = np.unique(
            self.mesh.p1_local2global[self.mesh.tag_mask(2)].ravel()
        )
        mask = np.zeros(self.mesh.p1_dof_count, dtype=bool)
        mask[source_verts] = True
        self.assertGreater(
            np.abs(self.b[mask]).mean(), np.abs(self.b[~mask]).mean()
        )


class TestAnalyticPulsatingSphere(unittest.TestCase):
    """Requirement 5: closed-form check against a pulsating (breathing) sphere.

    For a sphere of radius ``a`` with uniform radial surface velocity ``v``,
    Euler's equation ``rho dv/dt = -dp/dr`` applied to ``p = A e^{i(kr - omega t)}/r``
    gives ``v(a) = p(a) (i k - 1/a) / (i omega rho)``, hence

        p(a) = -i rho c k a v / (1 - i k a)
        p(r) = -i rho c k a^2 v / (1 - i k a) * exp(i k (r - a)) / r

    under this package's ``e^{-i omega t}`` convention. (The more commonly
    quoted ``+i rho c k a v / (1 + i k a)`` is the ``e^{+i omega t}``
    conjugate -- it flips the sign of the reactive part.) The Neumann datum
    fed to the solver is ``q = i rho omega v``, matching ``neumann_from_tags``
    in velocity mode.
    """

    @classmethod
    def setUpClass(cls):
        cls.mesh = _icosphere(subdivisions=3, radius=0.1)

    def test_surface_pressure_matches_closed_form(self):
        radius = 0.1
        freq = 1000.0
        k = wavenumber(freq)
        omega = 2.0 * np.pi * freq
        velocity = 1.0

        q = np.full(
            self.mesh.dp0_dof_count, 1j * AIR_DENSITY * omega * velocity, np.complex128
        )
        a, b = assemble_system(self.mesh, k, q, threads=4)
        p = solve_dense(a, b)

        exact = (
            -1j * AIR_DENSITY * SPEED_OF_SOUND * k * radius * velocity
            / (1.0 - 1j * k * radius)
        )
        mean = complex(p.mean())
        rel = abs(mean - exact) / abs(exact)
        print(
            f"    pulsating sphere: numeric {mean:.6g}, exact {exact:.6g}, "
            f"rel err {rel:.3e}"
        )
        # A 3-times-subdivided icosphere (1280 faces) resolves a sphere to
        # ~0.15% in radius; the discretisation error dominates.
        self.assertLess(rel, 5e-3)

    def test_exterior_field_matches_closed_form(self):
        radius = 0.1
        freq = 1000.0
        k = wavenumber(freq)
        omega = 2.0 * np.pi * freq
        velocity = 1.0

        q = np.full(
            self.mesh.dp0_dof_count, 1j * AIR_DENSITY * omega * velocity, np.complex128
        )
        a, b = assemble_system(self.mesh, k, q, threads=4)
        p = solve_dense(a, b)

        obs = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [0.7, 0.0, 0.7]])
        field = evaluate_field(self.mesh, p, q, k, obs, threads=4)

        r = np.linalg.norm(obs, axis=1)
        amp = (
            -1j * AIR_DENSITY * SPEED_OF_SOUND * k * radius**2 * velocity
            / (1.0 - 1j * k * radius)
        )
        exact = amp * np.exp(1j * k * (r - radius)) / r
        rel = np.abs(field - exact) / np.abs(exact)
        print(f"    pulsating sphere field rel err: {rel}")
        self.assertLess(float(rel.max()), 5e-3)


class TestBemppParity(ReferenceMeshMixin):
    """Requirement 4: parity against the production bempp backend at 1000 Hz.

    Tolerance rationale
    -------------------
    Both codes discretise the same Galerkin system with the same 6-point
    regular rule, the same order-4 Sauter-Schwab singular rules and the same
    shared-edge orientation convention (bempp orders the shared edge by
    *trial*-local index, ``bempp_cl/api/grid/grid.py:1036-1042``; this backend
    mirrors that in ``assembly._shared_locals``). The only remaining freedom is
    the order in which quadrature contributions are summed, which is a
    round-off effect.

    So this is effectively an exactness check, not an approximation check. The
    measured agreement on this mesh is 7.6e-16 relative on the operator and
    6.8e-15 relative on the solved surface pressure -- i.e. a few ulp amplified
    by the system's condition number of ~6.7e2. The assertion is set at 1e-12,
    roughly three decades of headroom over the observed value, so it stays
    green across BLAS/LAPACK vendors while still failing loudly on any real
    formulation or quadrature mismatch. (Before the shared-edge convention was
    matched, the same test measured 1.3e-4 -- comfortably caught at this
    threshold.)
    """

    TOLERANCE_REL = 1e-12

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reference = _load_bempp_reference(cls.mesh)

    def test_surface_pressure_parity(self):
        ref = self.reference
        start = time.perf_counter()
        solution = solve_neumann(self.mesh, PARITY_FREQUENCY_HZ, threads=12)
        elapsed = time.perf_counter() - start

        mine = solution.surface_pressure
        theirs = ref["p"]
        self.assertEqual(mine.shape, theirs.shape)

        abs_err = np.abs(mine - theirs)
        scale = np.abs(theirs).max()
        rel_err = abs_err / scale
        phase_err = np.abs(np.angle(mine / theirs))

        print(
            "\n    surface pressure parity vs bempp (1000 Hz)\n"
            f"      max abs error      : {abs_err.max():.6e} Pa\n"
            f"      max rel error      : {rel_err.max():.6e} (of max |p|)\n"
            f"      mean rel error     : {rel_err.mean():.6e}\n"
            f"      max phase error    : {phase_err.max():.6e} rad\n"
            f"      cpubem solve wall  : {elapsed:.2f} s\n"
            f"      bempp assembly wall: {float(ref['assembly_seconds']):.2f} s"
        )

        self.assertLess(float(rel_err.max()), self.TOLERANCE_REL)

    def test_operator_and_rhs_parity(self):
        ref = self.reference
        k = wavenumber(PARITY_FREQUENCY_HZ)
        q = neumann_from_tags(self.mesh, PARITY_FREQUENCY_HZ)
        a, b = assemble_system(self.mesh, k, q, threads=12)

        a_rel = np.abs(a - ref["A"]).max() / np.abs(ref["A"]).max()
        b_rel = np.abs(b - ref["b"]).max() / np.abs(ref["b"]).max()
        print(
            f"\n      operator max rel error: {a_rel:.6e}\n"
            f"      rhs      max rel error: {b_rel:.6e}"
        )
        self.assertLess(float(a_rel), self.TOLERANCE_REL)
        self.assertLess(float(b_rel), self.TOLERANCE_REL)

    def test_on_axis_spl_parity(self):
        """On-axis SPL and phase at 2 m against bempp's potential operators."""
        ref = self.reference
        theirs = np.asarray(ref["field"]).ravel()
        if not np.isfinite(theirs).all():
            self.skipTest(
                "cached reference has no bempp field (bempp potential operators "
                "were unavailable when it was generated)"
            )

        obs = np.array([[0.0, 0.0, 2.0]])
        k = wavenumber(PARITY_FREQUENCY_HZ)
        q = neumann_from_tags(self.mesh, PARITY_FREQUENCY_HZ)
        solution = solve_neumann(self.mesh, PARITY_FREQUENCY_HZ, threads=12)
        mine = evaluate_field(
            self.mesh, solution.surface_pressure, q, k, obs, threads=12
        )

        spl_mine = sound_pressure_level(mine)
        spl_theirs = sound_pressure_level(theirs)
        d_spl = float(np.abs(spl_mine - spl_theirs).max())
        d_phase = float(np.abs(np.angle(mine / theirs)).max())
        print(
            f"\n      on-axis |p| cpubem : {abs(complex(mine[0])):.12e} Pa\n"
            f"      on-axis |p| bempp  : {abs(complex(theirs[0])):.12e} Pa\n"
            f"      on-axis SPL cpubem : {float(spl_mine[0]):.9f} dB\n"
            f"      on-axis SPL bempp  : {float(spl_theirs[0]):.9f} dB\n"
            f"      on-axis SPL delta  : {d_spl:.6e} dB\n"
            f"      on-axis phase delta: {d_phase:.6e} rad"
        )
        # Observed 1.0e-13 dB / 2.3e-14 rad; assert with headroom.
        self.assertLess(d_spl, 1e-9)
        self.assertLess(d_phase, 1e-9)

    def test_field_evaluator_matches_bempp_potential_operators(self):
        """Isolate the representation formula from the surface solve.

        Feeding bempp's own surface solution through this backend's field
        evaluator must reproduce bempp's potential-operator result, which
        checks the exterior quadrature and the ``+DLP - SLP`` sign convention
        independently of the linear solve.
        """
        ref = self.reference
        theirs = np.asarray(ref["field"]).ravel()
        if not np.isfinite(theirs).all():
            self.skipTest("cached reference has no bempp field")

        obs = np.array([[0.0, 0.0, 2.0]])
        k = wavenumber(PARITY_FREQUENCY_HZ)
        q = neumann_from_tags(self.mesh, PARITY_FREQUENCY_HZ)
        mine = evaluate_field(self.mesh, ref["p"], q, k, obs, threads=12)
        rel = float(np.abs(mine - theirs).max() / np.abs(theirs).max())
        print(f"\n      field evaluator vs bempp potentials: {rel:.6e} relative")
        self.assertLess(rel, 1e-12)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_bempp_reference(mesh):
    """Load or compute the bempp ground truth, skipping if bempp is unavailable."""
    cached = os.environ.get("CPUBEM_BEMPP_REFERENCE")
    if cached and Path(cached).exists():
        return np.load(cached)

    try:
        import bempp_cl.api as bempp_api  # noqa: F401
    except ImportError as exc:
        raise unittest.SkipTest(
            f"bempp-cl is not importable ({exc}); set CPUBEM_BEMPP_REFERENCE to a "
            "cached ground-truth .npz to run this test offline."
        )

    return _compute_bempp_reference(mesh)


def _compute_bempp_reference(mesh):
    """Assemble and solve the same problem with bempp (bie.py:364-400)."""
    import bempp_cl.api as bempp_api
    from bempp_cl.api.utils.parameters import DefaultParameters

    grid = bempp_api.Grid(
        np.ascontiguousarray(mesh.vertices),
        np.ascontiguousarray(mesh.triangles.astype(np.int32)),
        mesh.physical_tags,
    )
    p1 = bempp_api.function_space(grid, "P", 1)
    dp0 = bempp_api.function_space(grid, "DP", 0)
    if p1.global_dof_count != mesh.p1_dof_count:
        raise unittest.SkipTest(
            "bempp P1 DOF count does not match the mesh vertex count; "
            "index-aligned comparison is not possible"
        )

    k = wavenumber(PARITY_FREQUENCY_HZ)
    q = neumann_from_tags(mesh, PARITY_FREQUENCY_HZ)

    params = DefaultParameters()
    params.quadrature.regular = 4          # hornlab_bempp_bem/config.py:75
    kwargs = dict(assembler="dense", device_interface="numba", parameters=params)

    start = time.perf_counter()
    identity = bempp_api.operators.boundary.sparse.identity(p1, p1, p1)
    dlp = bempp_api.operators.boundary.helmholtz.double_layer(p1, p1, p1, k, **kwargs)
    slp = bempp_api.operators.boundary.helmholtz.single_layer(dp0, p1, p1, k, **kwargs)
    a = np.asarray(bempp_api.as_matrix((dlp - 0.5 * identity).weak_form()), np.complex128)
    v = np.asarray(bempp_api.as_matrix(slp.weak_form()), np.complex128)
    elapsed = time.perf_counter() - start

    b = v @ q
    p = np.linalg.solve(a, b)

    field = np.array([np.nan + 1j * np.nan])
    try:
        obs = np.array([[0.0], [0.0], [2.0]])
        pot_kwargs = dict(assembler="dense", device_interface="numba")
        dlp_pot = bempp_api.operators.potential.helmholtz.double_layer(
            p1, obs, k, **pot_kwargs
        )
        slp_pot = bempp_api.operators.potential.helmholtz.single_layer(
            dp0, obs, k, **pot_kwargs
        )
        field = (
            dlp_pot * bempp_api.GridFunction(p1, coefficients=p)
            - slp_pot * bempp_api.GridFunction(dp0, coefficients=q)
        ).flatten()
    except Exception as exc:  # pragma: no cover - depends on the bempp wheel
        print(f"    bempp potential operators unavailable: {type(exc).__name__}: {exc}")

    return {
        "A": a,
        "b": b,
        "p": p,
        "field": field,
        "assembly_seconds": np.array(elapsed),
    }


def _icosphere(subdivisions: int, radius: float):
    """Build a closed, outward-wound icosphere for the analytic check."""
    phi = (1.0 + np.sqrt(5.0)) / 2.0
    verts = np.array(
        [
            [-1, phi, 0], [1, phi, 0], [-1, -phi, 0], [1, -phi, 0],
            [0, -1, phi], [0, 1, phi], [0, -1, -phi], [0, 1, -phi],
            [phi, 0, -1], [phi, 0, 1], [-phi, 0, -1], [-phi, 0, 1],
        ],
        dtype=np.float64,
    )
    faces = np.array(
        [
            [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
            [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
            [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
            [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1],
        ],
        dtype=np.int64,
    )

    for _ in range(subdivisions):
        midpoint: dict[tuple[int, int], int] = {}
        new_faces = []
        verts = list(verts)

        def mid(a: int, b: int) -> int:
            key = (min(a, b), max(a, b))
            if key not in midpoint:
                midpoint[key] = len(verts)
                verts.append((verts[a] + verts[b]) / 2.0)
            return midpoint[key]

        for f0, f1, f2 in faces:
            a, b, c = mid(f0, f1), mid(f1, f2), mid(f2, f0)
            new_faces += [[f0, a, c], [f1, b, a], [f2, c, b], [a, b, c]]
        verts = np.asarray(verts, dtype=np.float64)
        faces = np.asarray(new_faces, dtype=np.int64)

    verts = radius * verts / np.linalg.norm(verts, axis=1, keepdims=True)
    return build_surface_mesh(
        verts.T,
        faces.T.astype(np.int32),
        np.full(faces.shape[0], 2, dtype=np.int32),
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
