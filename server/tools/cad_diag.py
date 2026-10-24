"""Find, read, replay and export WG's incident records (developer tool).

    python -m server.tools.cad_diag list [--since 1d] [--kind internal-failure]
    python -m server.tools.cad_diag show <id>
    python -m server.tools.cad_diag replay <id>
    python -m server.tools.cad_diag export <id> --out DIR [--redact]

Records live in ``<data>/diagnostics/incidents`` (``server.platform.incidents``).
``replay`` runs ``server.tools.cad_replay`` in its own process on the recorded
request or retained bundle, in a new scratch directory, and says whether the
recorded failure is fixed, still fails, or fails differently. Nothing here
writes into the live data directory.
"""

from __future__ import annotations

import argparse
import bisect
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

from server.cadlink.wgreturn import identifying_names
from server.platform.incidents import (
    IDENTIFIER_PATTERN,
    INTERFACE_ERROR_KIND,
    MIN_REDACTION_NAME,
    IncidentStore,
    NameMatcher,
    Redactor,
    account_name,
    comparable_text,
    redaction_rules,
    summary,
)
from server.platform.paths import app_root, resolve_data_dir


FIXED = "fixed"
STILL_FAILS = "still fails"
FAILS_DIFFERENTLY = "fails differently"
#: The replay never reached the pipeline: its request or bundle is gone.
NOT_REPLAYED = "could not replay"
#: Exit codes: a script can tell the outcomes apart.
EXIT_CODES = {FIXED: 0, STILL_FAILS: 1, NOT_REPLAYED: 2, FAILS_DIFFERENTLY: 3}

