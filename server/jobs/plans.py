"""The shared plan payload for planning and solver-mesh previews."""

from typing import Literal

from pydantic import BaseModel

from server.jobs.runtime.core import SubmissionResolution
from server.jobs.runtime.engine_policy import SolveBlocker


class EngineSubstitution(BaseModel):
    """The engine the request asked for, and the one standing in for it.

    Present only when a stored selection named an engine this host cannot run.
    `requested` is left exactly as the caller sent it, because the selection is
    host-local UI state that should re-engage untouched the day that engine
    becomes available here.
    """

    requested: str
    resolved: str
    reason: str


class PlanAdjustment(BaseModel):
    """A change the runtime makes to the submitted design before solving it.

    `bempp_wall_default` is the one kind today: a free-standing BEMPP solve
    whose wall thickness was left unset (`omitted`) or set to 0 mm, a bare
    shell (`explicit_zero`), is solved with an `effective_mm` closed wall.
    The explicit case is an override of what the user asked for, which is why
    the two are reported apart. `policy_version` names the rule that applied,
    so a run stays attributable if that rule later changes.
    """

    kind: Literal["bempp_wall_default"]
    requested: Literal["omitted", "explicit_zero"]
    effective_mm: float
    reason_code: str
    policy_version: int


class SolvePlanResponse(BaseModel):
    """The request-specific engine/formulation selected by the runtime."""

    readiness: SolveBlocker | None = None
    engine: str
    formulation: Literal["full-3d"]
    reason: str
    eligibility_reasons: list[str]
    engine_substitution: EngineSubstitution | None = None
    adjustments: list[PlanAdjustment] = []
    refusal: str | None = None


def solve_plan_response(resolved: SubmissionResolution) -> SolvePlanResponse:
    """Expose the same resolved decision on both preview endpoints."""

    plan = resolved.symmetry_metadata["solver_plan"]
    substitution = plan.get("engine_substitution")
    return SolvePlanResponse(
        engine=resolved.engine_name,
        readiness=plan.get("readiness"),
        refusal=plan.get("refusal"),
        formulation=plan["formulation"],
        reason=plan["reason"],
        eligibility_reasons=[str(reason) for reason in plan["eligibility_reasons"]],
        engine_substitution=(
            EngineSubstitution(**substitution) if substitution else None
        ),
        adjustments=[
            PlanAdjustment(**adjustment)
            for adjustment in plan.get("adjustments") or []
        ],
    )
