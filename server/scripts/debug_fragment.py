#!/usr/bin/env python3
"""Minimal reproducer for the gmsh.model.occ.fragment() failure on B-Rep cut."""
import sys, os, traceback, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run_in_subprocess(test_name, code):
    """Run a test in a subprocess to survive SIGABRT crashes."""
    server_dir = os.path.join(os.path.dirname(__file__), "..")
    venv_python = os.path.join(server_dir, "..", ".venv", "bin", "python3")
    full_code = f"""
import sys, os, traceback
sys.path.insert(0, {repr(os.path.abspath(server_dir))})
os.environ.setdefault("BEMPP_DEFAULT_DEVICE_INTERFACE", "numba")
import logging
logging.basicConfig(level=logging.WARNING)
for n in ("bempp", "solver", "gmsh"):
    logging.getLogger(n).setLevel(logging.WARNING)
{code}
"""
    print(f"\n--- {test_name} ---", flush=True)
    result = subprocess.run(
        [venv_python, "-c", full_code],
        capture_output=True, text=True, timeout=60,
    )
    print(result.stdout, end="", flush=True)
    if result.stderr:
        # Filter out the bempp warning
        for line in result.stderr.splitlines():
            if "bempp runtime not available" in line:
                continue
            print(f"  STDERR: {line}", flush=True)
    if result.returncode != 0:
        print(f"  EXIT CODE: {result.returncode}", flush=True)
    return result.returncode == 0


