"""Measure ATH LF import parity with one BEAT CPU solver and common sampling.

Run --dry-run first. Solves belong on the compute broker, run by its owner.
The external phase-1 corpus is discovered in a workspace ancestor, or supplied
with --phase1. All generated records and evidence live below --out. No ATH
corpus files are modified. References retain ATH's intact positive-X half,
omit ABEC's virtual interfaces, and reuse WG's imported mesh repair and X-half
solve support. Dry runs never launch Wine or discover a solver.
"""

from __future__ import annotations

import argparse
import asyncio
import configparser
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import qualify_assembly as assembly  # noqa: E402

DEFAULT_CASES = ("b-base", "a-explicit", "b-pair", "mtm-q14", "b-morph")
DEFAULT_FREQS = tuple(float(f) for f in np.geomspace(200, 8000, 12))
DEFAULT_RESOLUTION_MM = 48.
BANDS = (("below_1k", 0, 1000), ("1k_5k", 1000, 5000),
         ("5k_10k", 5000, 10000), ("10k_20k", 10000, 20000.0001))
NORMALISATION = {
    "basis": "unit RMS acceleration, solver exp(-jwt); A unchanged, B divided by -j*2*pi*f",
    "weights": "ABEC Driving_Values Value * Weight and Delay; no crossover, gain matching or time alignment",
    "wg_override": "Harness only: WG member weights use ATH effective weights; omitted ATH DrivingWeight is 0 (rigid). Product importer unchanged.",
    "groups": "HF and all LF radiators separately; combined is their coherent equal-weight sum after ATH weights",
    "secondary": "one real gain per geometry and signal group, aligning lowest-frequency on-axis to 0 dB; no phase alignment",
    "di": "full sphere, 19 theta x 36 phi; same throat origin and distance as the polar arcs",
    "tolerances": "DESIGN.md section 3.1 applied provisionally to different geometries; raw differences retained",
}


class EvidenceRefusal(ValueError):
    """An unsupported or missing reference, with an action for its owner."""


