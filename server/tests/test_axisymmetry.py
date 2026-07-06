import unittest
from types import SimpleNamespace
from unittest import mock

from solver.axisymmetry import (
    circsym_axisymmetric_rejection_reasons,
    normalize_solver_mode,
    reject_bempp_circsym_request,
    resolve_effective_solver_mode,
    solver_mode_from_request,
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

    def test_validate_allows_infinite_baffle_but_rejects_non_axisymmetric_wg_fields(self):
        self.assertIsNone(validate_circsym_axisymmetric({"sim_type": 1}))
        with self.assertRaisesRegex(ValueError, "CircSym requires a circular waveguide"):
            validate_circsym_axisymmetric({"sim_type": 2, "morph_target": "bad"})

    def test_bempp_circsym_request_is_rejected(self):
        reject_bempp_circsym_request(SimpleNamespace(solver_mode="full_3d"))
        with self.assertRaisesRegex(ValueError, "CircSym requires the Metal backend"):
            reject_bempp_circsym_request(SimpleNamespace(solver_mode="circsym"))

    def test_auto_is_the_default_solver_mode(self):
        self.assertEqual(normalize_solver_mode("auto"), "auto")
        self.assertEqual(normalize_solver_mode(""), "auto")
        self.assertEqual(normalize_solver_mode(None), "auto")
        self.assertEqual(solver_mode_from_request(SimpleNamespace()), "auto")


class ResolveEffectiveSolverModeTest(unittest.TestCase):
    def test_explicit_modes_pass_through_without_probing(self):
        with mock.patch(
            "solver.axisymmetry._circsym_rejection_reasons_for_payload"
        ) as probe:
            self.assertEqual(
                resolve_effective_solver_mode("full_3d", {}, solver_backend="metal"),
                ("full_3d", None),
            )
            self.assertEqual(
                resolve_effective_solver_mode("circsym", {}, solver_backend="metal"),
                ("circsym", None),
            )
            probe.assert_not_called()

    def test_auto_falls_back_to_full_3d_on_non_metal_backend(self):
        with mock.patch(
            "solver.axisymmetry._circsym_rejection_reasons_for_payload"
        ) as probe:
            mode, reason = resolve_effective_solver_mode(
                "auto", {}, solver_backend="bempp"
            )
            self.assertEqual(mode, "full_3d")
            self.assertIn("Metal", reason)
            probe.assert_not_called()

    def test_auto_selects_circsym_for_eligible_circular_geometry(self):
        with mock.patch(
            "solver.axisymmetry._circsym_rejection_reasons_for_payload",
            return_value=[],
        ):
            self.assertEqual(
                resolve_effective_solver_mode("auto", {}, solver_backend="metal"),
                ("circsym", None),
            )

    def test_auto_falls_back_to_full_3d_when_geometry_ineligible(self):
        with mock.patch(
            "solver.axisymmetry._circsym_rejection_reasons_for_payload",
            return_value=["CircSym requires a circular waveguide: morphTarget is 0.25, not 0"],
        ):
            mode, reason = resolve_effective_solver_mode(
                "auto", {"morph_target": 0.25}, solver_backend="metal"
            )
            self.assertEqual(mode, "full_3d")
            self.assertIn("morphTarget", reason)


if __name__ == "__main__":
    unittest.main()
