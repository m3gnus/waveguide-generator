"""Read the short-lived document heartbeat published by WGLink in Fusion.

The heartbeat arrives on two transports: the ``.fusion-status.json`` file
WGLink always writes, and, while the add-in holds a live session, the same
object posted over HTTP (``docs/reference/CADLINK-LIVE-PROTOCOL.md``, section
6). :func:`select_heartbeat` is the one place that chooses between them, and
every reader goes through it: a fresh live heartbeat of a current session,
else a fresh file heartbeat, else none. Both are held to the same validation
and the same freshness window.

The heartbeat is presence information, not a source of CAD geometry or design
truth.  WG still computes the current design hash itself and only uses the
reported link identity to decide whether the active Fusion document contains
that exact design state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Any, Mapping

from server.cadlink.fusion_delivery import DELIVERY_VERSION, addin_delivery_version
from server.cadlink.live.registry import registry_for
from server.platform.process import background_process_kwargs


FUSION_STATUS_FILENAME = ".fusion-status.json"
IPC_SUBDIRECTORY = Path("ipc") / "wglink"
FUSION_STATUS_TTL = timedelta(seconds=20)
_MAX_STATUS_BYTES = 256 * 1024
_LEGACY_SIGNATURE_EXPLANATION = (
    "stale detection unavailable: this returned bundle predates wgreturn 1.1 "
    "and carries no document signature"
)
ADDIN_OUTDATED_MESSAGE = (
    "Fusion is running a WGLink add-in older than this Waveguide Generator. Restart "
    "Fusion so it loads the WGLink that WG installed, then try again."
)
_SCOPED_SELECTION_EXPLANATION = (
    "stale detection unavailable: this return covers a selected assembly "
    "subtree, and Fusion reports its document signature for the whole root"
)
_EARLIER_OBSERVATION_EXPLANATION = (
    "stale detection unavailable: the Fusion model has moved since WGLink last "
    "measured it, so WG holds an observation of an earlier revision"
)
#: WGLink with its automatic coordination off publishes status only when it runs
#: a command (at start-up, after each command, removed at shutdown), so between
#: commands its heartbeat ages past ``FUSION_STATUS_TTL`` while Fusion and the
#: add-in are both fine. That is not an offline add-in, and not a closed Fusion.
_NOT_OBSERVED_EXPLANATION = (
    "stale detection unavailable: WGLink reports status only when it runs a "
    "command (its automatic coordination is off), so WG has not observed Fusion "
    "since the last one"
)
_NO_OBSERVATION_EXPLANATION = (
    "stale detection unavailable: WGLink has not measured this Fusion "
    "document's geometry yet"
)

#: Which revision the measured half of a heartbeat link describes.
#:
#: WGLink's periodic heartbeat inspects no geometry. It publishes identity from
#: stored attributes, and for ``localBodyState``, ``bodyFingerprintHash``,
#: ``documentSignatureHash``, ``documentBodyCount`` and ``sourceStateHash``
#: whatever a previous measurement left in its cache
#: (``fusion-addins/WGLink/README.md``, "The heartbeat reads cached state
#: only"). The two revision tokens beside them are the only thing that says
#: which revision that cache belongs to, and without them a cached hash that
#: equals the returned one reads as "compared and unchanged".
OBSERVATION_CURRENT = "current"
OBSERVATION_STALE = "stale"
OBSERVATION_NONE = "none"
#: Neither token present. That is a bounded set of add-ins, not an open-ended
#: compatibility waiver. :func:`read_fusion_status` answers ``addin_outdated``
#: to any heartbeat below WG's own ``DELIVERY_VERSION`` -- 3, and an add-in
#: older than the one that introduced it reports no delivery version at all --
#: before it reads a link at all. Above that floor, WGLink has carried both
#: names since its ``3dd9771``: the commit that made the heartbeat cache-only
#: added them in the same change, without a delivery bump, and the record
#: builder emits every field of its map unconditionally, null included. So an
#: omission can only come from an add-in at or above the delivery-3 floor and
#: older than ``3dd9771`` -- exactly the ones whose heartbeat measured the
#: geometry it published, on the tick that published it. Their cached values
#: are a measurement that occurred, so answering from them claims nothing that
#: did not happen.
OBSERVATION_UNKNOWN = "unknown"
_REVISION_TOKENS = ("geometryRevisionToken", "measuredRevisionToken")


def _observation_freshness(link: Mapping[str, Any]) -> str:
    """Classify a link's measured half: none, an earlier revision, or current."""

    if not any(name in link for name in _REVISION_TOKENS):
        return OBSERVATION_UNKNOWN
    # Read the hashes, not the tokens, for whether there is an observation at
    # all: an empty signature with an unknown body state is what a restart or a
    # switch to an unmeasured document publishes. It is never another
    # document's measurement, and never a claim of a fresh one.
    if not link.get("documentSignatureHash") and link.get("localBodyState") == "unknown":
        return OBSERVATION_NONE
    geometry = link.get("geometryRevisionToken")
    measured = link.get("measuredRevisionToken")
    if geometry and measured and geometry == measured:
        return OBSERVATION_CURRENT
    # Unequal tokens, or a null ``geometryRevisionToken`` meaning the revision
    # could not be keyed at all: either way the measurement describes a
    # revision the document may already have left.
    return OBSERVATION_STALE


