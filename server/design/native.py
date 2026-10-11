"""Canonical, fail-closed translation for the bounded native constructions."""
from __future__ import annotations

import math
from typing import Any

NATIVE_FORMULAS = frozenset({
    "OSSE-ADAPTER", "OSSE-ADAPTER-CONTROLS", "OSSE-ADAPTER-AXIAL",
    "OSSE-ROUNDOVER", "OSSE-AXIAL", "OSSE-ARC", "SOURCE-DISK",
})


def _number(value: Any, name: str, default: float) -> float:
    if value is None:
        return default
    if hasattr(value, "raw"):
        if value.raw is not None:
            raise ValueError(f"{name} requires a plain finite number for native formulas")
        value = value.value
    if type(value) not in (int, float):
        raise ValueError(f"{name} requires a plain finite number for native formulas")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} requires a plain finite number for native formulas")
    return result


def native_mesher_config(root: Any) -> dict[str, Any]:
    """Preserve intrinsic dimensions; placement and density are finished mm."""
    def number(value: Any, name: str, default: float = 0) -> float:
        return _number(value, name, default)

    def inactive(value: Any, name: str, default: float = 0) -> None:
        if number(value, name, default) != default:
            raise ValueError(f"{root.formula} does not support {name}")

    for name in ("throat_ext_angle", "throat_ext_length", "slot_length"):
        inactive(getattr(root, name), name)
    if root.text_import_version is not None:
        raise ValueError("Native formulas do not accept imported ATH geometry")
    if root.simulation.sim_type == "infinite-baffle":
        raise ValueError(f"{root.formula} does not support infinite baffles")
    if root.coverage_mode not in (None, "auto") or root.length_mode not in (None, "profile"):
        raise ValueError("Native formulas require profile length and full circular coverage")
    inactive(root.morph.target_shape, "morph.target_shape")
    # Inactive morph/enclosure values retain the editor's preconfiguration.
    if root.enclosure is not None:
        inactive(root.enclosure.depth, "enclosure.depth")
    for name, default in (("shape", 2), ("radius", -1), ("curvature", 0), ("velocity", 1)):
        inactive(getattr(root.source, name), f"source.{name}", default)
    if root.source.contours or root.source.velocity_convention not in (None, "normal"):
        raise ValueError("Native formulas require a flat normal-driven source")

    mesh = root.mesh
    inactive(mesh.quadrants, "mesh.quadrants", 1234)
    for name, default in (("corner_segments", 4), ("throat_segments", 0), ("throat_slice_density", .5), ("aperture_resolution_scale", 1)):
        inactive(getattr(mesh, name), f"mesh.{name}", default)
    if mesh.sampling_mode not in (None, "uniform") or mesh.z_map_points or mesh.max_edge is not None:
        raise ValueError("Native formulas do not support custom sampling or max edge controls")
    allow = number(mesh.allow_large_mesh, "mesh.allow_large_mesh")
    if allow not in (0, 1):
        raise ValueError("mesh.allow_large_mesh must be 0 or 1")
    budget = number(mesh.max_triangles, "mesh.max_triangles", 18000)
    if not budget.is_integer() or not 0 < budget <= 10000000:
        raise ValueError("mesh.max_triangles must be a positive integer up to 10000000")
    config: dict[str, Any] = {
        "formula": root.formula,
        "mesh": {
            "quadrants": 1234,
            "vertical_offset_mm": number(mesh.vertical_offset, "mesh.vertical_offset"),
            "max_triangles": int(budget), "allow_large_mesh": bool(allow),
            "scale_to_metres": True,
        },
    }
    if root.formula == "SOURCE-DISK":
        if root.scale is not None:
            inactive(root.scale, "scale", 1)
        inactive(mesh.wall_thickness, "mesh.wall_thickness")
        if mesh.mouth_roundover_radius_mm is not None:
            raise ValueError("SOURCE-DISK does not support mouth roundovers")
        config["source_body"] = root.source_body.model_dump()
        config["mode"] = "standalone-source"
        config["mesh"]["size_mm"] = mesh.size_mm if mesh.size_mm is not None else 3
        return config

    if mesh.size_mm is not None:
        raise ValueError("mesh.size_mm is only supported by SOURCE-DISK")
    config.update({
        "mode": "freestanding" if root.formula == "OSSE-ROUNDOVER" else "bare",
        "scale": number(root.scale, "scale", 1),
        "profile": {"L_mm": root.L, "r0_mm": root.r0, "a_deg": root.a, "a0_deg": root.a0, "k": root.k, "s": root.s},
        "source": {"source_shape": 0},
    })
    config["mesh"]["wall_thickness_mm"] = number(mesh.wall_thickness, "mesh.wall_thickness", 5 if root.formula == "OSSE-ROUNDOVER" else 0)
    for field, key in (("throat_resolution", "throat_res_mm"), ("mouth_resolution", "mouth_res_mm"), ("rear_resolution", "rear_res_mm")):
        if (value := getattr(mesh, field)) is not None:
            config["mesh"][key] = number(value, f"mesh.{field}")
    for field in ("angular_segments", "length_segments"):
        if (value := getattr(mesh, field)) is not None:
            n = number(value, f"mesh.{field}")
            if not n.is_integer() or not 0 < n <= 10000000:
                raise ValueError(f"mesh.{field} must be a positive integer up to 10000000")
            config["mesh"][field] = int(n)
    required_adapter = root.formula in {"OSSE-ADAPTER", "OSSE-ADAPTER-CONTROLS", "OSSE-ADAPTER-AXIAL"}
    if required_adapter != (root.throat_adapter is not None):
        raise ValueError(f"{root.formula} {'requires' if required_adapter else 'does not support'} throat_adapter")
    if root.throat_adapter is not None:
        expected = "controls" if root.formula == "OSSE-ADAPTER-CONTROLS" else "authored"
        if root.throat_adapter.mode != expected:
            raise ValueError(f"{root.formula} requires throat_adapter.mode = {expected}")
        config["throat_adapter"] = root.throat_adapter.model_dump(mode="json")
    for field, required in (("axial_scale", root.formula in {"OSSE-AXIAL", "OSSE-ADAPTER-AXIAL"}), ("terminating_arc", root.formula == "OSSE-ARC")):
        value = getattr(root, field)
        if required != (value is not None):
            raise ValueError(f"{root.formula} {'requires' if required else 'does not support'} {field}")
        if value is not None:
            config[field] = value.model_dump() if hasattr(value, "model_dump") else value
    if root.formula == "OSSE-ROUNDOVER":
        if mesh.mouth_roundover_radius_mm is None:
            raise ValueError("OSSE-ROUNDOVER requires mesh.mouth_roundover_radius_mm")
        config["mesh"]["mouth_roundover_radius_mm"] = mesh.mouth_roundover_radius_mm
    elif mesh.mouth_roundover_radius_mm is not None:
        raise ValueError(f"{root.formula} does not support mouth roundovers")
    return config
