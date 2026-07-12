import unittest

import numpy as np

from solver.beam_shape import beam_shape_summary


def _angles(theta_max=180.0, theta_count=37, phi_count=72):
    theta = np.linspace(0.0, theta_max, theta_count)
    phi = np.arange(phi_count) * (360.0 / phi_count)
    return theta, phi


def _superellipse_radius(psi_rad, a, b, exponent):
    term = np.abs(np.cos(psi_rad) / a) ** exponent + np.abs(np.sin(psi_rad) / b) ** exponent
    return term ** (-1.0 / exponent)


def _superellipse_balloon(a, b, exponent, theta, phi):
    """SPL grid whose -6 dB contour is exactly the given tangent superellipse."""
    theta_grid, phi_grid = np.meshgrid(theta, phi, indexing="ij")
    contour = _superellipse_radius(np.deg2rad(phi_grid), a, b, exponent)
    # Keep tan() finite and monotonic by clamping the polar angle near 90 deg;
    # everything past the front map only needs to stay below the -6 dB level.
    radius = np.tan(np.deg2rad(np.clip(theta_grid, 0.0, 88.0)))
    spl = -6.0 * (radius / contour) ** 2
    spl[theta_grid > 88.0] = -60.0
    return spl[np.newaxis, :, :]


class BeamShapeSummaryTest(unittest.TestCase):
    def test_axisymmetric_parabola_fits_circle(self):
        theta, phi = _angles()
        theta_grid = np.meshgrid(theta, phi, indexing="ij")[0]
        spl = (-12.0 * (theta_grid / 30.0) ** 2)[np.newaxis, :, :]
        summary = beam_shape_summary(theta, phi, spl, [1000.0])

        self.assertTrue(summary["valid"][0])
        self.assertAlmostEqual(summary["shape_exponent"][0], 2.0, delta=0.15)
        self.assertLess(summary["fit_residual_percent"][0], 2.0)
        expected_bw = 2.0 * 30.0 / np.sqrt(2.0)
        self.assertAlmostEqual(summary["horizontal_beamwidth_deg"][0], expected_bw, delta=0.6)
        self.assertAlmostEqual(summary["vertical_beamwidth_deg"][0], expected_bw, delta=0.6)
        self.assertAlmostEqual(summary["aspect_ratio"][0], 1.0, delta=0.03)
        self.assertEqual(summary["di_domain"], "sphere")
        self.assertGreater(summary["spherical_di_db"][0], 3.0)
        self.assertLess(summary["spherical_di_db"][0], 30.0)

    def test_recovers_square_and_diamond_exponents(self):
        theta, phi = _angles()
        a = b = np.tan(np.deg2rad(25.0))
        for target in (4.0, 1.2):
            spl = _superellipse_balloon(a, b, target, theta, phi)
            summary = beam_shape_summary(theta, phi, spl, [2000.0])
            self.assertTrue(summary["valid"][0])
            self.assertAlmostEqual(summary["shape_exponent"][0], target, delta=0.35)
            self.assertLess(summary["fit_residual_percent"][0], 2.5)

    def test_aspect_ratio_and_beamwidths(self):
        theta, phi = _angles()
        a = np.tan(np.deg2rad(35.0))
        b = np.tan(np.deg2rad(20.0))
        spl = _superellipse_balloon(a, b, 2.0, theta, phi)
        summary = beam_shape_summary(theta, phi, spl, [1500.0])

        self.assertTrue(summary["valid"][0])
        self.assertAlmostEqual(summary["horizontal_beamwidth_deg"][0], 70.0, delta=1.0)
        self.assertAlmostEqual(summary["vertical_beamwidth_deg"][0], 40.0, delta=1.0)
        self.assertAlmostEqual(summary["aspect_ratio"][0], 70.0 / 40.0, delta=0.06)

    def test_no_crossing_marks_invalid_but_keeps_di(self):
        theta, phi = _angles()
        theta_grid = np.meshgrid(theta, phi, indexing="ij")[0]
        spl = (-5.0 * (theta_grid / 180.0) ** 2)[np.newaxis, :, :]
        summary = beam_shape_summary(theta, phi, spl, [200.0])

        self.assertFalse(summary["valid"][0])
        self.assertIsNone(summary["shape_exponent"][0])
        self.assertIsNone(summary["horizontal_beamwidth_deg"][0])
        self.assertIsNotNone(summary["spherical_di_db"][0])

    def test_uniform_balloon_has_zero_di(self):
        theta, phi = _angles()
        spl = np.zeros((1, theta.size, phi.size))
        summary = beam_shape_summary(theta, phi, spl, [100.0])

        self.assertFalse(summary["valid"][0])
        self.assertAlmostEqual(summary["spherical_di_db"][0], 0.0, delta=0.01)

    def test_hemisphere_domain_flag(self):
        theta, phi = _angles(theta_max=90.0, theta_count=19)
        theta_grid = np.meshgrid(theta, phi, indexing="ij")[0]
        spl = (-12.0 * (theta_grid / 30.0) ** 2)[np.newaxis, :, :]
        summary = beam_shape_summary(theta, phi, spl, [1000.0], hemisphere=True)

        self.assertEqual(summary["di_domain"], "hemisphere")
        self.assertTrue(summary["valid"][0])
        self.assertAlmostEqual(
            summary["horizontal_beamwidth_deg"][0], 2.0 * 30.0 / np.sqrt(2.0), delta=0.6
        )

    def test_nan_frequency_is_skipped_others_survive(self):
        theta, phi = _angles()
        theta_grid = np.meshgrid(theta, phi, indexing="ij")[0]
        good = -12.0 * (theta_grid / 30.0) ** 2
        bad = good.copy()
        bad[5, 7] = np.nan
        spl = np.stack([good, bad], axis=0)
        summary = beam_shape_summary(theta, phi, spl, [1000.0, 1250.0])

        self.assertTrue(summary["valid"][0])
        self.assertFalse(summary["valid"][1])
        self.assertIsNone(summary["shape_exponent"][1])
        self.assertIsNone(summary["spherical_di_db"][1])

    def test_rejects_mismatched_shapes(self):
        theta, phi = _angles()
        spl = np.zeros((1, theta.size, phi.size + 1))
        self.assertIsNone(beam_shape_summary(theta, phi, spl, [100.0]))
        self.assertIsNone(beam_shape_summary(theta[:1], phi, np.zeros((1, 1, phi.size)), [100.0]))


if __name__ == "__main__":
    unittest.main()