def fusion_process_running(*, system: str | None = None) -> bool:
    """Detect Fusion without activating it or claiming that WGLink is alive."""

    resolved = platform.system() if system is None else system
    if resolved == "Darwin":
        pgrep = shutil.which("pgrep")
        command = [pgrep, "-x", "Autodesk Fusion"] if pgrep else None
    elif resolved == "Windows":
        tasklist = shutil.which("tasklist")
        command = (
            [tasklist, "/FI", "IMAGENAME eq Fusion360.exe", "/NH"]
            if tasklist
            else None
        )
    else:
        command = None
    if command is None:
        return False
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            check=False,
            capture_output=True,
            timeout=2,
            **background_process_kwargs(system=resolved),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if resolved == "Windows":
        return completed.returncode == 0 and b"Fusion360.exe" in completed.stdout
    return completed.returncode == 0


FUSION_RUNNING = "running"
FUSION_CLOSED = "closed"
FUSION_UNKNOWN = "unknown"


def fusion_process_state(*, system: str | None = None) -> str:
    """Whether Fusion runs: ``running``, ``closed``, or ``unknown``.

    :func:`fusion_process_running` reads a check that failed as "not running",
    which is right for presence and wrong for anything that must not act while
    Fusion is open. Here a missing ``pgrep`` or ``tasklist``, a check that
    times out, and one that cannot run or errors are ``unknown``, and WGLink
    activation treats that as open. A platform Fusion does not run on is
    ``closed``.
    """

    resolved = platform.system() if system is None else system
    if resolved == "Darwin":
        tool = shutil.which("pgrep")
        command = [tool, "-x", "Autodesk Fusion"] if tool else None
    elif resolved == "Windows":
        tool = shutil.which("tasklist")
        command = [tool, "/FI", "IMAGENAME eq Fusion360.exe", "/NH"] if tool else None
    else:
        return FUSION_CLOSED
    if command is None:
        return FUSION_UNKNOWN
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            check=False,
            capture_output=True,
            timeout=2,
            **background_process_kwargs(system=resolved),
        )
    except (OSError, subprocess.SubprocessError):
        return FUSION_UNKNOWN
    if resolved == "Windows":
        if completed.returncode != 0:
            return FUSION_UNKNOWN
        return FUSION_RUNNING if b"Fusion360.exe" in completed.stdout else FUSION_CLOSED
    # pgrep: 0 is a match, 1 is none, anything else is an error.
    if completed.returncode == 0:
        return FUSION_RUNNING
    return FUSION_CLOSED if completed.returncode == 1 else FUSION_UNKNOWN


def _utc_now() -> datetime:
    """The wall clock heartbeat freshness is measured against; a test moves it."""

    return datetime.now(timezone.utc)


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


#: How far in the future ``updatedAt`` may be. A minute of clock skew is much
#: more than these two processes on one machine should ever need.
FUSION_STATUS_MAX_SKEW = timedelta(minutes=1)

HEARTBEAT_LIVE = "live"
HEARTBEAT_FILE = "file"

#: Why a heartbeat object is not usable (:func:`heartbeat_problem`).
HEARTBEAT_INVALID = "invalid"
HEARTBEAT_STALE = "stale"

