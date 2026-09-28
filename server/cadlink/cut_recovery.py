"""Whether a model already cut in CAD can be solved as the reduced domain of the whole.

Stage 3, branch 2 of the CAD Link simplification. A model the user cut in half
(or to a quarter) in CAD arrives open along the cut. Solved as shown it is
part of a speaker in free space, which is always wrong, so WG either recovers
it -- mirrors it as the reduced domain of the whole speaker, reflecting the
kept side onto the one the solver mirrors when the cut kept the negative side
(``server.mesh.imported.reflect_triangle_mesh``) -- or refuses it, naming the
condition that failed. It never solves the open shell as it stands.

A cut is recovered only when every one of these holds (the overseer's flip
conditions, adopted 2026-09-23), judged on the mesh observations of the model
as it arrived (``domain_interpretation.observe``):

1. **The plane contains the radiation axis.** A cut square to it (front/back)
   is never a symmetry plane. A reduced model is solved in the frame it was
   modelled in, where WG mirrors only x = 0 and y = 0.
2. **The cut is clean.** A rigid-shell rim spanning the shell lies on the
   plane; nothing crosses the plane, no face lies in it (a cap closes it), no
   other edge of the model is open -- a rim on another coordinate plane
   included, unless it is itself a cut judged here (a quarter's second plane,
   all or nothing); and a source meets the plane -- the plane
   passes through the speaker's own drivers, which is what shows it is the
   speaker's symmetry plane rather than an open side of a box.
3. **Nothing says the halves differ.** Every declared source was found on the
   model by its own faces (one only on the removed side cannot be restored by
   a mirror); no source is identified as a left or right one (its mirror image
   would impersonate the other side's own source); the model does not
   intersect itself (its mirror image would overlap).
4. **The plane is known:** an origin plane of the CAD frame. A cut off the
   origin planes, or an oblique one, is refused in this version.

Recovery is all or nothing: every cut the geometry shows is recovered, or the
model is refused. A partly mirrored model leaks through the cut left open.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import re
from typing import Any

from .domain_interpretation import (
    MIN_RIM_EDGES,
    PLANES,
    SUPPORTED_PLANES,
    Observations,
    plane_words,
)


CONTRACT = "cad-cut-recovery-v1"

#: The failed conditions, by code. Every refusal names one or more of them.
FRONT_BACK = "front-back-cut"
UNSUPPORTED_PLANE = "unsupported-plane"
#: The model is confirmed to face another way than it was modelled (+z): a
#: reduced domain is solved only as modelled, and a mirror never resets the
#: confirmed frame.
FRAME_NOT_MODELLED = "frame-not-as-modelled"
#: A curve of the model meets the cut plane at an angle: its mirror image
#: would kink there, so the halves are not each other's mirror images (an
#: off-centre driver or port cut through, say). Judged on the CAD geometry by
#: the mesher (``server.mesh.imported.asymmetric_section``).
ASYMMETRIC_SECTION = "asymmetric-section"
CAPPED = "capped"
CROSSING = "crossing-geometry"
OTHER_OPENINGS = "other-openings"
#: ``open-rim-on-<plane>``: an open rim on another coordinate plane that is
#: not itself a cut judged here (``other_open_edges`` does not count rims on a
#: coordinate plane, so a shell open on x = 0 *and* z = 0 needs this).
OPEN_RIM_PREFIX = "open-rim-on-"
NO_SOURCE_ON_PLANE = "no-source-on-plane"
SOURCE_IDENTITY = "source-identity"
SIDE_IDENTIFIED_SOURCE = "side-identified-source"
SELF_INTERSECTION = "self-intersection"
MESH_DENIED = "mesh-denied"

CONDITIONS = (
    FRONT_BACK,
    UNSUPPORTED_PLANE,
    FRAME_NOT_MODELLED,
    CAPPED,
    CROSSING,
    OTHER_OPENINGS,
    NO_SOURCE_ON_PLANE,
    SOURCE_IDENTITY,
    SIDE_IDENTIFIED_SOURCE,
    SELF_INTERSECTION,
    ASYMMETRIC_SECTION,
    MESH_DENIED,
)

#: Words that identify a source as belonging to one side of the speaker.
_SIDE_WORDS = {
    "left": "left",
    "l": "left",
    "lh": "left",
    "right": "right",
    "r": "right",
    "rh": "right",
}
_WORD = re.compile(r"[a-z0-9]+")


def geometry_cut_planes(observations: Observations | None) -> list[str]:
    """The CAD planes the observed geometry shows cut open (in CAD plane order).

    A plane is cut where a rigid-shell rim spanning its one-sided shell lies on
    it and WG did not make it (``rigid_cut_rim_edges``, the same reading as
    ``domain_interpretation.cut_shaped_open_rim``). An open end on the solver's
    z = 0 with no source the plane passes through is a horn mouth, not a cut.
    """

    if observations is None:
        return []
    planes = []
    for plane in PLANES:
        observation = observations.planes.get(plane)
        if observation is None or observation.wg_cut:
            continue
        if observation.rigid_cut_rim_edges < MIN_RIM_EDGES:
            continue
        if observation.solver_plane == "z0" and not observation.sources_bisected:
            continue
        planes.append(plane)
    return planes


def side_of_source(source: Mapping[str, Any]) -> str | None:
    """``left`` or ``right`` when a source's own names identify it with one side."""

    selectors = source.get("selectors") if isinstance(source.get("selectors"), Mapping) else {}
    names = [
        str(source.get("id") or ""),
        str(source.get("role") or ""),
        str(source.get("name") or ""),
        *(str(item) for item in selectors.get("appearance_labels") or ()),
        *(str(item) for item in selectors.get("shell_names") or ()),
    ]
    for name in names:
        # "Woofer_L", "HF-right", "LeftWoofer": words, split at case changes too.
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).casefold()
        for word in _WORD.findall(spaced):
            if word in _SIDE_WORDS:
                return _SIDE_WORDS[word]
    return None