_DURATION = re.compile(r"^(\d+)([mhdw])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
#: Request ids ``cad_replay --from-request`` accepts.
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_DIGEST = re.compile(r"(?:sha256:)?[0-9a-f]{64}")
_STEP_FILE_NAME = re.compile(r"FILE_NAME\s*\(.*?\)\s*;", re.DOTALL)
REDACTED_NAME = "redacted-model"


def parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """``30m``, ``2h``, ``1d`` or ``1w`` before now."""

    match = _DURATION.match(value.strip())
    if match is None:
        raise argparse.ArgumentTypeError("use a duration such as 30m, 2h, 1d or 1w")
    amount, unit = int(match.group(1)), match.group(2)
    return (now or datetime.now(timezone.utc)) - timedelta(**{_UNITS[unit]: amount})


class NotReplayable(Exception):
    """The replay cannot start: no record, no request, no bundle, or no report."""


def _store(data_dir: Path) -> IncidentStore:
    return IncidentStore(data_dir)


def _require(store: IncidentStore, incident_id: str) -> dict[str, Any]:
    record = store.get(incident_id)
    if record is None:
        raise NotReplayable(f"No incident {incident_id!r} (or the prefix is ambiguous) in {store.root}")
    return record


# -- list / show -----------------------------------------------------------------


def list_lines(records: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = []
    for record in records:
        line = summary(record)
        lines.append(
            f"{line['id']}  {line['lastSeen']}  x{line['count']}  {line['phase']}"
            + (f"  {line['message']}" if line.get("message") else "")
        )
    return lines


# -- replay -----------------------------------------------------------------------


def replay_arguments(record: Mapping[str, Any], data_dir: Path) -> list[str]:
    """The ``cad_replay`` arguments that reproduce this incident's request."""

    ids = (record.get("latest") or {}).get("ids") or {}
    operation = str(ids.get("operation_id") or "")
    if _REQUEST_ID.fullmatch(operation):
        return ["--from-request", operation, "--live-data-dir", str(data_dir)]
    digest = str(ids.get("manifest_sha256") or "")
    if _DIGEST.fullmatch(digest):
        bundle = retained_bundle(data_dir, digest)
        if bundle.is_dir():
            return [str(bundle), "--retained"]
    raise NotReplayable(
        "This incident names no replayable request: it has no inbox request id and no "
        "retained bundle digest whose copy still exists."
    )


def retained_bundle(data_dir: Path, digest: str) -> Path:
    from server.cadlink.intake.ingest import retained_snapshot_path

    return retained_snapshot_path(data_dir, digest)


def run_cad_replay(arguments: Sequence[str]) -> dict[str, Any]:
    """Run the replay tool in its own process and return its JSON report."""

    environment = {key: value for key, value in os.environ.items() if key != "WG2_DATA_DIR"}
    completed = subprocess.run(
        [sys.executable, "-m", "server.tools.cad_replay", *arguments, "--json"],
        cwd=app_root(), env=environment, capture_output=True, text=True, check=False,
    )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise NotReplayable(
            f"cad_replay did not produce a report (exit {completed.returncode}):\n{completed.stderr[-4000:]}"
        ) from exc


def _diagnostics(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        message["cad_diagnostic"] for message in payload.get("logs") or ()
        if isinstance(message, Mapping) and isinstance(message.get("cad_diagnostic"), Mapping)
    ]


def _same_failure(record: Mapping[str, Any], diagnostic: Mapping[str, Any]) -> bool:
    if diagnostic.get("stage") != record.get("kind"):
        return False
    recorded = ((record.get("latest") or {}).get("evidence") or {}).get("code")
    replayed = (diagnostic.get("evidence") or {}).get("code")
    return recorded is None or recorded == replayed


def verdict(record: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    """Fixed, still fails, fails differently, or could not replay: the replay against the record.

    The recorded diagnostic recurring is "still fails", whatever the outcome
    (a fallback incident recurs on a preparation that succeeds). Otherwise a
    clean preparation is "fixed", and any other refusal or error is new.
    """

    stages = [stage for stage in payload.get("stages") or () if isinstance(stage, Mapping)]
    if not stages or any(stage.get("stage") == "locate source" and stage.get("outcome") != "ok" for stage in stages):
        return NOT_REPLAYED
    if any(_same_failure(record, diagnostic) for diagnostic in _diagnostics(payload)):
        return STILL_FAILS
    return FIXED if payload.get("outcome") == "ok" else FAILS_DIFFERENTLY


def describe_replay(payload: Mapping[str, Any]) -> str:
    refusal = payload.get("refusal") or {}
    exception = payload.get("exception") or {}
    parts = [f"outcome {payload.get('outcome')}"]
    if refusal:
        parts.append(f"refusal {refusal.get('code')}: {refusal.get('detail') or refusal.get('message')}")
    if exception:
        parts.append(f"{exception.get('type')}: {exception.get('message')}")
    parts.append(f"scratch {payload.get('data_dir')}")
    return "; ".join(str(part) for part in parts)


# -- export -----------------------------------------------------------------------

#: What an identifying value becomes in a redacted export.
REDACTED = "<redacted>"
#: Keys whose values name a run, a request, a design, a document or a return.
IDENTIFYING_KEYS = frozenset({
    "job_id", "jobId", "operation_id", "operationId", "ingest_id", "ingestId",
    "preparation_id", "request_id", "client_request_id", "run_number", "manifest_sha256", "manifestSha256",
    "snapshot", "snapshotSha256", "snapshot_sha256", "artifact_sha256", "design_id", "designId", "design_ids",
    "designIds", "export_id", "lineage_id", "project_lineage_id", "bundle_id", "origin_bundle_id", "native_id",
    "document_native_id", "return_id", "project_id", "receipt_id", "receiptId",
})
#: Keys whose values are a model's or a body's name.
NAME_KEYS = frozenset({"document_name", "documentName", "model_name", "modelName"})
#: Record fields that are WG's vocabulary: what failed and how (``kind``,
#: ``phase``, ``code``, a stage or an axis). A value that is one of WG's words
#: (:data:`_WG_WORD`) is kept as it is, even when a body is named like it, so
#: the export still reads and replays; any other value (a sentence, a name) is
#: redacted like free text.
_RECORD_VOCABULARY = frozenset({
    "kind", "phase", "code", "stage", "reason_code", "reasonCode", "chosen_axis", "axis", "meshed_axis",
    "allowed_axes", "allowedAxes", "outcome", "status", "severity", "type", "provenance", "algorithm",
    "operation", "source", "versions", "fingerprint", "schema", "firstSeen", "lastSeen", "at", "capturedAt",
    "truncated", "count", "redacted",
})
#: A record's own fields, written by WG: kept as they are.
_RECORD_HEADER = ("schema", "id", "kind", "phase", "fingerprint", "firstSeen", "lastSeen", "count")
#: One of WG's own words: a code, a stage, a kind, a version (``open_half``,
#: ``cad-preparation``, ``0.3.6``). Lower-case ASCII, no spaces.
_WG_WORD = re.compile(r"[a-z0-9]+(?:[_.:-][a-z0-9]+)*")
#: What a caught interface error's own words become in a redacted export.
WITHHELD = "<client message withheld>"
#: Interface-error fields that are the interface's own words for what failed:
#: kept when they are one of WG's words, withheld otherwise.
_CLIENT_WORDS = frozenset({"operation", "source"})
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?")
WITHHELD_NOTE = (
    "Caught interface errors carry the interface's own text, which no name source covers: "
    f"the export replaces each such text with {WITHHELD!r} and its fingerprint."
)
#: Manifest values that are protocol vocabulary, never a user's words: a body
#: named like one must not turn the manifest invalid. Every enumerated field
#: of the wgreturn validator (``server.cadlink.wglink_protocol``) is here.
_MANIFEST_VOCABULARY = frozenset({
    "adapter", "adapter_version", "body_kind", "build_mode", "cad_app", "cad_version",
    "chirality", "external_reference", "formula", "handedness", "kind", "length_unit",
    "local_body_state", "matrix_convention", "media_type", "patch_policy", "purpose",
    "reason", "required_features", "role", "selection", "severity", "status",
    "wgreturn_version", "sha256", "signature_hash", "design_hash",
    "geometry_hash", "instance_id", "wglink_instance_id", "solver_anchor_instance_id",
    "default_drive_channel_id", "id", "created_at", "observed_at",
    "export_frame", "declared_by", "kept_side", "plane", "cut_planes", "origin_plane", "document_up",
    "settings_origin", "symmetry", "planes",
    # Ids: replaced by stand-ins (``_MANIFEST_IDS``) or removed, never by names.
    "design_id", "export_id", "lineage_id", "origin_bundle_id", "native_id",
})
#: Manifest identifiers: replaced by stable stand-ins (one per original value).
_MANIFEST_IDS = ("design_id", "export_id", "lineage_id")
_STEP_STRING = re.compile(r"'((?:[^']|'')*)'")
#: The entities whose first string (and a product's or an occurrence's second) is a user's name.
_STEP_NAMED = re.compile(
    r"\b(PRODUCT|NEXT_ASSEMBLY_USAGE_OCCURRENCE|MANIFOLD_SOLID_BREP|BREP_WITH_VOIDS|OPEN_SHELL|CLOSED_SHELL"
    r"|SHELL_BASED_SURFACE_MODEL|STYLED_ITEM|COLOUR_RGB|PRESENTATION_LAYER_ASSIGNMENT)"
    r"\s*\(\s*'((?:[^']|'')*)'(?:\s*,\s*'((?:[^']|'')*)')?",
    re.IGNORECASE,
)
_STEP_DESCRIPTION = re.compile(r"FILE_DESCRIPTION\s*\(.*?\)\s*;", re.DOTALL)
_STEP_DATA = re.compile(r"^\s*DATA\s*;", re.MULTILINE)
_STEP_ENTITY = re.compile(r"#\d+\s*=\s*\(?\s*([A-Z][A-Z0-9_]*)", re.IGNORECASE)
#: STEP entities whose strings are the format's own words (schema, context,
#: pre-defined colours and fonts): never rewritten, whatever a body is called.
_STEP_PROTOCOL_ENTITIES = frozenset({
    "APPLICATION_CONTEXT", "APPLICATION_PROTOCOL_DEFINITION", "PRODUCT_CONTEXT", "PRODUCT_DEFINITION_CONTEXT",
    "MECHANICAL_CONTEXT", "DESIGN_CONTEXT", "PRODUCT_CATEGORY", "PRODUCT_RELATED_PRODUCT_CATEGORY",
    "GEOMETRIC_REPRESENTATION_CONTEXT", "UNCERTAINTY_MEASURE_WITH_UNIT", "DRAUGHTING_PRE_DEFINED_COLOUR",
    "DRAUGHTING_PRE_DEFINED_CURVE_FONT", "PRE_DEFINED_MARKER", "DRAUGHTING_PRE_DEFINED_TEXT_FONT",
})
_STEP_ESCAPE = re.compile(
    r"\\X2\\((?:[0-9A-F]{4})+)\\X0\\|\\X4\\((?:[0-9A-F]{8})+)\\X0\\|\\X\\([0-9A-F]{2})|\\\\|''",
    re.IGNORECASE,
)


def _step_decode(raw: str) -> str:
    """A STEP string literal's contents as text (ISO 10303-21 escapes)."""

    def one(match: re.Match[str]) -> str:
        if match.group(1):
            hexed = match.group(1)
            return "".join(chr(int(hexed[index:index + 4], 16)) for index in range(0, len(hexed), 4))
        if match.group(2):
            hexed = match.group(2)
            return "".join(chr(int(hexed[index:index + 8], 16)) for index in range(0, len(hexed), 8))
        if match.group(3):
            return bytes([int(match.group(3), 16)]).decode("latin-1")
        return match.group(0)[0]

    return _STEP_ESCAPE.sub(one, raw)


def _step_encode(text: str) -> str:
    """Text as a STEP string literal's contents: ASCII, quotes doubled, the rest escaped."""

    parts = []
    for character in text:
        code = ord(character)
        if character == "'":
            parts.append("''")
        elif character == "\\":
            parts.append("\\\\")
        elif 32 <= code < 127:
            parts.append(character)
        elif code <= 0xFFFF:
            parts.append(f"\\X2\\{code:04X}\\X0\\")
        else:
            parts.append(f"\\X4\\{code:08X}\\X0\\")
    return "".join(parts)


class CannotRedact(Exception):
    """Nothing says which model a record is about: its names cannot be known."""


class ExportRedactor:
    """Every name and id an exported incident carries, replaced consistently.

    Names (the document's, the components', bodies', features', shells' and
    occurrences') become ``redacted-model`` and ``name-<n>``, matched as
    :class:`~server.platform.incidents.NameMatcher` finds them (whole words,
    any case, composed, plurals and numbers, each long word of a multi-word
    name) in text whose percent-escapes are decoded (``comparable_text``,
    which is the form written); ids become ``<redacted>``: the ones the record and the
    bundle name, and anything shaped like one (``IDENTIFIER_PATTERN``). The
    same original always becomes the same stand-in, in the record, the logs,
    the manifest and the STEP file alike, so the fixture still replays.
    """

    def __init__(self, base: Redactor) -> None:
        self._base = base
        self._names: dict[str, str] = {}
        self._ids: set[str] = set()
        self._matcher = NameMatcher(())
        self._id_pattern: re.Pattern[str] | None = None
        self._stale = True

    def add_name(self, value: Any, placeholder: str | None = None) -> None:
        name = comparable_text(str(value or "")).strip()
        if len(name) < MIN_REDACTION_NAME or name.lower() in self._names or name == REDACTED_NAME:
            return
        self._names[name.lower()] = placeholder or f"name-{len(self._names) + 1}"
        self._stale = True

    def add_names(self, names: Sequence[Any], *, document_first: bool = False) -> None:
        for index, name in enumerate(names):
            self.add_name(name, REDACTED_NAME if document_first and index == 0 and not self._names else None)

    def add_id(self, value: Any) -> None:
        text = comparable_text(str(value or "")).strip()
        for spelling in {text, text.removeprefix("sha256:")}:
            if len(spelling) >= 4 and spelling != REDACTED:
                self._ids.add(spelling)
                self._stale = True

    def names(self) -> list[str]:
        return list(self._names)

    def _compile(self) -> None:
        if self._stale:
            self._matcher = NameMatcher(self._names)
            ids = sorted({item.lower() for item in self._ids}, key=len, reverse=True)
            self._id_pattern = re.compile("|".join(map(re.escape, ids)), re.IGNORECASE) if ids else None
            self._stale = False

    def text(self, value: str, *, names: bool = True) -> str:
        """Paths, the account name and ids replaced; and the names, unless ``names`` is false."""

        value = self._base.text(comparable_text(value))
        self._compile()
        if self._id_pattern is not None:
            value = self._id_pattern.sub(REDACTED, value)
        value = IDENTIFIER_PATTERN.sub(REDACTED, value)
        if names:
            value = self._matcher.sub(lambda name: self._names[name.lower()], value)
        return value

    def word(self, value: str) -> str:
        """One of WG's words as it is (ids and paths still go); anything else as free text."""

        return self.text(value, names=not _WG_WORD.fullmatch(comparable_text(value)))

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            # A key that is one of WG's words ("code", "detail") stays: a body
            # called like one must not rename the record's fields. A key that
            # is not (a body's name keying its counts) is redacted.
            result = {}
            for key, item in value.items():
                name = str(key)
                if name in IDENTIFYING_KEYS and item not in (None, "", [], {}):
                    result[self.word(name)] = REDACTED
                elif name in _RECORD_VOCABULARY:
                    result[self.word(name)] = self._vocabulary(item)
                else:
                    result[self.word(name)] = self.value(item)
            return result
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        return value

    def _vocabulary(self, value: Any) -> Any:
        """A vocabulary value, at any depth: one of WG's words stays, anything else is free text."""

        if isinstance(value, str):
            return self.word(value)
        if isinstance(value, Mapping):
            return {self.word(str(key)): self._vocabulary(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._vocabulary(item) for item in value]
        return value

    # -- what to look for ------------------------------------------------------

    def collect_record(self, value: Any) -> None:
        """The ids and names a record holds, wherever they are."""

        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key) in IDENTIFYING_KEYS:
                    for each in item if isinstance(item, (list, tuple)) else (item,):
                        if isinstance(each, (str, int)) and not isinstance(each, bool):
                            self.add_id(each)
                elif str(key) in NAME_KEYS:
                    self.add_name(item, None if self._names else REDACTED_NAME)
                self.collect_record(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.collect_record(item)

    def collect_manifest(self, manifest: Mapping[str, Any]) -> None:
        # The document's name first: it is the one ``redacted-model`` stands for.
        self.add_names(identifying_names(manifest), document_first=True)
        document = manifest.get("document") if isinstance(manifest.get("document"), Mapping) else {}
        self.add_id(document.get("native_id"))
        self.add_id((manifest.get("return") or {}).get("id") if isinstance(manifest.get("return"), Mapping) else None)
        for instance in _mappings(manifest.get("instances")):
            for key in (*_MANIFEST_IDS, "origin_bundle_id"):
                self.add_id(instance.get(key))

    def collect_step(self, text: str) -> None:
        for match in _STEP_NAMED.finditer(text):
            for raw in match.groups()[1:]:
                if raw:
                    self.add_name(re.sub(r":\d+$", "", _step_decode(raw)))

    # -- rewriting -------------------------------------------------------------

    def step(self, text: str) -> str:
        """Every user string with its names and ids replaced; the file stays valid STEP.

        The header's author fields and description are blanked and its schema
        kept; in the data section every string literal is rewritten except the
        format's own words (``_STEP_PROTOCOL_ENTITIES``).
        """

        data = _STEP_DATA.search(text)
        split = data.end() if data else len(text)
        header, body = text[:split], text[split:]
        header = _STEP_FILE_NAME.sub(f"FILE_NAME('{REDACTED_NAME}','',(''),(''),'','','');", header, count=1)

        def description(match: re.Match[str]) -> str:
            literals = _STEP_STRING.findall(match.group(0))
            level = literals[-1] if len(literals) >= 2 else "2;1"
            return f"FILE_DESCRIPTION((''),'{level}');"

        header = _STEP_DESCRIPTION.sub(description, header, count=1)
        entities = [(match.start(), match.group(1).upper()) for match in _STEP_ENTITY.finditer(body)]
        starts = [start for start, _type in entities]

        def literal(match: re.Match[str]) -> str:
            index = bisect.bisect_right(starts, match.start()) - 1
            if index >= 0 and entities[index][1] in _STEP_PROTOCOL_ENTITIES:
                return match.group(0)
            decoded = _step_decode(match.group(1))
            redacted = self.text(decoded)
            return match.group(0) if redacted == comparable_text(decoded) else f"'{_step_encode(redacted)}'"

        return header + _STEP_STRING.sub(literal, body)

    def manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """The manifest with no document or origin-bundle id, stand-in names and ids (valid ULIDs)."""

        stand_ins: dict[str, str] = {}

        def stand_in(value: Any) -> Any:
            if not isinstance(value, str) or not value:
                return value
            # ``wgr_`` plus 26 ULID characters, as the protocol requires: an
            # all-digit ULID that names nothing.
            prefix = value.split("_", 1)[0] if "_" in value else "id"
            return stand_ins.setdefault(value, f"{prefix}_{len(stand_ins) + 1:026d}")

        if isinstance(manifest.get("document"), dict):
            manifest["document"]["name"] = REDACTED_NAME
            if "native_id" in manifest["document"]:
                manifest["document"]["native_id"] = None
        if isinstance(manifest.get("return"), dict) and "id" in manifest["return"]:
            manifest["return"]["id"] = stand_in(manifest["return"]["id"])
        for instance in _mappings(manifest.get("instances")):
            instance.pop("origin_bundle_id", None)
            for key in _MANIFEST_IDS:
                if key in instance:
                    instance[key] = stand_in(instance[key])
        return self._manifest_names(manifest)

    def _manifest_names(self, value: Any, key: str | None = None) -> Any:
        if key in _MANIFEST_VOCABULARY:
            return value
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {name: self._manifest_names(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [self._manifest_names(item, key) for item in value]
        return value


def _withhold(value: Any, key: str | None = None) -> Any:
    """A caught interface error's own words, withheld: each text becomes its fingerprint.

    What the interface sent (its message, stack and context) may hold any name,
    and no name source covers it. Only what says which operation failed and
    how (``operation`` and ``source``, when they are one of WG's words) and when
    (``at``, a timestamp) stays; numbers and flags stay too.
    """

    if isinstance(value, str):
        text = comparable_text(value)
        if (key in _CLIENT_WORDS and _WG_WORD.fullmatch(text)) or (key == "at" and _TIMESTAMP.fullmatch(text)):
            return value
        digest = hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()[:12]
        return f"{WITHHELD} {digest}"
    if isinstance(value, Mapping):
        return {
            (str(name) if _WG_WORD.fullmatch(str(name)) else _withhold(str(name))): _withhold(item, str(name))
            for name, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_withhold(item) for item in value]
    return value


def _mappings(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _occurrence_ids(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    occurrences = [record.get("latest") or {}, *(record.get("occurrences") or ())]
    return [item["ids"] for item in occurrences if isinstance(item, Mapping) and isinstance(item.get("ids"), Mapping)]


def export_incident(
    record: Mapping[str, Any], data_dir: Path, destination: Path, *, redact: bool,
    notes: list[str] | None = None,
) -> list[Path]:
    """Write the record, and its retained bundle when there is one, as a fixture.

    The bundle is WG's retained copy, which never holds the CAD document. With
    ``redact`` every name the record and the bundle hold (the document's, the
    components', bodies', features' and occurrences', the STEP products') becomes
    ``redacted-model`` or ``name-<n>``, every id ``<redacted>`` (the manifest's
    by stand-ins), the STEP header's author fields and description are blanked
    (the manifest's file hashes follow), the job logs are keyed ``job-<n>``, and
    paths and the account name are rewritten.

    The names come from every source there is: the retained bundle, the names
    the record kept (``redactionNames``), and the run, ingest and bundle its
    ids lead to. With none of them the names in its text cannot be known, and
    a redacted export is refused (:class:`CannotRedact`) rather than written
    with them in it. A caught interface error's own text (its message, stack
    and context, and an operation that is not one of WG's words) is the
    interface's, which no name source covers: it is withheld (:data:`WITHHELD`
    and its fingerprint), the record says so (``clientTextWithheld``), and so
    does a line added to ``notes``.
    """

    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"{destination} must be new or empty")
    ids = (record.get("latest") or {}).get("ids") or {}
    digest = str(ids.get("manifest_sha256") or "")
    bundle = retained_bundle(data_dir, digest) if _DIGEST.fullmatch(digest) else None
    if bundle is not None and not bundle.is_dir():
        bundle = None
    if not redact:
        destination.mkdir(parents=True, exist_ok=True)
        written = [destination / "incident.json"]
        written[0].write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if bundle is not None:
            target = destination / bundle.name
            shutil.copytree(bundle, target, ignore=shutil.ignore_patterns("*.f3d"))
            written.append(target)
        return written

    from server.diagnostics.incidents import IncidentNames

    redactor = ExportRedactor(Redactor(redaction_rules(data_dir=data_dir, app_root=app_root()), user=account_name()))
    sources: list[str] = []
    manifest: dict[str, Any] | None = None
    if bundle is not None:
        # The manifest first: its document name is the one ``redacted-model`` stands for.
        manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
        redactor.collect_manifest(manifest)
        for name in manifest.get("files") or {}:
            path = bundle / name
            if name.lower().endswith((".step", ".stp")) and path.is_file():
                redactor.collect_step(path.read_text(encoding="utf-8", errors="surrogateescape"))
        sources.append("bundle")
    kept = record.get("redactionNames")
    if isinstance(kept, list):
        redactor.add_names(kept, document_first=True)
        sources.append("record")
    resolver = IncidentNames(data_dir)
    for occurrence in _occurrence_ids(record):
        names, found = resolver.find(occurrence)
        redactor.add_names(names, document_first=True)
        sources.extend(found)
    if not sources:
        raise CannotRedact(
            f"{record.get('id')} cannot be exported with --redact: nothing says which model it is about "
            "(it kept no names, WG's copy of the return is gone, and so are the run and the ingest it "
            "names), so the names in its text cannot be known. Export it without --redact for your own "
            "use only."
        )
    redactor.collect_record(record)
    for job_id in (record.get("jobLogs") or {}):
        redactor.add_id(job_id)
    exported = {key: item for key, item in record.items() if key != "redactionNames"}
    exported["occurrences"] = [{"at": item.get("at")} for item in record.get("occurrences") or ()]
    exported["jobLogs"] = {
        f"job-{index}": log for index, log in enumerate((record.get("jobLogs") or {}).values(), start=1)
    }
    exported["redacted"] = True
    client = record.get("kind") == INTERFACE_ERROR_KIND
    if client:
        latest = dict(exported.get("latest") or {})
        for key in ("evidence", "exception"):
            if latest.get(key) is not None:
                latest[key] = _withhold(latest[key])
        exported["latest"] = latest
        exported["clientTextWithheld"] = True
        if notes is not None:
            notes.append(WITHHELD_NOTE)
    redacted = redactor.value(exported)
    # WG's own words about the record (its id, kind, phase, fingerprint and
    # times), whatever a body is called.
    redacted.update({key: record[key] for key in _RECORD_HEADER if key in record})
    if client and not _WG_WORD.fullmatch(comparable_text(str(record.get("phase") or ""))):
        redacted["phase"] = _withhold(str(record.get("phase") or ""))
    destination.mkdir(parents=True, exist_ok=True)
    written = [destination / "incident.json"]
    written[0].write_text(json.dumps(redacted, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if bundle is not None and manifest is not None:
        target = destination / "model.wgreturn"
        _write_redacted_bundle(bundle, manifest, target, redactor)
        written.append(target)
    return written


def _write_redacted_bundle(bundle: Path, manifest: dict[str, Any], target: Path, redactor: ExportRedactor) -> None:
    """Only the manifest and the members it lists; names and ids replaced, hashes updated."""

    import hashlib

    target.mkdir()
    members: dict[str, Any] = {}
    renamed: dict[str, str] = {}
    for name, entry in (manifest.get("files") or {}).items():
        source = bundle / name
        if not source.is_file() or source.suffix.lower() == ".f3d":
            continue
        new_name = redactor.text(name)
        renamed[name] = new_name
        destination = target / new_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if name.lower().endswith((".step", ".stp")):
            payload = redactor.step(source.read_text(encoding="utf-8", errors="surrogateescape")).encode(
                "utf-8", errors="surrogateescape",
            )
        else:
            payload = source.read_bytes()
        destination.write_bytes(payload)
        entry = dict(entry) if isinstance(entry, Mapping) else entry
        if isinstance(entry, dict):
            entry["sha256"] = "sha256:" + hashlib.sha256(payload).hexdigest()
            entry["size_bytes"] = len(payload)
        members[new_name] = entry
    manifest["files"] = members
    manifest = redactor.manifest(manifest)
    manifest["files"] = members  # member names are already rewritten, and hashed
    for section in ("assembly",):
        if isinstance(manifest.get(section), dict) and manifest[section].get("file") in renamed:
            manifest[section]["file"] = renamed[manifest[section]["file"]]
    (target / "wgreturn.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# -- entry point --------------------------------------------------------------------


def main(
    argv: list[str] | None = None,
    *,
    replay_runner: Callable[[Sequence[str]], dict[str, Any]] = run_cad_replay,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m server.tools.cad_diag", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, help="WG data directory (default: this machine's)")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="Incidents, most recent first")
    listing.add_argument("--since", type=parse_since, help="Only those seen within, e.g. 2h or 1d")
    listing.add_argument("--kind", help="Only this kind, e.g. internal-failure")
    listing.add_argument("--json", action="store_true")
    show = commands.add_parser("show", help="One incident record in full")
    show.add_argument("id", help="Incident id or a unique prefix")
    replay = commands.add_parser("replay", help="Replay the incident's request in scratch")
    replay.add_argument("id")
    replay.add_argument("--json", action="store_true", help="Print the verdict and the replay report")
    export = commands.add_parser("export", help="Write the record (and retained bundle) to a folder")
    export.add_argument("id")
    export.add_argument("--out", type=Path, required=True, help="New or empty folder")
    export.add_argument("--redact", action="store_true",
                        help="Replace every name and id (record, logs, manifest, STEP); blank STEP author "
                             "fields. Refused (exit 2) when nothing says which model the record is about")
    args = parser.parse_args(argv)

    data_dir = (args.data_dir or resolve_data_dir()).expanduser().resolve()
    store = _store(data_dir)
    if args.command == "list":
        records = store.list(since=args.since, kind=args.kind)
        if args.json:
            print(json.dumps([summary(record) for record in records], indent=2))
        else:
            print("\n".join(list_lines(records)) if records else f"No incidents in {store.root}")
        return 0
    if args.command == "replay":
        # "Could not replay" is its own outcome with its own exit code, never
        # the "still fails" a bare SystemExit(message) would read as.
        try:
            record = _require(store, args.id)
            payload = replay_runner(replay_arguments(record, data_dir))
        except NotReplayable as exc:
            print(f"{args.id}: {NOT_REPLAYED}\n  {exc}", file=sys.stderr)
            if args.json:
                print(json.dumps({"incident": args.id, "verdict": NOT_REPLAYED, "reason": str(exc)}, indent=2))
            return EXIT_CODES[NOT_REPLAYED]
        outcome = verdict(record, payload)
        if args.json:
            print(json.dumps({"incident": record.get("id"), "verdict": outcome, "replay": payload},
                             indent=2, default=str))
        else:
            print(f"{record.get('id')}: {outcome}")
            print(f"  replay: {describe_replay(payload)}")
        return EXIT_CODES[outcome]
    try:
        record = _require(store, args.id)
    except NotReplayable as exc:
        raise SystemExit(str(exc)) from exc
    if args.command == "show":
        print(json.dumps(record, indent=2, sort_keys=True))
        return 0
    notes: list[str] = []
    try:
        written = export_incident(record, data_dir, args.out.expanduser().resolve(), redact=args.redact, notes=notes)
    except CannotRedact as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for note in notes:
        print(note, file=sys.stderr)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
