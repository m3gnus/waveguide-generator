import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException

from api.routes_mesh import (
    build_mesh_from_params,
    build_step_from_params,
    build_viewport_geometry_from_params,
    router as mesh_router,
)
from api.routes_simulation import submit_simulation
from contracts import SimulationRequest, WaveguideParamsRequest
from hornlab_mesher.config_builder import build_geometry_params
from services.simulation_validation import validate_submit_simulation_request
from solver.axisymmetry import validate_circsym_axisymmetric
from solver.mesher_adapter import (
    build_inner_surface_step,
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
                "corner_radius_mm": 20.0,
            },
            {
                "t": 1.0,
                "shape": "rounded_rectangle",
                "corner_radius_mm": 35.0,
            },
        ],
        "overshoot_policy": "allow",
        "inflection_policy": "warn",
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

        for invalid in ("allow", "ignore", "", "enforce"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                ValueError, "inflection_policy"
            ):
                WaveguideParamsRequest(
                    **{**_freeform_payload(), "inflection_policy": invalid}
                )

    def test_overshoot_policy_is_normalized_and_validated(self):
        payload = _freeform_payload()
        payload["overshoot_policy"] = " ALLOW "
        self.assertEqual(
            WaveguideParamsRequest(**payload).model_dump()["overshoot_policy"],
            "allow",
        )

        for invalid in ("maybe", "typo", "", "enforce"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                ValueError, "overshoot_policy"
            ):
                WaveguideParamsRequest(
                    **{**_freeform_payload(), "overshoot_policy": invalid}
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

    def test_unknown_nested_freeform_fields_return_422(self):
        app = FastAPI()
        app.include_router(mesh_router)

        async def post_payload(payload):
            request_body = json.dumps(payload).encode("utf-8")
            messages = []
            request_pending = True

            async def receive():
                nonlocal request_pending
                if request_pending:
                    request_pending = False
                    return {
                        "type": "http.request",
                        "body": request_body,
                        "more_body": False,
                    }
                return {"type": "http.disconnect"}

            async def send(message):
                messages.append(message)

            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "path": "/api/mesh/build",
                    "raw_path": b"/api/mesh/build",
                    "query_string": b"",
                    "root_path": "",
                    "headers": [(b"content-type", b"application/json")],
                    "client": ("test", 50000),
                    "server": ("test", 80),
                },
                receive,
                send,
            )
            status = next(
                message["status"]
                for message in messages
                if message["type"] == "http.response.start"
            )
            body = b"".join(
                message.get("body", b"")
                for message in messages
                if message["type"] == "http.response.body"
            )
            return status, json.loads(body)["detail"]

        cases = (
            ("profile_h", "mouth_angl_deg"),
            ("cross_sections", "corner_radus_mm"),
            ("cross_sections", "corner_ratio"),
        )
        for block, unknown_key in cases:
            with self.subTest(block=block):
                payload = _freeform_payload()
                if block == "profile_h":
                    payload[block][unknown_key] = 30.0
                else:
                    payload[block][1][unknown_key] = 8.0
                status, detail = asyncio.run(post_payload(payload))
                self.assertEqual(status, 422)
                self.assertTrue(
                    any(
                        unknown_key in error["loc"]
                        and error["type"] == "extra_forbidden"
                        for error in detail
                    )
                )


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

        osse_validation = validate_submit_simulation_request(_solve_request("OSSE"))
        self.assertEqual(osse_validation.waveguide_params["formula_type"], "OSSE")

    def test_freeform_solve_preflight_rejects_invalid_geometry_at_admission(self):
        strength = _freeform_payload()
        strength["profile_h"]["points"][1][3] = 0.0

        shape = _freeform_payload()
        shape["cross_sections"][1]["shape"] = "banana"

        station_t = _freeform_payload()
        station_t["cross_sections"][1]["t"] = 2.0

        overshoot = _freeform_payload()
        overshoot["overshoot_policy"] = "maybe"

        convexity = {
            "formula_type": "FREEFORM",
            "profile_h": {"points": [[0, 12.7], [60, 34], [120, 70]]},
            "profile_v": {"points": [[0, 12.7], [60, 30], [120, 50]]},
            "cross_sections": [
                {"t": 0, "shape": "circle"},
                {
                    "t": 1,
                    "shape": "rounded_rectangle",
                    "corner_radius_mm": 3,
                },
            ],
        }
        corner_radius = {
            "formula_type": "FREEFORM",
            "profile_h": {
                "points": [[0, 12.7], [35, 20], [60, 4], [100, 30]]
            },
            "profile_v": {
                "points": [[0, 12.7], [35, 20], [60, 4], [100, 30]]
            },
            "overshoot_policy": "allow",
            "cross_sections": [
                {"t": 0, "shape": "circle"},
                {
                    "t": 0.35,
                    "shape": "rounded_rectangle",
                    "corner_radius_mm": 10,
                },
                {
                    "t": 1,
                    "shape": "rounded_rectangle",
                    "corner_radius_mm": 10,
                },
            ],
        }

        cases = (
            ({"formula_type": "FREEFORM"}, "missing: profile_h, profile_v, cross_sections"),
            (strength, "strength must be in (0, 3]"),
            (shape, "shape must be"),
            (station_t, "t must be in [0, 1]"),
            (overshoot, "overshoot_policy"),
            (convexity, "non-convex outline"),
            (corner_radius, "exceeds the weight-aware local limit"),
        )
        for payload, message in cases:
            with self.subTest(message=message), self.assertRaises(HTTPException) as raised:
                asyncio.run(submit_simulation(_solve_request("FREEFORM", payload)))
            self.assertEqual(raised.exception.status_code, 422)
            self.assertIn(message, str(raised.exception.detail))

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
        self.assertEqual(profile["inflectionPolicy"], "warn")
        self.assertEqual(profile["profileH"]["throatAngleDeg"], 15.5)
        self.assertEqual(profile["profileH"]["throatTangentScale"], 1.1)
        self.assertEqual(profile["profileH"]["points"][1], [60.0, 80.0, 25.0, 1.4])
        self.assertEqual(profile["profileV"]["points"][1], [60.0, 60.0, -10.0])
        self.assertEqual(profile["profileV"]["mouthTangentScale"], 0.8)
        self.assertEqual(profile["crossSections"][1]["cornerRadiusMm"], 20.0)
        self.assertNotIn("corner_radius_mm", profile["crossSections"][1])
        self.assertEqual(profile["crossSections"][2]["cornerRadiusMm"], 35.0)
        self.assertNotIn("corner_radius_mm", profile["crossSections"][2])

        params, formula, _mode = build_geometry_params(config)
        self.assertEqual(formula, "FREEFORM")
        self.assertEqual(params["profileH"], profile["profileH"])
        self.assertEqual(params["profileV"], profile["profileV"])
        self.assertEqual(params["crossSections"], profile["crossSections"])
        self.assertEqual(params["inflectionPolicy"], "warn")

    def test_adapter_rejects_missing_required_freeform_blocks(self):
        with self.assertRaisesRegex(
            ValueError, "missing: profile_h, profile_v, cross_sections"
        ):
            waveguide_payload_to_mesher_config({"formula_type": "FREEFORM"})

    def test_explicit_circsym_requires_degenerate_circular_freeform(self):
        unequal = _freeform_payload()
        with self.assertRaisesRegex(ValueError, "inner profile varies with azimuth"):
            validate_circsym_axisymmetric(unequal)

        shared = [[0.0, 12.7], [60.0, 70.0], [120.0, 120.0]]
        circular = _freeform_payload()
        circular["profile_h"]["points"] = shared
        circular["profile_v"] = {
            **circular["profile_h"],
            "points": list(shared),
        }
        circular["cross_sections"] = [
            {"t": 0.0, "shape": "circle"},
            {"t": 0.5, "shape": "ellipse"},
            {"t": 1.0, "shape": "ellipse"},
        ]
        self.assertIsNone(validate_circsym_axisymmetric(circular))

        circular["profile_h"]["mouth_angle_deg"] = 30.0
        circular["profile_v"]["mouth_angle_deg"] = 60.0
        with self.assertRaisesRegex(ValueError, "inner profile varies with azimuth"):
            validate_circsym_axisymmetric(circular)
        circular["profile_v"]["mouth_angle_deg"] = 30.0

        circular["cross_sections"][1] = {
            "t": 0.5,
            "shape": "superellipse",
            "exponent": 2.0,
        }
        self.assertIsNone(validate_circsym_axisymmetric(circular))

        circular["profile_h"]["throat_tangent_scale"] = 1.0
        circular["profile_v"]["throat_tangent_scale"] = 1.0
        circular["profile_h"]["points"] = [list(point) for point in shared]
        circular["profile_v"]["points"] = [list(point) for point in shared]
        circular["profile_h"]["points"][0] = [0.0, 12.7, 15.5]
        self.assertIsNone(validate_circsym_axisymmetric(circular))

        circular["profile_h"]["points"][0] = [0.0, 12.7]
        circular["cross_sections"][-1] = {"t": 1.0, "shape": "circle"}
        with self.assertRaisesRegex(ValueError, r"crossSections\[0\]"):
            validate_circsym_axisymmetric(circular)

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
        self.assertEqual(len(report["curveSamples"]["H"]), 192)
        self.assertEqual(len(report["curveSamples"]["V"]), 192)
        self.assertTrue(report["inflectionSpans"]["H"])
        self.assertEqual(
            set(report["inflectionSpans"]["H"][0]),
            {"zStartMm", "zEndMm", "tangentDropDeg"},
        )

    def test_viewport_diagnostics_use_normalized_params_for_blank_a0(self):
        payload = _freeform_payload()
        payload["a0"] = ""
        dumped = WaveguideParamsRequest(**payload).model_dump()
        result = build_viewport_geometry(dumped)

        self.assertEqual(result["params"]["a0"], 15.5)
        self.assertIn("freeform", result["metadata"])

    def test_viewport_diagnostics_failure_is_logged(self):
        dumped = WaveguideParamsRequest(**_freeform_payload()).model_dump()
        with patch(
            "solver.mesher_adapter._json_safe_metadata",
            side_effect=RuntimeError("diagnostics exploded"),
        ), self.assertLogs("solver.mesher_adapter", level="WARNING") as logs:
            result = build_viewport_geometry(dumped)

        self.assertNotIn("freeform", result["metadata"])
        self.assertIn("Failed to build FREEFORM viewport diagnostics", "\n".join(logs.output))

    def test_real_freeform_inner_surface_step_smoke(self):
        dumped = WaveguideParamsRequest(**_freeform_payload()).model_dump()
        result = build_inner_surface_step(dumped)

        step = result["step_text"]
        stats = result["stats"]
        self.assertTrue(step.startswith("ISO-10303-21;"))
        self.assertTrue(step.rstrip().endswith("END-ISO-10303-21;"))
        self.assertIn("B_SPLINE_SURFACE", step)
        self.assertEqual(stats["stepBody"], "inner_surface")
        self.assertFalse(stats["hasWallThickness"])
        self.assertFalse(stats["hasEnclosure"])
        self.assertGreater(stats["ringCount"], 0)
        self.assertGreater(stats["lengthSteps"], 0)


if __name__ == "__main__":
    unittest.main()