#: What :func:`select_heartbeat` returns: the payload and its transport, or
#: ``(None, None)``.
SelectedHeartbeat = tuple[Mapping[str, Any] | None, str | None]


def heartbeat_now() -> datetime:
    """The instant one answer measures heartbeat freshness at.

    A caller that selects a heartbeat and then reports a status from it reads
    this once and passes it to both, so a heartbeat that ages out in between
    cannot settle operations and then be reported as absent.
    """

    return _utc_now()


def heartbeat_updated_at(payload: Mapping[str, Any]) -> datetime | None:
    """A heartbeat's ``updatedAt`` as an aware UTC time, or None."""

    return _timestamp(payload.get("updatedAt"))


def heartbeat_problem(payload: object, checked_at: datetime | None = None) -> tuple[str, str] | None:
    """Why a heartbeat object is unusable at ``checked_at`` (default: now), or None.

    The validation both transports share: heartbeat schema 1 from Fusion with
    an ``updatedAt`` inside the freshness window (``FUSION_STATUS_TTL`` old at
    most, ``FUSION_STATUS_MAX_SKEW`` in the future at most). Returns
    ``(HEARTBEAT_INVALID | HEARTBEAT_STALE, field)``. The delivery version is
    not checked here: a fresh file heartbeat of an older add-in is still a
    status (``addin_outdated``).
    """

    checked_at = checked_at or _utc_now()
    if not isinstance(payload, Mapping):
        return HEARTBEAT_INVALID, ""
    if payload.get("schemaVersion") != 1:
        return HEARTBEAT_INVALID, "schemaVersion"
    if payload.get("cadApplication") != "fusion360":
        return HEARTBEAT_INVALID, "cadApplication"
    updated_at = _timestamp(payload.get("updatedAt"))
    if updated_at is None:
        return HEARTBEAT_INVALID, "updatedAt"
    if checked_at - updated_at > FUSION_STATUS_TTL or updated_at - checked_at > FUSION_STATUS_MAX_SKEW:
        return HEARTBEAT_STALE, "updatedAt"
    return None


def _read_file_heartbeat(data_dir: Path) -> object:
    marker = data_dir.resolve() / IPC_SUBDIRECTORY / FUSION_STATUS_FILENAME
    try:
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > _MAX_STATUS_BYTES:
            return None
        return json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None


def select_heartbeat(data_dir: Path, now: datetime | None = None) -> SelectedHeartbeat:
    """The heartbeat every reader uses, and which transport it came by.

    The live heartbeat of a session that is current in this data directory's
    registry wins while it is fresh; otherwise a fresh file heartbeat; otherwise
    none. A data directory without a running WG has no registry, so only its
    file is read. One status answer selects once and hands the result to every
    step that needs it.
    """

    checked_at = now or _utc_now()
    registry = registry_for(data_dir)
    if registry is not None:
        live = registry.current_heartbeat()
        if live is not None and heartbeat_problem(live, checked_at) is None:
            return live, HEARTBEAT_LIVE
    payload = _read_file_heartbeat(data_dir)
    if payload is not None and heartbeat_problem(payload, checked_at) is None:
        return payload, HEARTBEAT_FILE  # type: ignore[return-value]
    return None, None


def settleable_heartbeat(selected: SelectedHeartbeat) -> Mapping[str, Any] | None:
    """The selected heartbeat when it may settle operations: version 3 or later."""

    payload = selected[0]
    if payload is None or int(addin_delivery_version(payload) or 0) < DELIVERY_VERSION:
        return None
    return payload


def read_live_fusion_heartbeat(
    data_dir: Path, *, now: datetime | None = None
) -> Mapping[str, Any] | None:
    """Return a fresh version-3 heartbeat from either transport, or None without guessing."""

    return settleable_heartbeat(select_heartbeat(data_dir, now))


