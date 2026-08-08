"""Recover a design from v1's prepared mesher payload.

This is the *second* recovery source for imported v1 jobs, and deliberately the
lower-fidelity one. v1 wrote two copies of a design into every job row:

``script_snapshot.stateSnapshot``
    the editor state at submit time -- authoritative, handled by
    :mod:`server.design.legacy_snapshot`.

``config_json.options.mesh.waveguide_params``
    the flattened bag v1 handed to the mesher.

The plan assumed the second did not exist, and recorded the nine jobs whose
``script_snapshot_json`` is NULL -- the column arrived in a later v1 ALTER and
was never backfilled -- as unrecoverable by construction. Measured against the
live v1 database, *every* job carries ``waveguide_params``, including all nine,
so they are recoverable after all: the payload is what was actually meshed and
solved, which is precisely the design a rerun should reproduce.

It is used only when the snapshot is missing or unreadable, because v1 itself
warns that the prepared form "is not equivalent" to the design state
(``src/modules/simulation/jobs.js:7-10``). What it loses:

* ATH passthrough blocks (``Report``, ``ABEC.Polars:*``, ``GridExport:*``) and
  any other editor-only material -- they never reached the mesher.
* ``Scale``. v1 folds the scale factor into the payload's lengths before
  sending it, so re-declaring ``Scale`` here would apply it twice. The
  recovered design is the scaled one, which is the geometry that was solved.
* The sweep bounds and solver mode, which live beside the payload in
  ``config_json`` rather than inside it.

Everything else is a rename: ``waveguide_params`` is v1's own parameter bag with
snake_case keys, not the mesher's nested config, so the recovered bag is fed
through the same verified ``generateMWGConfigContent`` port the snapshot path
uses rather than a second, drift-prone field mapping.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .legacy_snapshot import LegacySnapshotError, snapshot_to_ath_text
from .textcfg import ParsedDesign, TextConfigError, parse


#: ``waveguide_params`` key -> v1 parameter-bag key. Read off the live v1
#: database's own rows; the value conventions (``source_shape`` 1/2, the
#: comma-joined enclosure resolutions, ``quadrants`` as 1/12/14/1234) are
#: identical on both sides, so only the spelling changes.
_RENAMED_KEYS = {
    "formula_type": "type",
    "throat_profile": "throatProfile",
    "throat_ext_angle": "throatExtAngle",
    "throat_ext_length": "throatExtLength",
    "slot_length": "slotLength",
    "circ_arc_term_angle": "circArcTermAngle",
    "circ_arc_radius": "circArcRadius",
    "gcurve_type": "gcurveType",
    "gcurve_dist": "gcurveDist",
    "gcurve_width": "gcurveWidth",
    "gcurve_aspect_ratio": "gcurveAspectRatio",
    "gcurve_se_n": "gcurveSeN",
    "gcurve_sf": "gcurveSf",
    "gcurve_sf_a": "gcurveSfA",
    "gcurve_sf_b": "gcurveSfB",
    "gcurve_sf_m1": "gcurveSfM1",
    "gcurve_sf_m2": "gcurveSfM2",
    "gcurve_sf_n1": "gcurveSfN1",
    "gcurve_sf_n2": "gcurveSfN2",
    "gcurve_sf_n3": "gcurveSfN3",
    "gcurve_rot": "gcurveRot",
    "morph_target": "morphTarget",
    "morph_width": "morphWidth",
    "morph_height": "morphHeight",
    "morph_corner": "morphCorner",
    "morph_rate": "morphRate",
    "morph_fixed": "morphFixed",
    "morph_allow_shrinkage": "morphAllowShrinkage",
    "n_angular": "angularSegments",
    "n_length": "lengthSegments",
    "corner_segments": "cornerSegments",
    "throat_segments": "throatSegments",
    "throat_slice_density": "throatSliceDensity",
    "sampling_mode": "samplingMode",
    "z_map_points": "zMapPoints",
    "throat_res": "throatResolution",
    "mouth_res": "mouthResolution",
    "rear_res": "rearResolution",
    "aperture_resolution_scale": "apertureResolutionScale",
    "wall_thickness": "wallThickness",
    "vertical_offset": "verticalOffset",
    "max_triangles": "maxTriangles",
    "allow_large_mesh": "allowLargeMesh",
    "enc_depth": "encDepth",
    "enc_space_l": "encSpaceL",
    "enc_space_t": "encSpaceT",
    "enc_space_r": "encSpaceR",
    "enc_space_b": "encSpaceB",
    "enc_edge": "encEdge",
    "enc_edge_type": "encEdgeType",
    "enc_front_resolution": "encFrontResolution",
    "enc_back_resolution": "encBackResolution",
    "source_shape": "sourceShape",
    "source_radius": "sourceRadius",
    "source_curv": "sourceCurv",
    "source_velocity": "sourceVelocity",
    "source_contours": "sourceContours",
    "sim_type": "simType",
    "output_stl": "outputSTL",
    "output_msh": "outputMSH",
}

#: Keys the payload and the bag spell the same way.
_SHARED_KEYS = frozenset(
    {"L", "R", "a", "a0", "r0", "k", "q", "s", "n", "h", "m", "b", "r", "tmax", "rot", "quadrants"}
)

#: Keys the ``generateMWGConfigContent`` port has no line for, so carrying them
#: over could only mislead. ``msh_version`` and ``step_body`` are export
#: choices, not geometry; the ICW/LC-OSSE fields (``termination`` and friends)
#: reach v2 through its own schema and are always null in v1 rows; the FREEFORM
#: profile arrays cannot be written as ATH text at all.
_IGNORED_KEYS = frozenset(
    {
        "coverage_angle",
        "cross_sections",
        "depth",
        "hold_end",
        "hold_start",
        "inflection_policy",
        "length_mode",
        "msh_version",
        "n_coeff",
        "overshoot_policy",
        "profile_h",
        "profile_v",
        "step_body",
        "termination",
        "theta1_deg",
    }
)


def payload_to_params(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Rename a ``waveguide_params`` payload into a v1 parameter bag."""

    if not isinstance(payload, Mapping):
        raise LegacySnapshotError("mesher payload must be a mapping")
    bag: dict[str, Any] = {}
    unknown: list[str] = []
    for key, value in payload.items():
        if key in _IGNORED_KEYS:
            continue
        name = _RENAMED_KEYS.get(key) or (key if key in _SHARED_KEYS else None)
        if name is None:
            unknown.append(str(key))
            continue
        # A null in the payload means "v1 sent no value", which is what an
        # absent bag key means to the writer. Keeping it would spell the ATH
        # line "null" and lose the field to migration 003 anyway.
        if value is None:
            continue
        bag[name] = value
    if unknown:
        raise LegacySnapshotError(
            "mesher payload holds unrecognised parameter(s): " + ", ".join(sorted(unknown))
        )
    if not bag.get("type"):
        raise LegacySnapshotError("mesher payload has no formula_type")
    return bag


def payload_to_design(payload: Mapping[str, Any]) -> ParsedDesign:
    """Convert v1's prepared mesher payload into a validated v2 design."""

    params = payload_to_params(payload)
    try:
        return parse(snapshot_to_ath_text(params))
    except LegacySnapshotError:
        raise
    except (TextConfigError, TypeError, ValueError) as exc:
        raise LegacySnapshotError(f"could not parse legacy mesher payload: {exc}") from exc


def job_config_payload(config: Any) -> Mapping[str, Any] | None:
    """Dig ``waveguide_params`` out of a v1 job's stored ``config_json``."""

    if not isinstance(config, Mapping):
        return None
    options = config.get("options")
    if not isinstance(options, Mapping):
        return None
    mesh = options.get("mesh")
    if not isinstance(mesh, Mapping):
        return None
    payload = mesh.get("waveguide_params")
    return payload if isinstance(payload, Mapping) else None


__all__ = ["job_config_payload", "payload_to_design", "payload_to_params"]