def _finite(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise EvidenceRefusal(f"Non-finite ABEC value {raw!r}. Supply finite reference values.")
    return value


def _blocks(text: str) -> list[tuple[str, str, str, str]]:
    """ABEC sections are unindented headings; comments carry radiator labels."""
    blocks: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        if not line[0].isspace() and not line[0].isdigit():
            match = re.fullmatch(r'(\w+)(?:\s+"([^"]+)")?\s*(?://\s*(.*))?', line.strip())
            if not match:
                raise EvidenceRefusal(f"Unsupported ABEC section {line!r}. Export a plain ABEC reference.")
            blocks.append([match[1], match[2] or "", match[3] or "", ""])
        elif blocks:
            blocks[-1][3] += line.split("//", 1)[0] + "\n"
    return [tuple(block) for block in blocks]


def _values(text: str) -> dict[str, str]:
    return {m[1].lower(): m[2].strip().strip('"') for m in re.finditer(
        r'(\w+)\s*=\s*("[^"]*"|[^\s;]+)', text)}


def parse_project(text: str) -> dict[str, Any]:
    """Resolve the scripts and mesh aliases from Project.abec, never from tags."""
    project = configparser.ConfigParser(interpolation=None)
    project.read_string(text)
    try:
        solving = project["Solving"]["Scriptname_Solving"]
        observations = [v for v in project["Observation"].values() if v.strip()]
        meshes = {}
        for value in project["MeshFiles"].values():
            filename, alias = value.rsplit(",", 1)
            meshes[alias.strip()] = filename.strip()
    except (KeyError, ValueError) as exc:
        raise EvidenceRefusal("Project.abec lacks its scripts or mesh aliases. Export a complete ABEC project.") from exc
    paths = [solving, *observations, *meshes.values()]
    if not solving or not observations or not meshes or any(
        Path(p).name != p or "\\" in p or p in {".", ".."} for p in paths
    ):
        raise EvidenceRefusal("ABEC references must be local filenames. Copy the complete project to one case directory.")
    return {"solving": solving, "observations": observations, "meshes": meshes}


def parse_solving(text: str) -> dict[str, Any]:
    elements, drives, scales = {}, [], {}
    symmetry: list[str] = []
    for kind, name, comment, body in _blocks(text):
        values = _values(body)
        required = {"MeshFile_Properties": ("meshfilealias", "scale"),
                    "Elements": ("meshfilealias", "subdomain"), "Driving": ("drvgroup", "refelements")}.get(kind, ())
        if any(key not in values for key in required):
            raise EvidenceRefusal(f"ABEC {kind} {name!r} lacks required fields. Supply the complete ATH solving script.")
        if kind == "Control_Solver":
            sym = values.get("sym", "").lower()
            if any(axis not in "xy" for axis in sym):
                raise EvidenceRefusal("Unsupported ABEC symmetry. Supply x/y symmetry or a full-domain reference.")
            symmetry = [axis + "0" for axis in dict.fromkeys(sym)]
        elif kind == "MeshFile_Properties":
            scale = values.get("scale", "")
            units = {"1mm": "mm", "1m": "m", "1cm": "cm"}.get(scale.lower())
            if units is None:
                raise EvidenceRefusal(f"Unsupported ABEC mesh scale {scale!r}. Export in mm, cm or m.")
            scales[values["meshfilealias"]] = units
        elif kind == "Elements":
            included = re.findall(r'^\s*\d+\s+Mesh\s+Include\s+(\S+)\s*$', body, re.M)
            if len(included) != 1:
                raise EvidenceRefusal(f"Elements {name} has no single physical group. Supply an ATH ABEC export.")
            elements[name] = {"physical_name": included[0].strip('"'), "alias": values["meshfilealias"],
                              "subdomains": [int(v) for v in values["subdomain"].split(",")]}
        elif kind == "Driving":
            direction = values.get("direction", "normal").lower()
            if direction not in {"normal", "z"}:
                raise EvidenceRefusal(f"Driving {name} uses {direction}. Only normal HF and z-axis LF are supported.")
            label = comment.split()[0] if comment else ""
            if label not in {"horn_driver", "woofer_A", "woofer_B"}:
                raise EvidenceRefusal(f"Driving {name} lacks ATH's radiator label. Retain the ATH comments in solving.txt.")
            drives.append({"name": name, "group": int(values["drvgroup"]), "element": values["refelements"],
                           "radiator": "HF" if label == "horn_driver" else label[-1],
                           "motion": "axial" if direction == "z" else "normal"})
    if not drives or any(d["element"] not in elements for d in drives):
        raise EvidenceRefusal("ABEC has no resolvable driving surfaces. Export solving.txt with its Elements sections.")
    if len({d["group"] for d in drives}) != len(drives):
        raise EvidenceRefusal("ABEC repeats a driving group. Supply distinct radiator groups.")
    return {"elements": elements, "drives": drives, "units": scales, "domain_planes": symmetry}


def parse_observation(text: str, *, read_arcs: bool = True) -> dict[str, Any]:
    """Read acceleration weights and exact ATH polar grids (third value = count)."""
    from server.design.solve_block import _classify_inclination, _parse_range

    weights, arcs = {}, []
    for kind, name, comment, body in _blocks(text):
        values = _values(body)
        if kind == "Driving_Values":
            if values.get("drvtype", "").lower() != "acceleration":
                raise EvidenceRefusal("ABEC excitation is not acceleration. Export DrvType=Acceleration.")
            value = _finite(values.get("value", "1"))
            for line in body.splitlines():
                row = _values(line)
                if "drvgroup" not in row:
                    continue
                delay = re.fullmatch(r'([-+\d.eE]+)(ms|s)', row.get("delay", "0ms"))
                if delay is None:
                    raise EvidenceRefusal("Unsupported ABEC delay. Specify seconds or milliseconds.")
                group = int(row["drvgroup"])
                if group in weights:
                    raise EvidenceRefusal("ABEC repeats excitation values. Supply one value per driving group.")
                weights[group] = {"weight": value * _finite(row.get("weight", "1")),
                                  "delay_ms": _finite(delay[1]) * (1000 if delay[2] == "s" else 1)}
        elif read_arcs and kind == "BE_Spectrum" and values.get("plottype", "").lower() == "polar":
            if values.get("baseplane", "").lower() != "zx":
                raise EvidenceRefusal("Unsupported ABEC polar base plane. Supply BasePlane=zx.")
            raw = values.get("polarrange", "")
            span = _parse_range(raw)
            if span is None or _finite(raw.split(",")[-1]) < 2 or not _finite(raw.split(",")[-1]).is_integer():
                raise EvidenceRefusal("ABEC polar range is missing or invalid. Export a finite start,end,count.")
            start, end, step = span
            count = int(round((end - start) / step)) + 1
            distance = re.fullmatch(r'([-+\d.eE]+)(mm|cm|m)', values.get("distance", ""))
            if distance is None:
                raise EvidenceRefusal("ABEC polar distance lacks units. Export Distance in m, cm or mm.")
            inclination = _finite(values.get("inclination", "0"))
            if inclination not in {0, 90}:
                raise EvidenceRefusal("This ATH polar inclination needs a general plane adapter. Supply 0-degree horizontal or 90-degree vertical arcs.")
            arcs.append({"plane": _classify_inclination(str(inclination)), "inclination": inclination,
                         "angle_range": [start, end, count],
                         "distance_m": _finite(distance[1]) * {"m": 1, "cm": .01, "mm": .001}[distance[2]],
                         "driving_groups": [int(v) for v in values.get("drvgroups", "").split(",") if v],
                         "header": values.get("graphheader", name)})
    return {"weights": weights, "arcs": arcs}


def polar_options(arcs: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Harness observation, independent of ATH's optional source-selective arcs."""
    return {"angle_range": [0, 90, 19], "distance": 2., "norm_angle": 0,
            "observation_origin": "throat", "enabled_axes": ["horizontal", "vertical"],
            "spherical_sampling": True, "spherical_theta_count": 19, "spherical_phi_count": 36,
            "field_plane": False}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def locate_case(phase1: Path, name: str) -> tuple[Path, Path]:
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', name):
        raise EvidenceRefusal("Case names must be simple directory names. Choose a corpus or ath-runs case.")
    corpus = phase1 / "corpus" / name
    run = phase1 / "ath-runs" / name
    cfg = corpus / f"{name}.cfg" if corpus.is_dir() else run / f"{name}.cfg"
    project = corpus / "Project.abec" if corpus.is_dir() else run / "out" / name / "ABEC_FreeStanding" / "Project.abec"
    if not cfg.is_file() or not project.is_file():
        raise EvidenceRefusal(f"{name}: ATH inputs/output missing. Regenerate a separate reference using phase1/ath-tools/probe.py.")
    return cfg, project


def coarse_cfg(text: str, resolution: float) -> str:
    """Change only the copied reference's sampling and declared X symmetry.

    Quadrant 14 avoids ATH's q1234 missing negative-X LF half. Geometry
    parameters and excitation stay intact; the original corpus is read-only.
    WG consumes this very same config through its existing importer.
    """
    text, count = re.subn(r'(?m)^Mesh\.Quadrants\s*=.*$', 'Mesh.Quadrants = 14', text)
    if count != 1:
        raise EvidenceRefusal("Reference needs one Mesh.Quadrants setting. Supply the phase-1 fixture.")
    for key in ("Mesh.ThroatResolution", "Mesh.MouthResolution", "Mesh.RearResolution", "Mesh.InterfaceResolution",
                "FrontResolution", "BackResolution", "Resolution"):
        pattern = rf'(?m)^{re.escape(key)}\s*=.*$'
        text = re.sub(pattern, f'{key} = {resolution:g}', text)
    # The corpus LF blocks contain scalar values only. Insert an absent
    # resolution without interpreting geometry; import remains textcfg's job.
    text = re.sub(r'(LFSource\.[AB]\s*=\s*\{)(?![^}]*\bResolution\s*=)',
                  rf'\1\nResolution = {resolution:g}', text)
    for key in ("Mesh.RearResolution", "Mesh.InterfaceResolution"):
        if not re.search(rf'(?m)^{re.escape(key)}\s*=', text):
            text += f'\n{key} = {resolution:g}\n'
    return text


def regenerate_reference(phase1: Path, name: str, out: Path, resolution: float) -> Path:
    """Run ATH and phase-1's measurement owner, confined to evidence storage."""
    cfg, _ = locate_case(phase1, name)
    copied = coarse_cfg(cfg.read_text(), resolution)
    generated = out / "references"
    folder = generated / "ath-runs" / name
    folder.mkdir(parents=True, exist_ok=True)
    executable = phase1.parents[1] / "261011-ath-core" / "ath" / "ath202608.exe"
    wine = Path.home() / "Applications/Wine Staging.app/Contents/Resources/wine/bin/wine"
    if not executable.is_file() or not wine.is_file():
        raise EvidenceRefusal("ATH/Wine is unavailable. Restore the phase-1 ATH executable and Wine Staging before regenerating references.")
    fingerprint = {"cfg_sha256": hashlib.sha256(copied.encode()).hexdigest(), "ath_sha256": _sha(executable)}
    marker = folder / "generation.json"
    if marker.is_file() and json.loads(marker.read_text()) == fingerprint and (folder / "measured.json").is_file():
        return generated
    prefix = out / "wine-prefix"
    if not prefix.exists():
        shutil.copytree(Path.home() / ".wine", prefix, symlinks=True)
    shutil.copy2(executable, folder / executable.name)
    (folder / f"{name}.cfg").write_text(copied)
    output_root = folder / "out"
    output_root.mkdir(exist_ok=True)
    (folder / "ath.cfg").write_text('OutputRootDir = "Z:' + str(output_root).replace('/', '\\') + '"\n')
    env = dict(os.environ, WINEPREFIX=str(prefix), WINEDLLOVERRIDES="mscoree=d;mshtml=d", WINEDEBUG="-all")
    try:
        with (folder / "ath.log").open("w") as log:
            subprocess.run([str(wine), executable.name, f"{name}.cfg"], cwd=folder, env=env,
                           stdout=log, stderr=subprocess.STDOUT, timeout=100, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = (folder / "ath.log").read_text(errors="replace").strip()
        raise EvidenceRefusal(f"ATH regeneration failed: {detail}. Regenerate the copied cfg where Wine can run, then rerun the harness.") from exc
    # This owner runs Gmsh on the unmodified ATH GEO and measures its circles.
    spec = importlib.util.spec_from_file_location("phase1_measure", phase1 / "ath-tools" / "measure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = generated
    previous = Path.cwd()
    try:
        module.measure(name)
    finally:
        os.chdir(previous)
    marker.write_text(json.dumps(fingerprint, indent=2) + "\n")
    return generated


def weld_reference(mesh: Any) -> tuple[Any, dict[str, Any]]:
    """Reuse the imported-mesh repair owner; never fill missing surfaces."""
    import meshio
    from hornlab_mesher.mesh_repair_ops import WELD_TOLERANCE_MM
    from hornlab_mesher.step_import import postprocess_mesh
    from server.mesh.file_import import FileMesh

    raw = meshio.Mesh(mesh.points, [("triangle", mesh.triangles)], cell_data={"gmsh:physical": [mesh.tags]})
    processed, repair, _topology = postprocess_mesh(raw, [], symmetry_planes=(), tolerance=.005)
    points = processed.points
    faces = processed.cells_dict["triangle"]
    if len(faces) != len(mesh.triangles):
        raise EvidenceRefusal("The imported-mesh repair removed facets. Inspect the ATH reference before admitting it for parity.")
    before = mesh.points[mesh.triangles]
    after = points[faces]
    # Winding may reverse a triangle. Match its corners without interpreting
    # that reordering as geometric movement; repair retains face row order.
    displacement = float(np.linalg.norm(after[:, :, None] - before[:, None, :], axis=3).min(axis=2).max())
    welded_vertices = repair["welded_vertices"]
    return FileMesh(points, faces, mesh.tags, mesh.names), {
        "owner": "hornlab_mesher.step_import.postprocess_mesh (WG mesh-import repair)",
        "tolerance_mm": WELD_TOLERANCE_MM, "removed_vertices": len(mesh.points) - len(points),
        "welded_vertices": welded_vertices,
        "unused_vertices_removed": len(mesh.points) - len(points) - welded_vertices,
        "max_corner_displacement_mm": displacement, "triangles_added_or_removed": 0,
        "repair": repair,
    }


def edge_figure(folder: Path, reference: Mapping[str, Any], after: Any) -> dict[str, Any]:
    """Locate every open edge in mm, with shared topology and classic styling."""
    from server.mesh.surface_topology import surface_patches
    import hornlab_plots as plots
    from hornlab_plots.style import theme_rc_context
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    before = reference["boundary"]
    planes = reference["solving"]["domain_planes"]
    woofers = reference["measurement"]["woofers"]
    edges = surface_patches(before.triangles).rim_edges
    corners = before.points[edges]
    mids = corners.mean(1)
    labels = np.full(len(edges), "horn quadrant seam", dtype=object)
    for woofer in woofers:
        center = np.asarray(woofer["aperture_center_mm"])
        radius = woofer["radius_mm"]
        nearby = np.linalg.norm(mids[:, :2] - center[:2], axis=1) <= radius + 1e-3
        labels[nearby] = "LF half-diaphragm / aperture"
        baffle_seam = (np.abs(mids[:, 0]) < 1e-6) & (np.abs(mids[:, 2] - center[2]) < 1e-6) & ~nearby
        labels[baffle_seam] = "baffle quadrant seam"
    for plane in planes:
        on_plane = (np.abs(corners[:, :, {"x0": 0, "y0": 1}[plane]]) < .005).all(1)
        labels[on_plane] = "declared symmetry rim"
    detail = {}
    colors = plots.get_theme("classic").palette
    groups = sorted(set(labels))
    with theme_rc_context("classic"):
        fig, axes = plt.subplots(2, 3, figsize=(14, 9))
        try:
            for row, mesh in enumerate((before, after)):
                opened = surface_patches(mesh.triangles).rim_edges
                for col, (i, j) in enumerate(((0, 1), (0, 2), (1, 2))):
                    ax = axes[row, col]
                    facets = mesh.points[mesh.triangles]
                    wire = facets[:, [0, 1, 2, 0]][:, :, [i, j]]
                    ax.add_collection(LineCollection(wire, colors="0.8", linewidths=.2))
                    if row == 0:
                        for index, label in enumerate(groups):
                            selected = labels == label
                            ax.add_collection(LineCollection(corners[selected][:, :, [i, j]], colors=colors[index % len(colors)],
                                                             linewidths=1.4, label=f"{label} ({selected.sum()})"))
                    elif len(opened):
                        ax.add_collection(LineCollection(mesh.points[opened][:, :, [i, j]], colors=colors[0], linewidths=1.4))
                    ax.autoscale()
                    ax.set_aspect("equal")
                    ax.set_xlabel("XYZ"[i] + " [mm]")
                    ax.set_ylabel("XYZ"[j] + " [mm]")
                    ax.set_title(f"{'Retained ATH' if row == 0 else 'Prepared ATH'}: {len(opened)} open edges")
            axes[0, 0].legend(fontsize=7)
            fig.suptitle(folder.name + " — ATH boundary after omission of virtual I1-2 interface")
            fig.tight_layout()
            fig.savefig(folder / "ath-open-edges.png", dpi=160)
        finally:
            plt.close(fig)
    for label in groups:
        selected = corners[labels == label]
        detail[label] = {"edges": len(selected), "bounds_mm": [selected.min(axis=(0, 1)).tolist(), selected.max(axis=(0, 1)).tolist()]}
    return {"figure": "ath-open-edges.png", "before": detail,
            "after_open_edges": len(surface_patches(after.triangles).rim_edges)}


def read_reference(phase1: Path, name: str) -> dict[str, Any]:
    cfg, project_path = locate_case(phase1, name)
    project = parse_project(project_path.read_text())
    parent = project_path.parent
    solving = parse_solving((parent / project["solving"]).read_text())
    obs = [parse_observation((parent / p).read_text(), read_arcs=False) for p in project["observations"]]
    weights: dict[int, Any] = {}
    arcs = []
    for item in obs:
        for group, weight in item["weights"].items():
            if group in weights and weights[group] != weight:
                raise EvidenceRefusal("Observation scripts disagree on excitation. Supply one consistent ATH project.")
            weights[group] = weight
        arcs.extend(item["arcs"])
    if len(project["meshes"]) != 1:
        raise EvidenceRefusal("Multiple ABEC mesh files need a subdomain adapter. Supply ATH's single-mesh export.")
    alias, filename = next(iter(project["meshes"].items()))
    if alias not in solving["units"] or any(e["alias"] != alias for e in solving["elements"].values()):
        raise EvidenceRefusal("ABEC mesh aliases do not agree. Retain the matching Project.abec and solving.txt.")
    from server.mesh.file_import import FileMesh, file_mesh_integrity, parse_file_mesh, transform_file_mesh

    mesh_path = parent / filename
    raw = parse_file_mesh(mesh_path.read_text())
    names = {name: tag for tag, name in raw.names.items()}
    missing = [e["physical_name"] for e in solving["elements"].values() if e["physical_name"] not in names]
    if missing:
        raise EvidenceRefusal(f"ABEC physical groups are missing: {missing}. Supply the mesh from this Project.abec.")
    # The interface is an ABEC subdomain coupling surface, not a solid wall.
    interfaces = [names[e["physical_name"]] for e in solving["elements"].values() if len(e["subdomains"]) > 1]
    keep = ~np.isin(raw.tags, interfaces)
    boundary = FileMesh(raw.points, raw.triangles[keep], raw.tags[keep], raw.names)
    drives = []
    for drive in solving["drives"]:
        physical_name = solving["elements"][drive["element"]]["physical_name"]
        if physical_name not in names or drive["group"] not in weights:
            raise EvidenceRefusal(f"Driving {drive['name']} lacks its physical group or weight. Supply the matching ABEC files.")
        drives.append({**drive, **weights[drive["group"]], "physical_name": physical_name, "tag": names[physical_name]})
    # Reuse measure.py's retained measurements, rather than copying its circle
    # extraction or running Gmsh (which rewrites the reference in place).
    expected = parent / "expected.json"
    measured = phase1 / "ath-runs" / name / "measured.json"
    measurement_path = expected if expected.is_file() else measured
    if not measurement_path.is_file():
        raise EvidenceRefusal("No phase-1 measure.py evidence. Run that owner on a separate reference copy first.")
    measured_data = json.loads(measurement_path.read_text())
    if expected.is_file():
        for filename, evidence in measured_data["files"].items():
            if _sha(parent / filename) != evidence["sha256"]:
                raise EvidenceRefusal(f"{name}/{filename} changed since measurement. Remeasure a new reference copy.")
    else:
        extents = np.array([raw.points.min(0), raw.points.max(0)])
        if not np.allclose(extents, measured_data["mesh_extents_mm"], atol=1e-6, rtol=0):
            raise EvidenceRefusal("ATH mesh bounds changed since measure.py. Remeasure a new reference copy.")
    inputs = [cfg, project_path, mesh_path, parent / "bem_mesh.geo", parent / project["solving"],
              *(parent / p for p in project["observations"]), measurement_path]
    transformed, _ = transform_file_mesh(boundary, solving["units"][alias], "+z", "+y")
    return {"cfg": cfg, "boundary": boundary, "raw_triangles": len(raw.triangles), "drives": drives,
            "boundary_integrity": file_mesh_integrity(transformed, solving["domain_planes"]),
            "arcs": arcs, "solving": solving, "measurement": measured_data,
            "provenance": {str(p.relative_to(phase1)): _sha(p) for p in inputs},
            "interface_triangles_omitted": int((~keep).sum())}


def _full_mesh(mesh: Any, planes: Sequence[str]) -> Any:
    """Complete only the symmetry ATH declared, for geometry measurement."""
    from server.mesh.file_import import FileMesh

    points, triangles = mesh.points.copy(), mesh.triangles.copy()
    tags = mesh.tags.copy()
    for plane in planes:
        reflected = points.copy()
        reflected[:, {"x0": 0, "y0": 1}[plane]] *= -1
        triangles = np.concatenate([triangles, triangles[:, [0, 2, 1]] + len(points)])
        points = np.concatenate([points, reflected])
        tags = np.tile(tags, 2)
    return FileMesh(points, triangles, tags, mesh.names)


def positive_x_half(mesh: Any, tolerance: float = 1e-8) -> Any:
    """Select existing +X facets, without clipping or remeshing any triangle.

    The corpus has a conforming x=0 seam. Refuse any other input rather than
    introducing a second symmetry cutter alongside WG's production owner.
    Coordinates and tolerance use the input mesh's units.
    """
    from server.mesh.file_import import FileMesh

    x = mesh.points[mesh.triangles, 0]
    if np.any((x.min(axis=1) < -tolerance) & (x.max(axis=1) > tolerance)):
        raise EvidenceRefusal("A facet crosses x=0. Export a conforming positive-X half mesh before running parity.")
    keep = (x.min(axis=1) >= -tolerance) & (x.max(axis=1) > tolerance)
    if not np.any(keep):
        raise EvidenceRefusal("The positive-X half is empty. Export ATH quadrants 14 with its complete LF diaphragm.")
    used, inverse = np.unique(mesh.triangles[keep], return_inverse=True)
    return FileMesh(mesh.points[used].copy(), inverse.reshape(-1, 3), mesh.tags[keep].copy(), mesh.names)


def x_half_reference(mesh: Any, scale_m: float) -> tuple[Any, dict[str, Any]]:
    """Repair coincident seams and prove the reflected surface closes using WG owners."""
    from server.mesh.file_import import FileMesh, file_mesh_integrity

    mm = FileMesh(mesh.points * (scale_m * 1000), mesh.triangles, mesh.tags, mesh.names)
    half = positive_x_half(mm)
    completed, repair = weld_reference(_full_mesh(half, ["x0"]))
    half = positive_x_half(completed)
    report = file_mesh_integrity(FileMesh(completed.points * .001, completed.triangles,
                                         completed.tags, completed.names), [])
    reduced = file_mesh_integrity(FileMesh(half.points * .001, half.triangles, half.tags, half.names), ["x0"])
    if report["open_edge_count"] or not report["valid"] or not report["orientation_valid"]:
        raise EvidenceRefusal(f"The retained +X half does not close under X reflection ({report['open_edge_count']} open edges). Repair the ATH export before solving.")
    half.points /= scale_m * 1000
    return half, {"kept_half": "+X (x >= 0)", "domain_planes": ["x0"], "repair": repair,
                  "reflected_integrity": report, "half_integrity": reduced}


def lf_half_coverage(half_mm: Any, woofers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Locate even undriven LF surfaces, which ATH puts in its rigid group."""
    corners = half_mm.points[half_mm.triangles]
    coverage = {}
    for woofer in woofers:
        center, radius = np.asarray(woofer["aperture_center_mm"]), woofer["radius_mm"]
        radial = np.linalg.norm(corners[:, :, :2] - center[:2], axis=2)
        selected = ((radial.max(1) <= radius + .001) & (corners[:, :, 2].max(1) <= center[2] + .001)
                    & (corners[:, :, 2].min(1) < center[2] - .001)
                    & (corners[:, :, 2].min(1) >= center[2] - .5 * radius))
        samples = radial[selected]
        cone = bool(len(samples) and samples.max() > .35 * radius + .001)
        cap = bool(len(samples) and samples.min() < .35 * radius - .001)
        if not cone or not cap:
            raise EvidenceRefusal(f"The retained +X half lacks the LF {woofer['source']} cone or dust cap. Supply an intact ATH positive half before solving.")
        coverage[woofer["source"]] = {"triangles": int(selected.sum()), "contains_cone": cone, "contains_dust_cap": cap,
                                      "complete_under_x_reflection": True,
                                      "bounds_mm": [corners[selected].min((0, 1)).tolist(), corners[selected].max((0, 1)).tolist()]}
    return coverage


def _triangle_distances(point: np.ndarray, facets: np.ndarray) -> np.ndarray:
    """Exact point-to-triangle distances (plane projection plus all edges)."""
    a, b, c = facets[:, 0], facets[:, 1], facets[:, 2]
    ab, ac, ap = b - a, c - a, point - a
    n = np.cross(ab, ac)
    n2 = np.einsum("ij,ij->i", n, n)
    height = np.einsum("ij,ij->i", ap, n)
    projected = point - np.divide(height, n2, out=np.zeros_like(height), where=n2 > 0)[:, None] * n
    inside = np.ones(len(facets), dtype=bool)
    edge_distance = np.full(len(facets), np.inf)
    for i in range(3):
        start, end = facets[:, i], facets[:, (i + 1) % 3]
        edge = end - start
        relative = projected - start
        inside &= np.einsum("ij,ij->i", np.cross(edge, relative), n) >= -1e-10
        denominator = np.einsum("ij,ij->i", edge, edge)
        t = np.divide(np.einsum("ij,ij->i", point - start, edge), denominator,
                      out=np.zeros_like(denominator), where=denominator > 0)
        edge_distance = np.minimum(edge_distance, np.linalg.norm(point - start - np.clip(t, 0, 1)[:, None] * edge, axis=1))
    plane_distance = np.divide(np.abs(height), np.sqrt(n2), out=np.full_like(height, np.inf), where=n2 > 0)
    return np.where(inside & (n2 > 0), plane_distance, edge_distance)


def surface_hausdorff(a: Any, b: Any, defect_woofers: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Deterministic sampled lower bound and certified Lipschitz upper bound.

    Every used vertex and triangle centroid is tested against the other
    *surface*, not its vertices. A centroid covers its whole flat triangle
    within the largest centroid-to-corner radius, bounding unsampled maxima.
    """
    from scipy.spatial import cKDTree

    def directed(source: Any, target: Any) -> tuple[dict[str, Any], np.ndarray]:
        facets = target.points[target.triangles]
        centers = facets.mean(1)
        radii = np.linalg.norm(facets - centers[:, None], axis=2).max(1)
        tree = cKDTree(centers)
        src_facets = source.points[source.triangles]
        src_centers = src_facets.mean(1)
        samples = np.concatenate([source.points[np.unique(source.triangles)], src_centers])
        distances = []
        radius = float(radii.max())
        for batch in np.array_split(samples, max(1, math.ceil(len(samples) / 128))):
            initial, _ = tree.query(batch)
            candidates = tree.query_ball_point(batch, initial + radius + 1e-9)
            lengths = np.array([len(indices) for indices in candidates])
            indices = np.concatenate(candidates).astype(int)
            values = _triangle_distances(np.repeat(batch, lengths, axis=0), facets[indices])
            offsets = np.concatenate([[0], np.cumsum(lengths)[:-1]])
            distances.extend(np.minimum.reduceat(values, offsets).tolist())
        centroid_distances = np.asarray(distances[-len(src_centers):])
        upper = np.max(centroid_distances + np.linalg.norm(src_facets - src_centers[:, None], axis=2).max(1))
        values = np.asarray(distances)
        excluded = np.zeros(len(samples), dtype=bool)
        for woofer in defect_woofers:
            center = np.asarray(woofer["aperture_center_mm"])
            radius = woofer["radius_mm"]
            excluded |= ((samples[:, 0] <= 1e-3) & (np.linalg.norm(samples[:, :2] - center[:2], axis=1) <= radius + 1e-3)
                         & (samples[:, 2] <= center[2] + 1e-6) & (samples[:, 2] >= center[2] - radius * .5))
        index = int(values.argmax())
        retained = np.flatnonzero(~excluded)
        kept_max = int(retained[values[retained].argmax()]) if len(retained) else None
        used = np.unique(source.triangles)
        tag = int(source.tags[np.flatnonzero((source.triangles == used[index]).any(1))[0]]) if index < len(used) else int(source.tags[index - len(used)])
        return {"sampled_max_mm": float(values[index]), "sampled_p95_mm": float(np.percentile(values, 95)),
                "sampled_p99_mm": float(np.percentile(values, 99)), "sampled_rms_mm": float(np.sqrt(np.mean(values**2))),
                "max_location_mm": samples[index].tolist(), "max_surface": source.names.get(tag, str(tag)),
                "max_excluding_defect_region_mm": float(values[kept_max]) if kept_max is not None else None,
                "max_excluding_defect_location_mm": samples[kept_max].tolist() if kept_max is not None else None,
                "excluded_samples": int(excluded.sum()), "upper_bound_mm": float(upper), "samples": len(samples)}, values

    (ab, ab_values), (ba, ba_values) = directed(a, b), directed(b, a)
    values = np.concatenate([ab_values, ba_values])
    worst = ab if ab["sampled_max_mm"] >= ba["sampled_max_mm"] else ba
    return {"method": "all used vertices + facet centroids to exact target triangles; Lipschitz whole-facet upper bound",
            "a_to_b": ab, "b_to_a": ba, "lower_bound_mm": max(ab["sampled_max_mm"], ba["sampled_max_mm"]),
            "sampled_p95_mm": float(np.percentile(values, 95)), "sampled_p99_mm": float(np.percentile(values, 99)),
            "sampled_rms_mm": float(np.sqrt(np.mean(values**2))), "max_location_mm": worst["max_location_mm"],
            "max_surface": worst["max_surface"], "max_direction": "A to B" if worst is ab else "B to A",
            "max_excluding_defect_region_mm": max(v["max_excluding_defect_region_mm"] for v in (ab, ba)
                                                  if v["max_excluding_defect_region_mm"] is not None),
            "defect_exclusion": "sample points in negative-X LF aperture footprints including x=0 cut (0.001 mm slack), baffle z - 0.5r through baffle z; target surfaces unchanged",
            "upper_bound_mm": max(ab["upper_bound_mm"], ba["upper_bound_mm"])}


def geometry_parity(reference: Mapping[str, Any], record: Mapping[str, Any], wg_mesh: Any) -> dict[str, Any]:
    from server.mesh.file_import import FileMesh

    units = next(iter(reference["solving"]["units"].values()))
    scale_mm = {"mm": 1, "cm": 10, "m": 1000}[units]
    boundary = reference["boundary"]
    ath_mesh = _full_mesh(FileMesh(boundary.points * scale_mm, boundary.triangles, boundary.tags, boundary.names),
                          reference["solving"]["domain_planes"])
    wg_mm = _full_mesh(FileMesh(wg_mesh.points * 1000, wg_mesh.triangles, wg_mesh.tags, wg_mesh.names), ["x0"])
    a_bounds = np.array([ath_mesh.points[np.unique(ath_mesh.triangles)].min(0), ath_mesh.points[np.unique(ath_mesh.triangles)].max(0)])
    b_bounds = np.array([wg_mm.points.min(0), wg_mm.points.max(0)])
    # Phase-1 proves outer mesh bounds are the enclosure bounds for this corpus.
    # Do not extend that assumption to arbitrary geometry.
    woofers = []
    baffle_z = float(b_bounds[1, 2])
    imported = {w["ath"]["key"]: w for w in record["_qualification_woofers"]}
    radiators = {r["radiator_id"]: r for r in record["radiators"]}
    for woofer in reference["measurement"]["woofers"]:
        target = radiators[imported[woofer["source"]]["id"]]
        a_center = np.asarray(woofer["aperture_center_mm"])
        b_center = np.array([*target["center_mm"], baffle_z])
        woofers.append({"source": woofer["source"], "a_center_mm": a_center.tolist(), "b_center_mm": b_center.tolist(),
                        "center_delta_mm": (b_center - a_center).tolist(), "a_radius_mm": woofer["radius_mm"],
                        "b_radius_mm": target["radius_mm"], "radius_delta_mm": target["radius_mm"] - woofer["radius_mm"]})
    return {"measurement_owner": "phase1/ath-tools/measure.py (retained measured.json/expected.json)",
            "woofers": woofers, "enclosure_bounds_a_mm": a_bounds.tolist(), "enclosure_bounds_b_mm": b_bounds.tolist(),
            "enclosure_dimensions_delta_mm": (np.ptp(b_bounds, axis=0) - np.ptp(a_bounds, axis=0)).tolist(),
            "enclosure_bounds_delta_mm": (b_bounds - a_bounds).tolist(),
            "surface_hausdorff": surface_hausdorff(ath_mesh, wg_mm, reference["measurement"]["woofers"])}


def metric_differences(freqs: Sequence[float], angles: Sequence[float], a: np.ndarray, b: np.ndarray,
                       di_a: Sequence[float], di_b: Sequence[float]) -> dict[str, Any]:
    """Absolute SPL/phase errors, with deep-null exclusion and raw band totals.

    Pressure shape is (frequency, plane, angle). Each curve's maximum is
    taken across frequency; both curves must be within 20 dB for gating.
    Raw magnitude maxima/RMS include the deep nulls and are always reported.
    """
    f, angles = np.asarray(freqs), np.asarray(angles)
    a, b = np.asarray(a), np.asarray(b)
    shape = (len(f), 2, len(angles))
    if a.shape != shape or b.shape != shape or not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
        raise ValueError("pressure arrays must be finite and share (frequency, 2 planes, angle) shape")
    if np.asarray(di_a).shape != f.shape or np.asarray(di_b).shape != f.shape or not np.isfinite([di_a, di_b]).all():
        raise ValueError("DI must be finite at every frequency")
    zero = np.flatnonzero(np.isclose(angles, 0, atol=1e-8))
    if not len(zero):
        raise ValueError("the polar grid has no on-axis observation")
    on = int(zero[0])
    levels = [20 * np.log10(np.maximum(np.abs(p), 1e-20) / 20e-6) for p in (a, b)]
    eligible = ((levels[0] >= levels[0].max(axis=0, keepdims=True) - 20)
                & (levels[1] >= levels[1].max(axis=0, keepdims=True) - 20)
                & (np.abs(a) > 1e-20) & (np.abs(b) > 1e-20))
    delta = levels[1] - levels[0]
    phase = np.angle(b * np.conj(a), deg=True)
    di = np.asarray(di_b) - np.asarray(di_a)
    polar = (angles >= 0) & (angles <= 90)

    def stats(values: np.ndarray) -> dict[str, Any]:
        values = np.asarray(values).reshape(-1)
        return {"count": int(values.size), "max_abs": float(np.abs(values).max()) if values.size else None,
                "rms": float(np.sqrt(np.mean(values ** 2))) if values.size else None}

    quantities = {"on_axis_spl_db": (delta[:, 0, on], eligible[:, 0, on]),
                  "horizontal_spl_db": (delta[:, 0, polar], eligible[:, 0, polar]),
                  "vertical_spl_db": (delta[:, 1, polar], eligible[:, 1, polar]),
                  "phase_deg": (phase[:, :, polar], eligible[:, :, polar]),
                  "di_db": (di, np.ones(len(f), dtype=bool))}
    bands = {}
    for label, low, high in BANDS:
        selected = (f >= low) & (f < high)
        entries = {}
        for quantity, (values, mask) in quantities.items():
            values, mask = values[selected], mask[selected]
            tolerance = 5 if quantity == "phase_deg" else .3 if quantity == "di_db" else 1 if low >= 10000 else .5
            checked = stats(values[mask])
            entries[quantity] = {"raw": stats(values), "eligible": checked, "deep_null": stats(values[~mask]),
                                 "tolerance": tolerance, "pass": checked["max_abs"] <= tolerance if checked["count"] else None}
        bands[label] = entries
    verdicts = [entry["pass"] for band in bands.values() for entry in band.values() if entry["pass"] is not None]
    acoustic_samples = bool(eligible[:, :, polar].any())
    return {"bands": bands, "pass": all(verdicts) if verdicts and acoustic_samples else None,
            "spl_delta_db": delta.tolist(), "phase_delta_deg": phase.tolist(), "di_delta_db": di.tolist(),
            "eligible_mask": eligible.tolist()}


def _make_setup(record: Mapping[str, Any], channels: list[dict[str, Any]], freqs: Sequence[float],
                polar: Mapping[str, Any]) -> dict[str, Any]:
    from server.solver.imported import imported_record_symmetry
    # Prepared imports require full polar coverage. Solve a common full circle
    # and select the harness nodes afterwards, without interpolation.
    start, end, count = polar["angle_range"]
    step = (end - start) / (count - 1)
    intervals = 360 / step
    if not math.isclose(intervals, round(intervals), abs_tol=1e-8):
        raise EvidenceRefusal("Observation step does not divide a full circle. Choose a compatible harness grid.")
    effective = {**polar, "angle_range": [-180, 180, int(round(intervals)) + 1],
                 "enabled_axes": ["horizontal", "vertical", "diagonal"]}
    geometry = {"drive_channels": channels}
    if record.get("producer") == "design-assembly":
        geometry["required_features"] = [assembly.ASSEMBLY_RECORD_FEATURE]
    return {"schema_version": 1, "geometry": geometry,
            "options": {"engine": "beat-cpu", "symmetry": imported_record_symmetry(record).mode,
                        "frequencies_hz": list(freqs), "polar_config": effective}}


def _cpu_estimate(count: int, frequencies: int, channels: int) -> dict[str, Any]:
    return {"kind": "awaiting b-base/B CPU calibration", "triangles": count,
            "frequencies": frequencies, "channels": channels}


def calibrated_estimates(results: Mapping[str, Any], calibration: Mapping[str, Any]) -> None:
    """Conservative serial A+B estimate; expose scaling and contention margin."""
    for entry in results.values():
        estimates = entry.get("estimated_beat_cpu_time", {})
        for estimate in estimates.values():
            seconds = (calibration["seconds_per_frequency_per_channel"] * estimate["frequencies"]
                       * estimate["channels"] * (estimate["triangles"] / calibration["triangles"]) ** 2.5)
            estimate.update(kind="scaled from one b-base/B CPU frequency, including its startup",
                            minutes=round(seconds / 60, 2), contention_minutes=round(2 * seconds / 60, 2))
        if set(estimates) == {"A", "B"}:
            minutes = sum(item["contention_minutes"] for item in estimates.values())
            entry["estimated_pair_minutes"] = round(minutes, 2)
            entry["broker_budget"] = {"under_15_minutes": minutes < 15,
                                      "assumption": "serial A+B, N^2.5, linear frequencies/channels, 2x shared-CPU margin; estimate only"}


def _build_x_half_sync(dump: dict[str, Any], work_dir: str, *, cancel_cb: Any = None) -> dict[str, Any]:
    """Use the existing native assembly builder and its normal admission guards."""
    from server.design.schema import DesignConfig
    from server.mesh.builder import assembly_mesher_config, _build_design_artifact

    design = DesignConfig.model_validate(dump)
    config, placement = assembly_mesher_config(design)
    if any(row["x_mm"] != 0 for row in placement["woofers"]):
        raise EvidenceRefusal("An LF radiator is off x=0. Centre every radiator before using this symmetric parity fixture.")
    config["mesh"]["quadrants"] = 14
    built = _build_design_artifact(design, config, cancel_cb, assembly=True, work_dir=work_dir)
    return {**built, "placement": placement}


def build_x_half(dump: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    from server.mesh.child import run_mesh_build

    work_dir = data_dir / "parity-half-build"
    work_dir.mkdir(parents=True, exist_ok=True)
    return asyncio.run(run_mesh_build(_build_x_half_sync, dump, str(work_dir)))


def prepare_case(name: str, phase1: Path, data_dir: Path, store: Any, freqs: Sequence[float],
                 maximum: int, revision: int, resolution: float | None = None,
                 out: Path | None = None, side: str = "both",
                 regenerate: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    from server.design.textcfg import parse
    from server.jobs.models_api import MeshRegistration, register_mesh
    from server.mesh.file_import import FileMesh, file_mesh_integrity, mesh_text, parse_file_mesh

    reference = read_reference(phase1, name)
    result: dict[str, Any] = {"case": name, "status": "geometry-only", "issues": [], "provenance": reference["provenance"],
                              "ath_drives": reference["drives"], "ath_arcs": reference["arcs"],
                              "ath_raw_triangles": reference["raw_triangles"],
                              "ath_boundary_triangles": len(reference["boundary"].triangles),
                              "interface_triangles_omitted": reference["interface_triangles_omitted"],
                              "normalisation": NORMALISATION,
                              "ath_boundary_integrity": reference["boundary_integrity"]}
    result["estimated_beat_cpu_time"] = {"A": _cpu_estimate(len(reference["boundary"].triangles), len(freqs), len(reference["drives"]))}
    try:
        parsed = parse(reference["cfg"].read_text())
    except ValueError as exc:
        result.update(status="not-importable", issues=[f"WG config import refused: {exc}"])
        return result, {}
    original = reference
    regeneration_failed = False
    if resolution is not None:
        if out is None:
            raise ValueError("regeneration requires an evidence output directory")
        if side != "B" and regenerate:
            try:
                generated = regenerate_reference(phase1, name, out, resolution)
                reference = read_reference(generated, name)
                result["regenerated_provenance"] = reference["provenance"]
            except EvidenceRefusal as exc:
                result["issues"].append(str(exc))
                regeneration_failed = True
        elif side != "B":
            result["issues"].append("A uses retained ATH sampling; matched-resolution parity needs coordinator regeneration. Dry run did not launch Wine.")
            regeneration_failed = True
        copied = coarse_cfg(original["cfg"].read_text(), resolution)
        folder = out / name
        folder.mkdir(exist_ok=True)
        (folder / "harness.cfg").write_text(copied)
        parsed = parse(copied)
        result["resolution_mm"] = resolution
    result["ath_original_boundary_integrity"] = original["boundary_integrity"]
    scale_m = {"mm": .001, "cm": .01, "m": 1.}[next(iter(reference["solving"]["units"].values()))]
    half, closure = x_half_reference(_full_mesh(reference["boundary"], reference["solving"]["domain_planes"]), scale_m)
    reference = {**reference, "boundary": half, "solving": {**reference["solving"], "domain_planes": ["x0"]}}
    result["ath_x_half"] = closure
    result["ath_weld"] = closure["repair"]
    result["ath_boundary_triangles"] = len(half.triangles)
    result["ath_boundary_integrity"] = closure["half_integrity"]
    result["symmetry"] = {"A": "half_yz", "B": "half_yz"}
    result["ath_x_half"]["lf_diaphragms"] = lf_half_coverage(
        FileMesh(half.points * scale_m * 1000, half.triangles, half.tags, half.names), reference["measurement"]["woofers"])
    result["ath_reference_regenerated"] = resolution is not None and side != "B" and regenerate and not regeneration_failed
    if out is not None:
        folder = out / name
        folder.mkdir(exist_ok=True)
        result["open_edges"] = edge_figure(folder, original, weld_reference(_full_mesh(half, ["x0"]))[0])
    dump = parsed.design.model_dump(mode="json")
    result["product_imported_signal_groups"] = copy.deepcopy(dump["assembly"]["signal_groups"])
    result["effective_weights"] = match_ath_weights(dump, reference["drives"])
    result["parity_signal_groups"] = dump["assembly"]["signal_groups"]
    result["import_notes"] = parsed.migration_names
    # Mesh B even if A is invalid: geometric differences remain useful evidence.
    from server.cadlink.intake.design_assembly import _radiators
    from server.design.schema import DesignConfig
    from server.integration.mesher_identity import mesher_identity

    built = build_x_half(dump, data_dir)
    mesh_b = parse_file_mesh(built["msh_text"])
    mesh_b, result["wg_x_half"] = x_half_reference(mesh_b, 1.)
    result["wg_triangles"] = len(mesh_b.triangles)
    result["wg_boundary_integrity"] = file_mesh_integrity(mesh_b, ["x0"])
    result["wg_mesh_sha256"] = hashlib.sha256(built["msh_text"].encode()).hexdigest()
    result["wg_mesher_identity"] = mesher_identity()
    b_source_tags = built["metadata"]["source_tags"]
    b_radiators = _radiators(DesignConfig.model_validate(dump), built, list(b_source_tags))
    measurement_record = {"radiators": b_radiators, "_qualification_woofers": dump["assembly"]["woofers"]}
    result["geometry"] = geometry_parity(reference, measurement_record, mesh_b)
    result["estimated_beat_cpu_time"] = {"A": _cpu_estimate(len(reference["boundary"].triangles), len(freqs), len(reference["drives"])),
                                          "B": _cpu_estimate(len(mesh_b.triangles), len(freqs), len(reference["drives"]))}
    for estimate in result["estimated_beat_cpu_time"].values():
        estimate["channels"] = sum(d["weight"] != 0 for d in reference["drives"])
    selected_counts = ([len(half.triangles)] if side == "A" else [len(mesh_b.triangles)] if side == "B"
                       else [len(half.triangles), len(mesh_b.triangles)])
    if max(selected_counts) > maximum:
        result["issues"].append(f"Triangle budget {maximum} exceeded. Increase --max-triangles deliberately or choose a smaller reference.")
        result["status"] = "refused"
        return result, {}
    roles = {drive["tag"]: "HF" if drive["radiator"] == "HF" else "LF" for drive in reference["drives"]}
    record_a = None
    try:
        if side != "B":
            record_a = register_mesh(store, data_dir, MeshRegistration.model_validate({
                "filename": f"{name}-ath-boundary.msh", "msh_text": mesh_text(reference["boundary"]),
                "units": next(iter(reference["solving"]["units"].values())), "forward": "+z", "up": "+y",
                "roles": roles, "domain_planes": ["x0"], "reflect_planes": [], "auto_cut": False}))
            result["ath_prepared_triangles"] = record_a["mesh"]["stats"]["triangle_count"]
            result["ath_prepared_mesh_sha256"] = _sha(Path(record_a["mesh_store_path"]))
    except ValueError as exc:
        result["issues"].append(f"ATH file import refused: {exc}")
    polar = None
    try:
        polar = polar_options(reference["arcs"])
        result["observations"] = {"polar": polar, "extensions": [plane for plane in polar["enabled_axes"]
                                  if plane not in {arc["plane"] for arc in reference["arcs"]}],
                                  "origin_m": [0, 0, 0]}
    except EvidenceRefusal as exc:
        result["issues"].append(str(exc))
    by_key = {w["ath"]["key"]: w["id"] for w in dump["assembly"]["woofers"]}
    b_ids = {"HF": assembly.HF_RADIATOR_ID, **by_key}
    record_b = None
    if side != "A":
        # The existing design mesher owns all geometry and identities. Admit
        # its native half through the same imported half path as A. Existing
        # full-domain design records are never changed.
        b_roles = {b_source_tags[s]: "HF" if s == assembly.HF_RADIATOR_ID else "LF" for s in b_source_tags}
        record_b = register_mesh(store, data_dir, MeshRegistration.model_validate({
            "filename": f"{name}-wg-x-half.msh", "msh_text": mesh_text(mesh_b), "units": "m",
            "forward": "+z", "up": "+y", "roles": b_roles,
            "domain_planes": ["x0"], "reflect_planes": [], "auto_cut": False}))
        result["wg_prepared_triangles"] = record_b["mesh"]["stats"]["triangle_count"]
        result["wg_prepared_mesh_sha256"] = _sha(Path(record_b["mesh_store_path"]))
    plans = {}
    if polar is not None:
        active_ids = {b_ids[d["radiator"]] for d in reference["drives"] if d["weight"] != 0}
        for variant, record in (("A", record_a), ("B", record_b)):
            if record is None or side not in {"both", variant}:
                continue
            if variant == "A":
                channels = [{"id": d["radiator"], "source_ids": [f"group_{d['tag']}"], "motion": d["motion"]}
                            for d in reference["drives"] if d["weight"] != 0]
            else:
                channels = [{"id": e["radiator_id"], "source_ids": [f"group_{b_source_tags[s]}" for s in e["source_ids"]], "motion": e["motion"]}
                            for e in b_radiators if e["radiator_id"] in active_ids]
            setup = _make_setup(record, channels, freqs, polar)
            request = assembly.compose(record, setup, client_request_id=f"ath-parity-{name}-{variant}")
            if out is not None:
                _write_json(out / name / f"{variant}.request.json", request.model_dump(mode="json"))
                _write_json(out / name / f"{variant}.record.json", record)
                (out / name / f"{variant}.msh").write_text(assembly.compiled_mesh_text(record))
            plans[variant] = {"requests": {"beat-cpu": {"request": request, "notice": None, "transducer_channels": []}},
                              "radiator_ids": {d["radiator"]: d["radiator"] if variant == "A" else b_ids[d["radiator"]]
                                               for d in reference["drives"]}, "drives": reference["drives"],
                              "variant": variant, "polar": polar}
        if not plans:
            result["status"] = "refused"
            return result, plans
        result["observations"]["effective_polar"] = next(iter(plans.values()))["requests"]["beat-cpu"]["request"].options.polar_config.model_dump(mode="json")
        result["observations"]["coverage_note"] = "Both geometries use the pipeline's common full-circle superset; metrics select 0–90 degrees every 5 degrees, 2 m from the throat. ATH arcs are metadata only."
        result["status"] = "ready" if side == "B" or record_a is not None and not regeneration_failed else "refused"
    else:
        result["status"] = "refused"
    return result, plans


def match_ath_weights(dump: dict[str, Any], drives: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Qualification override only; zero channels are omitted so they stay rigid."""
    by_key = {w["ath"]["key"]: w["id"] for w in dump["assembly"]["woofers"]}
    ids = {"HF": assembly.HF_RADIATOR_ID, **by_key}
    effective = {key: next((float(d["weight"]) for d in drives if d["radiator"] == key), 0.) for key in ids}
    by_id = {ids[key]: value for key, value in effective.items()}
    delays = {ids[d["radiator"]]: d["delay_ms"] for d in drives}
    for woofer in dump["assembly"]["woofers"]:
        woofer["drive"] = "rigid" if by_id[woofer["id"]] == 0 else "prescribed"
    for group in dump["assembly"]["signal_groups"]:
        for member in group["members"]:
            weight = by_id[member["radiator_id"]]
            member.update(weight=abs(weight), invert=weight < 0, delay_ms=delays.get(member["radiator_id"], 0.))
    return effective


def grouped_bases(payload: bytes, plan: Mapping[str, Any]) -> dict[str, Any]:
    from server.solver.combine import combine_drive_channels, deserialize_channel_bases
    from server.solver.imported_channels import scale_channel_result

    unpacked = deserialize_channel_bases(payload)
    bases = copy.deepcopy(unpacked["results_by_id"])
    if plan["variant"] == "B":
        for basis in bases.values():
            scale_channel_result(basis, 1 / (-2j * np.pi * np.asarray(basis.frequencies_hz)))
    ids = plan["radiator_ids"]
    processing = {ids[d["radiator"]]: {"weight": d["weight"], "delay_ms": d["delay_ms"]} for d in plan["drives"]}
    groups = {}
    for group in ("HF", "LF", "combined"):
        members = [ids[d["radiator"]] for d in plan["drives"]
                   if group == "combined" or (d["radiator"] == "HF") == (group == "HF")]
        if not members or all(processing[m]["weight"] == 0 for m in members):
            groups[group] = {"status": "not-applicable", "reason": "ATH has no driven radiator in this signal group"}
            continue
        synthetic, _ = combine_drive_channels(
            bases, members=[group], member_channels={group: members}, member_processing=processing,
            level_match=False, align=False, channels={group: {"gain": {"mode": "manual", "db": 0}, "delay": {"mode": "manual", "ms": 0}, "invert": False}})
        import hornlab_plots

        sphere = synthetic.sphere_pressure_complex
        if sphere is None:
            raise ValueError("the solve did not retain its DI sphere")
        power = hornlab_plots.sphere_power_metrics(sphere, synthetic.sphere_theta_deg, synthetic.sphere_phi_deg,
                                                   distance_m=plan["polar"]["distance"])
        planes = list(synthetic.observation_planes)
        pressure = np.asarray(synthetic.pressure_complex)[:, [planes.index("horizontal"), planes.index("vertical")], :]
        start, end, count = plan["polar"]["angle_range"]
        requested = np.linspace(start, end, count)
        actual = np.asarray(synthetic.observation_angles_deg)
        selected = [int(np.argmin(abs(actual - angle))) for angle in requested]
        if not np.allclose(actual[selected], requested, rtol=0, atol=1e-8):
            raise ValueError("the solve grid does not contain the exact ATH observation angles")
        pressure = pressure[:, :, selected]
        groups[group] = {"status": "complete", "frequencies_hz": np.asarray(synthetic.frequencies_hz).tolist(),
                         "angles_deg": requested.tolist(),
                         "pressure_real": pressure.real.tolist(), "pressure_imag": pressure.imag.tolist(),
                         "di_db": np.asarray(power["directivity_index_db"]).tolist()}
    return groups


def compare_groups(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    comparisons = {}
    for group in ("HF", "LF", "combined"):
        left, right = a[group], b[group]
        if left["status"] != "complete" or right["status"] != "complete":
            comparisons[group] = {"status": "not-applicable", "reason": left.get("reason") or right.get("reason")}
            continue
        if left["frequencies_hz"] != right["frequencies_hz"] or left["angles_deg"] != right["angles_deg"]:
            raise ValueError("A and B observations differ")
        pa = np.asarray(left["pressure_real"]) + 1j * np.asarray(left["pressure_imag"])
        pb = np.asarray(right["pressure_real"]) + 1j * np.asarray(right["pressure_imag"])
        on = int(np.argmin(np.abs(left["angles_deg"])))
        levels = [abs(p[0, 0, on]) for p in (pa, pb)]
        if min(levels) <= 1e-20:
            raise ValueError("lowest-frequency on-axis reference is silent; cannot normalise")
        comparisons[group] = {"status": "complete", "absolute": metric_differences(
            left["frequencies_hz"], left["angles_deg"], pa, pb, left["di_db"], right["di_db"]),
            "lowest_frequency_aligned": metric_differences(left["frequencies_hz"], left["angles_deg"],
                pa * 20e-6 / levels[0], pb * 20e-6 / levels[1], left["di_db"], right["di_db"]),
            "lowest_frequency_b_minus_a_db": float(20 * np.log10(levels[1] / levels[0]))}
    return comparisons


def charts(folder: Path, a: Mapping[str, Any], b: Mapping[str, Any], comparison: Mapping[str, Any]) -> list[str]:
    import hornlab_plots as plots

    files = []
    for group, entry in comparison.items():
        if entry["status"] != "complete":
            continue
        left, right = a[group], b[group]
        f, angles = left["frequencies_hz"], left["angles_deg"]
        on = int(np.argmin(np.abs(angles)))
        levels = [20 * np.log10(np.maximum(np.hypot(data["pressure_real"], data["pressure_imag"]), 1e-20) / 20e-6)
                  for data in (left, right)]
        filename = f"{group}-on-axis.png"
        plots.save_frequency_response_plot(folder / filename, [plots.FrequencyResponseCurve(f, level[:, 0, on], label)
            for level, label in zip(levels, ("A: ATH geometry", "B: WG geometry"))],
            title=f"{folder.name} / {group}: unit acceleration, ATH weights", theme="classic")
        files.append(filename)
        curves = []
        for index, plane in enumerate(("horizontal", "vertical")):
            delta = np.asarray(entry["absolute"]["spl_delta_db"])[:, index, :]
            for label, values in (("A", levels[0][:, index]), ("B", levels[1][:, index]), ("B-minus-A", delta)):
                filename = f"{group}-{plane}-{label}.png"
                span = (-max(1, float(np.abs(delta).max())), max(1, float(np.abs(delta).max()))) if label == "B-minus-A" else (
                    min(float(level.min()) for level in levels), max(float(level.max()) for level in levels))
                _heatmap(folder / filename, f, angles, values.T, span,
                         f"{folder.name} / {group} / {plane} / {label} (absolute dB)")
                files.append(filename)
            curves.append(plots.FrequencyResponseCurve(f, np.max(np.abs(delta), axis=1), f"{plane} max |SPL error|"))
        phase = np.asarray(entry["absolute"]["phase_delta_deg"])
        phase_curves = [plots.FrequencyResponseCurve(f, np.max(np.abs(phase[:, index, :]), axis=1),
                                                     f"{plane} max |phase error|")
                        for index, plane in enumerate(("horizontal", "vertical"))]
        phase_file = f"{group}-phase-difference.png"
        plots.save_frequency_response_plot(folder / phase_file, phase_curves, title=f"{folder.name} / {group}: raw phase differences",
                                            ylabel="Absolute phase difference [deg]", theme="classic", span_db=10)
        files.append(phase_file)
        curves.append(plots.FrequencyResponseCurve(f, np.abs(entry["absolute"]["di_delta_db"]), "|DI error|"))
        filename = f"{group}-difference-vs-frequency.png"
        plots.save_frequency_response_plot(folder / filename, curves, title=f"{folder.name} / {group}: differences",
                                            ylabel="Absolute difference [dB]", theme="classic", span_db=10)
        files.append(filename)
    return files


def _heatmap(path: Path, freqs: Any, angles: Any, values: Any, span: tuple[float, float], title: str) -> None:
    """Canonical renderer, with an absolute/signed colour range and no clipping.

    The installed renderer predates its configurable display_range_db option.
    Its public Axes primitive preserves raw samples; changing the resulting
    QuadMesh limits also updates its existing colourbar. Theme owns all style.
    """
    import hornlab_plots as plots
    from hornlab_plots.style import theme_rc_context
    import matplotlib.pyplot as plt

    low, high = span
    if high <= low:
        low, high = low - .5, high + .5
    with theme_rc_context("classic"):
        fig, ax = plt.subplots(figsize=(11, 5))
        try:
            fig.patch.set_facecolor(plots.get_theme("classic").figure_bg)
            plots.render_single_heatmap(ax, np.asarray(freqs), np.asarray(angles), np.asarray(values), title,
                                        reference_level=0., theme="classic")
            ax.collections[0].set_clim(low, high)
            fig.tight_layout()
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        finally:
            plt.close(fig)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _phase1_default() -> Path | None:
    for ancestor in ROOT.parents:
        candidate = ancestor / "scratchpads" / "261021-assembly-redesign" / "phase1"
        if candidate.is_dir():
            return candidate
    return None


def coordinator_commands(args: Any, cases: Sequence[str]) -> list[str]:
    """Exact solve argv for the broker owner; printing never prepares a runtime."""
    out = args.out.resolve()
    base = [sys.executable, "scripts/qualify_ath_lf_parity.py", "--phase1", str(args.phase1.resolve()),
            "--resolution-mm", str(args.resolution_mm), "--max-triangles", str(args.max_triangles)]
    commands = [shlex.join([*base, "--case", "b-base", "--side", "B", "--calibrate", "--out", str(out / "calibration")])]
    for name in cases:
        commands.append(shlex.join([*base, "--case", name, "--side", "both", "--freqs",
                                   *[str(f) for f in args.command_freqs],
                                   "--calibration", str(out / "calibration" / "calibration.json"),
                                   "--out", str(out / name)]))
    return commands


def dry_run_table(results: Mapping[str, Any]) -> str:
    rows = ["A uses retained ATH sampling in a sandbox dry run; B uses the requested resolution.",
            "Open edges below are for each reflected closed surface; half-domain symmetry rims remain open by design.",
            "", "| Case | A triangles | B triangles | A open edges | B open edges | Symmetry | Status |",
            "|---|---:|---:|---:|---:|---|---|"]
    for name, entry in results.items():
        counts = [entry.get(key, {}).get("reflected_integrity", {}).get("open_edge_count", "unavailable")
                  for key in ("ath_x_half", "wg_x_half")]
        rows.append(f"| {name} | {entry.get('ath_boundary_triangles', 'unavailable')} | {entry.get('wg_triangles', 'unavailable')} | {counts[0]} | {counts[1]} | x=0, half_yz | {entry['status']} |")
    return "\n".join(rows) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", nargs="+", default=list(DEFAULT_CASES), help="case names, space or comma separated")
    parser.add_argument("--case", help="one case for a coordinator broker job; overrides --cases")
    parser.add_argument("--side", choices=["A", "B", "both"], default="both", help="solve one side and retain its bases, or compare both")
    parser.add_argument("--freqs", nargs="+", help="Hz, space or comma separated; default 12 log-spaced points, 200–8000 Hz")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--engine", choices=["beat-cpu"], default="beat-cpu")
    parser.add_argument("--max-triangles", type=int, default=8000)
    parser.add_argument("--resolution-mm", type=float, default=DEFAULT_RESOLUTION_MM, help="same copied-cfg horn, enclosure and LF characteristic length for A and B; existing mesher chord-error floors still apply")
    parser.add_argument("--retained-reference", action="store_true", help="diagnose retained meshes without ATH regeneration; never a coarse parity solve")
    parser.add_argument("--calibrate", "--calibrate-only", dest="calibrate_only", action="store_true", help="exactly one b-base/B CPU solve at 1000 Hz, at most 115 seconds; no ATH regeneration")
    parser.add_argument("--calibration", type=Path, help="previous calibration.json used for dry-run planning")
    parser.add_argument("--dry-run", action="store_true", help="import and mesh only; never discover or start a solver")
    parser.add_argument("--print-commands", action="store_true", help="print one calibration and one both-side command per selected case, then exit; broker owner submits them")
    parser.add_argument("--phase1", type=Path, default=_phase1_default(), help="external phase-1 evidence directory")
    parser.add_argument("--timeout", type=float, default=3600, help="per-geometry solve timeout in seconds")
    args = parser.parse_args(argv)
    if args.case:
        args.cases = [args.case]
    if args.calibrate_only:
        if args.dry_run:
            parser.error("--calibrate-only cannot be combined with --dry-run")
        if args.case not in {None, "b-base"} or args.side == "A":
            parser.error("--calibrate requires --case b-base --side B (or their defaults)")
        args.cases, args.freqs, args.timeout, args.side = ["b-base"], ["1000"], min(args.timeout, 115.), "B"
    cases = [part for value in args.cases for part in value.split(",")]
    try:
        freqs = [_finite(part) for value in args.freqs for part in value.split(",")] if args.freqs else list(DEFAULT_FREQS)
    except ValueError as exc:
        parser.error(str(exc))
    if not freqs or freqs[0] <= 0 or any(b <= a for a, b in zip(freqs, freqs[1:])):
        parser.error("--freqs must be positive, finite, strictly increasing Hz")
    if args.max_triangles < 1 or args.timeout <= 0 or not math.isfinite(args.timeout):
        parser.error("--max-triangles and --timeout must be positive")
    if args.resolution_mm <= 0 or not math.isfinite(args.resolution_mm):
        parser.error("--resolution-mm must be positive and finite")
    if args.retained_reference and not args.dry_run:
        parser.error("--retained-reference requires --dry-run")
    if args.phase1 is None:
        parser.error("phase-1 evidence not found; supply --phase1")
    if any(not re.fullmatch(r'[a-zA-Z0-9_-]+', name) for name in cases):
        parser.error("--cases must be simple directory names")
    if len(cases) != len(set(cases)):
        parser.error("--cases must not repeat a case")
    # Never place scratch artifacts in the source checkout by accident.
    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents:
        parser.error("--out must be outside this worktree")
    if args.print_commands:
        args.command_freqs = freqs
        print("# Coordinator: submit each following command as a separate compute-broker job.")
        print("# Run calibration first; use its measured estimates to reserve each case below 15 minutes.")
        print("\n".join(coordinator_commands(args, cases)))
        return 0
    out.mkdir(parents=True, exist_ok=True)
    from server.platform.temp_session import TemporarySession

    session = TemporarySession.create(base=out)
    session.activate()
    try:
        return _run(args, cases, freqs, out, session.path)
    finally:
        session.close(remove=True)


def _run(args: Any, cases: list[str], freqs: list[float], out: Path, session_path: Path) -> int:
    from server.cadlink.store import CadLinkStore

    data_dir = session_path / "data"
    store = CadLinkStore.for_data_dir(data_dir)
    store.initialize()
    results, plans = {}, {}
    try:
        for revision, name in enumerate(cases, 1):
            print(f"{name}: importing and compiling", flush=True)
            started = time.monotonic()
            try:
                entry, plan = prepare_case(name, args.phase1, data_dir, store, freqs, args.max_triangles, revision,
                                           args.resolution_mm, out, args.side,
                                           not args.dry_run and not args.retained_reference)
            except (EvidenceRefusal, ValueError, OSError) as exc:
                entry, plan = {"case": name, "status": "refused", "issues": [str(exc)]}, {}
            entry["geometry_wall_seconds"] = round(time.monotonic() - started, 2)
            results[name] = entry
            for variant, item in plan.items():
                plans[f"{name}/{variant}"] = item
            folder = out / name
            folder.mkdir(exist_ok=True)
            _write_json(folder / "case.json", entry)
            print(f"{name}: {entry['status']}; A={entry.get('ath_boundary_triangles', '?')} B={entry.get('wg_triangles', '?')} triangles", flush=True)
    finally:
        store.close()
    calibration = json.loads(args.calibration.read_text()) if args.calibration else None
    calibration_success = False
    if args.calibrate_only:
        calibration = None
        if "b-base/B" not in plans:
            results["b-base"]["issues"].append("B calibration could not be prepared; no solve started.")
        else:
            selected = {"b-base/B": plans["b-base/B"]}
            solved = asyncio.run(assembly._solve(data_dir, {"cases": selected}, [args.engine], args.timeout))
            outcome = solved["b-base/B/beat-cpu"]
            results["b-base"]["calibration_solve"] = outcome
            if outcome["status"] == "complete" and not outcome["findings"]:
                channels = len(selected["b-base/B"]["requests"]["beat-cpu"]["request"].geometry.drive_channels)
                calibration = {"case": "b-base/B", "engine": "beat-cpu", "frequency_hz": 1000.,
                               "symmetry": "half_yz",
                               "channels": channels, "triangles": results["b-base"]["wg_triangles"],
                               "wall_seconds": outcome["wall_seconds"],
                               "seconds_per_frequency_per_channel": outcome["wall_seconds"] / channels,
                               "resolution_mm": args.resolution_mm}
                _write_json(out / "calibration.json", calibration)
                calibration_success = True
            else:
                reason = outcome.get("reason") or "; ".join(outcome["findings"]) or outcome["status"]
                results["b-base"]["issues"].append(f"CPU calibration unavailable: {reason}. Prepare the matching BEAT CPU runtime before retrying; no calibration value was invented.")
    elif not args.dry_run:
        # Refuse the whole batch before the first solve when any reference is
        # invalid or over budget. The coordinator can select the ready subset.
        if all(entry["status"] == "ready" for entry in results.values()):
            if calibration is None:
                raise EvidenceRefusal("CPU calibration is missing. Run --case b-base --side B --calibrate once, then supply --calibration.")
            if calibration.get("symmetry") != "half_yz" or calibration.get("resolution_mm") != args.resolution_mm:
                raise EvidenceRefusal("CPU calibration uses another domain or resolution. Recalibrate the X-half mesh at the requested --resolution-mm.")
            calibrated_estimates(results, calibration)
            sides = ("A", "B") if args.side == "both" else (args.side,)
            if any(sum(entry["estimated_beat_cpu_time"][v]["contention_minutes"] for v in sides) >= 15 for entry in results.values()):
                raise EvidenceRefusal("Estimated selected solve exceeds 15 minutes. Coarsen --resolution-mm and prepare both sides again.")
            async def collect(runtime: Any, job_id: str, response: Mapping[str, Any], request: Any) -> Any:
                plan = next(p for p in plans.values() if p["requests"]["beat-cpu"]["request"] is request)
                payload = await asyncio.to_thread(runtime.store.get_channel_bases, job_id)
                if payload is None:
                    raise ValueError("completed solve did not retain its complex channel bases")
                case = next(name.split("/")[0] for name, p in plans.items() if p is plan)
                (out / case / f"{plan['variant']}.channel-bases.bin").write_bytes(payload)
                return grouped_bases(payload, plan)

            solved = asyncio.run(assembly._solve(data_dir, {"cases": plans}, [args.engine], args.timeout, on_result=collect))
            for name, entry in results.items():
                outcomes = {variant: solved[f"{name}/{variant}/{args.engine}"] for variant in sides}
                entry["solves"] = outcomes
                complete = all(o["status"] == "complete" and not o["findings"] for o in outcomes.values())
                if complete and args.side != "both":
                    entry["status"] = "side-complete"
                elif complete:
                    left, right = (outcomes[v]["measurement"] for v in ("A", "B"))
                    entry["metrics"] = compare_groups(left, right)
                    entry["charts"] = charts(out / name, left, right, entry["metrics"])
                    verdicts = [m["absolute"]["pass"] for m in entry["metrics"].values() if m["status"] == "complete"]
                    entry["status"] = "pass" if verdicts and all(v is True for v in verdicts) else "fail"
                else:
                    entry["status"] = "failed-solve"
                _write_json(out / name / "case.json", entry)
    if calibration is not None:
        calibrated_estimates(results, calibration)
    for name, entry in results.items():
        _write_json(out / name / "case.json", entry)
    refused = any(entry["status"] in {"not-importable", "refused", "failed-solve", "fail"} for entry in results.values())
    if args.calibrate_only:
        refused = not calibration_success
    summary = {"schema": "ath-lf-parity-v1", "dry_run": args.dry_run, "engine": args.engine, "side": args.side,
               "frequencies_hz": freqs, "max_triangles": args.max_triangles, "normalisation": NORMALISATION,
               "resolution_mm": args.resolution_mm, "calibration": calibration,
               "status": "incomplete" if refused else "calibration-complete" if args.calibrate_only else "geometry-only" if args.dry_run else "side-complete" if args.side != "both" else "pass",
               "cases": results}
    _write_json(out / "summary.json", summary)
    if args.dry_run:
        table = dry_run_table(results)
        (out / "dry-run.md").write_text(table)
        print(table, flush=True)
    print(f"{summary['status']}: {len(results)} cases; summary.json written", flush=True)
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
