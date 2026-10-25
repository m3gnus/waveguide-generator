"""The CAD operation contract: kinds, request identity, states and outcomes.

``docs/architecture/CAD-OPERATIONS.md`` is the specification and this module is
its executable half; change them together. Everything here is pure. The
durable half is ``CadLinkStore``'s ``cad_operations`` table.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from typing import Any, Literal

from server.cadlink.wglink_protocol import APP_NAME, MESH_TOO_LARGE_CODE, could_not_prepare_text
from server.contracts.canonical import canonical_utf8_strict_json as canonical_json
from server.mesh.errors import SolverMemoryTooLarge


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
# A return the shared preflight rules refuse (``server/cadlink/intake/preflight.py``).
PREFLIGHT_BLOCKED = "preflight_blocked"

# Reason code -> the only state it may accompany. Both are rejections because a
# refreshed baseline or target is a new operation, never a retry of this one.
REASON_CODES: Mapping[str, str] = {
    "baseline_conflict": REJECTED,
    "target_not_exact": REJECTED,
    # A snapshot that fails verification, or contradicts the operation's inputs.
    "snapshot_invalid": REJECTED,
    # The return changed after Fusion asked WG to solve it: send it again.
    "snapshot_changed": REJECTED,
    "request_payload_invalid": REJECTED,
    # A received snapshot whose bundle stayed unreadable past its bound.
    "snapshot_unavailable": REJECTED,
    # A model cut open in CAD that WG cannot rebuild into the whole speaker
    # (``cad_preparation.MODEL_CUT_UNSOLVABLE``): the same return never solves.
    "model_cut_unsolvable": REJECTED,
    # Cannot proceed now: each waits for the user, and the operation is kept.
    "setup_required": NEEDS_USER_INPUT,
    "findings_need_review": NEEDS_USER_INPUT,
    # A Solve named a person's axis, and another person's choice replaced it
    # before the solve was prepared: press Solve again (never a question about
    # which way a model radiates; server/cadlink/interpretation/solver_frame.py).
    "frame_confirmation_required": NEEDS_USER_INPUT,
    # The model's radiation axis -- a person's choice, or one the survey is
    # confident about -- is one its reduced domain cannot be solved along.
    # Never solved along another axis instead: send the whole model.
    "reduced_axis_not_allowed": NEEDS_USER_INPUT,
    "preparation_failed": NEEDS_USER_INPUT,
    MESH_TOO_LARGE_CODE: NEEDS_USER_INPUT,
    # The solver's dense memory guard refused the mesh (after Solve anyway):
    # the user can only send a coarser mesh. A calm failure, never a hold.
    "solver_memory_exceeded": NEEDS_USER_INPUT,
    "engine_unavailable": NEEDS_USER_INPUT,
    # The engine the setup names is present but cannot solve this return.
    "engine_cannot_solve_return": NEEDS_USER_INPUT,
    "submission_refused": NEEDS_USER_INPUT,
    # The setup's excitation cannot drive the prepared domain (a design the
    # user changes: the drive channels, or solving the whole model).
    "excitation_refused": NEEDS_USER_INPUT,
    # Neither the return Fusion sent nor WG's own copy of it can be read now:
    # sending it again works (and trying again, if it comes back).
    "return_unavailable": NEEDS_USER_INPUT,
    # No WGLink folder is selected, so the return cannot be read; choosing it
    # and trying again works.
    "wglink_folder_unset": NEEDS_USER_INPUT,
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
    # A delivered return the shared preflight rules block (wglink_protocol
    # ``preflight_findings``): refused at admission in the rule's own words.
    # The add-in never sends one; an older or another writer can.
    PREFLIGHT_BLOCKED: REJECTED,
    "adapter_refused": REJECTED,
    # The adapter reported that the request failed before it changed anything.
    "adapter_failed": REJECTED,
    "adapter_not_started": CANCELLED,
}


#: WG's own pipeline failures: developer matters, never a choice for the user.
#: They end as a calm failed preparation ("WG could not prepare ..."), with the
#: raw detail recorded for developers, never as a "Waiting for you" hold.
INTERNAL_FAILURE_CODES = frozenset(
    {"preparation_failed", "submission_refused", "snapshot_invalid"}
)
#: Failed preparations the user can act on: each says what to change, in its
#: own words and with one next step, and offers Try again only where trying
#: again can work. Never a hold, never recorded as a WG failure.
#: ``model_cut_unsolvable`` is one too, but keeps its stored words.
USER_ACTION_CODES = frozenset(
    {"snapshot_changed", "return_unavailable", "wglink_folder_unset",
     "excitation_refused", "solver_memory_exceeded", "reduced_axis_not_allowed",
     # WG stopped (quit, crash, restart) while preparing: nothing failed,
     # nothing is recorded, and Solve prepares it again.
     "interrupted"}
)
#: The failures whose words a user reads, in Fusion too: WG's own sentence and
#: next step for each. Every other failure reads as WG's recorded failure.
USER_WORDED_FAILURE_CODES = USER_ACTION_CODES | {"model_cut_unsolvable"}
QUESTION_CODES = frozenset({MESH_TOO_LARGE_CODE, "setup_required", "findings_need_review"})
#: Prepared and kept, waiting only for the user to press Solve: never a
#: question, never a failure. Clear failed keeps it; Solve continues it with
#: the same prepared mesh.
READY_CODES = frozenset({"ready_to_solve"})
#: Failed preparations the read model words itself, whatever text is stored.
CALM_FAILURE_CODES = INTERNAL_FAILURE_CODES | USER_ACTION_CODES
_MESH_CEILING_PREFIX = "This model meshes to "

#: What an operation asks of the user, beside its state (``attention``): a
#: state of ``needs_user_input`` only says the operation is kept and can still
#: continue, so readers -- Fusion's progress panel among them -- tell from this
#: which of those is a real question.
#:
#: - ``question``: a choice only the user can make (:data:`QUESTION_CODES`);
#:   WG asks it in a pop-up.
#: - ``ready``: prepared, and ready to solve when the user presses Solve
#:   (:data:`READY_CODES`); nothing is asked, nothing failed.
#: - ``action``: the user must change or do something (the design, the
#:   settings, the mesh, an engine, send the model again, press Solve again);
#:   nothing is asked. The default for any other reason.
#: - ``failed``: WG's own failure, recorded for the developers; Try again.
#: - None: nothing is asked (in progress, solved, cancelled, or waiting for an
#:   update restart).
ATTENTION_QUESTION = "question"
ATTENTION_READY = "ready"
ATTENTION_ACTION = "action"
ATTENTION_FAILED = "failed"


def effective_reason_code(code: Any, message: Any) -> str:
    """The reason a stored refusal stands for.

    Preparation stores the triangle ceiling as ``mesh_too_large``; rows from
    before that stored it as ``preparation_failed`` with the ceiling's message,
    and this is the one reader that still recognises them (by that message).
    """

    text = str(code or "")
    if text == "preparation_failed" and str(message or "").startswith(_MESH_CEILING_PREFIX):
        return MESH_TOO_LARGE_CODE
    return text


def is_calm_failure(code: Any, message: Any = "") -> bool:
    """Whether a refusal is a failed preparation rather than a user choice."""

    return effective_reason_code(code, message) in CALM_FAILURE_CODES


def reason_attention(state: Any, code: Any, message: Any = "") -> str | None:
    """What an operation in ``state`` with reason ``code`` asks of the user (``ATTENTION_*``)."""

    if state not in {NEEDS_USER_INPUT, REJECTED} or not code:
        return None
    effective = effective_reason_code(code, message)
    if effective == REASON_UPDATE_RESTART_PENDING:
        return None  # Queued again by itself once no restart is pending.
    if effective in INTERNAL_FAILURE_CODES:
        return ATTENTION_FAILED
    if state == NEEDS_USER_INPUT and effective in QUESTION_CODES:
        return ATTENTION_QUESTION
    if state == NEEDS_USER_INPUT and effective in READY_CODES:
        return ATTENTION_READY
    # Engine gates (their home is the Results solve progress), a manual axis
    # changed elsewhere, user-action failures, rejections and any reason not
    # listed: something to do, never a question.
    return ATTENTION_ACTION


#: What a model whose radiation axis its reduced domain cannot use is told to do.
AXIS_NOT_ALLOWED_NEXT_STEP = "Send the whole model from Fusion."


def axis_not_allowed_message(document_name: Any, axis: str) -> str:
    """The statement of ``reduced_axis_not_allowed``: the model and its axis."""

    name = str(document_name or "").strip() or "This model"
    return (
        f"{name} arrived cut in a way that cannot be solved along its radiation "
        f"axis ({axis})."
    )


def internal_failure_message(document_name: Any) -> str:
    """The one sentence a user sees for an internal preparation failure (the protocol's)."""

    return could_not_prepare_text(str(document_name or "").strip() or None)


def preparation_failure(
    code: str, document_name: Any, *, detail: Any = None, manual: bool = False,
) -> dict[str, Any]:
    """The read model of a failed preparation: calm words, and what can still work.

    ``detail`` is the stored message, used only where it is the user's own
    design statement (``excitation_refused``). ``manual``: the solve was
    started in WG, not sent by Fusion.
    """

    name = str(document_name or "").strip() or "this model"
    dismiss = {"id": "dismiss", "label": "Dismiss request"}
    retry = {"id": "retry", "label": "Try again"}
    send_again = (
        "Prepare it again in CAD Link, then solve it." if manual else "Send it again from Fusion."
    )
    if code == "solver_memory_exceeded":
        return {"code": code, "message": SolverMemoryTooLarge.user_message(document_name),
                "next_step": SolverMemoryTooLarge.NEXT_STEP, "options": [dismiss]}
    if code == "interrupted":
        return {"code": code, "message": f"Preparing {name} stopped when {APP_NAME} closed.",
                "next_step": "Solve it again.", "options": [retry, dismiss]}
    if code == "snapshot_changed":
        return {"code": code, "message": f"{name} changed after Fusion asked WG to solve it.",
                "next_step": "Send it again from Fusion.", "options": [dismiss]}
    if code == "return_unavailable":
        return {"code": code, "message": f"WG cannot read the copy of {name} it was sent.",
                "next_step": send_again, "options": [retry, dismiss]}
    if code == "wglink_folder_unset":
        return {"code": code, "message": f"WG cannot read {name}: no WGLink folder is selected.",
                "next_step": "Choose the WGLink folder in Settings → CAD Link, then try again.",
                "options": [retry, dismiss]}
    if code == "reduced_axis_not_allowed":
        statement = str(detail or "").strip() or axis_not_allowed_message(document_name, "its axis")
        return {"code": code, "message": statement, "next_step": AXIS_NOT_ALLOWED_NEXT_STEP,
                "options": [dismiss]}
    if code == "excitation_refused":
        statement = str(detail or "").strip() or f"{name} cannot be driven as its settings say."
        return {"code": code, "message": statement,
                "next_step": "Change the drive channels in this model's solve settings, "
                             "or solve the whole model, then press Solve.",
                "options": [dismiss]}
    # A rejected snapshot cannot be solved again; only dismissing it works.
    retries = [] if REASON_CODES.get(code) == REJECTED else [retry]
    return {"code": code, "message": internal_failure_message(name), "next_step": None,
            "options": [*retries, dismiss]}


def user_hold(status: str, cad: Mapping[str, Any]) -> bool:
    """A kept request: a question, or something to do, that is not a failed preparation.

    Read from :func:`reason_attention`, the one classification: a hold asks
    a question or names an action (an engine gate, a frame changed
    elsewhere); a failed preparation is never a hold, however an older
    version stored it. Includes refusals saved before holds had their own job
    status.
    """

    refusal = cad.get("refusal") or {}
    code, message = refusal.get("code"), refusal.get("message")
    if is_calm_failure(code, message):
        return False
    if not (status == "held" or (status == "error" and REASON_CODES.get(code) == NEEDS_USER_INPUT)):
        return False
    # A held row with no stored reason predates reasons on holds; it stays one.
    return not code or reason_attention(NEEDS_USER_INPUT, code, message) in {
        ATTENTION_QUESTION, ATTENTION_READY, ATTENTION_ACTION,
    }


def hold_stage_message(code: Any, message: Any = "") -> str:
    """The stage message of a kept request (:func:`user_hold`), from :func:`reason_attention`.

    Only a real question waits for the user; a prepared request is ready; any
    other hold names something to change, calmly, never as a refusal. A held
    row with no stored reason predates reasons on holds and stays a question.
    """

    if not code:
        return "Waiting for you"
    attention = reason_attention(NEEDS_USER_INPUT, code, message)
    if attention == ATTENTION_QUESTION:
        return "Waiting for you"
    if attention == ATTENTION_READY:
        return "Ready to solve"
    return "Needs a change"


#: The triangle ceiling's next step; each surface adds the choice it offers.
MESH_CEILING_NEXT_STEP = "Change the mesh resolution in Sources & Mesh… in Fusion, or check the model size."


def hold_details(code: str, message: str) -> dict[str, Any]:
    """One vocabulary for persisted holds and historical request readers."""

    choice_code = effective_reason_code(code, message)
    action, label, next_step = {
        MESH_TOO_LARGE_CODE: ("solve_anyway", "Solve anyway", MESH_CEILING_NEXT_STEP + " You can also solve anyway."),
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
# Optional inputs: absent from a request that does not carry them, so every
# digest made before they existed is unchanged. ``acknowledged`` names the WG
# holds the user already answered in Fusion before a Solve (the shared
# protocol's ``ACKNOWLEDGES``, e.g. Solve anyway over the triangle ceiling): a
# sorted, non-empty list of hold codes, part of the request's identity.
_OPTIONAL_INPUTS: Mapping[str, tuple[str, ...]] = {PREPARE_AND_SOLVE: ("acknowledged",)}


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
    kind: str, part: str, value: object, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{kind} {part} must be an object")
    unexpected = sorted(str(field) for field in set(value) - set(required) - set(optional))
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
    for field in optional:
        if field not in value:
            continue
        item = value[field]
        if (
            not isinstance(item, list) or not item
            or not all(isinstance(entry, str) and entry for entry in item)
            or item != sorted(set(item))
        ):
            raise ValueError(f"{kind} {part} field '{field}' must be a sorted list of distinct names")
        normalized[field] = list(item)
    return normalized


def normalize_request(
    kind: str, target: Mapping[str, Any] | None, inputs: Mapping[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a request against its kind's exact shape; return plain copies."""

    require_kind(kind)
    target_fields, input_fields = _SHAPES[kind]
    return (
        _fields(kind, "target", target, target_fields),
        _fields(kind, "inputs", inputs, input_fields, _OPTIONAL_INPUTS.get(kind, ())),
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
    *, return_id: str, bundle_path: str, manifest_sha256: str, acknowledged: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Target and inputs of a solve command. A solve addresses nothing in CAD.

    ``acknowledged``: the holds the user answered before sending (canonical,
    ``wglink_protocol.request_acknowledged``); only a request that has any
    names them.
    """

    inputs: dict[str, Any] = {
        "return_id": return_id, "bundle_path": bundle_path, "manifest_sha256": manifest_sha256,
    }
    if acknowledged:
        inputs["acknowledged"] = list(acknowledged)
    return normalize_request(PREPARE_AND_SOLVE, {}, inputs)


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
    "PREFLIGHT_BLOCKED",
    "ACCEPTED",
    "ATTENTION_ACTION",
    "ATTENTION_READY",
    "ATTENTION_FAILED",
    "ATTENTION_QUESTION",
    "AXIS_NOT_ALLOWED_NEXT_STEP",
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
    "MESH_CEILING_NEXT_STEP",
    "MESH_TOO_LARGE_CODE",
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
    "USER_ACTION_CODES",
    "USER_WORDED_FAILURE_CODES",
    "canonical_json",
    "check_transition",
    "fusion_mutation_precheck",
    "hold_details",
    "hold_stage_message",
    "internal_failure_message",
    "is_calm_failure",
    "normalize_request",
    "prepare_and_solve_request",
    "preparation_failure",
    "QUESTION_CODES",
    "READY_CODES",
    "axis_not_allowed_message",
    "reason_attention",
    "request_digest",
    "require_kind",
    "user_hold",
    "validate_outcome",
]
