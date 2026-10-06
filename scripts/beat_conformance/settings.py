"""Separate engine diagnostic observations from declared comparison inputs."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .recorder import record_sha256

# Aliases are wire names of the two public diagnostic formats.
DIAGNOSTIC_FIELDS = {
    "backend": ("bem_backend", "backend"),
    "precision": ("precision",),
    "time_convention": ("phasor_convention",),
    "symmetry_native": ("symmetry",),
    "blas_threads": ("blas_threads",),
    "quadrature.regular_quadrature_mode": ("regular_quadrature_mode",),
    "quadrature.regular_quadrature_order": ("regular_quadrature_order",),
    "quadrature.quadrature_order": ("regular_quadrature_base_order",),
    "quadrature.singular_order": ("singular_order",),
    "quadrature.wavelength_mesh_stat": ("regular_quadrature_wavelength_mesh_stat",),
    "quadrature.wavelength_kh_q1_max": ("regular_quadrature_wavelength_kh_q1_max",),
    "quadrature.wavelength_kh_q2_max": ("regular_quadrature_wavelength_kh_q2_max",),
}


def flatten(values: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat = {}
    for key, value in values.items():
        name = prefix + key
        if isinstance(value, dict) and name != "mesh.tag_areas_m2":
            flat.update(flatten(value, name + "."))
        else:
            flat[name] = value
    return flat


def observed_settings(declared: dict, native: Any, *, official: bool,
                      native_symmetry: str) -> tuple[dict, dict]:
    """Read every diagnostic row; never promote a config echo to observed usage."""
    values = flatten(declared)
    values["symmetry_native"] = native_symmetry
    evidence = {key: {"status": "declared", "value": value} for key, value in values.items()}
    rows = getattr(native, "solver_log", [])
    diagnostics = [row.get("native_diagnostics") or {} for row in rows]
    if len(rows) != len(native.frequencies_hz) or any(not d for d in diagnostics):
        raise ValueError("Agreement requires per-frequency native diagnostics")

    def observe(name, value, source):
        if name in values:
            from .agreement import settings_equal
            if not settings_equal(name, values[name], value):
                raise ValueError(f"Observed {name} differs from declared inputs")
        values[name] = value
        evidence[name] = {"status": "observed", "value": value, "source": source}

    for name, aliases in DIAGNOSTIC_FIELDS.items():
        samples = [next((d[a] for a in aliases if a in d), None) for d in diagnostics]
        if all(v is not None for v in samples):
            # Quadrature selection can change with frequency; preserve the entire axis.
            value = samples if name == "quadrature.regular_quadrature_order" else samples[0]
            if name != "quadrature.regular_quadrature_order" and any(v != value for v in samples):
                raise ValueError(f"Native {name} changes during the sweep")
            observe(name, value, "solver_log.native_diagnostics")
    for name in ("observation_angles_deg", "observation_planes"):
        value = getattr(native, name)
        if official:
            # The official adapter copies these from WG's request layout: the
            # value takes part in the comparison but is a declared echo.
            values[name] = value
            evidence[name] = {"status": "declared", "value": value, "source": "WG request layout echo"}
        else:
            observe(name, value, f"native.{name}")
    if official:
        samples = [d.get("engine_provenance", {}).get("runtime", {}).get("julia_threads") for d in diagnostics]
        if all(v is not None for v in samples):
            if any(v != samples[0] for v in samples):
                raise ValueError("Native threads change during the sweep")
            observe("threads", samples[0], "engine_provenance.runtime.julia_threads")
    mesh = getattr(native, "mesh_info", None)
    if mesh is not None:
        facts = asdict(mesh)
        for name, field in (("mesh.node_count", "n_vertices"), ("mesh.triangle_count", "n_triangles"),
                            ("mesh.tag_areas_m2", "physical_tag_areas_m2")):
            if field in facts:
                observe(name, facts[field], "HBB SolveResult.mesh_info")
        # MeshInfo exposes counts/areas, not node/connectivity arrays. Hash only these facts.
        evidence["hbb_mesh_info_sha256"] = {"status": "observed", "value": record_sha256(facts),
                                            "source": "HBB parsed MeshInfo; not a connectivity hash"}
    points = getattr(native, "observation_points", None)
    if points is not None:
        for name, value in flatten({"observation_points": points}).items():
            observe(name, value, "native.observation_points")
    return values, evidence
