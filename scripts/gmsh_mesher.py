#!/usr/bin/env python3
"""
Gmsh Meshing Script for MWG Horn Geometry

This script takes geometric surface definitions and generates high-quality meshes
using Gmsh's CAD kernel and meshing algorithms.

Usage:
    python3 gmsh_mesher.py input.geo output.msh [--element-size 2.0]
"""

import sys
import argparse
import json
import gmsh
import numpy as np


def create_horn_from_profile(profile_points, axis_points, name="horn"):
    """
    Create a horn by sweeping a profile along an axis.
    
    Args:
        profile_points: List of (x, y, z) points defining the profile at each station
        axis_points: List of (x, y, z) points defining the centerline axis
        name: Name for the geometry
    
    Returns:
        Surface tag
    """
    gmsh.model.add(name)
    
    # For now, use simple lofting through cross-sections
    # This is a placeholder - full implementation would use proper sweeping
    
    surfaces = []
    num_stations = len(profile_points)
    
    for i, station_points in enumerate(profile_points):
        # Create wire for this station
        point_tags = []
        for pt in station_points:
            tag = gmsh.model.occ.addPoint(pt[0], pt[1], pt[2])
            point_tags.append(tag)
        
        # Create curve loop
        curve_tags = []
        for j in range(len(point_tags)):
            next_j = (j + 1) % len(point_tags)
            curve_tag = gmsh.model.occ.addLine(point_tags[j], point_tags[next_j])
            curve_tags.append(curve_tag)
        
        loop_tag = gmsh.model.occ.addCurveLoop(curve_tags)
        surface_tag = gmsh.model.occ.addPlaneFilling(loop_tag)
        surfaces.append(surface_tag)
    
    # Loft between surfaces to create volume
    if len(surfaces) > 1:
        # ThruSections creates a lofted volume
        # For now, return the surfaces - full volume lofting would be more complex
        pass
    
    gmsh.model.occ.synchronize()
    return surfaces


def create_box_enclosure(width, height, depth, center, edge_radius=0, name="enclosure"):
    """
    Create a box enclosure with optional filleted edges.
    
    Args:
        width, height, depth: Box dimensions
        center: (x, y, z) center point
        edge_radius: Fillet radius (0 = no fillet)
        name: Name for the geometry
    
    Returns:
        Volume tag
    """
    x, y, z = center
    
    # Create box
    box_tag = gmsh.model.occ.addBox(
        x - width/2, y, z - depth/2,
        width, height, depth
    )
    
    # Apply fillet if requested
    if edge_radius > 0:
        # Get all edges
        edges = gmsh.model.occ.getEntities(1)
        edge_tags = [e[1] for e in edges]
        
        # Fillet edges
        gmsh.model.occ.fillet([3, box_tag], edge_tags, [edge_radius] * len(edge_tags))
    
    gmsh.model.occ.synchronize()
    return box_tag


def mesh_geometry(element_size=2.0, algorithm=6, optimize=True):
    """
    Generate mesh with specified parameters.
    
    Args:
        element_size: Target element size
        algorithm: Meshing algorithm (6=Frontal-Delaunay, 5=Delaunay)
        optimize: Whether to optimize mesh quality
    """
    # Set meshing options
    gmsh.option.setNumber("Mesh.ElementOrder", 1)  # Linear elements
    gmsh.option.setNumber("Mesh.CharacteristicLengthMin", element_size * 0.5)
    gmsh.option.setNumber("Mesh.CharacteristicLengthMax", element_size * 2.0)
    gmsh.option.setNumber("Mesh.Algorithm", algorithm)
    gmsh.option.setNumber("Mesh.Algorithm3D", 4)  # 3D Delaunay
    
    # Generate mesh
    gmsh.model.mesh.generate(2)  # 2D surface mesh
    
    if optimize:
        gmsh.model.mesh.optimize("Netgen")
    
    # Check mesh quality (get all element tags first)
    try:
        elementTags, _ = gmsh.model.mesh.getElementsByType(2)  # Type 2 = triangles
        if len(elementTags) > 0:
            quality = gmsh.model.mesh.getElementQualities(elementTags)
            print(f"Mesh quality - Min: {min(quality):.4f}, Mean: {np.mean(quality):.4f}, Max: {max(quality):.4f}")
    except Exception as e:
        print(f"Note: Could not compute quality metrics: {e}")


def load_geometry_json(filename):
    """Load geometry definition from JSON file."""
    with open(filename, 'r') as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description='Generate mesh using Gmsh')
    parser.add_argument('input', help='Input geometry file (.json or .geo)')
    parser.add_argument('output', help='Output mesh file (.msh, .stl, or .vtk)')
    parser.add_argument('--element-size', type=float, default=2.0,
                       help='Target element size (default: 2.0)')
    parser.add_argument('--algorithm', type=int, default=6,
                       help='Meshing algorithm (default: 6=Frontal-Delaunay)')
    parser.add_argument('--no-optimize', action='store_true',
                       help='Skip mesh optimization')
    parser.add_argument('--gui', action='store_true',
                       help='Show Gmsh GUI')
    
    args = parser.parse_args()
    
    # Initialize Gmsh
    gmsh.initialize()
    
    try:
        # Load geometry
        if args.input.endswith('.json'):
            geom = load_geometry_json(args.input)
            # Create geometry from JSON definition
            # This would parse the JSON and call appropriate creation functions
            print(f"Loading geometry from {args.input}")
        elif args.input.endswith('.geo'):
            # Load Gmsh .geo script
            gmsh.open(args.input)
        elif args.input.endswith('.step') or args.input.endswith('.stp'):
            # Load STEP CAD file
            gmsh.model.occ.importShapes(args.input)
            gmsh.model.occ.synchronize()
        elif args.input.endswith('.stl'):
            # Load STL and remesh
            gmsh.merge(args.input)
            gmsh.model.mesh.classifySurfaces(0, True, True)
            gmsh.model.mesh.createGeometry()
            gmsh.model.occ.synchronize()
        else:
            print(f"Unsupported input format: {args.input}")
            return 1
        
        # Generate mesh
        print(f"Generating mesh with element size {args.element_size}...")
        mesh_geometry(
            element_size=args.element_size,
            algorithm=args.algorithm,
            optimize=not args.no_optimize
        )
        
        # Write output
        print(f"Writing mesh to {args.output}...")
        gmsh.write(args.output)
        
        # Show GUI if requested
        if args.gui:
            gmsh.fltk.run()
        
        print("✓ Meshing complete")
        return 0
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        gmsh.finalize()


if __name__ == '__main__':
    sys.exit(main())
