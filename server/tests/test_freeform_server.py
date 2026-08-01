import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.routes_mesh import (
    build_mesh_from_params,
    build_step_from_params,
    build_viewport_geometry_from_params,
)
from api.routes_simulation import submit_simulation
from contracts import SimulationRequest, WaveguideParamsRequest
from hornlab_mesher.config_builder import build_geometry_params
from services.simulation_validation import validate_submit_simulation_request
from solver.axisymmetry import validate_circsym_axisymmetric
from solver.mesher_adapter import (
    build_viewport_geometry,
    waveguide_payload_to_mesher_config,
)


def _freeform_payload() -> dict:
    return {
        "formula_type": "FREEFORM",
        "a0": 15.5,
        "profile_h": {
            "points": [[0.0, 12.7], [60.0, 80.0, 25.0, 1.4], [120.0, 160.0]],
            "throat_angle_deg": 15.5,
            "mouth_angle_deg": 70.0,
            "throat_tangent_scale": 1.1,
            "mouth_tangent_scale": 0.9,
        },
        "profile_v": {
            "points": [[0.0, 12.7], [60.0, 60.0, -10.0], [120.0, 110.0]],
            "throat_angle_deg": 15.5,
            "mouth_angle_deg": 60.0,
            "throat_tangent_scale": 1.2,
            "mouth_tangent_scale": 0.8,
        },
        "cross_sections": [
            {"t": 0.0, "shape": "circle"},
            {
                "t": 0.4,
                "shape": "rounded_rectangle",
                "corner_ratio": 0.12,
            },
            {
                "t": 1.0,
                "shape": "rounded_rectangle",
                "corner_radius_mm": 10.0,
            },
        ],
        "overshoot_policy": "allow",
        "inflection_policy": "allow",
        "n_angular": 16,
        "n_length": 8,
        "wall_thickness": 0.0,
    }


def _solve_request(formula_type: str, waveguide_params: dict | None = None) -> SimulationRequest:
    params = dict(waveguide_params or {})
    params["formula_type"] = formula_type
    return SimulationRequest(
        frequency_range=[100.0, 1000.0],
        num_frequencies=2,
        sim_type="2",
        solver_backend="metal",
        options={
            "mesh": {
                "strategy": "hornlab_mesher",
                "waveguide_params": params,
            }
        },
    )


class FreeformContractTest(unittest.TestCase):
    def test_all_freeform_fields_survive_model_dump(self):
        payload = _freeform_payload()
        dumped = WaveguideParamsRequest(**payload).model_dump()

        for key in (
            "profile_h",
            "profile_v",
            "cross_sections",
            "overshoot_policy",
            "inflection_policy",
        ):
            self.assertIn(key, dumped)
        self.assertEqual(dumped["profile_h"], payload["profile_h"])
        self.assertEqual(dumped["profile_v"], payload["profile_v"])
        for dumped_station, payload_station in zip(
            dumped["cross_sections"], payload["cross_sections"], strict=True
        ):
            for key, value in payload_station.items():
                self.assertEqual(dumped_station[key], value)
        self.assertEqual(dumped["overshoot_policy"], payload["overshoot_policy"])
        self.assertEqual(dumped["inflection_policy"], payload["inflection_policy"])

    def test_inflection_policy_is_normalized_and_validated(self):
        payload = _freeform_payload()
        payload["inflection_policy"] = " WARN "
        self.assertEqual(
            WaveguideParamsRequest(**payload).model_dump()["inflection_policy"],
            "warn",
        )

        for invalid in ("ignore", "", "enforce"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                ValueError, "inflection_policy"
            ):
                WaveguideParamsRequest(
                    **{**_freeform_payload(), "inflection_policy": invalid}
                )

    def test_light_collection_bounds_are_enforced(self):
        payload = _freeform_payload()
        payload["profile_h"] = {"points": [[0.0, 12.7]]}
        with self.assertRaisesRegex(ValueError, "2-64"):
            WaveguideParamsRequest(**payload)

        payload = _freeform_payload()
        payload["cross_sections"] = [{"t": 0.0, "shape": "circle"}]
        with self.assertRaisesRegex(ValueError, "2-32"):
            WaveguideParamsRequest(**payload)

        payload = _freeform_payload()
        payload["profile_h"]["points"][1] = [60.0]
        with self.assertRaisesRegex(ValueError, "2-4"):
            WaveguideParamsRequest(**payload)


