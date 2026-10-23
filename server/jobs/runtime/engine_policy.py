"""One engine selection and actionable refusal policy for every solve caller."""

from dataclasses import dataclass, field
from typing import Annotated, Awaitable, Callable, Literal, Sequence

from pydantic import BaseModel, Field

from server.engines.registry import EngineInfo, full3d_engine_order


class PrepareRemedy(BaseModel):
    kind: Literal["prepare"] = "prepare"
    engine: str
    disabled_reason: str | None = None


class UseEngineRemedy(BaseModel):
    kind: Literal["use_engine"] = "use_engine"
    engine: str
    label: str
    same_solver: bool


class WaitRemedy(BaseModel):
    kind: Literal["wait"] = "wait"


class SettingsRemedy(BaseModel):
    kind: Literal["settings"] = "settings"
    section: str = "simulation"


SolveRemedy = Annotated[
    PrepareRemedy | UseEngineRemedy | WaitRemedy | SettingsRemedy,
    Field(discriminator="kind"),
]


class SolveBlocker(BaseModel):
    code: str
    message: str
    engine: str
    remedies: list[SolveRemedy] = Field(default_factory=list)


class EngineUnavailableError(RuntimeError):
    """A known engine cannot run here; older callers may supply just prose."""

    def __init__(self, message: str, *, blocker: SolveBlocker | None = None):
        self.blocker = blocker
        self.details = {"blocker": blocker.model_dump(mode="json")} if blocker else {}
        super().__init__(message)


@dataclass(frozen=True)
class EngineVerdict:
    solves: bool
    stage: str | None = None
    code: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class EngineSelection:
    engine: str
    passed_over: list[str] = field(default_factory=list)
    fallback_reason: str | None = None

    @property
    def precision(self) -> str | None:
        """The selected BEAT backend fixes execution precision, including Auto."""
        if self.engine == "beat-cpu":
            return "float64"
        if self.engine in {"beat-metal", "beat-cuda", "beat-rocm"}:
            return "float32"
        return None


ACCURATE_CPU_FALLBACK = "Accurate ran on BEAT CPU because no GPU backend was ready"


def _same_solver(a: str, b: str) -> bool:
    return a == b or (a.startswith("beat") and b.startswith("beat-"))


async def select_engine(
    requested: str,
    accuracy: str,
    candidates: Sequence[EngineInfo],
    verdict: Callable[[EngineInfo], Awaitable[EngineVerdict]],
    *,
    alternative_verdict: Callable[[EngineInfo], Awaitable[EngineVerdict]] | None = None,
    preferred: Sequence[str] = (),
) -> EngineSelection | SolveBlocker:
    """Explicit choices never substitute; Auto walks the declared ready order.

    Accurate Auto and the legacy BEAT family walk only BEAT backends. Geometry
    admission stays with the supplied verdict, also used to validate remedies.
    """
    declared = {info.name: info for info in candidates}
    order = full3d_engine_order()
    family = requested == "beat" or (requested == "auto" and accuracy == "accurate")
    walk = (
        (
            tuple(n for n in ("beat-metal", "beat-cuda", "beat-rocm", "beat-cpu") if n in order)
            if family
            else order
        )
        if requested in {"auto", "beat"}
        else (requested,)
    )
    if requested in {"auto", "beat"} and preferred:
        walk = tuple(n for n in preferred if n in walk) + tuple(
            n for n in walk if n not in preferred
        )
    if requested == "beat" and "beat" in declared:
        walk = (*walk, "beat")
    answers = {}
    passed_over = []
    for name in walk:
        info = declared.get(name)
        if info is None:
            continue
        answer = await verdict(info)
        answers[name] = answer
        if answer.solves:
            return EngineSelection(
                name, passed_over, ACCURATE_CPU_FALLBACK if family and name == "beat-cpu" else None
            )
        passed_over.append(f"{name}: {answer.reason}")

    # Auto names a concrete engine with a fix, preferring a compatible runtime
    # that can be prepared over an unsupported or absent device.
    target = requested
    if requested in {"auto", "beat"}:
        unavailable_names = [
            n for n in walk if n in answers and answers[n].stage in {"availability", "adapter"}
        ]
        recoverable = [
            n
            for n in unavailable_names
            if declared[n].runtime_preparable or declared[n].runtime_state == "preparing"
        ]
        disabled_names = [
            n for n in unavailable_names if declared[n].runtime_state in {"unprepared", "failed"}
        ]
        target = next(
            iter(recoverable or disabled_names or unavailable_names or list(answers)),
            "beat-cpu" if family else "metal",
        )
    info = declared.get(target)
    answer = answers.get(
        target,
        EngineVerdict(
            False,
            "availability",
            "engine_unavailable",
            "not available on this machine (this host does not detect it)",
        ),
    )
    if target == "dryrun" and info is None:
        answer = EngineVerdict(
            False,
            "availability",
            "engine_unavailable",
            "Dry-run solves require WG2_ENABLE_DRYRUN=1.",
        )
    label = info.display_label() if info else target
    remedies: list[SolveRemedy] = []
    unavailable = answer.stage in {"availability", "adapter"}
    code = "engine_unavailable" if unavailable else (answer.code or "engine_unsupported")
    if unavailable and info and info.runtime_state == "preparing":
        message = f"{label} is still preparing. Wait for preparation to finish."
        remedies.append(WaitRemedy())
    elif unavailable and info and info.runtime_state in {"unprepared", "failed"}:
        disabled = None if info.runtime_preparable else info.reason
        remedies.append(PrepareRemedy(engine=target, disabled_reason=disabled))
        message = (
            f"{label} needs preparing. Prepare {label} to solve."
            if disabled is None
            else f"{label} cannot be prepared here: {disabled}"
        )
    elif unavailable:
        message = f"Solve engine '{target}' is unavailable. {answer.reason or 'Not available on this machine.'}"
    else:
        message = f"{label} cannot solve this model: {(answer.reason or 'unsupported features').rstrip('.')}."

    if requested in {"auto", "beat"} and len(passed_over) > 1:
        message += " No ready compatible engine: " + "; ".join(passed_over) + "."

    # A remedy is evaluated as an explicit pick, including adapter preflight.
    # Keep same-physics BEAT backends ahead of different formulations.
    alternatives = []
    for name in order:
        other = declared.get(name)
        if name == target or other is None or not other.available or name == "dryrun":
            continue
        result = await (alternative_verdict or verdict)(other)
        if result.solves:
            same = _same_solver(target, name)
            alternatives.append(
                UseEngineRemedy(
                    engine=name,
                    same_solver=same,
                    label=f"Use {other.display_label()}"
                    + ("" if same else " (different solver — results will differ)"),
                )
            )
    alternatives.sort(key=lambda remedy: not remedy.same_solver)
    remedies.extend(alternatives)
    remedies.append(SettingsRemedy())
    if alternatives:
        message += " " + "; ".join(remedy.label for remedy in alternatives) + "."
    return SolveBlocker(code=code, message=message, engine=target, remedies=remedies)
