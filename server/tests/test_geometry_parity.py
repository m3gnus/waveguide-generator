"""Python-side geometry parity tests.

Tests individual profile functions against hardcoded golden values,
ensuring the Python implementation doesn't regress independently.
These golden values are also tested on the JS side in
tests/geometry-parity.test.js.
"""

import math
import pytest
import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.solver.waveguide_builder import (
    _compute_osse_base_radius,
    _compute_osse_term_radius,
    _compute_osse_radius_at,
    _compute_rosse_profile,
    _compute_guiding_curve_radius,
    _invert_osse_coverage_angle,
    _get_rounded_rect_radius,
    _apply_morph,
    _evaluate_circular_arc,
)


# Tolerance for individual function outputs
TOL = 1e-9


class TestOsseBaseRadius:
    """Test OSSE oblate-spheroid base radius."""

    # Canonical params: r0=12.7, k=7, a0=15.5deg, a=45deg
    R0 = 12.7
    K = 7
    A0_RAD = math.radians(15.5)
    A_RAD = math.radians(45.0)

    def test_at_throat(self):
        r = _compute_osse_base_radius(0, self.R0, self.K, self.A0_RAD, self.A_RAD)
        # At z=0: sqrt((k*r0)^2) + r0*(1-k) = k*r0 + r0*(1-k) = r0
        assert abs(r - self.R0) < TOL

    def test_monotonic_increase(self):
        z_values = [0, 10, 30, 60, 90, 120]
        radii = [_compute_osse_base_radius(z, self.R0, self.K, self.A0_RAD, self.A_RAD) for z in z_values]
        for i in range(1, len(radii)):
            assert radii[i] > radii[i - 1], f"Not monotonic at z={z_values[i]}"

    def test_known_value_at_z60(self):
        r = _compute_osse_base_radius(60, self.R0, self.K, self.A0_RAD, self.A_RAD)
        # Pre-computed: sqrt((7*12.7)^2 + 2*7*12.7*60*tan(15.5deg) + 60^2*tan(45deg)^2) + 12.7*(1-7)
        t1 = (7 * 12.7) ** 2
        t2 = 2 * 7 * 12.7 * 60 * math.tan(math.radians(15.5))
        t3 = (60 ** 2) * (math.tan(math.radians(45)) ** 2)
        expected = math.sqrt(t1 + t2 + t3) + 12.7 * (1 - 7)
        assert abs(r - expected) < TOL


class TestOsseTermRadius:
    """Test OSSE super-exponential termination radius."""

    L = 120.0
    S = 0.6
    N = 4.158
    Q = 0.991

    def test_at_throat(self):
        r = _compute_osse_term_radius(0, self.L, self.S, self.N, self.Q)
        assert r == 0.0

    def test_at_mouth(self):
        # At z=L, z_norm = q*L/L = q ≈ 0.991 < 1
        r = _compute_osse_term_radius(self.L, self.L, self.S, self.N, self.Q)
        z_norm = self.Q * self.L / self.L
        expected = (self.S * self.L / self.Q) * (1 - (1 - z_norm ** self.N) ** (1 / self.N))
        assert abs(r - expected) < TOL

    def test_beyond_saturation(self):
        # z_norm > 1 -> returns s*L/q
        z_big = self.L * 2 / self.Q  # z_norm = q*z/L = 2 > 1
        r = _compute_osse_term_radius(z_big, self.L, self.S, self.N, self.Q)
        expected = self.S * self.L / self.Q
        assert abs(r - expected) < TOL


class TestRosseProfile:
    """Test R-OSSE profile computation."""

    def test_basic_profile(self):
        t = np.array([0, 0.25, 0.5, 0.75, 1.0])
        x, y, L = _compute_rosse_profile(
            t, R=140, a_deg=45, r0=12.7, a0_deg=15.5,
            k=2, r_param=0.4, b_param=0.2, m_param=0.85, q_param=3.4,
        )
        assert len(x) == len(t)
        assert len(y) == len(t)
        assert L > 0
        # At t=0: x should be near 0 (not exactly 0 due to m-term)
        assert y[0] == pytest.approx(12.7, abs=1e-6)  # throat radius
        # At t=1: y should approach R
        assert y[-1] == pytest.approx(140, rel=0.01)

    def test_near_zero_angle(self):
        """Test the edge case where a≈0 (near-conical) — c3≈0, c2≠0."""
        t = np.array([0, 0.5, 1.0])
        x, y, L = _compute_rosse_profile(
            t, R=140, a_deg=0.001, r0=12.7, a0_deg=15.5,
            k=2, r_param=0.4, b_param=0.2, m_param=0.85, q_param=3.4,
        )
        assert math.isfinite(L), f"L should be finite, got {L}"
        assert L > 0, f"L should be positive, got {L}"
        for i in range(len(t)):
            assert math.isfinite(float(x[i])), f"x not finite at t={t[i]}"
            assert math.isfinite(float(y[i])), f"y not finite at t={t[i]}"


