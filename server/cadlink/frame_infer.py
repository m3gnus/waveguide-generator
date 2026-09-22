"""Which way a CAD-authored model radiates: the automatic solver frame.

docs/architecture/CAD-OPERATIONS.md, "Unlinked solver frame". A return with no
WG instance carries no throat frame. Instead of asking the user every time, WG
infers the forward axis from the tessellated model and preselects it; Solve
confirms the visible, preselected axis (M1b), and WG asks only when this module
abstains.

One pure function, :func:`infer_frame`, over the full model in CAD
coordinates. Three kinds of evidence each score the six assembly axes:

- **normals** (the prior): the mean oriented normal of each source role, then
  the roles weighted HF 9 : MF 3 : LF 1, passive cardioid 0. Averaging within a
  role first keeps a large LF area from outvoting the HF throat. Opposing
  normals within one role still cancel, which is why this alone never decides.
- **visibility** (geometric): how much of each role's area a far observer on
  each axis sees, as projected area, over the axial view and four small tilts,
  with the same role weights. This is the high-frequency limit of "the axis
  with the most output", and it does not cancel opposing taps.
- **aperture** (geometric): the largest recessed opening of the envelope seen
  from each axis that the sources reach -- a horn mouth, a driver recess.

Each evidence type that decides casts a confidence-scaled vote. The result is
automatic only when the vote share, the normalised lead and the number of
distinct evidence types supporting the winner (at least one geometric) all
pass; otherwise WG asks, with the reason in words. It also asks when there is
no HF or MF source, when source identity is not validated, when the sources
cannot be seen from outside, when the surface orientation is unreliable, and
when the unrestricted winner is not a supported axis. It never promotes a
weaker supported axis.

Everything here -- weights, thresholds, sampling -- is frozen under
:data:`ALGORITHM_VERSION`; a change is a new version, and the suggestion cache
is keyed by it. The survey is deterministic: fixed lattices, no randomness.

:func:`survey_mesh_from_record` adapts an ingestion record's solver mesh: it
reads the production source tags (never area matching), mirrors a reduced
record back to the full model across its domain planes -- WG-generated cuts
included, because those are provisional and must not constrain the answer --
and maps it back into CAD coordinates with the inverse of the frame the record
was meshed in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import math
from typing import Any

import numpy as np

ALGORITHM_VERSION = "frame-infer-v1"

AXES: tuple[str, ...] = ("+z", "-z", "+x", "-x", "+y", "-y")
_AXIS_VECTORS = np.asarray(
    [(0, 0, 1), (0, 0, -1), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)], dtype=float
)

#: Per-role weights. A role not named here does not vote.
ROLE_WEIGHTS: dict[str, float] = {"HF": 9.0, "MF": 3.0, "LF": 1.0, "PASSIVE_CARDIOID": 0.0}
#: Roles whose presence makes a front meaningful. LF alone is omnidirectional.
DIRECTIONAL_ROLES = frozenset({"HF", "MF"})

NORMALS = "normals"
VISIBILITY = "visibility"
APERTURE = "aperture"
EVIDENCE_WEIGHTS: dict[str, float] = {NORMALS: 0.40, VISIBILITY: 0.35, APERTURE: 0.25}
GEOMETRIC_EVIDENCE = frozenset({VISIBILITY, APERTURE})

MIN_VOTE_SHARE = 0.60
MIN_LEAD = 0.25
MIN_SUPPORTING_EVIDENCE = 2
#: An evidence type supports its axis only at or above this confidence.
MIN_EVIDENCE_STRENGTH = 0.10
#: A decisive but weak evidence still votes, scaled by at least this.
VOTE_FLOOR = 0.05

TILT_DEG = 20.0
#: Pixels across the model's bounding-box diagonal, before source refinement.
BASE_RESOLUTION = 192
MAX_RESOLUTION = 768
#: The smallest voting role spans at least this many pixels across.
MIN_SOURCE_PIXELS = 5
MAX_SAMPLES = 2_500_000
#: A role seen less than this (mean projected fraction) is unseen.
UNSEEN_VISIBILITY = 0.02
#: WG asks when unseen roles carry at least this share of the voting weight.
MAX_UNSEEN_WEIGHT_SHARE = 0.25
#: WG asks when more of the silhouette than this shows back faces.
BACKFACE_LIMIT = 0.20
#: An opening is recessed by this fraction of the model's depth along the view.
RECESS_FRACTION = 0.02
#: An opening smaller than this fraction of the silhouette is no evidence.
APERTURE_MIN_FRACTION = 0.005

#: Blocking findings that mean a source's faces were not resolved as authored.
IDENTITY_FINDINGS = frozenset(
    {"geometry-overrode-paint", "source-paint-missing", "source-area-drift-override"}
)

STATUS_AUTOMATIC = "automatic"
STATUS_ASK = "ask"
STATUS_UNAVAILABLE = "unavailable"


class SurveyUnavailable(ValueError):
    """The record's geometry cannot be surveyed."""


