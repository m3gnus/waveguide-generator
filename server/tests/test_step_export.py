import asyncio
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from fastapi import HTTPException

from api.routes_mesh import build_step_from_params
from contracts import WaveguideParamsRequest
from contracts import SimulationRequest
from solver import mesher_adapter


_ATH_MUH_CONFIG_PATH = os.environ.get("ATH_MUH_CONFIG")
ATH_MUH_CONFIG = Path(_ATH_MUH_CONFIG_PATH) if _ATH_MUH_CONFIG_PATH else None


class StepExportRouteTest(unittest.TestCase):
    def test_step_export_forces_single_layer_payload(self):
        captured_payloads = []

        def fake_build_inner_surface_step(payload):
            captured_payloads.append(payload)
            return {
                "step_text": "ISO-10303-21;\nEND-ISO-10303-21;\n",
                "stats": {
                    "singleLayer": True,
                    "hasWallThickness": False,
                    "hasEnclosure": False,
                    "hasSourceCap": False,
                },
            }

        request = WaveguideParamsRequest(
            formula_type="OSSE",
            enc_depth=280,
            wall_thickness=12,
            quadrants=12,
        )

        with patch("api.routes_mesh.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_mesh.HORNLAB_MESHER_RUNTIME_READY", True
        ), patch("api.routes_mesh.build_inner_surface_step", fake_build_inner_surface_step):
            response = asyncio.run(build_step_from_params(request))

        self.assertEqual(response["generatedBy"], "hornlab-waveguide-mesher")
        self.assertIn("ISO-10303-21", response["step"])
        self.assertEqual(response["stats"]["singleLayer"], True)
        self.assertEqual(len(captured_payloads), 1)
        self.assertEqual(captured_payloads[0]["quadrants"], 1234)
        self.assertEqual(captured_payloads[0]["enc_depth"], 0.0)
        self.assertEqual(captured_payloads[0]["wall_thickness"], 0.0)
        self.assertEqual(captured_payloads[0]["step_body"], "inner_surface")

    def test_step_export_rejects_unsupported_body(self):
        request = WaveguideParamsRequest(
            formula_type="OSSE",
            step_body="throat_plate",
        )

        with patch("api.routes_mesh.HORNLAB_MESHER_AVAILABLE", True), patch(
            "api.routes_mesh.HORNLAB_MESHER_RUNTIME_READY", True
        ):
            with self.assertRaises(HTTPException) as exc:
                asyncio.run(build_step_from_params(request))
        self.assertEqual(exc.exception.status_code, 422)
        self.assertIn("Supported STEP body", exc.exception.detail)


