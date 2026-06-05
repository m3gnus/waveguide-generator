import importlib.util
import unittest

import numpy as np


MATPLOTLIB_AVAILABLE = importlib.util.find_spec("matplotlib") is not None


@unittest.skipUnless(MATPLOTLIB_AVAILABLE, "matplotlib is not installed")
class ChartRenderingTest(unittest.TestCase):
    def test_response_phase_compensates_observer_propagation(self):
        from solver.charts import _response_phase_degrees

        freqs = np.array([100.0, 1000.0, 10000.0])
        distance_m = 2.0
        raw_phase = np.rad2deg(-2.0 * np.pi * freqs * distance_m / 343.0)
        wrapped_phase = (raw_phase + 180.0) % 360.0 - 180.0

        phase = _response_phase_degrees(
            freqs,
            wrapped_phase,
            reference_distance_m=distance_m,
            sound_speed=343.0,
        )

        np.testing.assert_allclose(phase, np.zeros_like(freqs), atol=1e-9)

    def test_frequency_response_accepts_phase_trace(self):
        from solver.charts import render_frequency_response

        image = render_frequency_response(
            [100.0, 1000.0, 10000.0],
            [90.0, 94.0, 91.0],
            [170.0, -170.0, -120.0],
        )

        self.assertIsInstance(image, str)
        self.assertGreater(len(image), 100)

    def test_impedance_axis_margin_is_scaled_for_normalized_values(self):
        import matplotlib.pyplot as plt
        from solver.charts import render_impedance

        captured_ylim = None
        original_close = plt.close

        def capture_close(fig):
            nonlocal captured_ylim
            captured_ylim = fig.axes[0].get_ylim()
            original_close(fig)

        try:
            plt.close = capture_close
            image = render_impedance(
                [100.0, 200.0, 400.0],
                [0.95, 1.0, 1.05],
                [0.05, 0.0, -0.05],
            )
        finally:
            plt.close = original_close

        self.assertIsInstance(image, str)
        self.assertIsNotNone(captured_ylim)
        self.assertLess(captured_ylim[1] - captured_ylim[0], 2.0)

    def test_impedance_chart_normalizes_legacy_absolute_values(self):
        from solver.charts import _normalize_impedance_for_plot

        real, imag = _normalize_impedance_for_plot(
            np.array([415.03, 830.06]),
            np.array([0.0, 207.515]),
            rho_c=415.03,
        )

        np.testing.assert_allclose(real, [1.0, 2.0])
        np.testing.assert_allclose(imag, [0.0, 0.5])

    def test_impedance_skips_sparse_samples(self):
        from solver.charts import render_impedance

        image = render_impedance(
            [100.0, 200.0, 400.0],
            [None, 410.0, 420.0],
            [25.0, None, -15.0],
        )

        self.assertIsInstance(image, str)
        self.assertGreater(len(image), 100)

    def test_directivity_index_accepts_nested_per_plane_di(self):
        from solver.charts import render_directivity_index

        image = render_directivity_index(
            [100.0, 200.0, 400.0],
            {"di": {"horizontal": [6.0, None, 8.0], "vertical": [5.5, 6.0, 7.0]}},
        )

        self.assertIsInstance(image, str)
        self.assertGreater(len(image), 100)

    def test_render_all_charts_keeps_available_sparse_charts(self):
        from solver.charts import render_all_charts

        charts = render_all_charts(
            {
                "frequencies": [100.0, 200.0, 400.0],
                "spl": [90.0, None, 92.0],
                "phase_degrees": [0.0, 45.0, 90.0],
                "di": {"di": {"horizontal": [6.0, None, 8.0]}},
                "di_frequencies": [100.0, 200.0, 400.0],
                "impedance_frequencies": [100.0, 200.0, 400.0],
                "impedance_real": [None, 410.0, 420.0],
                "impedance_imaginary": [25.0, None, -15.0],
            }
        )

        self.assertIsInstance(charts["frequency_response"], str)
        self.assertIsInstance(charts["directivity_index"], str)
        self.assertIsInstance(charts["impedance"], str)


if __name__ == "__main__":
    unittest.main()
