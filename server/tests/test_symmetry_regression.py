"""
Regression tests for symmetry reduction eligibility on ATH reference configs.

These tests ensure that future geometry or solver changes cannot silently
change the symmetry reduction eligibility for reference cases. The expected
values are captured from the current working state and should be updated
only intentionally when the behavior is deliberately changed.

Run with:
    cd server && python3 -m pytest tests/test_symmetry_regression.py -v
"""
import os
import re
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ath"

EXPECTED_RESULTS = {
    "osse-simple": {
        "formula_type": "OSSE",
        "quadrants": 1234,
        "symmetry_applied": False,
        "symmetry_eligible": False,
        "detected_symmetry_type": "full",
        "reduction_factor": 1.0,
        "has_enclosure": False,
        "mesh_vertex_count_min": 1000,
        "mesh_vertex_count_max": 2000,
        "mesh_triangle_count_min": 2000,
        "mesh_triangle_count_max": 3500,
        "surface_tags": [1, 2],
    },
    "rosse-simple": {
        "formula_type": "R-OSSE",
        "quadrants": 1234,
        "symmetry_applied": False,
        "symmetry_eligible": False,
        "detected_symmetry_type": "full",
        "reduction_factor": 1.0,
        "has_enclosure": False,
        "mesh_vertex_count_min": 1000,
        "mesh_vertex_count_max": 2000,
        "mesh_triangle_count_min": 2000,
        "mesh_triangle_count_max": 3500,
        "surface_tags": [1, 2],
    },
    "osse-with-enclosure": {
        "formula_type": "OSSE",
        "quadrants": 1234,
        "symmetry_applied": False,
        "symmetry_eligible": False,
        "detected_symmetry_type": "full",
        "reduction_factor": 1.0,
        "has_enclosure": False,
        "mesh_vertex_count_min": 800,
        "mesh_vertex_count_max": 1500,
        "mesh_triangle_count_min": 1500,
        "mesh_triangle_count_max": 3000,
        "surface_tags": [1, 2],
    },
}


def parse_ath_cfg(filepath: Path) -> Dict[str, Any]:
    """Parse ATH .cfg file into a nested dictionary."""
    result: Dict[str, Any] = {}
    current_section: Optional[str] = None
    current_subsection: Optional[str] = None

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";"):
                continue

            section_match = re.match(r"^([A-Za-z0-9_-]+)\s*=\s*\{", line)
            if section_match:
                current_section = section_match.group(1)
                current_subsection = None
                result[current_section] = {}
                continue

            subsection_match = re.match(r"^([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)\s*=\s*\{", line)
            if subsection_match:
                parent = subsection_match.group(1)
                current_subsection = subsection_match.group(2)
                if parent not in result:
                    result[parent] = {}
                result[parent][current_subsection] = {}
                continue

            if line == "}":
                if current_subsection:
                    current_subsection = None
                else:
                    current_section = None
                continue

            if current_section is None:
                continue

            kv_match = re.match(r"^([A-Za-z0-9_-]+)\s*=\s*(.+)$", line)
            if kv_match:
                key = kv_match.group(1)
                value = kv_match.group(2).strip()

                target = result[current_section]
                if current_subsection:
                    target = result[current_section].setdefault(current_subsection, {})

                target[key] = value

    return result