class StepExportAdapterTest(unittest.TestCase):
    def test_step_writer_exports_bspline_surface_geometry(self):
        class FakeOption:
            def setNumber(self, *_args):
                return None

        class FakeOcc:
            def __init__(self):
                self.points = []
                self.bsplines = []
                self.wires = []
                self.thru_sections = []
                self.removed = []

            def addPoint(self, x, y, z):
                self.points.append((x, y, z))
                return len(self.points)

            def addBSpline(self, point_tags):
                self.bsplines.append(tuple(point_tags))
                return len(self.bsplines)

            def addWire(self, curve_tags, **kwargs):
                self.wires.append((tuple(curve_tags), kwargs))
                return len(self.wires)

            def addThruSections(self, wire_tags, **kwargs):
                self.thru_sections.append((tuple(wire_tags), kwargs))
                return [(2, len(self.thru_sections))]

            def remove(self, dim_tags, **kwargs):
                self.removed.append((tuple(dim_tags), kwargs))
                return None

            def synchronize(self):
                return None

        class FakeModel:
            def __init__(self):
                self.occ = FakeOcc()

            def add(self, *_args):
                return None

        class FakeGmsh:
            def __init__(self):
                self.model = FakeModel()
                self.option = FakeOption()
                self.initialized = False

            def isInitialized(self):
                return self.initialized

            def initialize(self):
                self.initialized = True

            def clear(self):
                return None

            def write(self, path):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(
                        "ISO-10303-21;\n"
                        "#1 = ADVANCED_FACE('',(),#2,.T.);\n"
                        "#2 = B_SPLINE_SURFACE_WITH_KNOTS('',3,3,(),.UNSPECIFIED.,.F.,.F.,.F.,(),(),.UNSPECIFIED.);\n"
                        "END-ISO-10303-21;\n"
                    )

            def finalize(self):
                self.initialized = False

        fake_gmsh = FakeGmsh()
        inner_points = np.asarray(
            [
                [[1.0, 0.0, 0.0], [2.0, 0.0, 10.0], [4.0, 0.0, 20.0]],
                [[0.0, 1.0, 0.0], [0.0, 2.0, 10.0], [0.0, 4.0, 20.0]],
                [[-1.0, 0.0, 0.0], [-2.0, 0.0, 10.0], [-4.0, 0.0, 20.0]],
                [[0.0, -1.0, 0.0], [0.0, -2.0, 10.0], [0.0, -4.0, 20.0]],
            ]
        )

        with patch.dict(sys.modules, {"gmsh": fake_gmsh}):
            step_text = mesher_adapter._write_inner_surface_step(inner_points)

        self.assertIn("ISO-10303-21", step_text)
        self.assertEqual(len(fake_gmsh.model.occ.points), 15)
        self.assertEqual(len(fake_gmsh.model.occ.bsplines), 3)
        self.assertEqual(len(fake_gmsh.model.occ.wires), 3)
        self.assertEqual(len(fake_gmsh.model.occ.thru_sections), 1)
        wire_tags, kwargs = fake_gmsh.model.occ.thru_sections[0]
        self.assertEqual(wire_tags, (1, 2, 3))
        self.assertEqual(kwargs["makeSolid"], False)
        self.assertEqual(kwargs["makeRuled"], True)
        self.assertEqual(kwargs["maxDegree"], 1)
        self.assertEqual(len(fake_gmsh.model.occ.removed), 1)
        removed_dim_tags, remove_kwargs = fake_gmsh.model.occ.removed[0]
        self.assertEqual(removed_dim_tags, ((1, 1), (1, 2), (1, 3)))
        self.assertEqual(remove_kwargs["recursive"], True)

        mouth_ring = {tuple(point) for point in inner_points[:, -1, :].tolist()}
        exported_points = set(fake_gmsh.model.occ.points)
        self.assertTrue(mouth_ring.issubset(exported_points))

    def test_step_writer_rejects_empty_geometry_step(self):
        class FakeOption:
            def setNumber(self, *_args):
                return None

        class FakeOcc:
            def addPoint(self, *_args):
                return 1

            def addBSpline(self, *_args, **_kwargs):
                return 1

            def addWire(self, *_args, **_kwargs):
                return 1

            def addThruSections(self, *_args, **_kwargs):
                return [(2, 1)]

            def remove(self, *_args, **_kwargs):
                return None

            def synchronize(self):
                return None

        class FakeModel:
            def __init__(self):
                self.occ = FakeOcc()

            def add(self, *_args):
                return None

        class FakeGmsh:
            def __init__(self):
                self.model = FakeModel()
                self.option = FakeOption()
                self.initialized = False

            def isInitialized(self):
                return self.initialized

            def initialize(self):
                self.initialized = True

            def clear(self):
                return None

            def write(self, path):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("ISO-10303-21;\nEND-ISO-10303-21;\n")

            def finalize(self):
                self.initialized = False

        inner_points = np.asarray(
            [
                [[1.0, 0.0, 0.0], [2.0, 0.0, 10.0]],
                [[0.0, 1.0, 0.0], [0.0, 2.0, 10.0]],
                [[-1.0, 0.0, 0.0], [-2.0, 0.0, 10.0]],
                [[0.0, -1.0, 0.0], [0.0, -2.0, 10.0]],
            ]
        )

        with patch.dict(sys.modules, {"gmsh": FakeGmsh()}):
            with self.assertRaisesRegex(RuntimeError, "without surface face geometry"):
                mesher_adapter._write_inner_surface_step(inner_points)

    def test_step_writer_preserves_morphed_mouth_boundary_without_loft_overshoot(self):
        try:
            import gmsh  # noqa: F401
        except ImportError:
            self.skipTest("gmsh is not installed")

        n_phi = 12
        z_values = [0.0, 40.0, 80.0, 120.0]
        inner_points = np.zeros((n_phi, len(z_values), 3), dtype=float)
        for i in range(n_phi):
            phi = 2.0 * np.pi * i / n_phi
            for j, z in enumerate(z_values):
                blend = j / (len(z_values) - 1)
                half_w = 40.0 + 140.0 * blend
                half_h = 40.0 + 40.0 * blend
                x = half_w * np.sign(np.cos(phi)) * abs(np.cos(phi))
                y = half_h * np.sign(np.sin(phi)) * abs(np.sin(phi))
                inner_points[i, j] = [x, y, z]

        step_text = mesher_adapter._write_inner_surface_step(inner_points)

        self.assertEqual(step_text.count("ADVANCED_FACE"), len(z_values) - 1)
        self.assertGreaterEqual(step_text.count("B_SPLINE_SURFACE"), 1)
        self.assertRegex(step_text, re.compile(r"\b120(?:\.0+)?\b"))
        self.assertRegex(step_text, re.compile(r"\b180(?:\.0+)?\b"))

        step_path = None
        initialized_here = False
        try:
            if not gmsh.isInitialized():
                gmsh.initialize()
                initialized_here = True
            gmsh.option.setNumber("General.Terminal", 0)
            with tempfile.NamedTemporaryFile(prefix="waveguide-step-test-", suffix=".step", delete=False) as tmp:
                step_path = Path(tmp.name)
            step_path.write_text(step_text, encoding="utf-8")

            gmsh.clear()
            gmsh.model.add("StepReimport")
            gmsh.merge(str(step_path))
            gmsh.model.occ.synchronize()
            surface_bboxes = [
                gmsh.model.getBoundingBox(2, tag)
                for _dim, tag in gmsh.model.getEntities(2)
            ]
        finally:
            if step_path is not None:
                step_path.unlink(missing_ok=True)
            if initialized_here and gmsh.isInitialized():
                gmsh.finalize()

        self.assertTrue(surface_bboxes)
        min_x = min(bbox[0] for bbox in surface_bboxes)
        min_y = min(bbox[1] for bbox in surface_bboxes)
        min_z = min(bbox[2] for bbox in surface_bboxes)
        max_x = max(bbox[3] for bbox in surface_bboxes)
        max_y = max(bbox[4] for bbox in surface_bboxes)
        max_z = max(bbox[5] for bbox in surface_bboxes)
        self.assertGreaterEqual(min_x, -180.0001)
        self.assertLessEqual(max_x, 180.0001)
        self.assertGreaterEqual(min_y, -80.0001)
        self.assertLessEqual(max_y, 80.0001)
        self.assertGreaterEqual(min_z, -0.0001)
        self.assertLessEqual(max_z, 120.0001)

    def test_ath_muh_step_export_keeps_bounded_rectangular_mouth_with_limited_faces(self):
        if ATH_MUH_CONFIG is None or not ATH_MUH_CONFIG.exists():
            self.skipTest("ATH_MUH_CONFIG reference config is not available")
        try:
            import gmsh  # noqa: F401
            from hornlab_mesher.cli import build_geometry_params, load_config
            from hornlab_mesher.profiles import build_point_grid
        except ImportError:
            self.skipTest("gmsh or hornlab-waveguide-mesher is not installed")

        params, _formula, _mode = build_geometry_params(load_config(ATH_MUH_CONFIG))
        params = {
            **params,
            "quadrants": "1234",
            "encDepth": 0.0,
            "wallThickness": 0.0,
            "angularSegments": 160,
            "cornerSegments": 8,
        }
        grid = build_point_grid(params)
        n_phi = int(grid["grid_n_phi"])
        n_length = int(grid["grid_n_length"])
        inner_points = np.asarray(grid["inner_points"], dtype=float).reshape(n_phi, n_length + 1, 3)

        step_text = mesher_adapter._write_inner_surface_step(inner_points)

        self.assertEqual(n_phi, 160)
        self.assertEqual(n_length, 20)
        self.assertEqual(step_text.count("ADVANCED_FACE"), n_length)
        self.assertLessEqual(step_text.count("PRODUCT("), n_length + 2)

        step_path = None
        initialized_here = False
        try:
            if not gmsh.isInitialized():
                gmsh.initialize()
                initialized_here = True
            gmsh.option.setNumber("General.Terminal", 0)
            with tempfile.NamedTemporaryFile(prefix="waveguide-ath-muh-", suffix=".step", delete=False) as tmp:
                step_path = Path(tmp.name)
            step_path.write_text(step_text, encoding="utf-8")

            gmsh.clear()
            gmsh.model.add("AthMuhStepReimport")
            gmsh.merge(str(step_path))
            gmsh.model.occ.synchronize()
            surface_bboxes = [
                gmsh.model.getBoundingBox(2, tag)
                for _dim, tag in gmsh.model.getEntities(2)
            ]
        finally:
            if step_path is not None:
                step_path.unlink(missing_ok=True)
            if initialized_here and gmsh.isInitialized():
                gmsh.finalize()

        self.assertTrue(surface_bboxes)
        min_x = min(bbox[0] for bbox in surface_bboxes)
        min_y = min(bbox[1] for bbox in surface_bboxes)
        min_z = min(bbox[2] for bbox in surface_bboxes)
        max_x = max(bbox[3] for bbox in surface_bboxes)
        max_y = max(bbox[4] for bbox in surface_bboxes)
        max_z = max(bbox[5] for bbox in surface_bboxes)
        self.assertGreaterEqual(min_x, -197.2222)
        self.assertLessEqual(max_x, 197.2222)
        self.assertGreaterEqual(min_y, -345.9112)
        self.assertLessEqual(max_y, 345.9112)
        self.assertGreaterEqual(min_z, -0.0001)
        self.assertLessEqual(max_z, 135.0001)

    def test_inner_surface_step_adapter_forces_bare_full_domain_config(self):
        captured_configs = []

        def fake_viewport_geometry(config):
            captured_configs.append(config)
            return {
                "grid": {
                    "grid_n_phi": 4,
                    "grid_n_length": 1,
                    "inner_points": [
                        1, 0, 0,
                        0, 1, 0,
                        -1, 0, 0,
                        0, -1, 0,
                        2, 0, 10,
                        0, 2, 10,
                        -2, 0, 10,
                        0, -2, 10,
                    ],
                }
            }

        with patch.object(
            mesher_adapter,
            "build_viewport_geometry_from_config",
            fake_viewport_geometry,
        ), patch.object(
            mesher_adapter,
            "_write_inner_surface_step",
            return_value="ISO-10303-21;\nEND-ISO-10303-21;\n",
        ):
            result = mesher_adapter.build_inner_surface_step(
                {
                    "formula_type": "OSSE",
                    "enc_depth": 280,
                    "wall_thickness": 12,
                    "quadrants": 12,
                }
            )

        self.assertEqual(result["stats"]["singleLayer"], True)
        self.assertEqual(result["stats"]["stepBody"], "inner_surface")
        self.assertEqual(result["stats"]["hasWallThickness"], False)
        self.assertEqual(result["stats"]["hasThroatPlate"], False)
        self.assertEqual(len(captured_configs), 1)
        config = captured_configs[0]
        self.assertEqual(config["mode"], "bare")
        self.assertNotIn("enclosure", config)
        self.assertEqual(config["mesh"]["wallThickness"], 0.0)
        self.assertEqual(config["mesh"]["quadrants"], 1234)