def _fingerprint_hash(value: object) -> str | None:
    if not isinstance(value, (Mapping, list)):
        return None
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _read_return_manifest(returned_bundle: Path | None) -> Mapping[str, Any] | None:
    if returned_bundle is None:
        return None
    try:
        resolved = returned_bundle.expanduser().resolve()
        if resolved.is_symlink() or not resolved.is_dir() or resolved.suffix != ".wgreturn":
            return None
        manifest = json.loads((resolved / "wgreturn.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, Mapping) else None


def _returned_fingerprint_hash(
    manifest: Mapping[str, Any] | None,
    *,
    instance_id: str,
    design_id: str | None,
) -> str | None:
    """Read body evidence for one exact CAD instance.

    Older synthetic/preview manifests sometimes omitted ``instance_id``.  They
    remain usable only when the return has one instance.  A repeated design is
    never allowed to fall back to its first record: two placements can share
    every design/export field while carrying different bodies.
    """

    if manifest is None:
        return None
    instances = manifest.get("instances")
    if not isinstance(instances, list):
        return None
    exact = [
        instance
        for instance in instances
        if isinstance(instance, Mapping)
        and instance.get("instance_id") == instance_id
    ]
    if len(exact) == 1:
        candidates = exact
    elif len(instances) == 1 and design_id is not None:
        only = instances[0]
        candidates = (
            [only]
            if isinstance(only, Mapping)
            and only.get("design_id") == design_id
            else []
        )
    else:
        candidates = []
    for instance in candidates:
        evidence = instance.get("body_evidence")
        if isinstance(evidence, Mapping):
            return _fingerprint_hash(evidence.get("observed_fingerprint"))
    return None


def _returned_document_signature_hash(manifest: Mapping[str, Any] | None) -> str | None:
    if manifest is None:
        return None
    assembly = manifest.get("assembly")
    if not isinstance(assembly, Mapping):
        return None
    return _string(assembly.get("signature_hash"))


def _returned_scope_selection(manifest: Mapping[str, Any] | None) -> str | None:
    if manifest is None:
        return None
    scope = manifest.get("scope")
    if not isinstance(scope, Mapping):
        return None
    return _string(scope.get("selection"))


def _returned_document_summary(
    manifest: Mapping[str, Any] | None,
) -> tuple[int | None, str | None]:
    if manifest is None:
        return None, None
    assembly = manifest.get("assembly")
    raw_sources = manifest.get("sources")
    body_count = assembly.get("n_bodies_expected") if isinstance(assembly, Mapping) else None
    if isinstance(body_count, bool) or not isinstance(body_count, int):
        body_count = None
    if not isinstance(raw_sources, list):
        return body_count, None
    sources = []
    for raw in raw_sources:
        if not isinstance(raw, Mapping):
            return body_count, None
        sources.append({
            "id": raw.get("id"),
            "role": raw.get("role"),
            "instance_id": raw.get("instance_id"),
            "expected_connected_components": raw.get("expected_connected_components"),
            "observed": raw.get("observed"),
        })
    return body_count, _fingerprint_hash(sources)


def _recovery_required(value: object) -> dict[str, Any] | None:
    """The WG operation the document is marked as applying, if any.

    The add-in marks an operation before its first write and clears the mark
    after the operation's evidence. The heartbeat is not published while an
    operation runs, so a mark seen here began and did not finish.
    """

    if not isinstance(value, Mapping):
        return None
    operation_id = _string(value.get("operationId"))
    if operation_id is None:
        return None
    return {
        "operationId": operation_id,
        "kind": _string(value.get("kind")),
        "instanceId": _string(value.get("instanceId")),
        "exportId": _string(value.get("exportId")),
        "phase": _string(value.get("phase")),
    }


def _link_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    instance_id = _string(value.get("instanceId"))
    if instance_id is None:
        return None
    parameter_count = value.get("parameterCount")
    if isinstance(parameter_count, bool) or not isinstance(parameter_count, int):
        parameter_count = 0
    parameter_drift_count = value.get("parameterDriftCount")
    if isinstance(parameter_drift_count, bool) or not isinstance(parameter_drift_count, int):
        parameter_drift_count = 0
    raw_drifted_parameters = value.get("driftedParameters")
    drifted_parameters = (
        sorted(set(raw_drifted_parameters))
        if (
            isinstance(raw_drifted_parameters, list)
            and all(isinstance(name, str) and bool(name) for name in raw_drifted_parameters)
        )
        else None
    )
    if drifted_parameters is not None:
        # New WGLink builds publish the names and derive the count from them.
        # Do the same at the trust boundary so a torn or hand-written heartbeat
        # cannot make the aggregate disagree with the fields WG will mark.
        parameter_drift_count = len(drifted_parameters)
    document_body_count = value.get("documentBodyCount")
    if isinstance(document_body_count, bool) or not isinstance(document_body_count, int):
        document_body_count = 0
    payload = {
        "instanceId": instance_id,
        "bundlePath": _string(value.get("bundlePath")),
        "designId": _string(value.get("designId")),
        "lineageId": _string(value.get("lineageId")),
        "editVersion": _string(value.get("editVersion")),
        "designHash": _string(value.get("designHash")),
        "designName": _string(value.get("designName")),
        "formula": _string(value.get("formula")),
        "configPresent": value.get("configPresent") is True,
        "parameterCount": max(0, parameter_count),
        "parameterDriftCount": max(0, parameter_drift_count),
        "localBodyState": _string(value.get("localBodyState")) or "unknown",
        "bodyFingerprintHash": _string(value.get("bodyFingerprintHash")),
        "documentSignatureHash": _string(value.get("documentSignatureHash")),
        "documentBodyCount": max(0, document_body_count),
        "sourceStateHash": _string(value.get("sourceStateHash")),
        "exportId": _string(value.get("exportId")),
        "exportSequence": _string(value.get("exportSequence")),
    }
    # Older add-ins publish only parameterDriftCount. Omitting the new member
    # retains that distinction so WG can keep the aggregate fallback instead
    # of pretending it knows which parameters changed.
    if drifted_parameters is not None:
        payload["driftedParameters"] = drifted_parameters
    # Both revision tokens are additive under heartbeat schema 1 and travel the
    # same way: an add-in that never sends one omits the member, a new add-in
    # with nothing to report sends null. Defaulting them would collapse those
    # two into one and make an older add-in look like a document nobody has
    # measured.
    for name in _REVISION_TOKENS:
        if name in value:
            payload[name] = _string(value.get(name))
    return payload


#: How an add-in's heartbeat reaches WG, from its own diagnostics: ``command`` --
#: published only when it runs a command (automatic coordination off) --,
#: ``continuous`` -- on its own clock --, or ``unknown`` (an add-in that does not
#: say, such as the shipped pin).
OBSERVATION_POLICY_COMMAND = "command"
OBSERVATION_POLICY_CONTINUOUS = "continuous"
OBSERVATION_POLICY_UNKNOWN = "unknown"


def _activation(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    diagnostics = payload.get("diagnostics")
    activation = diagnostics.get("activation") if isinstance(diagnostics, Mapping) else None
    return activation if isinstance(activation, Mapping) else None


def observation_policy(payload: Mapping[str, Any]) -> str:
    activation = _activation(payload)
    if activation is None:
        return OBSERVATION_POLICY_UNKNOWN
    return (
        OBSERVATION_POLICY_COMMAND
        if activation.get("automaticCoordination") is False
        else OBSERVATION_POLICY_CONTINUOUS
    )


def addin_declares_inbox_transfer(payload: Mapping[str, Any]) -> bool:
    """Whether this add-in sends Send and Solve through WG's request inbox.

    The M1 add-in reports its activation gate (``diagnostics.activation``) and
    writes every WG-bound request to the inbox; the shipped pin reports neither,
    and still publishes a plain Send only as a return WG must find by listing.
    """

    activation = _activation(payload)
    setting = activation.get("setting") if activation is not None else None
    return (
        activation is not None
        and isinstance(activation.get("automaticCoordination"), bool)
        and isinstance(activation.get("settingsKey"), str)
        and bool(activation["settingsKey"])
        and isinstance(setting, str)
        and setting in {"default", "settings", "invalid"}
    )


def addin_inbox_session_signature(data_dir: Path | None) -> tuple[str | None, bool] | None:
    """Identity and validated inbox declaration from the file heartbeat.

    The delivery loop uses this only as a change detector. It deliberately
    reads no CAD geometry and does not require freshness: a command-driven M1
    heartbeat may be quiet, while a replacement heartbeat changes the session.
    """

    if data_dir is None:
        return None, False
    marker = data_dir.resolve() / IPC_SUBDIRECTORY / FUSION_STATUS_FILENAME
    try:
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > _MAX_STATUS_BYTES:
            return None, False
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        # An atomic writer may be between bytes, or Windows may temporarily
        # deny the read. This is no observation; the caller retains its last
        # signature until a complete read proves a transition.
        return None
    if not isinstance(payload, Mapping):
        return None, False
    return _string(payload.get("sessionId")), addin_declares_inbox_transfer(payload)


def _command_driven_heartbeat(data_dir: Path, checked_at: datetime) -> Mapping[str, Any] | None:
    """The file heartbeat of an add-in that runs no automatic coordination.

    Only when it is otherwise valid and merely older than the freshness window
    (never one from the future), and it states in its own diagnostics that its
    automatic coordination is off. An add-in that does coordinate and went quiet
    is offline, and stays reported so.
    """

    payload = _read_file_heartbeat(data_dir)
    if heartbeat_problem(payload, checked_at) != (HEARTBEAT_STALE, "updatedAt"):
        return None
    assert isinstance(payload, Mapping)
    updated_at = _timestamp(payload.get("updatedAt"))
    if updated_at is None or updated_at > checked_at:
        return None
    if observation_policy(payload) != OBSERVATION_POLICY_COMMAND:
        return None
    return payload


def _not_observed_since(status: dict[str, Any]) -> dict[str, Any]:
    """Withdraw every claim a report from before now would make about now."""

    status = {**status, "statusObserved": False, "observedAt": status.get("updatedAt")}
    if status.get("observationFreshness") in {OBSERVATION_CURRENT, OBSERVATION_UNKNOWN}:
        status["observationFreshness"] = OBSERVATION_STALE
        status["staleDetectionExplanation"] = _NOT_OBSERVED_EXPLANATION
        status["documentChangeDetectable"] = False
    if status.get("state") == "current":
        status["state"] = "stale"
    return status


def read_fusion_status(
    workspace_root: Path,
    *,
    current_design_hash: str,
    current_formula: str,
    design_id: str | None,
    instance_id: str | None = None,
    process_running: bool = False,
    returned_bundle: Path | None = None,
    returned_manifest: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    heartbeat: SelectedHeartbeat | None = None,
) -> dict[str, Any]:
    """Classify the active Fusion document against the design on screen.

    ``workspace_root`` is WG's data directory. ``heartbeat`` is a selection a
    caller already made with :func:`select_heartbeat` for this same answer;
    without it this selects for itself. It is never read twice.
    """

    checked_at = now or _utc_now()
    closed: dict[str, Any] = {
        "cadApplication": "fusion360",
        "state": "addin_offline" if process_running else "closed",
        "running": False,
        "processRunning": process_running,
        "adapterVersion": None,
        "workspaceRoot": None,
        "updatedAt": None,
        "documentName": None,
        "documentId": None,
        "currentFormula": current_formula,
        "fusionFormula": None,
        "link": None,
        "matchingLinks": [],
        "selectedInstanceId": None,
        "wgChangesAvailable": False,
        "fusionChangesAvailable": False,
        "documentChanged": False,
        "documentChangeDetectable": False,
        # Which revision the selected link's measured half describes. There is
        # no selected link in any of the states this default covers, so the
        # answer is not "unknown" -- it is "no link to describe".
        "observationFreshness": None,
        "staleDetectionExplanation": None,
        "addinDeliveryVersion": None,
        "recoveryRequired": None,
        "heartbeatTransport": None,
        # When this answer's evidence was observed, and for how long WG treats
        # an observation as current: the page ages a status it holds by these,
        # so a status read once is never presented as current for ever.
        "statusTtlSeconds": FUSION_STATUS_TTL.total_seconds(),
        "observationPolicy": None,
        "addinInboxTransfer": False,
    }
    payload, transport = heartbeat if heartbeat is not None else select_heartbeat(workspace_root, checked_at)
    # A future timestamp is not trusted either (``FUSION_STATUS_MAX_SKEW``).
    if payload is None or heartbeat_problem(payload, checked_at) is not None:
        reported = _command_driven_heartbeat(workspace_root, checked_at) if process_running else None
        if reported is None:
            return closed
        # The last status that add-in chose to report, classified as of when it
        # reported it, and marked as not observed since: never "offline", never
        # "current". Settlement never reads it (``select_heartbeat`` only).
        return _not_observed_since(
            read_fusion_status(
                workspace_root,
                current_design_hash=current_design_hash,
                current_formula=current_formula,
                design_id=design_id,
                instance_id=instance_id,
                process_running=True,
                returned_bundle=returned_bundle,
                returned_manifest=returned_manifest,
                now=_timestamp(reported.get("updatedAt")),
                heartbeat=(reported, HEARTBEAT_FILE),
            )
        )
    updated_at = _timestamp(payload.get("updatedAt"))
    assert updated_at is not None

    base = {
        **closed,
        "running": True,
        "processRunning": True,
        "heartbeatTransport": transport,
        "sessionId": _string(payload.get("sessionId")),
        "adapterVersion": _string(payload.get("adapterVersion")),
        "workspaceRoot": _string(payload.get("workspaceRoot")),
        "updatedAt": updated_at.isoformat().replace("+00:00", "Z"),
        "observationPolicy": observation_policy(payload),
        "addinInboxTransfer": addin_declares_inbox_transfer(payload),
    }
    delivery = addin_delivery_version(payload)
    base["addinDeliveryVersion"] = delivery
    if delivery is None or delivery < DELIVERY_VERSION:
        # There is no route to an older add-in: WG installs the one it ships,
        # and until Fusion loads that one nothing is exchanged with this one.
        return {**base, "state": "addin_outdated"}
    document = payload.get("document")
    if document is None:
        return {**base, "state": "no_document"}
    if not isinstance(document, Mapping):
        return closed
    document_name = _string(document.get("name"))
    document_id = _string(document.get("id"))
    raw_links = document.get("links")
    links = (
        [
            link
            for item in raw_links
            if (link := _link_payload(item)) is not None
        ]
        if isinstance(raw_links, list)
        else []
    )
    base["documentName"] = document_name
    base["documentId"] = document_id
    base["recoveryRequired"] = _recovery_required(document.get("applyingOperation"))

    matching = [link for link in links if design_id and link.get("designId") == design_id]
    if not matching and design_id is None:
        matching = [link for link in links if link.get("designHash") == current_design_hash]
    if not matching:
        return {**base, "state": "not_linked"}

    matching = sorted(matching, key=lambda candidate: str(candidate["instanceId"]))
    base["matchingLinks"] = matching
    selected = (
        [link for link in matching if link.get("instanceId") == instance_id]
        if instance_id is not None
        else matching
    )
    # One link retains the original zero-configuration path.  Repeated
    # instances of the same design require an exact choice, and a stale or
    # duplicated instance id fails closed instead of silently selecting the
    # first heartbeat record.
    if len(selected) != 1 or (instance_id is None and len(matching) != 1):
        return {**base, "state": "instance_selection_required"}

    link = selected[0]
    base["selectedInstanceId"] = link["instanceId"]
    observation_freshness = _observation_freshness(link)
    # An observation of a revision Fusion has already left, or none at all, is
    # not evidence of anything: its comparisons may be repeated word for word by
    # a document that has since moved. Only the *absence* of a difference is
    # withdrawn here.
    #
    # Positive evidence is kept, and not because a difference cannot stop being
    # one -- it can. Undo the edit back to the returned state and an observation
    # of the edited revision still reports ``documentChanged`` at a revision
    # where nothing differs any more. It is kept because the direction is the
    # conservative one: holding "may have changed" over a document that now
    # matches costs a redundant Receive, while withdrawing it would announce
    # that Fusion matches the returned model on the strength of a measurement
    # that cannot speak for the revision the document is at. And WGLink
    # re-measures inline before any guarded mutation
    # (``_require_live_state``), so a positive this stale is decided again at
    # the one moment it could cost anything.
    observation_is_current = observation_freshness in {
        OBSERVATION_CURRENT,
        OBSERVATION_UNKNOWN,
    }
    fusion_hash = link.get("designHash")
    current_body_hash = link.get("bodyFingerprintHash")
    if returned_manifest is None:
        returned_manifest = _read_return_manifest(returned_bundle)
    returned_body_hash = _returned_fingerprint_hash(
        returned_manifest,
        instance_id=str(link["instanceId"]),
        design_id=design_id,
    )
    current_document_hash = link.get("documentSignatureHash")
    returned_document_hash = _returned_document_signature_hash(returned_manifest)
    returned_scope_selection = _returned_scope_selection(returned_manifest)
    returned_body_count, returned_source_hash = _returned_document_summary(returned_manifest)
    # WGLink's heartbeat describes the document root. A scoped return describes
    # only the selected subtree, so none of the document-level aggregates can
    # be compared meaningfully. ``None`` retains compatibility for callers that
    # supply an incomplete synthetic manifest; validated bundles always carry
    # scope.selection.
    document_aggregates_detectable = returned_scope_selection in {None, "root"}
    linked_body_changed = (
        (
            link.get("localBodyState") == "modified"
            or bool(link.get("parameterDriftCount"))
        )
        and (current_body_hash is None or current_body_hash != returned_body_hash)
    )
    document_changed = bool(
        document_aggregates_detectable
        and current_document_hash
        and returned_document_hash
        and current_document_hash != returned_document_hash
    )
    document_change_detectable = bool(
        observation_is_current
        and document_aggregates_detectable
        and current_document_hash
        and returned_document_hash
    )
    stale_detection_explanation: str | None = None
    # An observation problem comes first, and without waiting for a returned
    # bundle: it is why *every* comparison here is unavailable, not only the
    # one against a return.
    if observation_freshness == OBSERVATION_STALE:
        stale_detection_explanation = _EARLIER_OBSERVATION_EXPLANATION
    elif observation_freshness == OBSERVATION_NONE:
        stale_detection_explanation = _NO_OBSERVATION_EXPLANATION
    # A scoped return is not a legacy one: telling the user their bundle
    # predates wgreturn 1.1 when they simply picked a subtree in the Send
    # dialog sends them looking for the wrong problem.
    elif returned_bundle is not None:
        if not document_aggregates_detectable:
            stale_detection_explanation = _SCOPED_SELECTION_EXPLANATION
        elif returned_document_hash is None:
            stale_detection_explanation = _LEGACY_SIGNATURE_EXPLANATION
    inventory_changed = bool(
        document_aggregates_detectable
        and returned_body_count is not None
        and link.get("documentBodyCount")
        and link.get("documentBodyCount") != returned_body_count
    )
    source_changed = bool(
        document_aggregates_detectable
        and returned_source_hash
        and link.get("sourceStateHash")
        and link.get("sourceStateHash") != returned_source_hash
    )
    fusion_changes_available = (
        linked_body_changed or document_changed or inventory_changed or source_changed
    )
    wg_changes_available = (
        fusion_hash != current_design_hash or link.get("configPresent") is not True
    )
    state = (
        "current"
        if (
            observation_is_current
            and not wg_changes_available
            and not fusion_changes_available
            and link.get("parameterDriftCount") == 0
            and link.get("localBodyState") == "unmodified"
        )
        else "stale"
    )
    return {
        **base,
        "state": state,
        "fusionFormula": link.get("formula"),
        "link": link,
        "wgChangesAvailable": wg_changes_available,
        "fusionChangesAvailable": fusion_changes_available,
        "documentChanged": document_changed,
        "documentChangeDetectable": document_change_detectable,
        "observationFreshness": observation_freshness,
        "staleDetectionExplanation": stale_detection_explanation,
    }


__all__ = [
    "ADDIN_OUTDATED_MESSAGE",
    "FUSION_STATUS_FILENAME",
    "FUSION_STATUS_MAX_SKEW",
    "FUSION_STATUS_TTL",
    "HEARTBEAT_FILE",
    "HEARTBEAT_INVALID",
    "HEARTBEAT_LIVE",
    "HEARTBEAT_STALE",
    "SelectedHeartbeat",
    "FUSION_CLOSED",
    "FUSION_RUNNING",
    "FUSION_UNKNOWN",
    "OBSERVATION_CURRENT",
    "OBSERVATION_NONE",
    "OBSERVATION_STALE",
    "OBSERVATION_UNKNOWN",
    "fusion_process_running",
    "fusion_process_state",
    "heartbeat_now",
    "heartbeat_problem",
    "heartbeat_updated_at",
    "read_live_fusion_heartbeat",
    "read_fusion_status",
    "select_heartbeat",
    "settleable_heartbeat",
]
