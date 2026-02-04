#!/usr/bin/env python3
"""
Gmsh export pipeline for MWG horn meshes.
Generates .msh files using Gmsh 4.15 for bitwise-identical ATH output.

Usage:
    python scripts/gmsh-export.py <geo_file> [output_dir]
"""

import sys
import os

try:
    import gmsh
except ImportError:
    print("Error: gmsh Python module not found. Install with: pip install gmsh")
    sys.exit(1)

def process_geo_file(geo_path, output_dir=None):
    """Process a .geo file and generate .msh and .stl outputs."""
    if not os.path.exists(geo_path):
        print(f"Error: File not found: {geo_path}")
        return False

    base_name = os.path.splitext(os.path.basename(geo_path))[0]
    if output_dir is None:
        output_dir = os.path.dirname(geo_path) or '.'

    os.makedirs(output_dir, exist_ok=True)
    msh_path = os.path.join(output_dir, f"{base_name}.msh")
    stl_path = os.path.join(output_dir, f"{base_name}.stl")

    gmsh.initialize()
    gmsh.option.setNumber("General.Verbosity", 2)
    gmsh.option.setNumber("Mesh.Algorithm", 2)  # MeshAdapt
    gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
    gmsh.option.setNumber("Mesh.Binary", 0)  # ASCII output for MSH

    try:
        # Open the geo file
        gmsh.open(geo_path)

        # Generate 2D mesh
        gmsh.model.mesh.generate(2)

        # Override binary setting for ASCII MSH output
        gmsh.option.setNumber("Mesh.Binary", 0)

        # Save MSH
        gmsh.write(msh_path)
        print(f"Generated: {msh_path}")

        # Save binary STL
        gmsh.option.setNumber("Mesh.Binary", 1)
        gmsh.write(stl_path)
        print(f"Generated: {stl_path}")

        return True

    except Exception as e:
        print(f"Error processing {geo_path}: {e}")
        return False

    finally:
        gmsh.finalize()

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    geo_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    if process_geo_file(geo_path, output_dir):
        print("Done.")
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