def ath_to_waveguide_params(ath: Dict[str, Any]) -> Dict[str, Any]:
    """Convert parsed ATH config to WaveguideParamsRequest format."""
    params: Dict[str, Any] = {
        "formula_type": "R-OSSE",
    }

    if "R-OSSE" in ath:
        rosse = ath["R-OSSE"]
        params["formula_type"] = "R-OSSE"
        params["R"] = rosse.get("R")
        params["a"] = rosse.get("a")
        params["r"] = rosse.get("r", 0.4)
        params["b"] = rosse.get("b", 0.2)
        params["m"] = rosse.get("m", 0.85)
        params["tmax"] = rosse.get("tmax", 1.0)
        params["a0"] = rosse.get("a0", 15.5)
        params["r0"] = rosse.get("r0", 12.7)
        params["k"] = rosse.get("k", 2.0)
        params["q"] = rosse.get("q", 3.4)
    elif "OSSE" in ath:
        osse = ath["OSSE"]
        params["formula_type"] = "OSSE"
        params["L"] = osse.get("L", "120")
        params["a"] = osse.get("a")
        params["a0"] = osse.get("a0", 15.5)
        params["r0"] = osse.get("r0", 12.7)
        params["k"] = osse.get("k", 7.0)
        params["s"] = osse.get("s", "0.58")
        params["n"] = osse.get("n", 4.158)
        params["q"] = osse.get("q", 0.991)
        params["h"] = osse.get("h", 0.0)

    if "MORPH" in ath:
        morph = ath["MORPH"]
        params["morph_target"] = int(morph.get("TargetShape", 0) or 0)
        if morph.get("TargetWidth") and float(morph.get("TargetWidth", 0) or 0) > 0:
            params["gcurve_type"] = 0

    mesh = ath.get("Mesh", {})
    params["n_angular"] = int(mesh.get("AngularSegments", 60) or 60)
    params["n_length"] = int(mesh.get("LengthSegments", 15) or 15)
    params["throat_res"] = float(mesh.get("ThroatResolution", 8.0) or 8.0)
    params["mouth_res"] = float(mesh.get("MouthResolution", 20.0) or 20.0)
    params["quadrants"] = int(mesh.get("Quadrants", 1234) or 1234)

    enc = ath.get("Mesh.Enclosure", ath.get("Mesh", {}).get("Enclosure", {}))
    if enc:
        params["enc_depth"] = float(enc.get("Depth", 0) or 0)
        params["wall_thickness"] = float(enc.get("Spacing", "25,25,25,25").split(",")[0] or 6.0)
    else:
        params["enc_depth"] = 0.0
        params["wall_thickness"] = 6.0

    params["source_shape"] = 2
    params["source_radius"] = -1.0
    params["rear_res"] = 40.0

    return params


def get_reference_configs() -> List[Dict[str, Any]]:
    """Load all ATH reference configs from fixtures directory."""
    configs = []

    if not FIXTURES_DIR.exists():
        return configs

    for cfg_file in sorted(FIXTURES_DIR.glob("*.cfg")):
        try:
            ath = parse_ath_cfg(cfg_file)
            params = ath_to_waveguide_params(ath)
            configs.append(
                {
                    "name": cfg_file.stem,
                    "path": str(cfg_file),
                    "ath": ath,
                    "params": params,
                }
            )
        except Exception as e:
            pass

    return configs