class ViewportGeometryAdapterTest(unittest.TestCase):
    def test_payload_sim_type_one_selects_infinite_baffle_and_preserves_sampling(self):
        config = mesher_adapter.waveguide_payload_to_mesher_config(
            {
                "formula_type": "OSSE",
                "L": "150",
                "a": "62",
                "a0": 0,
                "r0": 18,
                "n_angular": 100,
                "n_length": 32,
                "sampling_mode": "ath-default-zmap",
                "sim_type": 1,
                "enc_depth": 0,
                "wall_thickness": 0,
            }
        )

        self.assertEqual(config["mode"], "infinite-baffle")
        self.assertEqual(config["mesh"]["samplingMode"], "ath-default-zmap")
        self.assertEqual(config["mesh"]["wallThickness"], 0)

    def test_payload_preserves_angular_slot_length_expression(self):
        config = mesher_adapter.waveguide_payload_to_mesher_config(
            {
                "formula_type": "OSSE",
                "slot_length": "45 - 42*sin(2*p)^4",
                "length_mode": "total",
            }
        )

        self.assertEqual(
            config["profile"]["slotLength"],
            "45 - 42*sin(2*p)^4",
        )
        self.assertEqual(config["profile"]["_athLengthMode"], "total")

    def test_m2_payload_total_length_mode_keeps_mouth_at_ath_size(self):
        from hornlab_mesher.config_builder import build_geometry_params
        from hornlab_mesher.profiles import build_point_grid

        config = mesher_adapter.waveguide_payload_to_mesher_config(
            {
                "formula_type": "OSSE",
                "length_mode": "total",
                "L": "150",
                "a": "62 - 10*sin(p)^2 - 10*sin(2*(p+pi/4))^4",
                "a0": 0,
                "r0": 18,
                "k": 0.9,
                "s": 0.9,
                "n": "3 + 5*sin(2*p)^2",
                "q": 0.996,
                "slot_length": "45 - 42*sin(2*p)^4",
                "morph_target": 1,
                "morph_corner": 8,
                "morph_rate": 3,
                "morph_fixed": 0,
                "morph_allow_shrinkage": 0,
                "n_angular": 100,
                "corner_segments": 4,
                "n_length": 32,
                "sampling_mode": "ath-default-zmap",
                "quadrants": 1234,
                "sim_type": 1,
                "enc_depth": 0,
                "wall_thickness": 0,
            }
        )
        params, _formula, mode = build_geometry_params(config)
        self.assertEqual(mode, "infinite-baffle")

        grid = build_point_grid(params)
        n_phi = int(grid["grid_n_phi"])
        n_length = int(grid["grid_n_length"])
        inner = np.asarray(grid["inner_points"], dtype=float).reshape(n_phi, n_length + 1, 3)
        mouth = inner[:, -1, :]

        self.assertEqual((n_phi, n_length), (104, 32))
        self.assertLess(abs(float(np.max(np.abs(mouth[:, 0]))) - 229.0), 1.0e-6)
        self.assertLess(abs(float(np.max(np.abs(mouth[:, 1]))) - 204.0), 1.0e-6)
        self.assertLess(abs(float(np.max(mouth[:, 2])) - 150.0), 1.0e-6)

    def test_viewport_geometry_serves_point_grid_without_gmsh(self):
        captured_configs = []

        def fake_viewport_geometry(config):
            captured_configs.append(config)
            return {
                "params": {"type": "OSSE", "sourceShape": 1},
                "formula": "OSSE",
                "mode": "freestanding",
                "grid": {
                    "inner_points": [0.0] * (8 * 3 * 3),
                    "outer_points": None,
                    "grid_n_phi": 8,
                    "grid_n_length": 2,
                    "full_circle": True,
                    "sampling_mode": "uniform",
                },
                "enclosure": None,
            }

        with patch.object(
            mesher_adapter,
            "build_viewport_geometry_from_config",
            fake_viewport_geometry,
        ):
            result = mesher_adapter.build_viewport_geometry(
                {
                    "formula_type": "OSSE",
                    "n_angular": 96,
                    "n_length": 48,
                    "quadrants": 12,
                }
            )

        self.assertEqual(len(captured_configs), 1)
        config = captured_configs[0]
        self.assertEqual(config["mesh"]["quadrants"], 1234)
        self.assertEqual(config["mesh"]["angularSegments"], 96)
        self.assertEqual(config["mesh"]["lengthSegments"], 48)
        self.assertEqual(result["formula"], "OSSE")
        self.assertEqual(result["mode"], "freestanding")
        self.assertEqual(result["grid"]["grid_n_phi"], 8)
        self.assertIsNone(result["enclosure"])
        self.assertEqual(result["params"]["sourceShape"], 1)
        self.assertEqual(
            result["metadata"]["source"], "hornlab_waveguide_mesher_point_grid"
        )
        self.assertEqual(result["metadata"]["units"], "mm")
        self.assertEqual(result["metadata"]["gridNPhi"], 8)
        self.assertEqual(result["metadata"]["gridNLength"], 2)
        self.assertEqual(result["metadata"]["samplingMode"], "uniform")

    def test_viewport_geometry_clamps_display_density(self):
        captured_configs = []

        def fake_viewport_geometry(config):
            captured_configs.append(config)
            return {"params": {}, "formula": "OSSE", "mode": "bare", "grid": {}, "enclosure": None}

        with patch.object(
            mesher_adapter,
            "build_viewport_geometry_from_config",
            fake_viewport_geometry,
        ):
            mesher_adapter.build_viewport_geometry(
                {"formula_type": "OSSE", "n_angular": 100000, "n_length": 0}
            )

        config = captured_configs[0]
        self.assertEqual(
            config["mesh"]["angularSegments"],
            mesher_adapter.VIEWPORT_GEOMETRY_MAX_ANGULAR_SEGMENTS,
        )
        self.assertEqual(
            config["mesh"]["lengthSegments"],
            mesher_adapter.VIEWPORT_GEOMETRY_MIN_LENGTH_SEGMENTS,
        )

    def test_viewport_geometry_returns_enclosure_rings_in_enclosure_mode(self):
        def fake_viewport_geometry(config):
            return {
                "params": {"type": "OSSE"},
                "formula": "OSSE",
                "mode": "enclosure",
                "grid": {"grid_n_phi": 8, "grid_n_length": 2, "sampling_mode": "uniform"},
                "enclosure": {
                    "mouth_points": [0.0] * (8 * 3),
                    "profile_rings": [
                        {"role": "front_inset", "points": [0.0] * 12},
                        {"role": "side_back_outer", "points": [0.0] * 12},
                    ],
                    "bounds": {"bx0": -1.0, "bx1": 1.0, "by0": -1.0, "by1": 1.0,
                               "z_front": 10.0, "z_back": -5.0, "cx": 0.0, "cy": 0.0},
                    "plan_type": 1,
                    "edge_type": 1,
                    "edge_mm": 0.0,
                    "edge_depth": 0.0,
                },
            }

        with patch.object(
            mesher_adapter,
            "build_viewport_geometry_from_config",
            fake_viewport_geometry,
        ):
            result = mesher_adapter.build_viewport_geometry(
                {"formula_type": "OSSE", "enc_depth": 220}
            )

        self.assertEqual(result["mode"], "enclosure")
        rings = result["enclosure"]["profile_rings"]
        self.assertEqual([ring["role"] for ring in rings], ["front_inset", "side_back_outer"])
        self.assertIn("bounds", result["enclosure"])


class SimulationRequestContractTest(unittest.TestCase):
    def test_solver_backend_defaults_to_auto(self):
        request = SimulationRequest(
            mesh={
                "vertices": [0.0, 0.0, 0.0],
                "indices": [0, 0, 0],
                "surfaceTags": [2],
            },
            frequency_range=[100.0, 1000.0],
            num_frequencies=2,
            sim_type="2",
        )

        self.assertEqual(request.solver_backend, "auto")


if __name__ == "__main__":
    unittest.main()
