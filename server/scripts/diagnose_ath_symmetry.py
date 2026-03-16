#!/usr/bin/env python3
"""
Diagnostic script for ATH reference config symmetry behavior.

Captures imported params, canonical mesh topology, and resulting
symmetry_policy / symmetry metadata for each reference config.

Run from server/ directory:
    python3 scripts/diagnose_ath_symmetry.py
    python3 scripts/diagnose_ath_symmetry.py --config osse-simple
    python3 scripts/diagnose_ath_symmetry.py --list
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("BEMPP_DEFAULT_DEVICE_INTERFACE", "numba")

import numpy as np

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
for name in ("bempp", "bempp_cl", "numba", "gmsh", "solver", "opencl"):
    logging.getLogger(name).setLevel(logging.WARNING)


FIXTURES_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "ath"


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
            configs.append({
                "name": cfg_file.stem,
                "path": str(cfg_file),
                "ath": ath,
                "params": params,
            })
        except Exception as e:
            print(f"  WARN: Failed to parse {cfg_file}: {e}")
    
    return configs


def diagnose_config(config: Dict[str, Any], verbose: bool = True) -> Dict[str, Any]:
    """Run diagnostics on a single ATH config."""
    from solver.waveguide_builder import build_waveguide_mesh
    from solver.symmetry import evaluate_symmetry_policy
    from solver.mesh import prepare_mesh
    
    name = config["name"]
    params = config["params"]
    ath = config["ath"]
    
    result: Dict[str, Any] = {
        "name": name,
        "params": {
            "formula_type": params.get("formula_type"),
            "quadrants": params.get("quadrants"),
            "n_angular": params.get("n_angular"),
            "n_length": params.get("n_length"),
            "enc_depth": params.get("enc_depth"),
            "morph_target": params.get("morph_target", 0),
            "gcurve_type": params.get("gcurve_type", 0),
        },
        "mesh": {},
        "symmetry_policy": {},
        "symmetry": {},
        "errors": [],
    }
    
    if verbose:
        print(f"\n{'=' * 70}")
        print(f"CONFIG: {name}")
        print(f"{'=' * 70}")
        print(f"  Formula: {params.get('formula_type')}")
        print(f"  Quadrants: {params.get('quadrants')}")
        print(f"  Grid: {params.get('n_angular')} x {params.get('n_length')}")
        print(f"  Enclosure: {params.get('enc_depth')}")
    
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
            "tag_counts": {
                tag: surface_tags.count(tag)
                for tag in sorted(set(surface_tags))
            } if surface_tags else {},
        }
        
        if verbose:
            print(f"\n  MESH TOPOLOGY:")
            print(f"    Vertices: {n_verts}")
            print(f"    Triangles: {n_tris}")
            print(f"    Surface tags: {sorted(set(surface_tags))}")
            for tag, count in result["mesh"]["tag_counts"].items():
                print(f"      Tag {tag}: {count} tris")
        
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
        symmetry = symmetry_result.get("symmetry", {})
        
        result["symmetry_policy"] = {
            "requested": policy.get("requested"),
            "applied": policy.get("applied"),
            "eligible": policy.get("eligible"),
            "decision": policy.get("decision"),
            "reason": policy.get("reason"),
            "detected_symmetry_type": policy.get("detected_symmetry_type"),
            "detected_symmetry_planes": policy.get("detected_symmetry_planes"),
            "detected_reduction_factor": policy.get("detected_reduction_factor"),
            "reduction_factor": policy.get("reduction_factor"),
            "excitation_centered": policy.get("excitation_centered"),
            "throat_center": policy.get("throat_center"),
            "error": policy.get("error"),
        }
        
        result["symmetry"] = {
            "symmetry_type": symmetry.get("symmetry_type"),
            "reduction_factor": symmetry.get("reduction_factor"),
        }
        
        if verbose:
            print(f"\n  SYMMETRY POLICY:")
            print(f"    Requested: {policy.get('requested')}")
            print(f"    Applied: {policy.get('applied')}")
            print(f"    Eligible: {policy.get('eligible')}")
            print(f"    Decision: {policy.get('decision')}")
            print(f"    Reason: {policy.get('reason')}")
            print(f"    Detected type: {policy.get('detected_symmetry_type')}")
            print(f"    Detected planes: {policy.get('detected_symmetry_planes')}")
            print(f"    Detected reduction: {policy.get('detected_reduction_factor')}")
            print(f"    Actual reduction: {policy.get('reduction_factor')}")
            if policy.get("throat_center"):
                tc = policy.get("throat_center")
                print(f"    Throat center: ({tc[0]:.3f}, {tc[1]:.3f}, {tc[2]:.3f})")
            
            print(f"\n  SYMMETRY METADATA:")
            print(f"    Type: {symmetry.get('symmetry_type')}")
            print(f"    Reduction: {symmetry.get('reduction_factor')}")
    
    except Exception as e:
        result["errors"].append(str(e))
        if verbose:
            print(f"\n  ERROR: {e}")
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Diagnose ATH reference config symmetry")
    parser.add_argument("--config", type=str, help="Run specific config (e.g., 'osse-simple')")
    parser.add_argument("--list", action="store_true", help="List available configs")
    parser.add_argument("--json", type=str, help="Output results to JSON file")
    parser.add_argument("--quiet", action="store_true", help="Suppress detailed output")
    args = parser.parse_args()
    
    configs = get_reference_configs()
    
    if not configs:
        print("No ATH reference configs found in tests/fixtures/ath/")
        sys.exit(1)
    
    if args.list:
        print("Available ATH reference configs:")
        for cfg in configs:
            params = cfg["params"]
            print(f"  {cfg['name']}: {params.get('formula_type')}, q={params.get('quadrants')}")
        sys.exit(0)
    
    if args.config:
        configs = [c for c in configs if c["name"] == args.config]
        if not configs:
            print(f"Config '{args.config}' not found")
            sys.exit(1)
    
    print("=" * 70)
    print("ATH REFERENCE CONFIG SYMMETRY DIAGNOSTIC")
    print("=" * 70)
    
    results = []
    for config in configs:
        result = diagnose_config(config, verbose=not args.quiet)
        results.append(result)
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    for r in results:
        policy = r.get("symmetry_policy", {})
        mesh = r.get("mesh", {})
        status = "OK" if not r.get("errors") else "ERROR"
        applied = "YES" if policy.get("applied") else "NO"
        sym_type = policy.get("detected_symmetry_type") or "?"
        reduction = policy.get("reduction_factor") or 1.0
        verts = mesh.get("vertex_count") or "?"
        tris = mesh.get("triangle_count") or "?"
        print(f"  {r['name']:30s} [{status:5s}] applied={applied:3s} type={sym_type:12s} red={reduction:.1f}x verts={str(verts):>5s} tris={str(tris):>5s}")
    
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nResults written to {args.json}")
    
    sys.exit(0)


if __name__ == "__main__":
    main()
