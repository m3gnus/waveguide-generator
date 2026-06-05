import importlib.util
import unittest


MATPLOTLIB_AVAILABLE = importlib.util.find_spec("matplotlib") is not None


@unittest.skipUnless(MATPLOTLIB_AVAILABLE, "matplotlib is not installed")
class ChartRenderingTest(unittest.TestCase):
    def test_frequency_response_accepts_phase_trace(self):
        from solver.charts import render_frequency_response

        image = render_frequency_response(
            [100.0, 1000.0, 10000.0],
            [90.0, 94.0, 91.0],
            [170.0, -170.0, -120.0],
        )

        self.assertIsInstance(image, str)
        self.assertGreater(len(image), 100)

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