class TestGuidingCurve:
    """Test guiding curve radius computation."""

    def test_superellipse(self):
        params = {
            "gcurve_type": 1,
            "gcurve_width": 300,
            "gcurve_aspect_ratio": 0.8,
            "gcurve_se_n": 3,
            "gcurve_rot": 0,
        }
        # At phi=0: should return width/2 (along major axis)
        r0 = _compute_guiding_curve_radius(0, params)
        assert r0 == pytest.approx(150.0, abs=TOL)

        # At phi=pi/2: should return width/2 * aspect
        r90 = _compute_guiding_curve_radius(math.pi / 2, params)
        assert r90 == pytest.approx(120.0, abs=TOL)

    def test_disabled(self):
        params = {"gcurve_type": 0}
        assert _compute_guiding_curve_radius(0, params) is None


class TestRoundedRectRadius:
    """Test rounded rectangle radius computation."""

    def test_axis_aligned(self):
        halfW, halfH, cornerR = 100, 60, 10
        assert abs(_get_rounded_rect_radius(0, halfW, halfH, cornerR) - 100) < TOL
        assert abs(_get_rounded_rect_radius(math.pi / 2, halfW, halfH, cornerR) - 60) < TOL

    def test_symmetric(self):
        halfW, halfH, cornerR = 100, 60, 10
        r1 = _get_rounded_rect_radius(math.pi / 4, halfW, halfH, cornerR)
        r2 = _get_rounded_rect_radius(-math.pi / 4, halfW, halfH, cornerR)
        assert abs(r1 - r2) < TOL

    def test_no_corner(self):
        halfW, halfH = 100, 60
        r = _get_rounded_rect_radius(math.pi / 4, halfW, halfH, 0)
        # Pure rectangle: min(halfW/cos, halfH/sin) at pi/4
        cos45 = math.cos(math.pi / 4)
        sin45 = math.sin(math.pi / 4)
        expected = min(halfW / cos45, halfH / sin45)
        assert abs(r - expected) < TOL


class TestCoverageInversion:
    """Test coverage angle binary search inversion."""

    def test_roundtrip(self):
        """Compute radius at known angle, then invert to recover the angle."""
        a_deg = 45.0
        z_main = 60.0
        r0_main = 12.7
        a0_deg = 15.5
        k, s, n, q, L = 7, 0.6, 4.158, 0.991, 120.0

        target_r = _compute_osse_radius_at(z_main, a_deg, a0_deg, r0_main, k, s, n, q, L)
        recovered = _invert_osse_coverage_angle(target_r, z_main, r0_main, a0_deg, k, s, n, q, L)
        assert abs(recovered - a_deg) < 1e-5, f"Expected {a_deg}, got {recovered}"


class TestMorph:
    """Test morph application."""

    def test_no_morph(self):
        params = {"morph_target": 0}
        r = _apply_morph(50.0, 0.5, 0.0, params)
        assert r == 50.0

    def test_before_morph_start(self):
        params = {"morph_target": 2, "morph_fixed": 0.5, "morph_rate": 3,
                  "morph_width": 100, "morph_height": 80}
        r = _apply_morph(50.0, 0.3, 0.0, params)
        assert r == 50.0  # t < morph_fixed

    def test_full_morph_circle(self):
        params = {"morph_target": 2, "morph_fixed": 0.0, "morph_rate": 1,
                  "morph_width": 100, "morph_height": 80, "morph_corner": 0,
                  "morph_allow_shrinkage": 1}
        # At t=1 with rate=1, morph_factor=1 -> fully morphed to circle target
        target_r = math.sqrt(50 * 40)  # sqrt(halfW * halfH)
        r = _apply_morph(30.0, 1.0, 0.0, params)
        assert abs(r - target_r) < TOL
