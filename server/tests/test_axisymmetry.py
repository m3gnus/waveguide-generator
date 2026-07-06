import unittest
from types import SimpleNamespace

from solver.axisymmetry import (
    circsym_axisymmetric_rejection_reasons,
    normalize_solver_mode,
    reject_bempp_circsym_request,
    validate_circsym_axisymmetric,
)


class AxisymmetryHelperTest(unittest.TestCase):
    def test_solver_mode_aliases(self):
        self.assertEqual(normalize_solver_mode("circ_sym"), "circsym")
        self.assertEqual(normalize_solver_mode("axisymmetric"), "circsym")
        self.assertEqual(normalize_solver_mode("3d"), "full_3d")
        with self.assertRaisesRegex(ValueError, "solver_mode"):
            normalize_solver_mode("planar")

    def test_rejection_reasons_cover_live_wg_fields(self):
        self.assertEqual(circsym_axisymmetric_rejection_reasons({}), [])
        self.assertIn(
            "morphTarget",
            "; ".join(circsym_axisymmetric_rejection_reasons({"morph_target": 0.25})),
        )
        self.assertIn(
            "enclosure depth",
            "; ".join(circsym_axisymmetric_rejection_reasons({"enc_depth": 20.0})),
        )

    def test_dead_cross_section_fields_are_delegated_to_mesher(self):
        reasons = circsym_axisymmetric_rejection_reasons(
            {"cross_section_exponent": 2.4, "aspect_ratio": 1.2}
        )

        self.assertEqual(reasons, [])

    def test_validate_rejects_infinite_baffle_and_non_axisymmetric_wg_fields(self):
        with self.assertRaisesRegex(ValueError, "infinite baffle"):
            validate_circsym_axisymmetric({"sim_type": 1})
        with self.assertRaisesRegex(ValueError, "CircSym requires a circular waveguide"):
            validate_circsym_axisymmetric({"sim_type": 2, "morph_target": "bad"})

    def test_bempp_circsym_request_is_rejected(self):
        reject_bempp_circsym_request(SimpleNamespace(solver_mode="full_3d"))
        with self.assertRaisesRegex(ValueError, "CircSym requires the Metal backend"):
            reject_bempp_circsym_request(SimpleNamespace(solver_mode="circsym"))


if __name__ == "__main__":
    unittest.main()
