import unittest
import importlib.util
from unittest.mock import patch

import numpy as np


MATPLOTLIB_AVAILABLE = importlib.util.find_spec("matplotlib") is not None


def _pattern(points):
    return [[float(a), float(db)] for a, db in points]


@unittest.skipUnless(MATPLOTLIB_AVAILABLE, "matplotlib is not installed")
class DirectivityPlotTest(unittest.TestCase):
    def _frequencies(self):
        return [100.0, 1000.0]

    def _render(self, directivity):
        from solver.directivity_plot import render_directivity_plot
        return render_directivity_plot(self._frequencies(), directivity)

    def _vertical_only(self):
        return {
            "horizontal": [],
            "vertical": [
                _pattern([(0, 0), (90, -6), (180, -12)]),
                _pattern([(0, 0), (90, -8), (180, -15)]),
            ],
            "diagonal": [],
        }

    def _diagonal_only(self):
        return {
            "horizontal": [],
            "vertical": [],
            "diagonal": [
                _pattern([(0, 0), (90, -7), (180, -14)]),
                _pattern([(0, 0), (90, -9), (180, -18)]),
            ],
        }

    def _mixed(self):
        return {
            "horizontal": [
                _pattern([(0, 0), (90, -5), (180, -11)]),
                _pattern([(0, 0), (90, -7), (180, -14)]),
            ],
            "vertical": [
                _pattern([(0, 0), (90, -6), (180, -13)]),
                _pattern([(0, 0), (90, -8), (180, -16)]),
            ],
            "diagonal": [
                _pattern([(0, 0), (90, -7), (180, -15)]),
                _pattern([(0, 0), (90, -9), (180, -17)]),
            ],
        }

    def test_renders_vertical_only(self):
        image = self._render(self._vertical_only())
        self.assertIsInstance(image, str)
        self.assertGreater(len(image), 100)

    def test_renders_diagonal_only(self):
        image = self._render(self._diagonal_only())
        self.assertIsInstance(image, str)
        self.assertGreater(len(image), 100)

    def test_renders_mixed_planes(self):
        image = self._render(self._mixed())
        self.assertIsInstance(image, str)
        self.assertGreater(len(image), 100)


@unittest.skipUnless(MATPLOTLIB_AVAILABLE, "matplotlib is not installed")
class DirectivityTickGenerationTest(unittest.TestCase):
    def test_preferred_frequency_ticks_default_directivity_span(self):
        from solver.directivity_plot import _preferred_frequency_ticks

        ticks = _preferred_frequency_ticks(100.0, 10000.0)
        expected = list(np.arange(100.0, 1000.0 + 0.1, 100.0)) + list(np.arange(2000.0, 10000.0 + 0.1, 1000.0))
        self.assertEqual(ticks, expected)

    def test_preferred_frequency_ticks_clips_to_visible_range(self):
        from solver.directivity_plot import _preferred_frequency_ticks

        ticks = _preferred_frequency_ticks(350.0, 4500.0)
        self.assertEqual(ticks, [400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0, 2000.0, 3000.0, 4000.0])


