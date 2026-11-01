"""Canonical, fail-closed translation for the bounded native constructions."""
from __future__ import annotations

import math
from typing import Any

NATIVE_FORMULAS = frozenset({
    "OSSE-ADAPTER", "OSSE-ADAPTER-CONTROLS", "OSSE-ADAPTER-AXIAL",
    "OSSE-ROUNDOVER", "OSSE-AXIAL", "OSSE-ARC", "OSSE-NATIVE", "SOURCE-DISK",
})


# Labels match the controls used for native designs. Keep diagnostic keys in
# exception text for existing matchers, and show only the typed public fields.
_CONTROL_LABELS = {
    "throat_ext_angle": "Throat extension angle", "throat_ext_length": "Throat extension length",
    "slot_length": "Straight slot length", "scale": "Transverse / uniform scale",
    "morph.target_shape": "Target shape", "enclosure.depth": "Enclosure depth",
    "source.shape": "Source shape", "source.radius": "Source radius", "source.curvature": "Source curvature",
    "source.velocity": "Source velocity", "cross_section.exponent": "Cross-section exponent",
    "cross_section.aspect_ratio": "Aspect ratio", "mesh.quadrants": "Symmetry",
    "mesh.corner_segments": "Corner segments", "mesh.throat_segments": "Throat segments",
    "mesh.throat_slice_density": "Throat slice density", "mesh.aperture_resolution_scale": "Aperture resolution scale",
    "mesh.allow_large_mesh": "Solve anyway", "mesh.max_triangles": "Triangle limit",
    "mesh.vertical_offset": "Vertical offset", "mesh.wall_thickness": "Wall thickness",
    "mesh.throat_resolution": "Throat resolution", "mesh.mouth_resolution": "Mouth resolution",
    "mesh.rear_resolution": "Rear resolution", "mesh.angular_segments": "Angular segments",
    "mesh.length_segments": "Length segments", "throat_adapter": "Throat adapter",
    "axial_scale": "Absolute axial scale", "terminating_arc": "Terminating arc",
}


def _refusal(field: str, message: str, next_step: str, diagnostic: str):
    from hornlab_mesher.errors import UserRefusal
    refusal = UserRefusal(field, message, next_step)
    refusal.diagnostic = diagnostic
    return refusal


def _literal_error(name: str):
    label = _CONTROL_LABELS[name]
    return _refusal(label, f"{label} needs a finite number for this horn family.",
                    f"Enter a number in {label} instead of an expression.",
                    f"{name} requires a plain finite number for native formulas")


def has_native_roundover(root: Any) -> bool:
    """Whether the native construction owns a closed mouth roundover."""
    return root.formula == "OSSE-ROUNDOVER" or (
        root.formula == "OSSE-NATIVE" and root.mesh.mouth_roundover_radius_mm is not None
    )


def _number(value: Any, name: str, default: float) -> float:
    if value is None:
        return default
    if hasattr(value, "raw"):
        if value.raw is not None:
            raise _literal_error(name)
        value = value.value
    if type(value) not in (int, float):
        raise _literal_error(name)
    result = float(value)
    if not math.isfinite(result):
        raise _literal_error(name)
    return result