@dataclass(frozen=True)
class SurveySource:
    tag: int
    source_id: str
    role: str


@dataclass(frozen=True)
class SurveyMesh:
    """A full model in CAD coordinates, millimetres, wound outward into the air."""

    points_mm: np.ndarray
    triangles: np.ndarray
    tags: np.ndarray
    sources: tuple[SurveySource, ...]
    #: None when the source identity is validated; otherwise why it is not.
    identity_problem: str | None = None


@dataclass(frozen=True)
class FrameInference:
    algorithm: str
    status: str
    #: The preselected axis; None whenever WG asks.
    axis: str | None
    #: The winner before the supported axes were applied, and before the gates.
    unrestricted_axis: str | None
    confidence: float
    vote_share: float
    vote_lead: float
    supporting_evidence: tuple[str, ...]
    reason_code: str
    reason: str
    supported_axes: tuple[str, ...]
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "status": self.status,
            "axis": self.axis,
            "unrestrictedAxis": self.unrestricted_axis,
            "confidence": round(self.confidence, 6),
            "voteShare": round(self.vote_share, 6),
            "voteLead": round(self.vote_lead, 6),
            "supportingEvidence": list(self.supporting_evidence),
            "reasonCode": self.reason_code,
            "reason": self.reason,
            "supportedAxes": list(self.supported_axes),
            "evidence": self.evidence,
        }


def unavailable(reason: str, *, supported_axes: Iterable[str] = AXES) -> FrameInference:
    """What WG reports when the survey could not run: it asks."""

    return FrameInference(
        algorithm=ALGORITHM_VERSION,
        status=STATUS_UNAVAILABLE,
        axis=None,
        unrestricted_axis=None,
        confidence=0.0,
        vote_share=0.0,
        vote_lead=0.0,
        supporting_evidence=(),
        reason_code="survey-unavailable",
        reason=f"WG could not work out which way this model faces ({reason}). Pick the front.",
        supported_axes=tuple(axis for axis in AXES if axis in set(supported_axes)),
    )


# -- geometry ------------------------------------------------------------------


def _round_scores(values: np.ndarray) -> dict[str, float]:
    return {axis: round(float(value), 6) for axis, value in zip(AXES, values, strict=True)}


def _decide(scores: np.ndarray, *, relative: bool, floor: float = 1e-9) -> tuple[str | None, float]:
    """The best axis and its confidence, or (None, 0) when nothing stands out."""

    order = np.argsort(-scores, kind="stable")
    best, second = float(scores[order[0]]), float(max(scores[order[1]], 0.0))
    if best <= floor:
        return None, 0.0
    margin = best - second
    confidence = margin / best if relative else margin
    return AXES[int(order[0])], float(np.clip(confidence, 0.0, 1.0))