@dataclass(frozen=True)
class Failure:
    code: str
    message: str

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class CutRecovery:
    """The verdict on the cuts the geometry shows."""

    #: Every CAD plane the geometry shows cut, and the side each kept.
    planes: tuple[str, ...] = ()
    kept_sides: Mapping[str, str | None] = field(default_factory=dict)
    #: Per plane, the flip conditions it fails (empty when it passes).
    failures: Mapping[str, tuple[Failure, ...]] = field(default_factory=dict)

    @property
    def recoverable(self) -> bool:
        return bool(self.planes) and not any(self.failures.get(plane) for plane in self.planes)

    @property
    def reflect(self) -> tuple[str, ...]:
        """The recovered planes whose cut kept the negative side."""

        if not self.recoverable:
            return ()
        return tuple(plane for plane in self.planes if self.kept_sides.get(plane) == "negative")

    def reason(self, plane: str | None = None) -> str | None:
        """The failed conditions, in words: for one plane, or the first that failed."""

        chosen = [plane] if plane is not None else list(self.planes)
        for candidate in chosen:
            failures = self.failures.get(candidate) or ()
            if failures:
                return "; ".join(item.message for item in failures)
        return None

    def with_failure(self, failure: Failure) -> CutRecovery:
        """This verdict with one more failed condition on every plane (all or nothing)."""

        return CutRecovery(
            planes=self.planes,
            kept_sides=dict(self.kept_sides),
            failures={
                plane: (*(self.failures.get(plane) or ()), failure) for plane in self.planes
            },
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT,
            "planes": list(self.planes),
            "kept_sides": {plane: self.kept_sides.get(plane) for plane in self.planes},
            "recoverable": self.recoverable,
            "reflect": list(self.reflect),
            "failures": {
                plane: [item.to_json() for item in self.failures.get(plane) or ()]
                for plane in self.planes
            },
        }


def _kept_side(negative: int, positive: int) -> str | None:
    if negative and not positive:
        return "negative"
    if positive and not negative:
        return "positive"
    return None


