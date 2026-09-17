"""Which setup a snapshot is prepared with when its operation names none.

docs/architecture/CAD-OPERATIONS.md, "Project setups". A solve Fusion sends for
project B is prepared from B's own setup -- the one WG last recorded for that
project and its source inventory -- while the editor keeps whatever project is
open. Nothing here reads the live UI: the frontend records a project's setup as
the user changes it, and the solver selection likewise.

The engine is always the one selected in WG: a project's recorded setup keeps
the engine it was recorded with only until the selection says otherwise. The
setup a preparation used is itself a setup revision, so the operation names the
exact inputs it was prepared and solved with.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from typing import Any

from server.jobs.models import SolveRequest

from .operations import canonical_json
from .roles import canonical_source_role
from .setup import CadSolveSetup, setup_content, setup_digest, validate_setup
from .store import CadLinkStore


SOLVER_SELECTION = "solver_selection"
# As the frontend's importedSubmission.ts states them.
_POLAR_AXIS_ORDER = ("horizontal", "vertical", "diagonal")
_DEFAULT_DIAGONAL_INCLINATION_DEG = 45


def inventory_sha256(sources: Sequence[Mapping[str, Any]]) -> str:
    """A source inventory's identity: which sources, in which roles, required or not.

    Roles are canonical (``canonical_source_role``), as the returns listing and
    so the frontend state them, whatever spelling the manifest used.

    The source ``id`` is part of the key. When a return requires
    ``source-identity-v1`` that id is the CAD-authored identity of the logical
    source, the same across exports, so a project's setup follows its sources
    from one export to the next and a reassigned identity does not inherit it.
    The byte shape is unchanged, so an inventory from a return without the
    feature keeps its exact digest.
    """

    entries = sorted(
        [
            str(item.get("id") or ""),
            canonical_source_role(str(item.get("role") or "")),
            bool(item.get("required")),
        ]
        for item in sources
        if isinstance(item, Mapping)
    )
    return "sha256:" + hashlib.sha256(canonical_json(entries).encode("utf-8")).hexdigest()


def solver_anchor(manifest: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """A snapshot's solver anchor instance: the one its frame names, else its only one.

    The project a snapshot belongs to (``snapshot_project``) and the design that
    backend preparation names to the ingest's project gate
    (``preparation._project_gate``) both come from this, so they cannot disagree.
    """

    instances = [
        instance for instance in manifest.get("instances") or [] if isinstance(instance, Mapping)
    ]
    frame = manifest.get("coordinate_system")
    anchor_id = frame.get("solver_anchor_instance_id") if isinstance(frame, Mapping) else None
    anchor = next(
        (instance for instance in instances if instance.get("instance_id") == anchor_id), None
    )
    if anchor is None and len(instances) == 1:
        anchor = instances[0]
    return anchor


def snapshot_project(store: CadLinkStore, manifest: Mapping[str, Any]) -> str | None:
    """The project lineage a snapshot belongs to, without claiming one.

    The same project ingestion files the return under (``ingest._resolve_project``):
    the lineage of the solver anchor instance's WG design, or, when the anchor
    names no design, the lineage its Fusion document already has. None when WG
    has never seen that document: such a snapshot has no recorded setup yet.
    """

    anchor = solver_anchor(manifest)
    design_id = str((anchor or {}).get("design_id") or "").strip()
    if design_id:
        row = store.get_design(design_id)
        return str((row or {}).get("lineage_id") or "").strip() or None
    document = manifest.get("document") if isinstance(manifest.get("document"), Mapping) else {}
    native_id = str(document.get("native_id") or "").strip()
    if not native_id:
        return None
    row = store.get_lineage_for_cad_document(native_id)
    return str((row or {}).get("lineage_id") or "").strip() or None


def solver_selection(store: CadLinkStore) -> str | None:
    """The engine selected in WG's solver selector, as the frontend last recorded it."""

    value = store.get_setting(SOLVER_SELECTION)
    engine = value.get("engine") if isinstance(value, Mapping) else None
    return str(engine) if isinstance(engine, str) and engine else None


def project_setup(
    store: CadLinkStore, lineage_id: str, sources: Sequence[Mapping[str, Any]]
) -> tuple[CadSolveSetup, str] | None:
    """The setup a project's snapshot is prepared with, and its revision id.

    The project's recorded setup for exactly these sources, with the engine
    selected in WG. None when the project has none for them: a first-time
    model waits for the user to choose its settings.
    """

    row = store.get_project_setup(lineage_id, inventory_sha256(sources))
    if row is None:
        return None
    revision = store.get_setup_revision(str(row["revision_id"]))
    if revision is None:
        return None
    setup = validate_setup(json.loads(revision["setup_json"]))
    engine = solver_selection(store)
    if engine and setup.options.get("engine") != engine:
        setup = validate_setup(
            {**setup.model_dump(mode="json"), "options": {**setup.options, "engine": engine}}
        )
        revision = store.create_setup_revision(setup_content(setup), setup_digest(setup))
    return setup, str(revision["revision_id"])


def widen_polar_to_derivation(request: SolveRequest, derivation: Any) -> SolveRequest:
    """Never ask for a narrower polar grid than the ingestion derived.

    The runtime refuses a narrower one (``polar_grid_narrowing``). This is the
    server's copy of the frontend's ``widenPolarToDerivation``: the range grows
    to cover every axis's derived extent at the requested step, rounding as
    JavaScript does, and an axis pinned over the full circle is enabled. An
    axis that states no extent is read as the runtime reads it: pinned.
    """

    axes = derivation.get("axes") if isinstance(derivation, Mapping) else None
    if not isinstance(axes, Mapping) or not axes:
        return request
    data = request.model_dump(mode="json")
    polar = (data.get("options") or {}).get("polar_config")
    if not isinstance(polar, dict):
        return request
    start, end, count = (float(polar["angle_range"][0]), float(polar["angle_range"][1]),
                         int(polar["angle_range"][2]))
    step = (end - start) / (count - 1) if count > 1 else 5.0
    requested = set(polar.get("enabled_axes") or [])
    enabled = set(requested)
    low, high = start, end
    for axis, spec in axes.items():
        spec = spec if isinstance(spec, Mapping) else {}
        minimum = float(spec.get("minimum_deg", -180.0))
        maximum = float(spec.get("maximum_deg", 180.0))
        if minimum <= -180.0 and maximum >= 180.0:
            enabled.add(str(axis))
        low, high = min(low, minimum), max(high, maximum)
    if (low, high) != (start, end):
        # Math.round, not Python's half-to-even: the same request as the frontend's.
        samples = math.floor((high - low) / step + 0.5) + 1
        polar["angle_range"] = [low, high, max(count, samples)]
    polar["enabled_axes"] = [axis for axis in _POLAR_AXIS_ORDER if axis in enabled]
    if "diagonal" in enabled and "diagonal" not in requested:
        # A diagonal the user never enabled carries no inclination intent; the
        # runtime accepts only the supported default for one forced on.
        polar["inclination"] = _DEFAULT_DIAGONAL_INCLINATION_DEG
    return SolveRequest.model_validate(data)


__all__ = [
    "SOLVER_SELECTION",
    "inventory_sha256",
    "project_setup",
    "snapshot_project",
    "solver_selection",
    "widen_polar_to_derivation",
]