def evaluate_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Build mesh and evaluate symmetry policy for a config."""
    from solver.symmetry import evaluate_symmetry_policy
    from solver.waveguide_builder import build_waveguide_mesh

    params = config["params"]

    result: Dict[str, Any] = {
        "name": config["name"],
        "formula_type": params.get("formula_type"),
        "quadrants": params.get("quadrants"),
        "has_enclosure": (params.get("enc_depth") or 0) > 0,
        "mesh": {},
        "symmetry_policy": {},
        "errors": [],
    }

    try:
        mesh_result = build_waveguide_mesh(params, include_canonical=True, symmetry_cut=None)
        canonical = mesh_result.get("canonical_mesh", {})

        vertices = canonical.get("vertices", [])
        indices = canonical.get("indices", [])
        surface_tags = canonical.get("surfaceTags", [])

        n_verts = len(vertices) // 3
        n_tris = len(indices) // 3

        result["mesh"] = {
            "vertex_count": n_verts,
            "triangle_count": n_tris,
            "surface_tags": sorted(set(surface_tags)) if surface_tags else [],
        }

        verts_np = np.array(vertices, dtype=np.float64).reshape(-1, 3)
        indices_np = np.array(indices, dtype=np.int32).reshape(-1, 3)
        tags_np = np.array(surface_tags, dtype=np.int32)

        throat_elements = np.where(tags_np == 2)[0]

        symmetry_result = evaluate_symmetry_policy(
            vertices=verts_np.T,
            indices=indices_np.T,
            surface_tags=tags_np,
            throat_elements=throat_elements,
            enable_symmetry=True,
            tolerance=1e-3,
            quadrants=params.get("quadrants"),
        )

        policy = symmetry_result.get("policy", {})
        result["symmetry_policy"] = {
            "requested": policy.get("requested"),
            "applied": policy.get("applied"),
            "eligible": policy.get("eligible"),
            "decision": policy.get("decision"),
            "reason": policy.get("reason"),
            "detected_symmetry_type": policy.get("detected_symmetry_type"),
            "reduction_factor": policy.get("reduction_factor"),
        }

    except Exception as e:
        result["errors"].append(str(e))

    return result


class SymmetryRegressionTest(unittest.TestCase):
    def test_fixtures_directory_exists(self):
        self.assertTrue(
            FIXTURES_DIR.exists(),
            f"ATH fixtures directory not found: {FIXTURES_DIR}",
        )

    def test_all_expected_configs_exist(self):
        configs = get_reference_configs()
        config_names = {c["name"] for c in configs}
        expected_names = set(EXPECTED_RESULTS.keys())

        missing = expected_names - config_names
        self.assertEqual(
            missing,
            set(),
            f"Missing expected config files: {missing}",
        )

    def test_osse_simple_symmetry_eligibility(self):
        configs = [c for c in get_reference_configs() if c["name"] == "osse-simple"]
        self.assertEqual(len(configs), 1, "osse-simple config not found")

        result = evaluate_config(configs[0])
        expected = EXPECTED_RESULTS["osse-simple"]

        self.assertEqual(result["errors"], [], f"Unexpected errors: {result['errors']}")
        self.assertEqual(
            result["symmetry_policy"]["applied"],
            expected["symmetry_applied"],
            f"Symmetry applied mismatch: got {result['symmetry_policy']['applied']}, expected {expected['symmetry_applied']}",
        )
        self.assertEqual(
            result["symmetry_policy"]["eligible"],
            expected["symmetry_eligible"],
            f"Symmetry eligible mismatch: got {result['symmetry_policy']['eligible']}, expected {expected['symmetry_eligible']}",
        )
        self.assertEqual(
            result["symmetry_policy"]["detected_symmetry_type"],
            expected["detected_symmetry_type"],
            f"Detected symmetry type mismatch",
        )
        self.assertEqual(
            float(result["symmetry_policy"]["reduction_factor"] or 1.0),
            expected["reduction_factor"],
            f"Reduction factor mismatch",
        )

        mesh = result["mesh"]
        self.assertGreaterEqual(
            mesh["vertex_count"],
            expected["mesh_vertex_count_min"],
            f"Vertex count too low: {mesh['vertex_count']} < {expected['mesh_vertex_count_min']}",
        )
        self.assertLessEqual(
            mesh["vertex_count"],
            expected["mesh_vertex_count_max"],
            f"Vertex count too high: {mesh['vertex_count']} > {expected['mesh_vertex_count_max']}",
        )
        self.assertEqual(
            mesh["surface_tags"],
            expected["surface_tags"],
            f"Surface tags mismatch: {mesh['surface_tags']} != {expected['surface_tags']}",
        )

    def test_rosse_simple_symmetry_eligibility(self):
        configs = [c for c in get_reference_configs() if c["name"] == "rosse-simple"]
        self.assertEqual(len(configs), 1, "rosse-simple config not found")

        result = evaluate_config(configs[0])
        expected = EXPECTED_RESULTS["rosse-simple"]

        self.assertEqual(result["errors"], [], f"Unexpected errors: {result['errors']}")
        self.assertEqual(
            result["symmetry_policy"]["applied"],
            expected["symmetry_applied"],
            f"Symmetry applied mismatch",
        )
        self.assertEqual(
            result["symmetry_policy"]["eligible"],
            expected["symmetry_eligible"],
            f"Symmetry eligible mismatch",
        )
        self.assertEqual(
            result["symmetry_policy"]["detected_symmetry_type"],
            expected["detected_symmetry_type"],
            f"Detected symmetry type mismatch",
        )
        self.assertEqual(
            float(result["symmetry_policy"]["reduction_factor"] or 1.0),
            expected["reduction_factor"],
            f"Reduction factor mismatch",
        )

        mesh = result["mesh"]
        self.assertGreaterEqual(
            mesh["vertex_count"],
            expected["mesh_vertex_count_min"],
            f"Vertex count too low",
        )
        self.assertLessEqual(
            mesh["vertex_count"],
            expected["mesh_vertex_count_max"],
            f"Vertex count too high",
        )
        self.assertEqual(
            mesh["surface_tags"],
            expected["surface_tags"],
            f"Surface tags mismatch",
        )

    def test_osse_with_enclosure_symmetry_eligibility(self):
        configs = [c for c in get_reference_configs() if c["name"] == "osse-with-enclosure"]
        self.assertEqual(len(configs), 1, "osse-with-enclosure config not found")

        result = evaluate_config(configs[0])
        expected = EXPECTED_RESULTS["osse-with-enclosure"]

        self.assertEqual(result["errors"], [], f"Unexpected errors: {result['errors']}")
        self.assertEqual(
            result["symmetry_policy"]["applied"],
            expected["symmetry_applied"],
            f"Symmetry applied mismatch",
        )
        self.assertEqual(
            result["symmetry_policy"]["eligible"],
            expected["symmetry_eligible"],
            f"Symmetry eligible mismatch",
        )
        self.assertEqual(
            result["symmetry_policy"]["detected_symmetry_type"],
            expected["detected_symmetry_type"],
            f"Detected symmetry type mismatch",
        )
        self.assertEqual(
            float(result["symmetry_policy"]["reduction_factor"] or 1.0),
            expected["reduction_factor"],
            f"Reduction factor mismatch",
        )

        mesh = result["mesh"]
        self.assertGreaterEqual(
            mesh["vertex_count"],
            expected["mesh_vertex_count_min"],
            f"Vertex count too low",
        )
        self.assertLessEqual(
            mesh["vertex_count"],
            expected["mesh_vertex_count_max"],
            f"Vertex count too high",
        )
        self.assertEqual(
            mesh["surface_tags"],
            expected["surface_tags"],
            f"Surface tags mismatch: {mesh['surface_tags']} != {expected['surface_tags']}",
        )

    def test_all_configs_have_consistent_symmetry_policy(self):
        configs = get_reference_configs()
        self.assertGreater(len(configs), 0, "No reference configs found")

        for config in configs:
            expected = EXPECTED_RESULTS.get(config["name"])
            if expected is None:
                self.skipTest(f"No expected results for {config['name']}")

            result = evaluate_config(config)

            self.assertEqual(
                result["errors"],
                [],
                f"{config['name']}: Unexpected errors: {result['errors']}",
            )

            self.assertEqual(
                result["formula_type"],
                expected["formula_type"],
                f"{config['name']}: Formula type mismatch",
            )

            self.assertEqual(
                result["quadrants"],
                expected["quadrants"],
                f"{config['name']}: Quadrants mismatch",
            )

            self.assertEqual(
                result["has_enclosure"],
                expected["has_enclosure"],
                f"{config['name']}: Enclosure presence mismatch",
            )


if __name__ == "__main__":
    unittest.main()