def assess_cut_recovery(
    observations: Observations | None,
    *,
    sources: Sequence[Mapping[str, Any]],
    radiation_axis: str,
    identity_problem: str | None = None,
    integrity_problem: str | None = None,
    absent_sources: Iterable[str] = (),
) -> CutRecovery:
    """Judge every cut the observations show against the flip conditions.

    ``radiation_axis`` is the forward axis the snapshot is (to be) solved
    along, in CAD terms ("+z" as modelled); ``identity_problem`` why the
    sources are not validated identities (``ingest._source_identity_problem``)
    and ``integrity_problem`` why a mirrored reconstruction would overlap
    (``ingest._reconstruction_integrity_problem``), or None.
    """

    planes = geometry_cut_planes(observations)
    if observations is None or not planes:
        return CutRecovery()
    # A source clear of the plane is allowed: one identity may own a mirrored
    # pair of drivers (PartyMEH's MF pair), which a cut leaves one of. A pair
    # identified as left and right is refused (SIDE_IDENTIFIED_SOURCE).
    del absent_sources
    axis_letter = str(radiation_axis or "+z")[-1:].casefold()
    shared: list[Failure] = []
    if observations.other_open_edges:
        shared.append(
            Failure(
                OTHER_OPENINGS,
                f"besides the cut the model has {observations.other_open_edges} other open "
                "edge(s), through which a mirrored model would leak",
            )
        )
    for other in PLANES:
        observation = observations.planes.get(other)
        if other in planes or observation is None or observation.wg_cut:
            continue
        if observation.rim_edges >= MIN_RIM_EDGES:
            shared.append(
                Failure(
                    OPEN_RIM_PREFIX + other,
                    f"the model is also open along {plane_words(other)} "
                    f"({observation.rim_edges} rim edges), which a mirror would not close",
                )
            )
    if identity_problem:
        shared.append(Failure(SOURCE_IDENTITY, identity_problem))
    for source in sources:
        side = side_of_source(source)
        if side is not None:
            shared.append(
                Failure(
                    SIDE_IDENTIFIED_SOURCE,
                    f"source {source.get('id')} is identified as the {side} one, so its mirror "
                    "image would stand in for the other side's own source",
                )
            )
    if integrity_problem:
        shared.append(Failure(SELF_INTERSECTION, integrity_problem))
    kept: dict[str, str | None] = {}
    failures: dict[str, tuple[Failure, ...]] = {}
    for plane in planes:
        observation = observations.planes[plane]
        words = plane_words(plane)
        own: list[Failure] = []
        if plane[0] == axis_letter:
            own.append(
                Failure(
                    FRONT_BACK,
                    f"the cut on {words} is square to the radiation axis ({radiation_axis}), "
                    "so it is not a symmetry plane of the speaker",
                )
            )
        elif str(radiation_axis or "+z") != "+z":
            own.append(Failure(FRAME_NOT_MODELLED, frame_not_modelled_message(str(radiation_axis))))
        elif plane not in SUPPORTED_PLANES:
            own.append(
                Failure(
                    UNSUPPORTED_PLANE,
                    f"WG mirrors a cut model only in the frame it was modelled in, where it "
                    f"cannot mirror {words}; cut it on the YZ or XZ origin plane",
                )
            )
        side = _kept_side(observation.negative, observation.positive)
        kept[plane] = side
        if side is None:
            own.append(
                Failure(CROSSING, f"geometry crosses {words}, so the model is not cut there")
            )
        if observation.cap_triangles:
            own.append(
                Failure(
                    CAPPED,
                    f"a face lies in {words} and would solve as a wall across the cut",
                )
            )
        if not observation.sources_on_plane:
            own.append(
                Failure(
                    NO_SOURCE_ON_PLANE,
                    f"no source meets {words}, so nothing shows it is the speaker's "
                    "symmetry plane rather than an open side",
                )
            )
        failures[plane] = (*own, *shared)
    return CutRecovery(planes=tuple(planes), kept_sides=kept, failures=failures)


def frame_not_modelled_message(axis: str) -> str:
    return (
        "WG mirrors a cut model only in the frame it was modelled in (+z), and this "
        f"model is confirmed to face {axis}; mirroring it would change the frame you confirmed"
    )


def frame_flip_problem(planes: Iterable[str], radiation_axis: str) -> str | None:
    """Why the confirmed frame forbids mirroring these CAD planes, or None.

    The same conditions :func:`assess_cut_recovery` judges, for recorded
    evidence: a plane square to the radiation axis is never a symmetry plane,
    and a mirror is solved only as modelled, never by resetting the frame.
    """

    axis = str(radiation_axis or "+z")
    letter = axis[-1:].casefold()
    for plane in planes:
        if plane[0] == letter:
            return (
                f"the cut on {plane_words(plane)} is square to the radiation axis ({axis}), "
                "so it is not a symmetry plane of the speaker"
            )
    if axis != "+z":
        return frame_not_modelled_message(axis)
    return None


def recovery_options(
    options: Mapping[str, Any],
    recovery: CutRecovery,
    *,
    plan_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The preparation options that mesh a recoverable cut as its reduced domain.

    Prepared as a declaration of the recovered planes (the mesher then checks
    the open section, leaks and winding on the final mesh), with the planes
    that kept the negative side reflected, in the frame the model was modelled
    in. The options enter the mesh cache key, so the reading is part of what
    was prepared.
    """

    planes = [plane for plane in SUPPORTED_PLANES if plane in recovery.planes]
    return {
        **{key: value for key, value in options.items() if key != "solver_frame"},
        "declared_cut_planes": [
            plane
            for plane in SUPPORTED_PLANES
            if plane in set(options.get("declared_cut_planes") or ()) | set(planes)
        ],
        "reflect_planes": list(recovery.reflect),
        "domain_interpretation": {
            **dict(plan_identity or {}),
            "applied": planes,
            "recovered": {
                "contract": CONTRACT,
                "kept_sides": {plane: recovery.kept_sides.get(plane) for plane in planes},
                "reflect": list(recovery.reflect),
            },
        },
    }


def planes_in_words(planes: Iterable[str]) -> str:
    return " and ".join(plane_words(plane) for plane in planes)


__all__ = [
    "CONDITIONS",
    "CONTRACT",
    "OPEN_RIM_PREFIX",
    "frame_flip_problem",
    "CutRecovery",
    "Failure",
    "assess_cut_recovery",
    "geometry_cut_planes",
    "recovery_options",
    "side_of_source",
]