def main():
    print("=" * 60, flush=True)
    print("DEBUG: gmsh fragment() on various geometry types", flush=True)
    print("=" * 60, flush=True)

    # Test 1: Simple sphere fragment
    run_in_subprocess("Test 1: Sphere fragment", """
import gmsh
gmsh.initialize()
gmsh.option.setNumber("General.Terminal", 0)
gmsh.model.add("test_sphere")

sphere = gmsh.model.occ.addSphere(0, 0, 0, 10)
gmsh.model.occ.synchronize()
surfaces_before = gmsh.model.getEntities(2)
print(f"  Surfaces before fragment: {len(surfaces_before)}", flush=True)

p1 = gmsh.model.occ.addPoint(0, -20, -20)
p2 = gmsh.model.occ.addPoint(0,  20, -20)
p3 = gmsh.model.occ.addPoint(0,  20,  20)
p4 = gmsh.model.occ.addPoint(0, -20,  20)
l1 = gmsh.model.occ.addLine(p1, p2)
l2 = gmsh.model.occ.addLine(p2, p3)
l3 = gmsh.model.occ.addLine(p3, p4)
l4 = gmsh.model.occ.addLine(p4, p1)
cl = gmsh.model.occ.addCurveLoop([l1, l2, l3, l4])
cut_surf = gmsh.model.occ.addPlaneSurface([cl])
gmsh.model.occ.synchronize()

surface_dimtags = gmsh.model.getEntities(2)
object_dimtags = [dt for dt in surface_dimtags if dt[1] != cut_surf]
tool_dimtags = [(2, cut_surf)]
print(f"  Fragmenting {len(object_dimtags)} surfaces...", flush=True)

out, out_map = gmsh.model.occ.fragment(object_dimtags, tool_dimtags)
gmsh.model.occ.synchronize()
print(f"  SUCCESS: {len(out)} output entities", flush=True)

# Remove X<0
to_remove = []
for dim, tag in gmsh.model.getEntities(2):
    try:
        com = gmsh.model.occ.getCenterOfMass(dim, tag)
        if com[0] < -1e-8:
            to_remove.append((dim, tag))
    except: pass
if to_remove:
    gmsh.model.occ.remove(to_remove, recursive=True)
    gmsh.model.occ.synchronize()
remaining = gmsh.model.getEntities(2)
print(f"  After removing X<0: {len(remaining)} surfaces", flush=True)
gmsh.finalize()
""")

    # Test 2: Horn geometry — list surfaces BEFORE attempting cut
    run_in_subprocess("Test 2: Horn surfaces (no cut)", """
from solver.waveguide_builder import build_waveguide_mesh
from contracts import WaveguideParamsRequest
params = WaveguideParamsRequest(
    formula_type="R-OSSE", R="60", r="0.4", b="0.2", m="0.85", tmax="1.0",
    r0="12.7", a0="15.5", k="2.0", q="3.4", throat_profile=1,
    throat_ext_angle=0.0, throat_ext_length=0.0, slot_length=0.0, rot=0.0,
    gcurve_type=0, morph_target=0, n_angular=60, n_length=15,
    quadrants=1234, throat_res=8.0, mouth_res=20.0, rear_res=40.0,
    wall_thickness=6.0, enc_depth=0.0, source_shape=2, source_radius=-1.0,
).model_dump()
params["quadrants"] = 1234
import numpy as np
result = build_waveguide_mesh(params, include_canonical=True, symmetry_cut=None)
cm = result["canonical_mesh"]
v = np.array(cm["vertices"]).reshape(-1, 3)
t = np.array(cm["indices"]).reshape(-1, 3)
print(f"  Full mesh: {len(v)} verts, {len(t)} tris", flush=True)
print(f"  X range: [{v[:,0].min():.4f}, {v[:,0].max():.4f}]", flush=True)
n_pos = (v[:,0] > 1e-6).sum()
n_neg = (v[:,0] < -1e-6).sum()
n_zero = np.abs(v[:,0]).sum() <= 1e-6
print(f"  X>0: {n_pos}, X<0: {n_neg}", flush=True)
""")

    # Test 3: Horn geometry — attempt B-Rep cut
    run_in_subprocess("Test 3: Horn with B-Rep symmetry cut", """
from solver.waveguide_builder import build_waveguide_mesh
from contracts import WaveguideParamsRequest
import numpy as np
import logging
logging.getLogger("solver.waveguide_builder").setLevel(logging.DEBUG)
params = WaveguideParamsRequest(
    formula_type="R-OSSE", R="60", r="0.4", b="0.2", m="0.85", tmax="1.0",
    r0="12.7", a0="15.5", k="2.0", q="3.4", throat_profile=1,
    throat_ext_angle=0.0, throat_ext_length=0.0, slot_length=0.0, rot=0.0,
    gcurve_type=0, morph_target=0, n_angular=60, n_length=15,
    quadrants=1234, throat_res=8.0, mouth_res=20.0, rear_res=40.0,
    wall_thickness=6.0, enc_depth=0.0, source_shape=2, source_radius=-1.0,
).model_dump()
params["quadrants"] = 1234
try:
    result = build_waveguide_mesh(params, include_canonical=True, symmetry_cut="yz")
    cm = result["canonical_mesh"]
    v = np.array(cm["vertices"]).reshape(-1, 3)
    t = np.array(cm["indices"]).reshape(-1, 3)
    print(f"  Half mesh: {len(v)} verts, {len(t)} tris", flush=True)
    print(f"  X range: [{v[:,0].min():.4f}, {v[:,0].max():.4f}]", flush=True)
    n_neg = (v[:,0] < -1e-6).sum()
    print(f"  Vertices with X<0: {n_neg}", flush=True)
except Exception as exc:
    print(f"  FAILED: {exc}", flush=True)
    import traceback
    traceback.print_exc()
""")

    # Test 4: Try alternative approach — use gmsh.model.occ.cut() instead of fragment()
    run_in_subprocess("Test 4: Horn with occ.cut() approach (alternative)", """
import gmsh
import numpy as np
from solver.waveguide_builder import build_waveguide_mesh, _apply_symmetry_cut_yz
from contracts import WaveguideParamsRequest

# Build the horn geometry WITHOUT symmetry cut and WITHOUT meshing
# We need to intercept after geometry construction but before meshing
# to try a different cutting approach.

# For now, let's just try the cut() approach on a simpler model
gmsh.initialize()
gmsh.option.setNumber("General.Terminal", 0)
gmsh.model.add("test_cut")

# Create a BSpline surface that spans X<0 to X>0 (like a horn cross-section)
n_phi = 16
n_z = 4
radius = 10.0
pts = []
for iz in range(n_z):
    z = iz * 20.0
    r = radius + iz * 5.0  # expanding radius
    row = []
    for ip in range(n_phi):
        phi = 2 * np.pi * ip / (n_phi - 1)
        x = r * np.cos(phi)
        y = r * np.sin(phi)
        pt = gmsh.model.occ.addPoint(x, y, z)
        row.append(pt)
    pts.append(row)

curves = []
for iz in range(n_z):
    c = gmsh.model.occ.addBSpline(pts[iz])
    curves.append(c)

wires = [gmsh.model.occ.addWire([c]) for c in curves]
thru = gmsh.model.occ.addThruSections(wires, makeSolid=False, makeRuled=False)
gmsh.model.occ.synchronize()

surfs_before = gmsh.model.getEntities(2)
print(f"  BSpline surfaces before cut: {len(surfs_before)}", flush=True)

# Try using a box (3D) for intersection instead of a plane
# Create a large half-space box for X >= 0
bb = gmsh.model.getBoundingBox(-1, -1)
pad = 50
box = gmsh.model.occ.addBox(
    0, bb[1]-pad, bb[2]-pad,        # x0, y0, z0
    bb[3]+pad, (bb[4]-bb[1])+2*pad, (bb[5]-bb[2])+2*pad  # dx, dy, dz
)
gmsh.model.occ.synchronize()

# Get all surfaces EXCEPT the box
all_ents = gmsh.model.getEntities(2)
box_surfs = set()
for dim, tag in gmsh.model.getEntities(2):
    # Box surfaces are the ones we just added
    pass  # hard to distinguish; try fragment approach instead

# Actually, let's try fragment with a VOLUME (box) instead of a surface
all_surfs_before_box = [(d, t) for d, t in surfs_before]
tool_3d = [(3, box)]

print(f"  Trying fragment surfaces against box volume...", flush=True)
try:
    out, out_map = gmsh.model.occ.fragment(all_surfs_before_box, tool_3d)
    gmsh.model.occ.synchronize()
    print(f"  SUCCESS: {len(out)} output entities", flush=True)
    remaining_surfs = gmsh.model.getEntities(2)
    print(f"  Total surfaces after fragment: {len(remaining_surfs)}", flush=True)

    # Remove surfaces with COM at X < 0
    to_remove = []
    for dim, tag in remaining_surfs:
        try:
            com = gmsh.model.occ.getCenterOfMass(dim, tag)
            if com[0] < -1e-8:
                to_remove.append((dim, tag))
        except: pass
    # Also remove box volume
    gmsh.model.occ.remove([(3, box)], recursive=False)
    if to_remove:
        gmsh.model.occ.remove(to_remove, recursive=True)
    gmsh.model.occ.synchronize()

    final = gmsh.model.getEntities(2)
    print(f"  After cleanup: {len(final)} surfaces remaining", flush=True)
except Exception as exc:
    print(f"  FAILED: {exc}", flush=True)
    import traceback
    traceback.print_exc()

gmsh.finalize()
""")


if __name__ == "__main__":
    main()