class FreeformFormulaGateTest(unittest.TestCase):
    def test_freeform_is_accepted_by_all_mesh_route_gates(self):
        request = WaveguideParamsRequest(**_freeform_payload())
        captured = []

        async def fake_worker(function, payload):
            captured.append(payload)
            if function.__name__ == "fake_mesh":
                return {"msh_text": "", "stats": {}}
            return {"step_text": "ISO-10303-21;\nEND-ISO-10303-21;", "stats": {}}

        def fake_mesh(_payload):
            raise AssertionError("worker stub should provide the result")

        def fake_step(_payload):
            raise AssertionError("worker stub should provide the result")

        def fake_viewport(_payload):
            captured.append(_payload)
            return {"formula": "FREEFORM", "grid": {}, "metadata": {}}

        with patch("api.routes_mesh.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_mesh.HORNLAB_MESHER_RUNTIME_READY", True
        ), patch("api.routes_mesh.build_waveguide_mesh", fake_mesh), patch(
            "api.routes_mesh.build_inner_surface_step", fake_step
        ), patch("api.routes_mesh.build_viewport_geometry", fake_viewport), patch(
            "api.routes_mesh.run_on_gmsh_worker", fake_worker
        ):
            mesh_result = asyncio.run(build_mesh_from_params(request))
            step_result = asyncio.run(build_step_from_params(request))
            viewport_result = asyncio.run(build_viewport_geometry_from_params(request))

        self.assertEqual(mesh_result["generatedBy"], "hornlab-waveguide-mesher")
        self.assertEqual(step_result["generatedBy"], "hornlab-waveguide-mesher")
        self.assertEqual(viewport_result["formula"], "FREEFORM")
        self.assertEqual(len(captured), 3)
        self.assertTrue(all(payload["formula_type"] == "FREEFORM" for payload in captured))

    def test_freeform_is_accepted_by_solve_validation(self):
        validation = validate_submit_simulation_request(
            _solve_request("FREEFORM", _freeform_payload())
        )
        self.assertEqual(validation.waveguide_params["formula_type"], "FREEFORM")
        self.assertEqual(
            validation.waveguide_params["profile_h"]["points"],
            _freeform_payload()["profile_h"]["points"],
        )

    def test_unknown_formula_is_422_everywhere_and_names_freeform(self):
        unknown = WaveguideParamsRequest(formula_type="UNKNOWN")

        async def invoke_mesh_route(route):
            with patch("api.routes_mesh.HORNLAB_MESHER_AVAILABLE", True), patch(
                "api.routes_mesh.HORNLAB_MESHER_RUNTIME_READY", True
            ):
                with self.assertRaises(HTTPException) as raised:
                    await route(unknown)
            self.assertEqual(raised.exception.status_code, 422)
            self.assertIn("FREEFORM", raised.exception.detail)

        for route in (
            build_mesh_from_params,
            build_step_from_params,
            build_viewport_geometry_from_params,
        ):
            asyncio.run(invoke_mesh_route(route))

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(submit_simulation(_solve_request("UNKNOWN")))
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("FREEFORM", raised.exception.detail)


class FreeformAdapterTest(unittest.TestCase):
    def test_adapter_output_is_accepted_and_contains_no_foreign_coefficients(self):
        dumped = WaveguideParamsRequest(**_freeform_payload()).model_dump()
        config = waveguide_payload_to_mesher_config(dumped)
        profile = config["profile"]

        self.assertEqual(
            set(profile),
            {
                "formula",
                "a0",
                "profileH",
                "profileV",
                "crossSections",
                "overshootPolicy",
                "inflectionPolicy",
            },
        )
        self.assertEqual(profile["inflectionPolicy"], "allow")
        self.assertEqual(profile["profileH"]["throatAngleDeg"], 15.5)
        self.assertEqual(profile["profileH"]["throatTangentScale"], 1.1)
        self.assertEqual(profile["profileH"]["points"][1], [60.0, 80.0, 25.0, 1.4])
        self.assertEqual(profile["profileV"]["points"][1], [60.0, 60.0, -10.0])
        self.assertEqual(profile["profileV"]["mouthTangentScale"], 0.8)
        self.assertEqual(profile["crossSections"][1]["cornerRatio"], 0.12)
        self.assertNotIn("corner_ratio", profile["crossSections"][1])
        self.assertEqual(profile["crossSections"][2]["cornerRadiusMm"], 10.0)
        self.assertNotIn("corner_radius_mm", profile["crossSections"][2])

        params, formula, _mode = build_geometry_params(config)
        self.assertEqual(formula, "FREEFORM")
        self.assertEqual(params["profileH"], profile["profileH"])
        self.assertEqual(params["profileV"], profile["profileV"])
        self.assertEqual(params["crossSections"], profile["crossSections"])
        self.assertEqual(params["inflectionPolicy"], "allow")

    def test_adapter_rejects_missing_required_freeform_blocks(self):
        with self.assertRaisesRegex(
            ValueError, "missing: profile_h, profile_v, cross_sections"
        ):
            waveguide_payload_to_mesher_config({"formula_type": "FREEFORM"})

    def test_explicit_circsym_requires_degenerate_circular_freeform(self):
        unequal = _freeform_payload()
        with self.assertRaisesRegex(ValueError, "horizontal and vertical profile points differ"):
            validate_circsym_axisymmetric(unequal)

        shared = [[0.0, 12.7], [60.0, 70.0], [120.0, 120.0]]
        circular = _freeform_payload()
        circular["profile_h"]["points"] = shared
        circular["profile_v"]["points"] = list(shared)
        circular["cross_sections"] = [
            {"t": 0.0, "shape": "circle"},
            {"t": 0.5, "shape": "ellipse"},
            {"t": 1.0, "shape": "ellipse"},
        ]
        self.assertIsNone(validate_circsym_axisymmetric(circular))

        circular["cross_sections"][1] = {
            "t": 0.5,
            "shape": "superellipse",
            "exponent": 2.0,
        }
        self.assertIsNone(validate_circsym_axisymmetric(circular))

    def test_owner_viewport_smoke_returns_grid_and_freeform_metadata(self):
        dumped = WaveguideParamsRequest(**_freeform_payload()).model_dump()
        result = build_viewport_geometry(dumped)

        self.assertEqual(result["formula"], "FREEFORM")
        self.assertGreater(result["grid"]["grid_n_phi"], 0)
        self.assertGreater(result["grid"]["grid_n_length"], 0)
        self.assertTrue(result["grid"]["inner_points"])
        report = result["metadata"]["freeform"]
        self.assertIn("maxNormalDeviationMm", report)
        self.assertEqual(report["throatRadiusMm"], 12.7)
        self.assertEqual(report["tangentAnglesDeg"]["H"]["mouth"], 70.0)
        self.assertEqual(report["tangentAnglesDeg"]["V"]["mouth"], 60.0)
        self.assertEqual(report["anchorTangents"]["H"][1]["angleDeg"], 25.0)
        self.assertEqual(report["anchorTangents"]["H"][1]["strength"], 1.4)


if __name__ == "__main__":
    unittest.main()
