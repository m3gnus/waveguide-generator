"""Canonical, fail-closed translation for the bounded native constructions."""
from __future__ import annotations

import math
from typing import Any


NATIVE_FORMULAS = frozenset({
    "OSSE-ADAPTER", "OSSE-ADAPTER-CONTROLS", "OSSE-ADAPTER-AXIAL",
    "OSSE-ROUNDOVER", "OSSE-AXIAL", "OSSE-ARC", "OSSE-NATIVE", "SOURCE-DISK",
})


def field_label(name: str) -> str:
    from hornlab_mesher.controls import control_label
    if name in {"throat_adapter", "terminating_arc"}:
        return "Family"
    return control_label(name)


def _literal_error(name: str):
    from hornlab_mesher.controls import control_label
    from hornlab_mesher.errors import control_refusal
    label = control_label("native.scale" if name == "scale" else name)
    return control_refusal(f"{name} requires a plain finite number for native formulas", label, f"Enter a number in {label} instead of an expression.", message=f"{label} needs a finite number for this horn family.")


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
    from hornlab_mesher.controls import control_label
    from hornlab_mesher.errors import control_refusal
    def number(value: Any, name: str, default: float = 0) -> float:
        return _number(value, name, default)

    def inactive(value: Any, name: str, default: float = 0) -> None:
        if number(value, name, default) != default:
            raise control_refusal(f"{root.formula} does not support {name}", control_label(name), f"Reset {control_label(name)} to {default:g}, or choose a different horn family.", message=f"{control_label(name)} is unavailable for this horn family.")

    for name in ("throat_ext_angle", "throat_ext_length", "slot_length"):
        inactive(getattr(root, name), name)
    if root.text_import_version is not None:
        raise control_refusal('Native formulas do not accept imported ATH geometry', 'Family', 'Choose the original horn family to keep the imported geometry.', message='This horn family cannot use imported ATH geometry.')
    if root.simulation.sim_type == "infinite-baffle":
        raise control_refusal(f"{root.formula} does not support infinite baffles", "Mounting", "Change Mounting to Bare shell or Thickened waveguide (freestanding).", message="This horn family cannot use an infinite baffle.")
    if root.coverage_mode not in (None, "auto") or root.length_mode not in (None, "profile"):
        raise control_refusal('Native formulas require profile length and full circular coverage', control_label('common.length_mode'), 'Choose Profile length in Length mode and automatic Coverage mode, or start a new design in this Family.', message='This horn family needs profile length and full circular coverage.')
    inactive(root.morph.target_shape, "morph.target_shape")
    # Inactive morph/enclosure values retain the editor's preconfiguration.
    if root.enclosure is not None:
        inactive(root.enclosure.depth, "enclosure.depth")
    for name, default in (("shape", 2), ("radius", -1), ("curvature", 0), ("velocity", 1)):
        inactive(getattr(root.source, name), f"source.{name}", default)
    if root.source.contours or root.source.velocity_convention not in (None, "normal"):
        raise control_refusal('Native formulas require a flat normal-driven source', 'Source surface', 'Clear Source contours and choose a flat Source surface and normal Velocity convention.', message='This horn family needs a flat source driven in its normal direction.')

    inactive(root.cross_section.exponent, "cross_section.exponent", 2)
    inactive(root.cross_section.aspect_ratio, "cross_section.aspect_ratio", 1)

    mesh = root.mesh
    inactive(mesh.quadrants, "mesh.quadrants", 1234)
    for name, default in (("corner_segments", 4), ("throat_segments", 0), ("throat_slice_density", .5), ("aperture_resolution_scale", 1)):
        inactive(getattr(mesh, name), f"mesh.{name}", default)
    if mesh.sampling_mode not in (None, "uniform") or mesh.z_map_points or mesh.max_edge is not None:
        raise control_refusal('Native formulas do not support custom sampling or max edge controls', 'Z-map sampling mode', 'Choose Uniform in Z-map sampling mode, clear Z-map points, and clear Maximum edge guard.', message='This horn family uses uniform sampling.')
    allow = number(mesh.allow_large_mesh, "mesh.allow_large_mesh")
    if allow not in (0, 1):
        raise control_refusal('mesh.allow_large_mesh must be 0 or 1', 'Allow larger native exports', 'Set Allow larger native exports to 0 or 1.', message='Allow larger native exports needs an on or off value.')
    budget = number(mesh.max_triangles, "mesh.max_triangles", 18000)
    if not budget.is_integer() or not 0 < budget <= 10000000:
        raise control_refusal('mesh.max_triangles must be a positive integer up to 10000000', 'Native mesh triangle budget', 'Enter a whole number in Native mesh triangle budget.', message='Native mesh triangle budget must be a whole number from 1 to 10000000.')
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
            raise control_refusal('SOURCE-DISK does not support mouth roundovers', 'Mouth roundover radius', 'Clear Mouth roundover radius.', message='A standalone source cannot have a mouth roundover.')
        config["source_body"] = root.source_body.model_dump()
        config["mode"] = "standalone-source"
        config["mesh"]["size_mm"] = mesh.size_mm if mesh.size_mm is not None else 3
        return config

    if mesh.size_mm is not None:
        raise control_refusal('mesh.size_mm is only supported by SOURCE-DISK', 'Uniform source mesh size', 'Clear Uniform source mesh size, or choose the standalone source family.', message='Uniform source mesh size is only available for a standalone source.')
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
                raise control_refusal(f"mesh.{field} must be a positive integer up to 10000000", control_label(f"mesh.{field}"), f"Enter a whole number in {control_label(f'mesh.{field}')}.", message=f"{control_label(f'mesh.{field}')} must be a whole number from 1 to 10000000.")
            config["mesh"][field] = int(n)
    # Legacy markers have fixed slots; the mesher's native boundary owns the
    # supported composite combinations and canonical marker rules.
    composite = root.formula == "OSSE-NATIVE"
    required_adapter = root.formula in {"OSSE-ADAPTER", "OSSE-ADAPTER-CONTROLS", "OSSE-ADAPTER-AXIAL"}
    if not composite and required_adapter != (root.throat_adapter is not None):
        raise control_refusal(f"{root.formula} {'requires' if required_adapter else 'does not support'} throat_adapter", "Throat adapter", "Choose a matching Family and Throat adapter.", message=f"This horn family {'needs' if required_adapter else 'cannot use'} a throat adapter.")
    if root.throat_adapter is not None:
        expected = "controls" if root.formula == "OSSE-ADAPTER-CONTROLS" else "authored"
        if not composite and root.throat_adapter.mode != expected:
            raise control_refusal(f"{root.formula} requires throat_adapter.mode = {expected}", "Throat adapter", "Choose a matching Family and Throat adapter mode.", message="The throat adapter mode does not match this horn family.")
        config["throat_adapter"] = root.throat_adapter.model_dump(mode="json")
    for field, required in (("axial_scale", root.formula in {"OSSE-AXIAL", "OSSE-ADAPTER-AXIAL"}), ("terminating_arc", root.formula == "OSSE-ARC")):
        value = getattr(root, field)
        if not composite and required != (value is not None):
            label = field_label(field)
            feature = "a terminating arc" if field == "terminating_arc" else label
            step = ("Start a new design in this Family to restore its required features, or choose another Family."
                    if field == "terminating_arc" else f"Change {label} or choose a matching Family.")
            raise control_refusal(f"{root.formula} {'requires' if required else 'does not support'} {field}", label, step,
                                  message=f"This horn family {'needs' if required else 'cannot use'} {feature}.")
        if value is not None:
            config[field] = value.model_dump() if hasattr(value, "model_dump") else value
    if roundover:
        if mesh.mouth_roundover_radius_mm is None:
            raise control_refusal('OSSE-ROUNDOVER requires mesh.mouth_roundover_radius_mm', 'Mouth roundover radius', 'Enter Mouth roundover radius.', message='This horn family needs a mouth roundover radius.')
        config["mesh"]["mouth_roundover_radius_mm"] = mesh.mouth_roundover_radius_mm
    elif mesh.mouth_roundover_radius_mm is not None:
        raise control_refusal(f"{root.formula} does not support mouth roundovers", "Mouth roundover radius", "Clear Mouth roundover radius, or choose a horn family with a roundover.", message="This horn family cannot use a mouth roundover.")
    return config
