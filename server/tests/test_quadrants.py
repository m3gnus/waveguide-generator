"""The mesh that gets built and the symmetry the solver is told must agree.

They used not to: the mesher adapter followed Ath's rule (anything
unrecognised is a quarter model) while the solver adapters fell through to a
full-domain ``None`` for the same input, so a quarter shell was solved as an
open sheet with no error anywhere.
"""
from __future__ import annotations

import types
import unittest

from solver.mesher_adapter import _normalise_quadrants
from solver.quadrants import (
    native_symmetry_plane_for_quadrants,
    normalise_quadrants,
    quadrants_leading_int,
)
from solver.result_mapping import native_symmetry_plane, waveguide_quadrants

# Every shape a Mesh.Quadrants value has been seen to take, including the ones
# that used to diverge between the two readers.
_VALUES = [
    1234, 12, 14, 1, "1234", "12", "14", "1",
    0, "", None, 2, 3, 13, 21, 34, 123, "1,2", "x14", "1234x", " 12 ",
    "+14", -1, 12.0, 1234.7, True, False, [], {},
]


def _request(quadrants, *, present=True):
    params = {"quadrants": quadrants} if present else {}
    return types.SimpleNamespace(
        options={"mesh": {"waveguide_params": params}}
    )


class QuadrantsTest(unittest.TestCase):
    def test_mesher_and_solver_agree_on_every_value(self):
        for value in _VALUES:
            with self.subTest(quadrants=value):
                self.assertEqual(
                    _normalise_quadrants(value),
                    waveguide_quadrants(_request(value)),
                )

    def test_unrecognised_values_are_a_quarter_model_not_full_domain(self):
        for value in (0, "", None, 2, 3, 13, 21, 34, 123, "1,2", "x14"):
            with self.subTest(quadrants=value):
                self.assertEqual(normalise_quadrants(value), 1)
                self.assertEqual(
                    native_symmetry_plane(_request(value)), "yz+xz",
                )

    def test_canonical_values_map_to_their_planes(self):
        self.assertIsNone(native_symmetry_plane(_request(1234)))
        self.assertEqual(native_symmetry_plane(_request(12)), "xz")
        self.assertEqual(native_symmetry_plane(_request(14)), "yz")
        self.assertEqual(native_symmetry_plane(_request(1)), "yz+xz")

    def test_absent_quadrants_key_is_a_full_model(self):
        request = _request(None, present=False)

        self.assertEqual(waveguide_quadrants(request), 1234)
        self.assertIsNone(native_symmetry_plane(request))

    def test_leading_int_follows_ath_atoi_parsing(self):
        self.assertEqual(quadrants_leading_int("1234x"), 1234)
        self.assertEqual(quadrants_leading_int("1,2"), 1)
        self.assertEqual(quadrants_leading_int("x1234"), 0)
        self.assertEqual(quadrants_leading_int(""), 0)
        self.assertEqual(quadrants_leading_int(None), 0)
        # Digits are never reordered: "21" is 21, not the set {1, 2}.
        self.assertEqual(quadrants_leading_int("21"), 21)
        self.assertEqual(normalise_quadrants("21"), 1)

    def test_booleans_are_not_treated_as_integers(self):
        self.assertEqual(quadrants_leading_int(True), 0)
        self.assertEqual(quadrants_leading_int(False), 0)

    def test_agrees_with_the_mesher_package_when_it_is_installed(self):
        """hornlab_mesher is the authority; this is the anti-drift pin."""
        try:
            from hornlab_mesher.profile_common import (
                _normalise_quadrants as mesher_normalise,
            )
        except ImportError:
            self.skipTest("hornlab-waveguide-mesher is not installed")

        for value in _VALUES:
            with self.subTest(quadrants=value):
                self.assertEqual(
                    str(normalise_quadrants(value)), mesher_normalise(value),
                )

    def test_plane_lookup_never_raises_on_a_normalised_value(self):
        for value in _VALUES:
            with self.subTest(quadrants=value):
                plane = native_symmetry_plane_for_quadrants(value)
                self.assertIn(plane, {None, "yz", "xz", "yz+xz"})


if __name__ == "__main__":
    unittest.main()
