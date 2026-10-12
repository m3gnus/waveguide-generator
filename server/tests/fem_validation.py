"""F11 closed-form references and synthetic CAD returns; all formula inputs are SI.

These are qualification fixtures, never a substitute solver. Geometry is in mm
only at the CAD boundary. Evidence is external and opt-in.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

import numpy as np

from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.cadlink.intake import ingest
from server.cadlink.joblane.default_setup import default_setup
from server.cadlink.joblane.setup import solve_request_for
from server.cadlink.store import CadLinkStore
from server.tests.test_cadlink_wgreturn import _manifest, write_bundle
from server.tests.test_fem_coupling import _coupling_bundle
from server.tests.test_fem_regions import _face, _region

RHO, C = 1.21, 343.0
PORT_RADIUS_M = .002
PORT_LENGTH_M = .030
CHAMBER_VOLUME_M3 = .040 ** 3


def sealed_compliance_pressure(rho, c, area_m2, displacement_m, volume_m3):
    """Pressure amplitude [Pa] = rho c² Sd x / V.

    Uniform, lossless sealed air, rigid walls, small piston displacement [m],
    well below the chamber's first mode; rho [kg/m³], c [m/s], Sd [m²], V [m³].
    """
    return rho * c**2 * area_m2 * displacement_m / volume_m3


def monopole_pressure(rho, frequency_hz, volume_velocity_m3_s, distance_m):
    """Far-field pressure amplitude [Pa] = rho omega |Q| / (4 pi r).

    Compact source in unbounded air, no ground/baffle images, r [m] much larger
    than the source, prescribed volume velocity Q [m³/s], frequency [Hz].
    """
    return rho * 2 * math.pi * frequency_hz * abs(volume_velocity_m3_s) / (4 * math.pi * distance_m)


def helmholtz_frequency(c, area_m2, volume_m3, length_m, inner_end_m, outer_end_m):
    """Lossless tuning [Hz] = c/(2 pi) sqrt(S / (V L_eff)).

    Compact rigid chamber V [m³], narrow circular neck S [m²], physical neck
    L [m]. L_eff = L + inner_end + outer_end [m]. The fixture uses 0.85a at
    the chamber junction (flanged) and 0.61a at the free protruding pipe end
    (unflanged), radius a [m]. No viscothermal losses or diaphragm compliance.
    """
    return c / (2 * math.pi) * math.sqrt(area_m2 / (volume_m3 * (length_m + inner_end_m + outer_end_m)))


def tube_input_impedance(rho, c, area_m2, length_m, frequency_hz, load_pa_s_m3=None):
    """Acoustic input impedance p/Q [Pa s/m³], Q positive INTO a rigid tube.

    Lossless plane wave, exp(-i omega t), below the first transverse mode:
    Zin = Zc (ZL - i Zc tan(kL)) / (Zc - i ZL tan(kL)), Zc = rho c / S.
    The default load is matched (plane-wave termination), giving Zin = Zc
    for every length/frequency. A rigid end is ZL -> infinity: dividing the
    numerator and denominator by ZL gives +i Zc cot(kL). The opposite sign
    belongs to exp(+i omega t), not the stored solver pressure convention.
    """
    zc = rho * c / area_m2
    zl = zc if load_pa_s_m3 is None else load_pa_s_m3
    tangent = math.tan(2 * math.pi * frequency_hz * length_m / c)
    if np.isinf(zl):
        return 1j * zc / tangent
    return zc * (zl - 1j * zc * tangent) / (zc - 1j * zl * tangent)


def tube_pressure_ratio(c, length_m, frequency_hz):
    """Matched p(outlet)/p(inlet) = exp(+i kL) for exp(-i omega t).

    The transfer matrix implicit in tube_input_impedance is
    p(0) = cos(kL) p(L) - i Zc sin(kL) Q(L). With p(L)=Zc Q(L),
    p(0)=exp(-i kL) p(L), hence the positive spatial sign for forward flow.
    """
    return np.exp(1j * 2 * math.pi * frequency_hz * length_m / c)


def surface_centroid(field, region_index, group_id):
    """Area-weighted centroid [m] of the actual sidecar opening triangles."""
    names = field[f"surface_names_{region_index}"].tolist()
    index, = [i for i, name in enumerate(names) if f"|group={group_id}|" in name]
    triangles = field[f"surface_triangles_{region_index}_{index}"]
    vertices = field[f"points_mm_{region_index}"][triangles] * .001
    areas = np.linalg.norm(np.cross(vertices[:, 1] - vertices[:, 0],
                                    vertices[:, 2] - vertices[:, 0]), axis=1) / 2
    return np.sum(vertices.mean(axis=1) * areas[:, None], axis=0) / areas.sum()


def surface_average(field, region_index, group_id):
    """Exact area-weighted integral of complex P1 pressure over a tagged face."""
    names = field[f"surface_names_{region_index}"].tolist()
    index, = [i for i, name in enumerate(names) if f"|group={group_id}|" in name]
    triangles = field[f"surface_triangles_{region_index}_{index}"]
    vertices = field[f"points_mm_{region_index}"][triangles] * .001
    areas = np.linalg.norm(np.cross(vertices[:, 1] - vertices[:, 0],
                                    vertices[:, 2] - vertices[:, 0]), axis=1) / 2
    offset = sum(len(field[f"points_mm_{i}"]) for i in range(region_index))
    pressure = field["pressure"][..., offset + triangles].mean(axis=-1)
    return np.sum(pressure * areas, axis=-1) / areas.sum()


def resonance_from_compliance(frequencies_hz, pressure_per_acceleration):
    """Infer tuning from a sign change of inverse chamber compliance.

    With unit acceleration, x = -1/omega². A driven, lossless Helmholtz
    chamber has p/x proportional to 1/(1 - fH²/f²), so Re(x/p) is affine in
    1/f². Linear interpolation in 1/f² avoids peak-bin and linewidth bias.
    The bracket must actually cross zero; a missing resonance fails.
    """
    frequencies = np.asarray(frequencies_hz, dtype=float)
    compliance = np.real(-1 / ((2 * math.pi * frequencies)**2 * np.asarray(pressure_per_acceleration)))
    crossings = np.flatnonzero(compliance[:-1] * compliance[1:] < 0)
    if len(crossings) != 1:
        raise ValueError(f"Expected one Helmholtz compliance crossing, found {len(crossings)}")
    i = crossings[0]
    x0, x1 = 1 / frequencies[i:i + 2]**2
    y0, y1 = compliance[i:i + 2]
    return float(1 / math.sqrt(x0 - y0 * (x1 - x0) / (y1 - y0)))


def stiffness_from_chamber_pressure(frequencies_hz, pressure_per_acceleration, piston_area_m2):
    """Infer K [Pa/m³] independently of tuning from inverse compliance's intercept.

    Positive drive acceleration is OUT of the air, so displaced volume INTO
    the chamber is Sd/omega². For p = K Sd/omega² / (1 - fH²/f²),
    Re(Sd/(omega² p)) = (1 - fH²/f²)/K. Its intercept at 1/f²=0 is 1/K.
    A constant pressure/drive scaling error changes K even if tuning is right.
    """
    frequencies = np.asarray(frequencies_hz, dtype=float)
    compliance = np.real(piston_area_m2 / (
        (2 * math.pi * frequencies)**2 * np.asarray(pressure_per_acceleration)))
    _, intercept = np.polyfit(1 / frequencies**2, compliance, 1)
    return float(1 / intercept)


def resonance_from_admittance(frequencies_hz, admittance):
    """Interpolate Im(Q/p)=0 across a bracket of a closed-tube pressure pole.

    For exp(-i omega t), Z=i Zc cot(kL), Y=-i tan(kL)/Zc. The first
    nonzero pressure resonance is kL=pi (c/(2L)); kL=pi/2 is an impedance
    zero and an admittance pole. Supply a local bracket of the former only.
    """
    frequencies = np.asarray(frequencies_hz, dtype=float)
    reactive = np.imag(np.asarray(admittance))
    crossings = np.flatnonzero(reactive[:-1] * reactive[1:] < 0)
    if len(crossings) != 1:
        raise ValueError(f"Expected one closed-tube admittance crossing, found {len(crossings)}")
    i = crossings[0]
    f0, f1 = frequencies[i:i + 2]
    y0, y1 = reactive[i:i + 2]
    return float(f0 - y0 * (f1 - f0) / (y1 - y0))


def comparison_evidence(computed, reference, tolerance):
    """JSON-safe complex values and relative errors for one qualification check."""
    computed, reference = np.asarray(computed), np.asarray(reference)
    errors = np.abs(computed - reference) / np.abs(reference)

    def values(array):
        if np.iscomplexobj(array):
            return {"real": array.real.tolist(), "imag": array.imag.tolist()}
        return array.tolist()

    return {"computed": values(computed), "reference": values(reference),
               "relative_error": errors.tolist(), "tolerance": tolerance,
               "passed": bool(np.all(np.isfinite(errors)) and np.all(errors <= tolerance))}


def write_evidence(case, frequencies, computed, reference, tolerance, record, **details):
    """Write errors before assertions; every additional check contributes to passed."""
    destination = os.environ.get("WG2_FEM_VALIDATION_DIR")
    if not destination:
        return
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2], text=True).strip()
    payload = {"case": case, "frequencies_hz": list(frequencies),
               **comparison_evidence(computed, reference, tolerance),
               "mesh_stats": {"regions": {r["region_id"]: r["stats"] for r in record["fem_regions"]},
                              "coupled_exterior": record.get("coupled_exterior", {}).get("stats")},
               "git_sha": sha, **details}
    payload["passed"] = payload["passed"] and all(
        check["passed"] for check in details.get("checks", {}).values())
    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{case}.json").write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")


def _write_geometry(root, case):
    """OCC air and actual material walls; no exterior source paint."""
    root.mkdir(parents=True)
    neck_length = PORT_LENGTH_M * 1000

    def build():
        import gmsh

        gmsh.model.add(f"f11-{case}")
        try:
            if case == "ported":
                chamber = gmsh.model.occ.addBox(0, 0, 0, 40, 40, 40)
                neck = gmsh.model.occ.addCylinder(20, 20, 40, 0, 0, neck_length, 2)
                gmsh.model.occ.fuse([(3, chamber)], [(3, neck)])
            else:
                w, h, length = (40, 30, 20) if case == "sealed" else (10, 10, 100)
                gmsh.model.occ.addBox(0, 0, 0, w, h, length)
            gmsh.model.occ.synchronize()
            gmsh.write(str(root / "air.step"))
        finally:
            gmsh.model.remove()

        gmsh.model.add(f"f11-{case}-walls")
        try:
            if case == "ported":
                chamber = gmsh.model.occ.addBox(0, 0, 0, 40, 40, 40)
                # Extend the cutter beyond the outlet to keep the pipe open.
                neck = gmsh.model.occ.addCylinder(20, 20, 40, 0, 0, neck_length + 1, 2)
                air, _ = gmsh.model.occ.fuse([(3, chamber)], [(3, neck)])
                outer = gmsh.model.occ.addBox(-1, -1, -1, 42, 42, 42)
                sleeve = gmsh.model.occ.addCylinder(20, 20, 40, 0, 0, neck_length, 3)
                outer, _ = gmsh.model.occ.fuse([(3, outer)], [(3, sleeve)])
            else:
                w, h, length = (40, 30, 20) if case == "sealed" else (10, 10, 100)
                air = [(3, gmsh.model.occ.addBox(0, 0, 0, w, h, length))]
                outer = [(3, gmsh.model.occ.addBox(-1, -1, -1, w + 2, h + 2, length + 2))]
            gmsh.model.occ.cut(outer, air)
            gmsh.model.occ.synchronize()
            gmsh.write(str(root / "material.step"))
        finally:
            gmsh.model.remove()

    _run_in_gmsh_session(build)
    return (root / "material.step").read_bytes(), (root / "air.step").read_bytes()


def validation_bundle(root, case):
    if case == "open":
        bundle, manifest, region = _coupling_bundle(root)
        region["mesh_size_mm"] = 6.
        for group in region["surface_groups"]:
            group["mesh_size_mm"] = 6.
        (bundle / "wgreturn.json").write_text(json.dumps(manifest))
        return bundle, manifest
    assembly, air = _write_geometry(root, case)
    if case == "sealed":
        region = _region(mesh_size_mm=6.)
        region["surface_groups"] = region["surface_groups"][:1]
        region["surface_groups"][0]["mesh_size_mm"] = 6.
        bounds = [[-1, -1, -1], [41, 31, 21]]
    else:
        ported = case == "ported"
        width = 40 if ported else 10
        outlet_z = 40 + PORT_LENGTH_M * 1000 if ported else 100
        area = math.pi * 2**2 if ported else 100
        low, high = ((18, 18, outlet_z), (22, 22, outlet_z)) if ported else ((0, 0, 100), (10, 10, 100))
        size = .75 if ported else 2.5 if case == "closed-tube" else 5.
        region = _region(mesh_size_mm=10. if ported else size)
        region["surface_groups"] = [
            {"group_id": "cone", "name": "Diaphragm", "kind": "moving", "drive_channel_id": "drive-lf",
             "mesh_size_mm": 10. if ported else size,
             "faces": [_face(width**2, (width / 2, width / 2, 0), [0, 0, 1], (0, 0, 0), (width, width, 0))]},
            {"group_id": "port", "name": "Outlet", "kind": (
                "interface" if ported else "rigid" if case == "closed-tube" else "plane-wave-termination"),
             "mesh_size_mm": size,
             "faces": [_face(area, (width / 2, width / 2, outlet_z), [0, 0, 1], low, high)]},
        ]
        if ported:
            # Resolve circular area independently of the coarse chamber mesh:
            # a seven-edge circular port would bias tuning by about 7%.
            region["surface_groups"].append({
                "group_id": "neck", "name": "Neck wall", "kind": "rigid", "mesh_size_mm": .75,
                "faces": [_face(2 * math.pi * 2 * (outlet_z - 40), (20, 20, (40 + outlet_z) / 2), None,
                                (18, 18, 40), (22, 22, outlet_z))]})
        bounds = [[-1, -1, -1], [width + 1, width + 1, outlet_z if ported else 101]]
    manifest = _manifest(assembly)
    manifest["instances"] = []
    manifest["sources"] = []
    manifest["coordinate_system"]["solver_anchor_instance_id"] = None
    manifest["assembly"]["bbox_mm"] = bounds
    manifest["scope"]["included"] = [{"object_id": "walls", "name": "Rigid walls", "body_kind": "solid",
                                         "visible": True, "external_reference": "local"}]
    manifest["scope"]["fem_regions"] = [region]
    manifest["required_features"].append("fem-regions-v1")
    manifest["files"][region["file"]] = {"sha256": "sha256:" + hashlib.sha256(air).hexdigest(),
        "size_bytes": len(air), "media_type": "model/step", "purpose": "fem-region"}
    bundle = write_bundle(root, manifest, step=assembly)
    path = bundle / region["file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(air)
    return bundle, manifest


def ingest_validation_bundle(bundle, root, case):
    """Real isolated intake; the thin port sleeve needs a finer exterior mesh."""
    store = CadLinkStore(root / "cadlink.db")
    try:
        return ingest.ingest_bundle(bundle, {
            "rigid_size_mm": 3. if case == "ported" else 12., "transition_mm": 12.,
            "source_size_mm": {},
        }, [], store, root / "data", defer_viewport=True)
    finally:
        store.close()


def validation_request(record, manifest, frequencies):
    """Bind the actual ingested identity through the CAD joblane setup API."""
    setup = default_setup(manifest, {"engine": "beat-cpu"})
    setup.geometry["mesh"] = dict(record["mesh_sizes"])
    setup.options.pop("frequency_range", None)
    setup.options.pop("num_frequencies", None)
    setup.options.update(frequencies_hz=list(frequencies), symmetry="full", stage_delay_ms=0,
                         polar_config={"angle_range": [-180., 180., 5], "norm_angle": 0.,
                                       "distance": 2., "enabled_axes": ["horizontal", "vertical", "diagonal"],
                                       "spherical_sampling": False, "field_plane": False})
    return solve_request_for(setup, ingest_id=record["ingest_id"],
        manifest_sha256=record["manifest_sha256"], artifact_sha256=record["artifact_sha256"],
        acknowledged_findings=[f["id"] for f in record["findings"] if not f.get("blocking")],
        fem_region_ids=[r["region_id"] for r in record["fem_regions"]])
