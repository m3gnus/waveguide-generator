"""Which setup a snapshot is prepared with when its operation names none.

docs/architecture/CAD-OPERATIONS.md, "Project setups". A solve Fusion sends for
project B is prepared from B's own setup -- the one WG last recorded for that
project and its source inventory -- while the editor keeps whatever project is
open. Nothing here reads the live UI: the frontend records a project's setup as
the user changes it, and the solver selection likewise.

The solver selection only seeds a model's first setup (WG's defaults,
``default_setup``). Once a model has a setup of its own, that setup wins,
engine and accuracy included, for a solve started in WG and one Fusion sends
alike. The setup a preparation used is itself a setup revision, so the
operation names the exact inputs it was prepared and solved with.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
from typing import Any

from server.jobs.models import SolveRequest

from server.cadlink.interpretation.domain_interpretation import excitation_problem
from server.cadlink.roles import inventory_sha256
from server.jobs.imported_setup import CadSolveSetup, solve_request_for, validate_setup
from server.cadlink.store import CadLinkStore


SOLVER_SELECTION = "solver_selection"
# As the frontend's importedSubmission.ts states them.
_POLAR_AXIS_ORDER = ("horizontal", "vertical", "diagonal")
_DEFAULT_DIAGONAL_INCLINATION_DEG = 45


def solver_selection(store: CadLinkStore) -> dict[str, str]:
    """The shared accuracy and engine choice, as WG last recorded it.

    It seeds a first-time model's setup only (``default_setup``); it never
    overrides a setup the model already has.
    """

    value = store.get_setting(SOLVER_SELECTION)
    engine = value.get("engine") if isinstance(value, Mapping) else None
    accuracy = value.get("accuracy") if isinstance(value, Mapping) else None
    return {
        **({"engine": engine} if isinstance(engine, str) and engine else {}),
        "accuracy": "accurate" if accuracy == "accurate" else "fast",
    }


def project_setup(
    store: CadLinkStore, lineage_id: str, sources: Sequence[Mapping[str, Any]]
) -> tuple[CadSolveSetup, str] | None:
    """The setup a project's snapshot is prepared with, and its revision id.

    The project's recorded setup for exactly these sources, as it was
    recorded: its engine and accuracy are the model's own, whatever the solver
    selection says now. None when the project has none for them: a first-time
    model is then solved with WG's default settings (``default_setup``),
    which the solver selection seeds.
    """

    row = store.get_model_setup(lineage_id, inventory_sha256(sources))
    if row is None:
        return None
    revision = store.get_setup_revision(str(row["revision_id"]))
    if revision is None:
        return None
    return validate_setup(json.loads(revision["setup_json"])), str(revision["revision_id"])


class ExcitationRefused(ValueError):
    """The setup's excitation cannot drive the prepared mesh's domain."""


def compose_solve_request(
    setup: CadSolveSetup,
    record: Mapping[str, Any],
    *,
    acknowledged_findings: Sequence[str] = (),
    client_request_id: str | None = None,
    label: str | None = None,
    fem_region_ids: Sequence[str] | None = None,
) -> SolveRequest:
    """The one request a setup makes of one prepared mesh, checked as every lane checks it.

    Shared by the WG Solve of a model (``server/jobs/model_solve.py``), the
    job-owned preparation of a Fusion "Solve in WG" (``server/jobs/
    cad_preparation.py``): the setup bound to the prepared
    mesh's identity, the polar grid never narrower than the ingestion derived
    (the runtime refuses that), and the excitation judged against the domain.
    Raises ``ValueError`` -- ``ExcitationRefused`` for the excitation -- with
    the reason.
    """

    request = solve_request_for(
        setup,
        ingest_id=str(record["ingest_id"]),
        manifest_sha256=str(record["manifest_sha256"]),
        artifact_sha256=str(record["artifact_sha256"]),
        acknowledged_findings=list(acknowledged_findings),
        client_request_id=client_request_id,
        label=label,
        fem_region_ids=list(fem_region_ids) if fem_region_ids is not None else None,
    )
    request = widen_polar_to_derivation(request, record.get("polar_grid_derivation"))
    # Acoustic compatibility, a hard check on every submission: the excitation
    # is the setup's, so it is judged again whenever the settings change.
    excitation = excitation_problem(record, getattr(request.geometry, "drive_channels", ()))
    if excitation is not None:
        raise ExcitationRefused(excitation)
    return request


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
    "ExcitationRefused",
    "SOLVER_SELECTION",
    "compose_solve_request",
    "inventory_sha256",
    "project_setup",
    "solver_selection",
    "widen_polar_to_derivation",
]