@unittest.skipUnless(MATPLOTLIB_AVAILABLE, "matplotlib is not installed")
class DirectivitySmoothingTest(unittest.TestCase):
    def _smoothing_probe_data(self):
        angles = np.array([-60.0, 0.0, 60.0])
        freqs = np.geomspace(500.0, 20_000.0, 56)
        values = (
            -10.0
            + 3.0 * np.sin(np.linspace(0.0, 9.0, freqs.size))[None, :]
            - np.abs(angles[:, None]) / 20.0
        )
        return angles, freqs, values

    def test_default_matches_legacy_output_on_realistic_grid(self):
        from solver.directivity_plot import (
            ANGLE_SAMPLES,
            FRACTIONAL_OCTAVE,
            FREQ_SAMPLES,
            MAX_DB,
            MIN_DB,
            _fill_missing_values,
            _fractional_octave_smooth,
            _interpolate_heatmap_grid,
            _prepare_heatmap_data,
        )

        angles, freqs, values = self._smoothing_probe_data()
        filled = _fill_missing_values(values)
        legacy_smoothed = _fractional_octave_smooth(
            filled,
            freqs,
            FRACTIONAL_OCTAVE,
        )
        self.assertTrue(np.array_equal(legacy_smoothed, filled))
        legacy_angles, legacy_freqs, legacy_values = _interpolate_heatmap_grid(
            angles,
            freqs,
            legacy_smoothed,
            ANGLE_SAMPLES,
            FREQ_SAMPLES,
        )
        legacy = (
            legacy_angles,
            legacy_freqs,
            np.clip(legacy_values, MIN_DB, MAX_DB),
        )

        actual = _prepare_heatmap_data(angles, freqs, values)

        for actual_array, legacy_array in zip(actual, legacy):
            np.testing.assert_array_equal(actual_array, legacy_array)

    def test_smoothing_option_changes_interpolated_data(self):
        from solver.directivity_plot import _prepare_heatmap_data

        angles, freqs, values = self._smoothing_probe_data()
        default_values = _prepare_heatmap_data(angles, freqs, values)[2]
        smoothed_values = _prepare_heatmap_data(
            angles,
            freqs,
            values,
            smooth=True,
        )[2]

        self.assertFalse(np.array_equal(smoothed_values, default_values))
        self.assertGreater(np.max(np.abs(smoothed_values - default_values)), 0.0)

    def test_smoothing_fraction_is_honoured(self):
        from solver.directivity_plot import (
            FRACTIONAL_OCTAVE,
            MAX_DB,
            MIN_DB,
            _fractional_octave_smooth,
            _prepare_heatmap_data,
        )

        angles, freqs, values = self._smoothing_probe_data()
        interp_angles, interp_freqs, interp_values = _prepare_heatmap_data(
            angles,
            freqs,
            values,
        )
        default_fraction = _prepare_heatmap_data(
            angles,
            freqs,
            values,
            smooth=True,
        )
        explicit_default_fraction = _prepare_heatmap_data(
            angles,
            freqs,
            values,
            smooth=True,
            smoothing_fraction=FRACTIONAL_OCTAVE,
        )
        twelfth_octave = _prepare_heatmap_data(
            angles,
            freqs,
            values,
            smooth=True,
            smoothing_fraction=12.0,
        )
        expected_twelfth_octave = np.clip(
            _fractional_octave_smooth(interp_values, interp_freqs, 12.0),
            MIN_DB,
            MAX_DB,
        )

        np.testing.assert_array_equal(twelfth_octave[0], interp_angles)
        np.testing.assert_array_equal(twelfth_octave[1], interp_freqs)
        np.testing.assert_array_equal(twelfth_octave[2], expected_twelfth_octave)
        for implicit, explicit in zip(
            default_fraction,
            explicit_default_fraction,
        ):
            np.testing.assert_array_equal(implicit, explicit)
        self.assertFalse(np.array_equal(twelfth_octave[2], default_fraction[2]))

    def test_render_forwards_smoothing_options(self):
        from solver import directivity_plot

        directivity = {
            "horizontal": [
                _pattern([(0, 0), (90, -6), (180, -12)]),
                _pattern([(0, 0), (90, -8), (180, -15)]),
            ],
        }
        with patch.object(
            directivity_plot,
            "_prepare_heatmap_data",
            wraps=directivity_plot._prepare_heatmap_data,
        ) as prepare:
            directivity_plot.render_directivity_plot(
                [100.0, 1000.0],
                directivity,
                smooth=True,
                smoothing_fraction=12.0,
            )

        self.assertEqual(prepare.call_args.kwargs["smooth"], True)
        self.assertEqual(prepare.call_args.kwargs["smoothing_fraction"], 12.0)


if __name__ == "__main__":
    unittest.main()
