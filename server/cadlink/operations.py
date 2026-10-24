"""The CAD operation contract: kinds, request identity, states and outcomes.

``docs/architecture/CAD-OPERATIONS.md`` is the specification and this module is
its executable half; change them together. Everything here is pure. The
durable half is ``CadLinkStore``'s ``cad_operations`` table.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from typing import Any, Literal

from server.contracts.canonical import canonical_utf8_strict_json as canonical_json


DIGEST_VERSION = 1

RECEIVE_SNAPSHOT = "receive_snapshot"
PREPARE_AND_SOLVE = "prepare_and_solve"
REQUEST_RETURN = "request_return"
INSERT_LINK = "insert_link"
UPDATE_LINK = "update_link"
KINDS = frozenset(
    {RECEIVE_SNAPSHOT, PREPARE_AND_SOLVE, REQUEST_RETURN, INSERT_LINK, UPDATE_LINK}
)

RECEIVED = "received"
PROCESSING = "processing"
NEEDS_USER_INPUT = "needs_user_input"
ACCEPTED = "accepted"
REJECTED = "rejected"
CANCELLED = "cancelled"
# Reserved: a Fusion mutation began and its completion evidence is missing.
# Never retried automatically, and not a rejection.
RECOVERY_REQUIRED = "recovery_required"
# The user dismissed an operation an attempt is working on. The attempt stops
# at its next step and records ``cancelled``; nothing claims it meanwhile.
CANCEL_REQUESTED = "cancel_requested"
STATES = frozenset(
    {
        RECEIVED,
        PROCESSING,
        NEEDS_USER_INPUT,
        ACCEPTED,
        REJECTED,
        CANCELLED,
        RECOVERY_REQUIRED,
        CANCEL_REQUESTED,
    }
)
# Where a CAD solve stands, independent of its state: how far its preparation
# got. A stage only moves forward within one attempt; a new attempt starts again
# from ``validating``.
STAGE_RECEIVED = "received"
STAGE_VALIDATING = "validating"
STAGE_PREPARING_MESH = "preparing-mesh"
STAGE_READY = "ready"
STAGE_SUBMITTED = "submitted"
# Persisted stages from legacy HTTP claims. Keep these values readable for
# file evidence settlement and bounded interruption recovery after an upgrade.
STAGE_ADAPTER_RECEIVED = "adapter-received"
STAGE_QUEUED_FOR_FUSION = "queued-for-fusion"
STAGE_EXECUTING = "executing"
FUSION_STAGES = (STAGE_ADAPTER_RECEIVED, STAGE_QUEUED_FOR_FUSION, STAGE_EXECUTING)
STAGES = (
    STAGE_RECEIVED,
    STAGE_VALIDATING,
    STAGE_PREPARING_MESH,
    STAGE_READY,
    STAGE_SUBMITTED,
    *FUSION_STAGES,
)
TERMINAL_STATES = frozenset({ACCEPTED, REJECTED, CANCELLED})
CLAIMABLE_STATES = frozenset({RECEIVED, PROCESSING, NEEDS_USER_INPUT})
# ``received`` is where acceptance starts, ``processing`` is what a claim sets
# and ``cancel_requested`` is what a dismissal of a running attempt sets; none
# is an outcome.
RECORDABLE_STATES = STATES - {RECEIVED, PROCESSING, CANCEL_REQUESTED}

# The reason a solve waits when an approved update restart held it at submission
# (docs/architecture/CAD-OPERATIONS.md, "Preparation"). The same string as the
# jobs system's refusal code, ``server.updates.restart.UPDATE_RESTART_PENDING``.
REASON_UPDATE_RESTART_PENDING = "update_restart_pending"

# Reason code -> the only state it may accompany. Both are rejections because a
# refreshed baseline or target is a new operation, never a retry of this one.
REASON_CODES: Mapping[str, str] = {
    "baseline_conflict": REJECTED,
    "target_not_exact": REJECTED,
    # A snapshot that fails verification, or contradicts the operation's inputs.
    "snapshot_invalid": REJECTED,
    "request_payload_invalid": REJECTED,
    # A received snapshot whose bundle stayed unreadable past its bound.
    "snapshot_unavailable": REJECTED,
    # A model cut open in CAD that WG cannot rebuild into the whole speaker
    # (``cad_preparation.MODEL_CUT_UNSOLVABLE``): the same return never solves.
    "model_cut_unsolvable": REJECTED,
    # Cannot proceed now: each waits for the user, and the operation is kept.
    "setup_required": NEEDS_USER_INPUT,
    "findings_need_review": NEEDS_USER_INPUT,
    # An unlinked (CAD-authored) model whose solver frame its project has not
    # confirmed for this frame requirement (server/cadlink/interpretation/solver_frame.py).
    "frame_confirmation_required": NEEDS_USER_INPUT,
    "preparation_failed": NEEDS_USER_INPUT,
    "mesh_too_large": NEEDS_USER_INPUT,
    # The solver's dense memory guard refused the mesh (after Solve anyway):
    # the user can only send a coarser mesh. A calm failure, never a hold.
    "solver_memory_exceeded": NEEDS_USER_INPUT,
    "engine_unavailable": NEEDS_USER_INPUT,
    # The engine the setup names is present but cannot solve this return.
    "engine_cannot_solve_return": NEEDS_USER_INPUT,
    "submission_refused": NEEDS_USER_INPUT,
    "interrupted": NEEDS_USER_INPUT,
    # Held by an approved update restart; re-queued once the latch is down.
    REASON_UPDATE_RESTART_PENDING: NEEDS_USER_INPUT,
    # Prepared, and waiting for the user to start the solve.
    "ready_to_solve": NEEDS_USER_INPUT,
    # A WG-produced Fusion request that never started can finish locally.
    "superseded": CANCELLED,
    "expired": CANCELLED,
    "session_changed": CANCELLED,
    "publication_failed": CANCELLED,
    "adapter_refused": REJECTED,
    # The adapter reported that the request failed before it changed anything.
    "adapter_failed": REJECTED,
    "adapter_not_started": CANCELLED,
}


#: WG's own pipeline failures: developer matters, never a choice for the user.
#: They end as a calm failed preparation ("WG could not prepare ..."), with the
#: raw detail recorded for developers, never as a "Waiting for you" hold.
#: A rejection the user can act on (``model_cut_unsolvable``: send the uncut
#: model) is not internal: it keeps its own words and is never a hold either.
INTERNAL_FAILURE_CODES = frozenset(
    {"preparation_failed", "submission_refused", "interrupted", "snapshot_invalid"}
)
#: Failed preparations the read model words itself, whatever text is stored.
CALM_FAILURE_CODES = INTERNAL_FAILURE_CODES | {"solver_memory_exceeded"}
_MESH_CEILING_PREFIX = "This model meshes to "


def _effective_code(code: Any, message: Any) -> str:
    """Legacy rows stored the mesh ceiling as ``preparation_failed``."""

    text = str(code or "")
    if text == "preparation_failed" and str(message or "").startswith(_MESH_CEILING_PREFIX):
        return "mesh_too_large"
    return text


def is_calm_failure(code: Any, message: Any = "") -> bool:
    """Whether a refusal is a failed preparation rather than a user choice."""

    return _effective_code(code, message) in CALM_FAILURE_CODES


def internal_failure_message(document_name: Any) -> str:
    """The one sentence a user sees for an internal preparation failure."""

    name = str(document_name or "").strip() or "this model"
    return f"WG could not prepare {name}. This has been recorded for the developers."


def preparation_failure(code: str, document_name: Any) -> dict[str, Any]:
    """The read model of a failed preparation: calm words, and what can still work."""

    name = str(document_name or "").strip() or "this model"
    dismiss = {"id": "dismiss", "label": "Dismiss request"}
    if code == "solver_memory_exceeded":
        return {
            "code": code, "message": f"{name} is too large for this machine's memory.",
            "next_step": "Use a coarser mesh resolution in Sources & Mesh… in Fusion, then send it again.",
            "options": [dismiss],
        }
    # A rejected snapshot cannot be solved again; only dismissing it works.
    retry = [] if REASON_CODES.get(code) == REJECTED else [{"id": "retry", "label": "Try again"}]
    return {"code": code, "message": internal_failure_message(name), "next_step": None,
            "options": [*retry, dismiss]}


def user_hold(status: str, cad: Mapping[str, Any]) -> bool:
    """Include refusals saved before holds had their own job status.

    A failed preparation is never a hold, however an older version stored it.
    """

    refusal = cad.get("refusal") or {}
    code, message = refusal.get("code"), refusal.get("message")
    if is_calm_failure(code, message):
        return False
    return status == "held" or (
        status == "error" and REASON_CODES.get(code) == NEEDS_USER_INPUT
    )


def hold_details(code: str, message: str) -> dict[str, Any]:
    """One vocabulary for persisted holds and historical request readers."""

    choice_code = _effective_code(code, message)
    action, label, next_step = {
        "mesh_too_large": (
            "solve_anyway", "Solve anyway",
            "Change the mesh resolution in Sources & Mesh… in Fusion, or check the model size. You can also solve anyway.",
        ),
        "setup_required": ("review", "Review settings", "Review this model's solve settings, then solve."),
        "findings_need_review": ("review", "Review model", "Review this model's preparation before solving."),
        "engine_unavailable": ("review", "Review solve", "Open Results to prepare the solve engine."),
        "engine_cannot_solve_return": ("review", "Review settings", "Review this model's solve settings."),
        "ready_to_solve": ("retry", "Solve", "Start the prepared solve when you are ready."),
    }.get(choice_code, ("review", "Review request", "Review this request in CAD Link before solving."))
    return {"code": code, "message": message, "next_step": next_step,
            "options": [{"id": action, "label": label}, {"id": "dismiss", "label": "Dismiss request"}]}


BASELINE_KINDS = frozenset({"document_signature_hash"})
OUTCOME_FIELDS = frozenset(
    {"message", "reconciled", "evidence", "setup_defaults", "frame_axis_automatic"}
)
EVIDENCE_FIELDS = ("operation_id", "export_id")

_EXACT_CAD_TARGET = ("document_id", "design_id", "instance_id", "expected_baseline")
# kind -> (target fields, input fields). Every field is required; no other
# field is accepted, so a transport detail can never reach the digest.
_SHAPES: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    RECEIVE_SNAPSHOT: ((), ("bundle_path", "manifest_sha256")),
    PREPARE_AND_SOLVE: ((), ("return_id", "bundle_path", "manifest_sha256")),
    REQUEST_RETURN: (_EXACT_CAD_TARGET, ()),
    INSERT_LINK: (("destination", "export_id"), ()),
    UPDATE_LINK: (_EXACT_CAD_TARGET, ("export_id",)),
}
# A legacy single-slot solve marker may omit its return id.
_MAY_BE_EMPTY = frozenset({(PREPARE_AND_SOLVE, "return_id")})


def require_kind(kind: object) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown CAD operation kind {kind!r}")
    return str(kind)


def _baseline(kind: str, value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "value"}:
        raise ValueError(
            f"{kind} target field 'expected_baseline' must be exactly {{kind, value}}"
        )
    baseline_kind = value["kind"]
    baseline_value = value["value"]
    if baseline_kind not in BASELINE_KINDS:
        raise ValueError(
            f"{kind} target field 'expected_baseline' has unknown kind {baseline_kind!r}"
        )
    if not isinstance(baseline_value, str) or not baseline_value:
        raise ValueError(f"{kind} target field 'expected_baseline' needs a non-empty value")
    return {"kind": str(baseline_kind), "value": baseline_value}


def _destination(kind: str, value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "value"}:
        raise ValueError(
            f"{kind} target field 'destination' must be exactly {{kind, value}}"
        )
    destination_kind = value["kind"]
    destination_value = value["value"]
    if destination_kind not in {"document", "new_document"}:
        raise ValueError(
            f"{kind} target field 'destination' has unknown kind {destination_kind!r}"
        )
    if not isinstance(destination_value, str) or not destination_value:
        raise ValueError(f"{kind} target field 'destination' needs a non-empty value")
    return {"kind": str(destination_kind), "value": destination_value}


def _fields(
    kind: str, part: str, value: object, required: tuple[str, ...]
) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{kind} {part} must be an object")
    unexpected = sorted(str(field) for field in set(value) - set(required))
    if unexpected:
        raise ValueError(f"{kind} {part} has unexpected field(s): {', '.join(unexpected)}")
    normalized: dict[str, Any] = {}
    for field in required:
        if field not in value:
            raise ValueError(f"{kind} {part} is missing '{field}'")
        item = value[field]
        if field == "expected_baseline":
            normalized[field] = _baseline(kind, item)
            continue
        if field == "destination":
            normalized[field] = _destination(kind, item)
            continue
        if not isinstance(item, str):
            raise ValueError(f"{kind} {part} field '{field}' must be a string")
        if not item and (kind, field) not in _MAY_BE_EMPTY:
            raise ValueError(f"{kind} {part} field '{field}' must not be empty")
        normalized[field] = item
    return normalized


def normalize_request(
    kind: str, target: Mapping[str, Any] | None, inputs: Mapping[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a request against its kind's exact shape; return plain copies."""

    require_kind(kind)
    target_fields, input_fields = _SHAPES[kind]
    return (
        _fields(kind, "target", target, target_fields),
        _fields(kind, "inputs", inputs, input_fields),
    )


