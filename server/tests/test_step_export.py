import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from api.routes_mesh import build_step_from_params
from contracts import WaveguideParamsRequest
from contracts import SimulationRequest
from solver import mesher_adapter


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
        surface_wire_tags, kwargs = fake_gmsh.model.occ.thru_sections[0]
        self.assertEqual(surface_wire_tags, (1, 2, 3))
        self.assertEqual(kwargs["makeSolid"], False)
        self.assertEqual(kwargs["makeRuled"], False)
        self.assertEqual(kwargs["maxDegree"], 3)

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
        self.assertEqual(len(captured_configs), 1)
        config = captured_configs[0]
        self.assertEqual(config["mode"], "bare")
        self.assertNotIn("enclosure", config)
        self.assertEqual(config["mesh"]["wallThickness"], 0.0)
        self.assertEqual(config["mesh"]["quadrants"], 1234)


class ViewportMeshAdapterTest(unittest.TestCase):
    def test_viewport_mesh_uses_mesher_gmsh_output_and_fixed_geometry_sampling(self):
        captured_configs = []

        def fake_build_from_config(config, mesh_path):
            captured_configs.append(config)
            return SimpleNamespace(
                formula="OSSE",
                mode="bare",
                physical_groups={1: "SD1G0", 2: "SD1D1001"},
            )

        canonical = {
            "vertices": [
                0.0, 0.0, 0.0,
                0.001, 0.002, 0.003,
                0.004, 0.005, 0.006,
                0.007, 0.008, 0.009,
            ],
            "indices": [0, 1, 2, 0, 2, 3],
            "surfaceTags": [1, 2],
            "metadata": {"tagCounts": {"1": 1, "2": 1, "3": 0, "4": 0}},
        }

        with patch.object(mesher_adapter, "build_from_config", fake_build_from_config), patch.object(
            mesher_adapter, "_canonical_mesh_from_msh", return_value=canonical
        ):
            mesh = mesher_adapter.build_viewport_mesh(
                {
                    "formula_type": "OSSE",
                    "n_angular": 8,
                    "n_length": 2,
                    "quadrants": 12,
                }
            )

        self.assertEqual(len(captured_configs), 1)
        config = captured_configs[0]
        self.assertEqual(config["mesh"]["angularSegments"], 128)
        self.assertEqual(config["mesh"]["lengthSegments"], 64)
        self.assertEqual(config["mesh"]["quadrants"], 1234)
        self.assertEqual(config["mesh"]["preserveGrid"], False)
        self.assertEqual(mesh["vertices"][3:6], [1.0, 3.0, 2.0])
        self.assertEqual(mesh["indices"], [0, 1, 2, 0, 2, 3])
        self.assertEqual(mesh["surfaceTags"], [1, 2])
        self.assertEqual(mesh["groups"]["horn"], {"start": 0, "end": 1})
        self.assertEqual(mesh["groups"]["throat_disc"], {"start": 1, "end": 2})
        self.assertEqual(mesh["metadata"]["source"], "hornlab_waveguide_mesher_gmsh")
        self.assertEqual(mesh["metadata"]["samplingMode"], "gmsh_surface_mesh")

    def test_viewport_mesh_sorts_tag_groups_into_contiguous_ranges(self):
        canonical = {
            "vertices": [
                0, 0, 0,
                1, 0, 0,
                0, 1, 0,
                0, 0, 1,
            ],
            "indices": [0, 1, 2, 0, 2, 3, 0, 3, 1],
            "surfaceTags": [2, 1, 3],
            "metadata": {"tagCounts": {"1": 1, "2": 1, "3": 1, "4": 0}},
        }

        with patch.object(
            mesher_adapter,
            "build_from_config",
            return_value=SimpleNamespace(formula="OSSE", mode="enclosure", physical_groups={}),
        ), patch.object(mesher_adapter, "_canonical_mesh_from_msh", return_value=canonical):
            mesh = mesher_adapter.build_viewport_mesh({"formula_type": "OSSE", "enc_depth": 220})

        self.assertEqual(mesh["surfaceTags"], [1, 3, 2])
        self.assertEqual(mesh["groups"]["horn"], {"start": 0, "end": 1})
        self.assertEqual(mesh["groups"]["enclosure"], {"start": 1, "end": 2})
        self.assertEqual(mesh["groups"]["throat_disc"], {"start": 2, "end": 3})


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