def _perpendicular_basis(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    helper = np.array((0.0, 0.0, 1.0)) if abs(direction[2]) < 0.9 else np.array((1.0, 0.0, 0.0))
    first = np.cross(direction, helper)
    first /= np.linalg.norm(first)
    second = np.cross(direction, first)
    return first, second


def _views(axis_vector: np.ndarray) -> list[np.ndarray]:
    """The axial view and four tilts of :data:`TILT_DEG` around it."""

    first, second = _perpendicular_basis(axis_vector)
    tilt = math.radians(TILT_DEG)
    views = [axis_vector]
    for offset in (first, -first, second, -second):
        view = math.cos(tilt) * axis_vector + math.sin(tilt) * offset
        views.append(view / np.linalg.norm(view))
    return views


def _lattice(n: int) -> np.ndarray:
    """Barycentric (u, v) of the centroids of an n x n triangle subdivision."""

    rows = []
    for i in range(n):
        for j in range(n - i):
            rows.append(((3 * i + 1) / (3 * n), (3 * j + 1) / (3 * n)))
            if i + j <= n - 2:
                rows.append(((3 * i + 2) / (3 * n), (3 * j + 2) / (3 * n)))
    return np.asarray(rows, dtype=float)


@dataclass(frozen=True)
class _Samples:
    points: np.ndarray
    normals: np.ndarray
    weights: np.ndarray  # area each sample stands for, mm^2
    triangle: np.ndarray


def _sample(corners: np.ndarray, normals: np.ndarray, areas: np.ndarray, spacing: float) -> _Samples:
    edges = np.stack(
        [
            np.linalg.norm(corners[:, 1] - corners[:, 0], axis=1),
            np.linalg.norm(corners[:, 2] - corners[:, 1], axis=1),
            np.linalg.norm(corners[:, 0] - corners[:, 2], axis=1),
        ],
        axis=1,
    ).max(axis=1)
    subdivisions = np.maximum(1, np.ceil(edges / spacing)).astype(np.int64)
    points, sample_normals, weights, owners = [], [], [], []
    for n in np.unique(subdivisions):
        chosen = np.flatnonzero(subdivisions == n)
        lattice = _lattice(int(n))
        a = corners[chosen, 0][:, None, :]
        ab = (corners[chosen, 1] - corners[chosen, 0])[:, None, :]
        ac = (corners[chosen, 2] - corners[chosen, 0])[:, None, :]
        grid = a + lattice[None, :, 0:1] * ab + lattice[None, :, 1:2] * ac
        count = len(lattice)
        points.append(grid.reshape(-1, 3))
        sample_normals.append(np.repeat(normals[chosen], count, axis=0))
        weights.append(np.repeat(areas[chosen] / count, count))
        owners.append(np.repeat(chosen, count))
    return _Samples(
        points=np.concatenate(points),
        normals=np.concatenate(sample_normals),
        weights=np.concatenate(weights),
        triangle=np.concatenate(owners),
    )


@dataclass(frozen=True)
class _Render:
    #: Per sample: front-facing and not hidden behind another surface.
    visible: np.ndarray
    #: Per sample: the view direction's cosine with the sample's normal.
    facing: np.ndarray
    #: Axial views only.
    top: np.ndarray | None = None
    pixel: np.ndarray | None = None
    shape: tuple[int, int] | None = None
    silhouette_px: int = 0
    backface_fraction: float = 0.0


def _render(samples: _Samples, direction: np.ndarray, pixel: float, *, axial: bool) -> _Render:
    first, second = _perpendicular_basis(direction)
    u = samples.points @ first
    v = samples.points @ second
    height = samples.points @ direction
    iu = np.floor((u - u.min()) / pixel).astype(np.int64)
    iv = np.floor((v - v.min()) / pixel).astype(np.int64)
    width = int(iv.max()) + 1
    flat = iu * width + iv
    size = (int(iu.max()) + 1) * width
    top = np.full(size, -np.inf)
    np.maximum.at(top, flat, height)
    facing = samples.normals @ direction
    # A sample's own surface rises across a pixel by up to the pixel's
    # diagonal times its slope; only another surface in front of that hides it.
    slope = np.sqrt(np.clip(1.0 - facing**2, 0.0, 1.0)) / np.maximum(facing, 0.1)
    tolerance = pixel * (0.5 + math.sqrt(2.0) * np.minimum(slope, 10.0))
    visible = (facing > 0.0) & (height >= top[flat] - tolerance)
    if not axial:
        return _Render(visible=visible, facing=facing)
    exact = 1e-9 * max(1.0, float(np.abs(height).max()))
    on_top = height >= top[flat] - exact
    front_px = np.unique(flat[on_top & (facing > 0.1)])
    back_px = np.unique(flat[on_top & (facing < -0.1)])
    silhouette = int(np.count_nonzero(np.isfinite(top)))
    back_only = np.setdiff1d(back_px, front_px, assume_unique=True)
    return _Render(
        visible=visible,
        facing=facing,
        top=top,
        pixel=flat,
        shape=(int(iu.max()) + 1, width),
        silhouette_px=silhouette,
        backface_fraction=len(back_only) / max(silhouette, 1),
    )


def _aperture(render: _Render, source_visible: np.ndarray, depth: float, pixel: float) -> float:
    """The largest recessed opening, seen along one axis, that a source reaches.

    Returned as a fraction of the silhouette. Recessed means below the
    envelope's front by :data:`RECESS_FRACTION` of the model's depth along the
    view; reached means the opening holds, or borders, a pixel where a voting
    source is directly visible.
    """

    from scipy import ndimage

    assert render.top is not None and render.pixel is not None and render.shape is not None
    top = render.top
    finite = np.isfinite(top)
    if not finite.any():
        return 0.0
    front = float(top[finite].max())
    recess = max(RECESS_FRACTION * depth, 1.5 * pixel)
    recessed = (finite & (top < front - recess)).reshape(render.shape)
    source_px = np.zeros(top.shape, dtype=bool)
    source_px[np.unique(render.pixel[source_visible])] = True
    reach = ndimage.binary_dilation(source_px.reshape(render.shape), iterations=1)
    labels, count = ndimage.label(recessed)
    if count == 0:
        return 0.0
    reached = np.unique(labels[reach & (labels > 0)])
    if not len(reached):
        return 0.0
    sizes = ndimage.sum_labels(np.ones_like(labels), labels, index=reached)
    return float(np.max(sizes)) / max(render.silhouette_px, 1)


# -- the decision ----------------------------------------------------------------


def _role_of(role: str) -> str:
    return str(role).strip().upper()


def infer_frame(mesh: SurveyMesh, *, supported_axes: Iterable[str] = AXES) -> FrameInference:
    """Infer the forward axis of a full model; abstain with a reason in words."""

    supported = tuple(axis for axis in AXES if axis in set(supported_axes))
    points = np.asarray(mesh.points_mm, dtype=float)
    triangles = np.asarray(mesh.triangles, dtype=np.int64)
    tags = np.asarray(mesh.tags, dtype=np.int64)
    if points.ndim != 2 or points.shape[1] != 3 or not len(points):
        return unavailable("the mesh has no points", supported_axes=supported)
    if triangles.ndim != 2 or triangles.shape[1] != 3 or not len(triangles):
        return unavailable("the mesh has no triangles", supported_axes=supported)
    if tags.shape != (len(triangles),):
        return unavailable("the mesh's triangle tags do not match its triangles", supported_axes=supported)
    if not np.isfinite(points).all():
        return unavailable("the mesh has non-finite points", supported_axes=supported)

    def ask(code: str, reason: str, **extra: Any) -> FrameInference:
        return FrameInference(
            algorithm=ALGORITHM_VERSION,
            status=STATUS_ASK,
            axis=None,
            unrestricted_axis=extra.pop("unrestricted_axis", None),
            confidence=float(extra.pop("confidence", 0.0)),
            vote_share=float(extra.pop("vote_share", 0.0)),
            vote_lead=float(extra.pop("vote_lead", 0.0)),
            supporting_evidence=tuple(extra.pop("supporting", ())),
            reason_code=code,
            reason=reason,
            supported_axes=supported,
            evidence=extra.pop("evidence", {}),
        )

    corners = points[triangles]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    doubled = np.linalg.norm(cross, axis=1)
    areas = 0.5 * doubled
    normals = np.divide(cross, doubled[:, None], out=np.zeros_like(cross), where=doubled[:, None] > 0)

    role_by_tag: dict[int, str] = {}
    for source in mesh.sources:
        role_by_tag[int(source.tag)] = _role_of(source.role)
    role_area: dict[str, float] = {}
    role_mask: dict[str, np.ndarray] = {}
    for role in sorted(set(role_by_tag.values())):
        weight = ROLE_WEIGHTS.get(role, 0.0)
        if weight <= 0.0:
            continue
        mask = np.isin(tags, [tag for tag, name in role_by_tag.items() if name == role])
        area = float(areas[mask].sum())
        if area > 0.0:
            role_area[role] = area
            role_mask[role] = mask
    roles_present = sorted(role_area, key=lambda role: (-ROLE_WEIGHTS[role], role))
    evidence: dict[str, Any] = {"roles": {role: {"weight": ROLE_WEIGHTS[role], "areaMm2": round(role_area[role], 3)} for role in roles_present}}

    if not DIRECTIONAL_ROLES & set(roles_present):
        return ask(
            "no-directional-source",
            "This model has no high- or mid-frequency source, so WG cannot tell which "
            "way it faces. Pick the front.",
            evidence=evidence,
        )
    if mesh.identity_problem:
        return ask(
            "source-identity-unvalidated",
            f"WG could not confirm which faces are the sources ({mesh.identity_problem}), "
            "so it will not guess which way the model faces. Pick the front.",
            evidence=evidence,
        )

    total_weight = sum(ROLE_WEIGHTS[role] for role in roles_present)

    # Evidence 1: role-weighted mean oriented normals.
    role_normals = {
        role: (areas[role_mask[role]][:, None] * normals[role_mask[role]]).sum(axis=0) / role_area[role]
        for role in roles_present
    }
    prior = sum(ROLE_WEIGHTS[role] * role_normals[role] for role in roles_present) / total_weight
    normal_scores = _AXIS_VECTORS @ prior
    normal_axis, normal_confidence = _decide(normal_scores, relative=False)
    evidence[NORMALS] = {
        "axis": normal_axis,
        "strength": round(normal_confidence, 6),
        "scores": _round_scores(normal_scores),
        "roleMeanNormals": {role: [round(float(x), 6) for x in role_normals[role]] for role in roles_present},
    }

    # Evidences 2 and 3 share one sampling of the surface.
    low, high = points.min(axis=0), points.max(axis=0)
    diagonal = float(np.linalg.norm(high - low))
    if diagonal <= 0.0:
        return unavailable("the mesh has no extent", supported_axes=supported)
    smallest_role = min(role_area.values())
    pixel = min(diagonal / BASE_RESOLUTION, math.sqrt(smallest_role) / MIN_SOURCE_PIXELS)
    pixel = max(pixel, diagonal / MAX_RESOLUTION)
    total_area = float(areas.sum())
    # Half-pixel spacing keeps every facing pixel covered by several samples.
    while total_area / (0.25 * (pixel / 2.0) ** 2) > MAX_SAMPLES:
        pixel *= 1.25
    samples = _sample(corners, normals, areas, pixel / 2.0)
    sample_role = {role: role_mask[role][samples.triangle] for role in roles_present}
    voting = np.zeros(len(samples.weights), dtype=bool)
    for mask in sample_role.values():
        voting |= mask

    visibility = np.zeros((len(roles_present), len(AXES)))
    aperture = np.zeros(len(AXES))
    backface = np.zeros(len(AXES))
    for axis_index, axis_vector in enumerate(_AXIS_VECTORS):
        heights = points @ axis_vector
        depth = float(heights.max() - heights.min())
        for view_index, direction in enumerate(_views(axis_vector)):
            render = _render(samples, direction, pixel, axial=view_index == 0)
            projected = samples.weights * np.clip(render.facing, 0.0, None) * render.visible
            for role_index, role in enumerate(roles_present):
                visibility[role_index, axis_index] += float(projected[sample_role[role]].sum()) / role_area[role]
            if view_index == 0:
                backface[axis_index] = render.backface_fraction
                aperture[axis_index] = _aperture(
                    render, render.visible & voting & (render.facing > 0.1), depth, pixel
                )
    visibility /= 5.0
    weights = np.asarray([ROLE_WEIGHTS[role] for role in roles_present])
    visibility_scores = (weights @ visibility) / total_weight
    visibility_axis, visibility_confidence = _decide(visibility_scores, relative=True, floor=UNSEEN_VISIBILITY)
    evidence[VISIBILITY] = {
        "axis": visibility_axis,
        "strength": round(visibility_confidence, 6),
        "scores": _round_scores(visibility_scores),
        "roleScores": {role: _round_scores(visibility[index]) for index, role in enumerate(roles_present)},
    }
    aperture_axis, aperture_confidence = _decide(aperture, relative=True, floor=APERTURE_MIN_FRACTION)
    evidence[APERTURE] = {
        "axis": aperture_axis,
        "strength": round(aperture_confidence, 6),
        "scores": _round_scores(aperture),
    }
    evidence["survey"] = {
        "pixelMm": round(pixel, 6),
        "samples": int(len(samples.weights)),
        "tiltDeg": TILT_DEG,
        "backfaceFraction": _round_scores(backface),
    }

    if float(backface.max()) > BACKFACE_LIMIT:
        return ask(
            "orientation-unreliable",
            "Parts of this model's surface face inwards, so WG cannot tell its front "
            "from its back. Pick the front.",
            evidence=evidence,
        )

    unseen = [role for index, role in enumerate(roles_present) if visibility[index].max() < UNSEEN_VISIBILITY]
    unseen_share = sum(ROLE_WEIGHTS[role] for role in unseen) / total_weight
    evidence["unseenRoles"] = unseen
    if unseen_share >= MAX_UNSEEN_WEIGHT_SHARE:
        names = " and ".join(_role_words(role) for role in unseen)
        return ask(
            "sources-not-visible",
            f"The {names} cannot be seen from outside the model (for example, inside a "
            "folded path), so WG cannot tell which way it faces. Pick the front.",
            evidence=evidence,
        )

    votes = np.zeros(len(AXES))
    supporters: dict[str, list[str]] = {axis: [] for axis in AXES}
    for name, axis, confidence in (
        (NORMALS, normal_axis, normal_confidence),
        (VISIBILITY, visibility_axis, visibility_confidence),
        (APERTURE, aperture_axis, aperture_confidence),
    ):
        if axis is None:
            continue
        votes[AXES.index(axis)] += EVIDENCE_WEIGHTS[name] * max(confidence, VOTE_FLOOR)
        if confidence >= MIN_EVIDENCE_STRENGTH:
            supporters[axis].append(name)
    total = float(votes.sum())
    evidence["votes"] = _round_scores(votes)
    if total <= 0.0:
        return ask(
            "no-evidence",
            _cancelled_reason(role_normals, roles_present)
            or "Nothing in this model's geometry shows which way it faces. Pick the front.",
            evidence=evidence,
        )
    order = np.argsort(-votes, kind="stable")
    winner = AXES[int(order[0])]
    share = float(votes[order[0]]) / total
    lead = float(votes[order[0]] - votes[order[1]]) / total
    backing = tuple(supporters[winner])
    common = {
        "unrestricted_axis": winner,
        "confidence": lead,
        "vote_share": share,
        "vote_lead": lead,
        "supporting": backing,
        "evidence": evidence,
    }
    if share < MIN_VOTE_SHARE or lead < MIN_LEAD:
        rival = AXES[int(order[1])]
        return ask(
            "evidence-conflicts",
            _cancelled_reason(role_normals, roles_present)
            or f"The evidence disagrees: parts of it point along {winner}, parts along "
            f"{rival}. Pick the front.",
            **common,
        )
    if len(backing) < MIN_SUPPORTING_EVIDENCE or not GEOMETRIC_EVIDENCE & set(backing):
        return ask(
            "evidence-weak",
            _cancelled_reason(role_normals, roles_present)
            or f"Only {_evidence_words(backing)} points along {winner}; WG needs two "
            "independent signs, one of them from the geometry. Pick the front.",
            **common,
        )
    if winner not in supported:
        allowed = ", ".join(supported) or "no axis"
        return ask(
            "unsupported-axis",
            f"This model appears to face {winner}, but it can be solved only along "
            f"{allowed} (a half or quarter declared in CAD is solved as modelled). "
            "Check the model, or send the whole model.",
            **common,
        )
    return FrameInference(
        algorithm=ALGORITHM_VERSION,
        status=STATUS_AUTOMATIC,
        axis=winner,
        unrestricted_axis=winner,
        confidence=lead,
        vote_share=share,
        vote_lead=lead,
        supporting_evidence=backing,
        reason_code="inferred",
        reason=f"Radiates along {winner}: {_evidence_words(backing)} agree.",
        supported_axes=supported,
        evidence=evidence,
    )


_ROLE_WORDS = {"HF": "high-frequency source", "MF": "midrange sources", "LF": "low-frequency sources"}
_EVIDENCE_WORDS = {
    NORMALS: "the way the sources face",
    VISIBILITY: "where the sources can be seen from",
    APERTURE: "the opening the sources sit in",
}


def _role_words(role: str) -> str:
    return _ROLE_WORDS.get(role, f"{role} sources")


def _evidence_words(names: Iterable[str]) -> str:
    words = [_EVIDENCE_WORDS[name] for name in names]
    if not words:
        return "no evidence"
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + " and " + words[-1]


def _cancelled_reason(role_normals: Mapping[str, np.ndarray], roles: Iterable[str]) -> str | None:
    """Words for sources that face opposite ways, when that is what happened."""

    for role in roles:
        if role in DIRECTIONAL_ROLES and float(np.linalg.norm(role_normals[role])) < 0.3:
            return (
                f"The {_role_words(role)} face different ways, so WG cannot tell which "
                "way the model faces. Pick the front."
            )
    directional = [role for role in roles if role in DIRECTIONAL_ROLES]
    if len(directional) >= 2:
        first, second = (role_normals[role] for role in directional[:2])
        if float(first @ second) < -0.5:
            return (
                "The sources face different ways, so WG cannot tell which way the "
                "model faces. Pick the front."
            )
    return None


# -- ingestion records -----------------------------------------------------------


def parse_tagged_msh(msh_text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[int, str]]:
    """Points, triangles, per-triangle physical tags and physical names of an ASCII MSH 2.2."""

    lines = [line.strip() for line in msh_text.splitlines()]
    try:
        version = lines[lines.index("$MeshFormat") + 1].split()
    except (ValueError, IndexError) as exc:
        raise SurveyUnavailable("the mesh artifact is not an ASCII Gmsh 2.2 mesh") from exc
    if len(version) < 2 or not version[0].startswith("2.") or version[1] != "0":
        raise SurveyUnavailable("the mesh artifact is not an ASCII Gmsh 2.2 mesh")
    names: dict[int, str] = {}
    if "$PhysicalNames" in lines:
        start = lines.index("$PhysicalNames")
        for row in lines[start + 2 : start + 2 + int(lines[start + 1])]:
            parts = row.split(maxsplit=2)
            if len(parts) == 3 and parts[0] == "2":
                names[int(parts[1])] = parts[2].strip('"')
    try:
        nodes_start = lines.index("$Nodes")
        count = int(lines[nodes_start + 1])
        rows = [line.split() for line in lines[nodes_start + 2 : nodes_start + 2 + count]]
        index = {row[0]: position for position, row in enumerate(rows)}
        points = np.asarray([row[1:4] for row in rows], dtype=float).reshape(-1, 3)
        elements_start = lines.index("$Elements")
        element_count = int(lines[elements_start + 1])
        corners: list[list[int]] = []
        tags: list[int] = []
        for line in lines[elements_start + 2 : elements_start + 2 + element_count]:
            parts = line.split()
            if len(parts) < 4 or parts[1] != "2":
                continue
            tag_count = int(parts[2])
            corners.append([index[node] for node in parts[3 + tag_count : 6 + tag_count]])
            tags.append(int(parts[3]) if tag_count >= 1 else 0)
    except (ValueError, IndexError, KeyError) as exc:
        raise SurveyUnavailable("the mesh artifact does not parse") from exc
    return (
        points,
        np.asarray(corners, dtype=np.int64).reshape(-1, 3),
        np.asarray(tags, dtype=np.int64),
        names,
    )


_PLANE_AXIS = {"x0": 0, "y0": 1}


def mirror_back(
    points: np.ndarray, triangles: np.ndarray, tags: np.ndarray, planes: Iterable[str]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The full model from a half or quarter reduced on ``planes`` (solver frame).

    Each reflection copies the mesh across its plane and reverses the copy's
    winding, so the full model stays wound outward.
    """

    for plane in planes:
        if plane not in _PLANE_AXIS:
            raise SurveyUnavailable(f"cannot mirror across {plane!r}")
        reflected = points.copy()
        reflected[:, _PLANE_AXIS[plane]] *= -1.0
        offset = len(points)
        points = np.concatenate([points, reflected])
        triangles = np.concatenate([triangles, triangles[:, [0, 2, 1]] + offset])
        tags = np.concatenate([tags, tags])
    return points, triangles, tags


def _record_identity_problem(record: Mapping[str, Any], names: Mapping[int, str], tags: np.ndarray) -> str | None:
    findings = [
        finding
        for finding in record.get("findings") or []
        if isinstance(finding, Mapping) and finding.get("kind") in IDENTITY_FINDINGS
    ]
    if findings:
        ids = sorted({str(finding.get("source_id") or "?") for finding in findings})
        return "the painted and resolved faces disagree for " + ", ".join(ids)
    tag_map = record.get("tag_map")
    if not isinstance(tag_map, Mapping):
        return "the record has no source tags"
    present = set(int(tag) for tag in np.unique(tags))
    for key, meaning in tag_map.items():
        if not isinstance(meaning, Mapping) or meaning.get("source_id") is None:
            continue
        tag = int(key)
        if tag not in present:
            return f"source {meaning['source_id']} has no faces in the mesh"
        name = names.get(tag, "")
        expected = (
            f"|source_id={meaning['source_id']}|",
            f"|role={meaning.get('role')}",
        )
        if not all(part in name for part in expected):
            return f"the mesh does not name source {meaning['source_id']} as the record does"
    return None


def survey_mesh_from_record(record: Mapping[str, Any], msh_text: str) -> SurveyMesh:
    """An ingestion record's solver mesh as a full model in CAD coordinates."""

    from server.solver.imported import imported_domain_planes

    points_m, triangles, tags, names = parse_tagged_msh(msh_text)
    if not len(triangles):
        raise SurveyUnavailable("the mesh has no triangles")
    normalisation = record.get("normalisation")
    matrix = normalisation.get("matrix") if isinstance(normalisation, Mapping) else None
    solver_from_assembly = np.asarray(matrix, dtype=float) if matrix is not None else None
    if solver_from_assembly is None or solver_from_assembly.shape != (4, 4):
        raise SurveyUnavailable("the record states no normalisation matrix")
    rotation = solver_from_assembly[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) or np.linalg.det(rotation) <= 0:
        raise SurveyUnavailable("the record's normalisation is not a rotation")
    points_mm = points_m * 1000.0
    points_mm, triangles, tags = mirror_back(points_mm, triangles, tags, imported_domain_planes(record))
    # assembly = R^T (solver - t)
    cad = (points_mm - solver_from_assembly[:3, 3]) @ rotation
    tag_map = record.get("tag_map") if isinstance(record.get("tag_map"), Mapping) else {}
    sources = tuple(
        SurveySource(tag=int(key), source_id=str(meaning["source_id"]), role=str(meaning.get("role")))
        for key, meaning in sorted(tag_map.items(), key=lambda item: int(item[0]))
        if isinstance(meaning, Mapping) and meaning.get("source_id") is not None
    )
    return SurveyMesh(
        points_mm=cad,
        triangles=triangles,
        tags=tags,
        sources=sources,
        identity_problem=_record_identity_problem(record, names, tags),
    )


def infer_record_frame(
    record: Mapping[str, Any], msh_text: str, *, supported_axes: Iterable[str] = AXES
) -> FrameInference:
    """:func:`infer_frame` over an ingestion record; never raises for bad geometry."""

    supported = tuple(supported_axes)
    try:
        mesh = survey_mesh_from_record(record, msh_text)
    except SurveyUnavailable as exc:
        return unavailable(str(exc), supported_axes=supported)
    return infer_frame(mesh, supported_axes=supported)


__all__ = [
    "ALGORITHM_VERSION",
    "AXES",
    "EVIDENCE_WEIGHTS",
    "FrameInference",
    "ROLE_WEIGHTS",
    "STATUS_ASK",
    "STATUS_AUTOMATIC",
    "STATUS_UNAVAILABLE",
    "SurveyMesh",
    "SurveySource",
    "SurveyUnavailable",
    "infer_frame",
    "infer_record_frame",
    "mirror_back",
    "parse_tagged_msh",
    "survey_mesh_from_record",
    "unavailable",
]