def native_mesher_config(root: Any) -> dict[str, Any]:
    """Preserve intrinsic dimensions; placement and density are finished mm."""
    def number(value: Any, name: str, default: float = 0) -> float:
        return _number(value, name, default)

    def inactive(value: Any, name: str, default: float = 0) -> None:
        if number(value, name, default) != default:
            raise _refusal(_CONTROL_LABELS[name], f"{_CONTROL_LABELS[name]} is unavailable for this horn family.", f"Reset {_CONTROL_LABELS[name]} to {default:g}, or choose a different horn family.", f"{root.formula} does not support {name}")

    for name in ("throat_ext_angle", "throat_ext_length", "slot_length"):
        inactive(getattr(root, name), name)
    if root.text_import_version is not None:
        raise _refusal('Horn family', 'This horn family cannot use imported ATH geometry.', 'Choose the original horn family to keep the imported geometry.', 'Native formulas do not accept imported ATH geometry')
    if root.simulation.sim_type == "infinite-baffle":
        raise _refusal("Mounting", "This horn family cannot use an infinite baffle.", "Change Mounting to Bare shell or Thickened waveguide (freestanding).", f"{root.formula} does not support infinite baffles")
    if root.coverage_mode not in (None, "auto") or root.length_mode not in (None, "profile"):
        raise _refusal('Coverage', 'This horn family needs profile length and full circular coverage.', 'Choose Profile length and automatic Coverage.', 'Native formulas require profile length and full circular coverage')
    inactive(root.morph.target_shape, "morph.target_shape")
    # Inactive morph/enclosure values retain the editor's preconfiguration.
    if root.enclosure is not None:
        inactive(root.enclosure.depth, "enclosure.depth")
    for name, default in (("shape", 2), ("radius", -1), ("curvature", 0), ("velocity", 1)):
        inactive(getattr(root.source, name), f"source.{name}", default)
    if root.source.contours or root.source.velocity_convention not in (None, "normal"):
        raise _refusal('Source shape', 'This horn family needs a flat source driven in its normal direction.', 'Choose a flat Source shape and normal Source velocity.', 'Native formulas require a flat normal-driven source')

    inactive(root.cross_section.exponent, "cross_section.exponent", 2)
    inactive(root.cross_section.aspect_ratio, "cross_section.aspect_ratio", 1)

    mesh = root.mesh
    inactive(mesh.quadrants, "mesh.quadrants", 1234)
    for name, default in (("corner_segments", 4), ("throat_segments", 0), ("throat_slice_density", .5), ("aperture_resolution_scale", 1)):
        inactive(getattr(mesh, name), f"mesh.{name}", default)
    if mesh.sampling_mode not in (None, "uniform") or mesh.z_map_points or mesh.max_edge is not None:
        raise _refusal('Sampling', 'This horn family uses uniform sampling.', 'Choose Uniform sampling and clear Max edge.', 'Native formulas do not support custom sampling or max edge controls')
    allow = number(mesh.allow_large_mesh, "mesh.allow_large_mesh")
    if allow not in (0, 1):
        raise _refusal('Solve anyway', 'Solve anyway needs an on or off value.', 'Set Solve anyway to 0 or 1.', 'mesh.allow_large_mesh must be 0 or 1')
    budget = number(mesh.max_triangles, "mesh.max_triangles", 18000)
    if not budget.is_integer() or not 0 < budget <= 10000000:
        raise _refusal('Triangle limit', 'Triangle limit must be a whole number from 1 to 10000000.', 'Enter a whole number in Triangle limit.', 'mesh.max_triangles must be a positive integer up to 10000000')
    config: dict[str, Any] = {
        "formula": root.formula,
        "mesh": {
            "quadrants": 1234,
            "vertical_offset_mm": number(mesh.vertical_offset, "mesh.vertical_offset"),
            "max_triangles": int(budget), "allow_large_mesh": bool(allow),
            "scale_to_metres": True,
        },
    }
    if mesh.surface_deviation_mm is not None:
        config["mesh"]["surface_deviation_mm"] = mesh.surface_deviation_mm
    if root.formula == "SOURCE-DISK":
        if root.scale is not None:
            inactive(root.scale, "scale", 1)
        inactive(mesh.wall_thickness, "mesh.wall_thickness")
        if mesh.mouth_roundover_radius_mm is not None:
            raise _refusal('Mouth roundover radius', 'A standalone source cannot have a mouth roundover.', 'Clear Mouth roundover radius.', 'SOURCE-DISK does not support mouth roundovers')
        config["source_body"] = root.source_body.model_dump()
        config["mode"] = "standalone-source"
        config["mesh"]["size_mm"] = mesh.size_mm if mesh.size_mm is not None else 3
        return config

    if mesh.size_mm is not None:
        raise _refusal('Uniform source mesh size', 'Uniform source mesh size is only available for a standalone source.', 'Clear Uniform source mesh size, or choose the standalone source family.', 'mesh.size_mm is only supported by SOURCE-DISK')
    roundover = has_native_roundover(root)
    config.update({
        "mode": "freestanding" if roundover else "bare",
        "scale": number(root.scale, "scale", 1),
        "profile": {"L_mm": root.L, "r0_mm": root.r0, "a_deg": root.a, "a0_deg": root.a0, "k": root.k, "s": root.s},
        "source": {"source_shape": 0},
    })
    config["mesh"]["wall_thickness_mm"] = number(mesh.wall_thickness, "mesh.wall_thickness", 5 if roundover else 0)
    for field, key in (("throat_resolution", "throat_res_mm"), ("mouth_resolution", "mouth_res_mm"), ("rear_resolution", "rear_res_mm")):
        if (value := getattr(mesh, field)) is not None:
            config["mesh"][key] = number(value, f"mesh.{field}")
    for field in ("angular_segments", "length_segments"):
        if (value := getattr(mesh, field)) is not None:
            n = number(value, f"mesh.{field}")
            if not n.is_integer() or not 0 < n <= 10000000:
                raise _refusal(_CONTROL_LABELS[f"mesh.{field}"], f"{_CONTROL_LABELS[f'mesh.{field}']} must be a whole number from 1 to 10000000.", f"Enter a whole number in {_CONTROL_LABELS[f'mesh.{field}']}.", f"mesh.{field} must be a positive integer up to 10000000")
            config["mesh"][field] = int(n)
    # Legacy markers have fixed slots; the mesher's native boundary owns the
    # supported composite combinations and canonical marker rules.
    composite = root.formula == "OSSE-NATIVE"
    required_adapter = root.formula in {"OSSE-ADAPTER", "OSSE-ADAPTER-CONTROLS", "OSSE-ADAPTER-AXIAL"}
    if not composite and required_adapter != (root.throat_adapter is not None):
        raise _refusal("Throat adapter", f"This horn family {'needs' if required_adapter else 'cannot use'} a throat adapter.", "Choose a matching Horn family and Throat adapter.", f"{root.formula} {'requires' if required_adapter else 'does not support'} throat_adapter")
    if root.throat_adapter is not None:
        expected = "controls" if root.formula == "OSSE-ADAPTER-CONTROLS" else "authored"
        if not composite and root.throat_adapter.mode != expected:
            raise _refusal("Throat adapter", "The throat adapter mode does not match this horn family.", "Choose a matching Horn family and Throat adapter mode.", f"{root.formula} requires throat_adapter.mode = {expected}")
        config["throat_adapter"] = root.throat_adapter.model_dump(mode="json")
    for field, required in (("axial_scale", root.formula in {"OSSE-AXIAL", "OSSE-ADAPTER-AXIAL"}), ("terminating_arc", root.formula == "OSSE-ARC")):
        value = getattr(root, field)
        if not composite and required != (value is not None):
            raise _refusal(_CONTROL_LABELS[field], f"This horn family {'needs' if required else 'cannot use'} {_CONTROL_LABELS[field]}.", f"Change {_CONTROL_LABELS[field]} or choose a matching Horn family.", f"{root.formula} {'requires' if required else 'does not support'} {field}")
        if value is not None:
            config[field] = value.model_dump() if hasattr(value, "model_dump") else value
    if roundover:
        if mesh.mouth_roundover_radius_mm is None:
            raise _refusal('Mouth roundover radius', 'This horn family needs a mouth roundover radius.', 'Enter Mouth roundover radius.', 'OSSE-ROUNDOVER requires mesh.mouth_roundover_radius_mm')
        config["mesh"]["mouth_roundover_radius_mm"] = mesh.mouth_roundover_radius_mm
    elif mesh.mouth_roundover_radius_mm is not None:
        raise _refusal("Mouth roundover radius", "This horn family cannot use a mouth roundover.", "Clear Mouth roundover radius, or choose a horn family with a roundover.", f"{root.formula} does not support mouth roundovers")
    return config