def request_digest(
    kind: str, target: Mapping[str, Any] | None, inputs: Mapping[str, Any] | None = None
) -> str:
    """The canonical request digest (``digestVersion`` 1)."""

    normalized_target, normalized_inputs = normalize_request(kind, target, inputs)
    document = {
        "digestVersion": DIGEST_VERSION,
        "inputs": normalized_inputs,
        "kind": kind,
        "target": normalized_target,
    }
    return "sha256:" + hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def prepare_and_solve_request(
    *, return_id: str, bundle_path: str, manifest_sha256: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Target and inputs of a solve command. A solve addresses nothing in CAD."""

    return normalize_request(
        PREPARE_AND_SOLVE,
        {},
        {"return_id": return_id, "bundle_path": bundle_path, "manifest_sha256": manifest_sha256},
    )


def validate_outcome(
    operation_id: str,
    state: str,
    *,
    reason: str | None = None,
    outcome: Mapping[str, Any] | None = None,
) -> str | None:
    """Check an outcome against the vocabulary; return its canonical JSON."""

    if state not in RECORDABLE_STATES:
        raise ValueError(
            f"state {state!r} cannot be recorded as an outcome; "
            f"use one of {', '.join(sorted(RECORDABLE_STATES))}"
        )
    if reason is not None:
        required_state = REASON_CODES.get(reason)
        if required_state is None:
            raise ValueError(
                f"unknown reason code {reason!r}; a human-readable explanation "
                "belongs in outcome.message"
            )
        if state != required_state:
            raise ValueError(f"reason {reason!r} is only valid with state {required_state!r}")
    if outcome is None:
        return None
    if not isinstance(outcome, Mapping):
        raise ValueError("outcome must be an object")
    unexpected = sorted(str(field) for field in set(outcome) - OUTCOME_FIELDS)
    if unexpected:
        raise ValueError(f"outcome has unexpected field(s): {', '.join(unexpected)}")
    normalized: dict[str, Any] = {}
    if "message" in outcome:
        message = outcome["message"]
        if not isinstance(message, str) or not message:
            raise ValueError("outcome.message must be a non-empty string")
        normalized["message"] = message
    reconciled = outcome.get("reconciled", False)
    if not isinstance(reconciled, bool):
        raise ValueError("outcome.reconciled must be true or false")
    if reconciled:
        if state != ACCEPTED:
            raise ValueError("a reconciled outcome is recorded as accepted: the work already applied")
        evidence = outcome.get("evidence")
        if (
            not isinstance(evidence, Mapping)
            or set(evidence) != set(EVIDENCE_FIELDS)
            or not all(isinstance(evidence[field], str) and evidence[field] for field in EVIDENCE_FIELDS)
        ):
            raise ValueError(
                "a reconciled outcome names the evidence it was read from: "
                "{operation_id, export_id}"
            )
        if evidence["operation_id"] != operation_id:
            raise ValueError(
                f"reconciled evidence names operation_id {evidence['operation_id']!r}, "
                f"not {operation_id!r}"
            )
        normalized["reconciled"] = True
        normalized["evidence"] = {field: evidence[field] for field in EVIDENCE_FIELDS}
    elif "evidence" in outcome:
        raise ValueError("outcome.evidence is recorded only with reconciled: true")
    elif "reconciled" in outcome:
        normalized["reconciled"] = False
    if "setup_defaults" in outcome:
        # A solve prepared with WG's default settings (default_setup.py): the
        # card and the run details say so.
        if not isinstance(outcome["setup_defaults"], bool):
            raise ValueError("outcome.setup_defaults must be true or false")
        if outcome["setup_defaults"]:
            if state != ACCEPTED:
                raise ValueError("outcome.setup_defaults is recorded only with an accepted solve")
            normalized["setup_defaults"] = True
    if "frame_axis_automatic" in outcome:
        # The solver axis WG chose itself, confident and unconfirmed
        # (readings.record_solved_frame_provenance).
        axis = outcome["frame_axis_automatic"]
        if state != ACCEPTED:
            raise ValueError("outcome.frame_axis_automatic is recorded only with an accepted solve")
        if axis not in ("+z", "-z", "+x", "-x", "+y", "-y"):
            raise ValueError("outcome.frame_axis_automatic must be a solver frame axis")
        normalized["frame_axis_automatic"] = axis
    return canonical_json(normalized) if normalized else None


# Kinds that mutate a CAD document, and so can be interrupted part-way.
MUTATING_KINDS = frozenset({INSERT_LINK, UPDATE_LINK})


def check_transition(
    kind: str, current_state: str, new_state: str, *, reconciled: bool
) -> None:
    """Rules that depend on where an operation is, not only where it goes.

    Only a CAD mutation can need recovery. An operation that needs recovery
    is settled by reading the document (a reconciled ``accepted``) or by the
    user (``cancelled``), never by a retry or a reclassification.
    """

    if new_state == RECOVERY_REQUIRED and kind not in MUTATING_KINDS:
        raise ValueError(
            f"recovery_required is only for operations that mutate a CAD document, not {kind}"
        )
    if current_state == RECOVERY_REQUIRED and not (
        new_state == CANCELLED or (new_state == ACCEPTED and reconciled)
    ):
        raise ValueError(
            "an operation in recovery_required is settled only by reading the document "
            "(accepted, reconciled) or by the user (cancelled)"
        )


def fusion_mutation_precheck(
    operation_id: str, *, applied_operation_id: str | None, baseline_matches: bool
) -> Literal["reconciled", "baseline_conflict", "apply"]:
    """What a Fusion-bound mutation may do on delivery or redelivery.

    This is the executable statement of the contract's order rule. Adapters
    in other repositories cannot import it; they implement the same order.

    Evidence comes first: if the exact target already carries this
    operation's id, the operation applied and its own write is why the
    document changed. The outcome is then ``accepted`` with ``reconciled``,
    and nothing mutates -- not even a "no-op" update, which writes. Only
    without that evidence does the expected baseline decide.
    """

    if applied_operation_id is not None and applied_operation_id == operation_id:
        return "reconciled"
    if not baseline_matches:
        return "baseline_conflict"
    return "apply"


__all__ = [
    "REASON_UPDATE_RESTART_PENDING",
    "ACCEPTED",
    "BASELINE_KINDS",
    "CANCELLED",
    "CALM_FAILURE_CODES",
    "CANCEL_REQUESTED",
    "CLAIMABLE_STATES",
    "FUSION_STAGES",
    "DIGEST_VERSION",
    "INSERT_LINK",
    "INTERNAL_FAILURE_CODES",
    "KINDS",
    "MUTATING_KINDS",
    "NEEDS_USER_INPUT",
    "PREPARE_AND_SOLVE",
    "PROCESSING",
    "REASON_CODES",
    "RECEIVED",
    "RECEIVE_SNAPSHOT",
    "RECORDABLE_STATES",
    "RECOVERY_REQUIRED",
    "REJECTED",
    "STAGES",
    "STAGE_ADAPTER_RECEIVED",
    "STAGE_EXECUTING",
    "STAGE_PREPARING_MESH",
    "STAGE_QUEUED_FOR_FUSION",
    "STAGE_READY",
    "STAGE_RECEIVED",
    "STAGE_SUBMITTED",
    "STAGE_VALIDATING",
    "REQUEST_RETURN",
    "STATES",
    "TERMINAL_STATES",
    "UPDATE_LINK",
    "canonical_json",
    "check_transition",
    "fusion_mutation_precheck",
    "hold_details",
    "internal_failure_message",
    "is_calm_failure",
    "normalize_request",
    "prepare_and_solve_request",
    "preparation_failure",
    "request_digest",
    "require_kind",
    "user_hold",
    "validate_outcome",
]
