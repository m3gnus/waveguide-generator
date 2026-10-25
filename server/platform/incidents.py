"""Incident records: WG's own unexpected failures, kept for the developers.

An internal pipeline failure is a developer matter. The user is told one calm
sentence; the evidence a developer needs to find and replay the failure goes
here, one JSON file per distinct incident under ``<data>/diagnostics/incidents``.

The records are

* **deduplicated** by kind and fingerprint: a repeat of the same failure
  updates the one record (first and last occurrence, count, the latest
  evidence and the ids of the last few occurrences) instead of adding a file;
* **bounded**: at most :data:`MAX_RECORDS` records (and, in a pool of their
  own, :data:`MAX_INTERFACE_RECORDS` interface errors), none older than
  :data:`MAX_AGE_DAYS` days since it last happened, and every string, list and
  nested mapping in a record is cut to a fixed size, so no record can carry a
  mesh or a model. A record file's modification time is its last occurrence,
  so pruning reads no record;
* **privacy-safe**: the application, data and temporary folders are rewritten
  to ``<wg>``, ``<data>`` and ``<tmp>``, the home folder and the location
  variables as a problem report's scrubber writes them (``~``, ``%APPDATA%``,
  ...), and the account name to ``<user>``, before anything is fingerprinted,
  cut or written;
* **self-redacting**: a record keeps, in ``redactionNames``, the names of the
  model it is about (the document's, its components', bodies' and features'),
  from the caller and from what its ids lead to (:func:`configure_incidents`'s
  ``names``). A redacted export and the summary a problem report puts on the
  clipboard never show them: they replace them with stand-ins even when the
  model's bundle and run are long gone. A problem report's saved file carries
  the records as they are, names included, like the evidence they redact (its
  dialog says the file may name models and parts). A record without the field
  was written where no name could be known, and cannot be redacted. A caught
  interface error's words are the interface's own, which no name source covers:
  neither shows them;
* **independent of the run list**: deleting runs (Clear failed) never deletes a
  record, and the job logs it removes are copied, as bounded tails, into the
  records that name those jobs first (:func:`retain_job_logs`).

This module sits in ``server.platform`` so every layer that diagnoses a failure
can record one. The application configures the process-wide store once
(:func:`configure_incidents`); until it does, :func:`record_incident` records
nothing, so a test, the replay tool or a child process never writes into a live
data directory by accident. Recording never raises.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
import getpass
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import tempfile
import threading
import traceback
from typing import Any
import unicodedata
from urllib.parse import unquote

from .json_file import write_json_atomic
from .scrub import HOME_PLACEHOLDER, ScrubRules, location_roots, usable_root


log = logging.getLogger("wg.incidents")

SCHEMA_VERSION = 1
INCIDENTS_DIRECTORY = ("diagnostics", "incidents")

MAX_RECORDS = 200
#: Caught interface errors keep a pool of their own, so a noisy interface can
#: never push the pipeline's incidents out.
INTERFACE_ERROR_KIND = "interface-error"
MAX_INTERFACE_RECORDS = 50
MAX_AGE_DAYS = 30
#: The ids and times of the most recent occurrences of one incident.
MAX_OCCURRENCES = 20
#: Job-log tails kept per incident (one per job, the most recent jobs).
MAX_JOB_LOGS = 3
MAX_JOB_LOG_TAIL_BYTES = 16 * 1024
MAX_STRING_CHARS = 4 * 1024
MAX_STACK_CHARS = 16 * 1024
MAX_LIST_ITEMS = 32
MAX_MAPPING_ITEMS = 64
MAX_DEPTH = 6
#: Names a record keeps to redact itself with, and the longest one.
MAX_REDACTION_NAMES = 256
MAX_REDACTION_NAME_CHARS = 256
#: Shorter names are not redacted: two letters name nothing, and match everything.
MIN_REDACTION_NAME = 3

#: Keys whose values are geometry or model content. An incident describes a
#: failure; it never copies the model it failed on.
GEOMETRY_KEYS = frozenset({
    "vertices", "triangles", "faces", "points", "nodes", "elements", "indices",
    "positions", "normals", "coordinates", "mesh_data", "geometry", "step", "f3d",
    "msh", "stl", "contour", "polyline", "samples",
})

_ID_KEYS = (
    "job_id", "operation_id", "ingest_id", "preparation_id", "manifest_sha256",
    "request_id", "run_number",
)
_SAFE_ID = re.compile(r"[^a-z0-9-]+")
#: What changes between occurrences of one failure: ids, digests, counts and
#: measurements. Replaced before fingerprinting so a repeat is recognised.
_VOLATILE = re.compile(
    r"(?<![A-Za-z0-9])tmp[A-Za-z0-9_]{3,}"  # tempfile's random directory names
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|\b0x[0-9a-f]+\b"
    r"|\b[0-9a-f]{8,}\b"
    r"|\d+(?:\.\d+)?(?:e[+-]?\d+)?",
    re.IGNORECASE,
)


#: Identifier formats that name a run, a request, a design or a CAD document,
#: wherever they appear in free text: Autodesk URNs and their fragments
#: (``dm.lineage:…``, ``lineage:…``, ``vf.…``), WG's prefixed ULIDs (``wgd_``,
#: ``wgr_``, ``wgi_`` ..., also cut short or run into a word), manual-solve
#: request ids, UUIDs, and 32- and 64-hex digests and request ids. Matched in
#: :func:`comparable_text`, so a percent-encoded URN is one too.
IDENTIFIER_PATTERN = re.compile(
    r"urn:adsk[^\s'\"<>()\[\]{},;]*"
    r"|(?<![A-Za-z0-9])(?:adsk\.[a-z]+:)?(?:dm|fs)\.(?:lineage|file|folder|version):[^\s'\"<>()\[\]{},;]+"
    r"|(?<![A-Za-z0-9.])lineage:[A-Za-z0-9._-]{6,}"
    r"|(?<![A-Za-z0-9])(?:vf|co)\.[A-Za-z0-9_-]{12,}"
    r"|wg[a-z]{1,2}_[0-9A-Z]{10,}(?![A-Za-z0-9])"
    r"|(?:cad-solve:)?manual-solve:[A-Za-z0-9_.:-]+"
    r"|(?<![A-Za-z0-9-])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![A-Za-z0-9-])"
    r"|(?:sha256:)?(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])"
    r"|(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])",
    re.IGNORECASE,
)
#: A word of a multi-word name this long is a name of its own: ``Kusten`` alone
#: still names ``Höga Kusten Design``. Shorter words (``v3``, ``of``) are not.
MIN_NAME_WORD = 4
#: What may follow a name of at least :data:`MIN_NAME_WORD` characters and
#: still be it: a plural (``Designs``, ``Boxes``) or a number (``Design2``).
_NAME_SUFFIX = r"(?:e?s|\d+)?"
_NAME_WORD = re.compile(r"^[^\w]+|[^\w]+$")


def comparable_text(text: str) -> str:
    """Text as names and ids are matched in: percent-escapes decoded, then NFC.

    A URL's ``urn%3Aadsk…`` is the URN, and a name typed in decomposed form
    (``o`` and a combining diaeresis) is the name typed composed. Redacted text
    is written in this form.
    """

    for _ in range(3):  # twice-encoded ids too
        if "%" not in text:
            break
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    return unicodedata.normalize("NFC", text)


class NameMatcher:
    """The names of one model, found in text whatever their form.

    Whole tokens, any case, the longest first, compared composed (NFC): a name
    matches only where it is not part of a longer word (a letter or a digit on
    either side), so a body called ``open`` leaves ``opened`` alone; ``_``,
    ``-``, ``.`` and ``'`` separate tokens (``wg_<stem>_length``, ``<name>.f3d``,
    ``<name>'s``). A name of :data:`MIN_NAME_WORD` characters or more also
    matches with a plural or a number after it, and each such word of a
    multi-word name is a name of its own: redacting too much is the safe side.
    :meth:`sub` hands the replacement the name a match belongs to.
    """

    def __init__(self, names: Iterable[str]) -> None:
        self._owners: dict[str, str] = {}
        whole = [unicodedata.normalize("NFC", str(name)).strip() for name in names]
        whole = [name for name in whole if len(name) >= MIN_REDACTION_NAME]
        for name in whole:
            self._owners.setdefault(name.lower(), name)
        for name in whole:
            words = [_NAME_WORD.sub("", word) for word in name.split()]
            if len(words) > 1:
                for word in words:
                    if len(word) >= MIN_NAME_WORD and any(character.isalpha() for character in word):
                        self._owners.setdefault(word.lower(), name)
        alternatives = []
        for term in sorted(self._owners, key=lambda item: (-len(item), item)):
            before = r"(?<![^\W_])" if term[0].isalnum() else ""
            after = ""
            if term[-1].isalnum():
                after = (_NAME_SUFFIX if len(term) >= MIN_NAME_WORD else "") + r"(?![^\W_])"
            alternatives.append(before + re.escape(term) + after)
        self._pattern = re.compile("|".join(alternatives), re.IGNORECASE) if alternatives else None

    def __bool__(self) -> bool:
        return self._pattern is not None

    def owner(self, matched: str) -> str:
        """The name a match found (a plural or a number after it dropped)."""

        key = unicodedata.normalize("NFC", matched).lower()
        for candidate in (key, key.rstrip("0123456789"), key[:-1], key[:-2]):
            if candidate in self._owners:
                return self._owners[candidate]
        longest = sorted(self._owners.items(), key=lambda item: -len(item[0]))
        return next((owner for term, owner in longest if key.startswith(term)), longest[0][1])

    def sub(self, replace: str | Callable[[str], str], text: str) -> str:
        """``text`` (composed) with every name replaced by ``replace`` or ``replace(name)``."""

        text = unicodedata.normalize("NFC", text)
        if self._pattern is None:
            return text
        if isinstance(replace, str):
            return self._pattern.sub(lambda _match: replace, text)
        return self._pattern.sub(lambda match: replace(self.owner(match.group(0))), text)


def redact_names(text: str, names: Iterable[str], *, placeholder: str = "<name>", ids: str | None = "<id>") -> str:
    """``text`` without these names (and, unless ``ids`` is None, without identifiers)."""

    text = NameMatcher(names).sub(placeholder, comparable_text(text))
    return IDENTIFIER_PATTERN.sub(ids, text) if ids is not None else text


def _redaction_names(*groups: Iterable[Any] | None) -> list[str]:
    """The names, in order, each once (any case), bounded."""

    kept: dict[str, str] = {}
    for group in groups:
        for item in group or ():
            if not isinstance(item, str):
                continue
            name = item.strip()[:MAX_REDACTION_NAME_CHARS]
            if len(name) >= MIN_REDACTION_NAME and name.lower() not in kept:
                kept[name.lower()] = name
    return list(kept.values())[:MAX_REDACTION_NAMES]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def redaction_rules(
    *, data_dir: Path | str | None, app_root: Path | str | None, home: Path | str | None = None,
    temp_dir: Path | str | None = None, environ: Mapping[str, str] | None = None,
    system: str | None = None,
) -> ScrubRules:
    """The report scrubber's roots (home, location variables), plus ``<wg>``, ``<data>``, ``<tmp>``.

    One set of roots with the problem report's :func:`~server.platform.scrub.scrub_rules`,
    so a folder reads the same in a record and in the report around it. The
    longest root wins. ``system`` (``platform.system()`` by default) decides
    the case rule, so the suite can build Windows rules on any host.
    """

    roots = list(location_roots(home=home, environ=environ))
    for value, placeholder in (
        (Path.home() if home is None else home, HOME_PLACEHOLDER),  # its resolved spelling too
        (app_root, "<wg>"),
        (data_dir, "<data>"),
        (tempfile.gettempdir() if temp_dir is None else temp_dir, "<tmp>"),
    ):
        if value is None:
            continue
        for spelling in {str(value), str(Path(value).resolve()) if Path(str(value)).is_absolute() else ""}:
            usable = usable_root(spelling)
            if usable:
                roots.append((usable, placeholder))
    os_name = platform.system() if system is None else system
    return ScrubRules(roots, ignore_case=os_name == "Windows")


def account_name() -> str | None:
    try:
        name = getpass.getuser()
    except Exception:  # noqa: BLE001 - no account name is nothing to rewrite
        return None
    return name if name and len(name) >= 3 else None


class Redactor:
    """Rewrite locations and the account name out of one record."""

    def __init__(self, rules: ScrubRules, *, user: str | None) -> None:
        self._rules = rules
        self._user = (
            # Not inside ``<user>`` itself: redacting twice changes nothing.
            re.compile(rf"(?<![A-Za-z0-9<]){re.escape(user)}(?![A-Za-z0-9>])", re.IGNORECASE)
            if user else None
        )

    def text(self, value: str) -> str:
        value = self._rules.apply(value) if self._rules else value
        return self._user.sub("<user>", value) if self._user else value

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {self.text(str(key)): self.value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        return value


def bounded(value: Any, *, depth: int = 0, limit: int = MAX_STRING_CHARS) -> Any:
    """A JSON-safe copy cut to the record bounds; geometry keys are withheld."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"... [{len(value) - limit} more characters]"
    if depth >= MAX_DEPTH:
        return f"[{type(value).__name__} below depth {MAX_DEPTH}]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_MAPPING_ITEMS:
                result["..."] = f"{len(value) - MAX_MAPPING_ITEMS} more entries"
                break
            name = str(key)
            if name.lower() in GEOMETRY_KEYS and not isinstance(item, (bool, int, float, type(None))):
                result[name] = "[withheld: geometry]"
            else:
                result[name] = bounded(item, depth=depth + 1, limit=limit)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        kept = [bounded(item, depth=depth + 1, limit=limit) for item in items[:MAX_LIST_ITEMS]]
        if len(items) > MAX_LIST_ITEMS:
            kept.append(f"... {len(items) - MAX_LIST_ITEMS} more items")
        return kept
    return bounded(repr(value), depth=depth, limit=limit)


def _normalized(value: Any) -> Any:
    if isinstance(value, str):
        return _VOLATILE.sub("#", value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return "#"
    if isinstance(value, Mapping):
        return {
            str(key): _normalized(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in _ID_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    return _VOLATILE.sub("#", repr(value))


def _stack_signature(exc: BaseException) -> list[str]:
    """Where it failed, without line numbers: they move with every edit."""

    frames = traceback.extract_tb(exc.__traceback__)
    return [f"{Path(frame.filename).name}:{frame.name}" for frame in frames[-8:]]


def fingerprint(
    kind: str, phase: str, *, exception: BaseException | None, evidence: Mapping[str, Any],
    signature: Any = None, redact: Callable[[str], str] | None = None,
) -> str:
    """The identity of one failure: what failed, where, and how, but not which run.

    ``evidence`` and ``signature`` are already redacted; ``redact`` does the
    same for the exception's message, so a path is a placeholder before it is
    normalised and a repeat in another folder is the same failure.
    """

    basis = {
        "kind": kind,
        "phase": phase,
        "signature": _normalized(signature) if signature is not None else _normalized(evidence),
        "exception": None if exception is None else {
            "type": type(exception).__name__,
            "message": _normalized((redact or str)(str(exception)))[:512],
            "stack": _stack_signature(exception),
        },
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _exception_record(exc: BaseException, redact: Callable[[str], str] = str) -> dict[str, Any]:
    """Redacted, then cut: a path is never split by the cut and left half named."""

    stack = redact("".join(traceback.format_exception(exc)))
    if len(stack) > MAX_STACK_CHARS:
        stack = "... [earlier frames cut]\n" + stack[-MAX_STACK_CHARS:]
    return {
        "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
        "message": redact(str(exc))[:MAX_STRING_CHARS],
        "stack": stack,
        "reasonCode": getattr(exc, "reason_code", None) or getattr(exc, "code", None),
    }


def read_log_tail(path: Path, *, limit: int = MAX_JOB_LOG_TAIL_BYTES) -> tuple[str, bool] | None:
    """The last ``limit`` bytes of a log, from its first whole line; ``None`` if absent."""

    try:
        with Path(path).open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - limit)
            handle.seek(start)
            raw = handle.read(limit)
    except (OSError, ValueError):
        return None
    if start > 0:
        newline = re.search(rb"\r\n|\r|\n", raw)
        raw = raw[newline.end():] if newline is not None else b""
    # Universal newlines, as the run's own log view reads it
    # (``JobStore.get_job_log``): a log written with ``\r\n`` -- by a Windows
    # text-mode writer, or copied there -- reads the same in a record.
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return text, start > 0


def incidents_dir(data_dir: Path | str) -> Path:
    return Path(data_dir).joinpath(*INCIDENTS_DIRECTORY)


def incident_id(kind: str, digest: str) -> str:
    """``<kind>-<12 hex>``: the file name says which pool a record is in."""

    return f"{_SAFE_ID.sub('-', kind.lower()).strip('-')[:40] or 'incident'}-{digest[:12]}"


_INTERFACE_PREFIX = incident_id(INTERFACE_ERROR_KIND, "").rstrip("-") + "-"


def _pool(name: str) -> str:
    return INTERFACE_ERROR_KIND if name.startswith(_INTERFACE_PREFIX) else "pipeline"


class IncidentStore:
    """The incident records of one data directory."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        app_root: Path | str | None = None,
        versions: Callable[[], Mapping[str, Any]] | Mapping[str, Any] | None = None,
        home: Path | str | None = None,
        user: str | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.root = incidents_dir(self.data_dir)
        self._versions = versions
        self._now = now
        self._lock = threading.Lock()
        self._redactor = Redactor(
            redaction_rules(data_dir=self.data_dir, app_root=app_root, home=home),
            user=account_name() if user is None else (user or None),
        )

    # -- writing ---------------------------------------------------------------

    def versions(self) -> dict[str, Any]:
        source = self._versions
        try:
            value = source() if callable(source) else source
        except Exception as exc:  # noqa: BLE001 - versions are evidence, not a precondition
            return {"error": f"versions unavailable: {exc}"}
        return dict(value or {})

    def record(
        self,
        kind: str,
        phase: str,
        *,
        ids: Mapping[str, Any] | None = None,
        exception: BaseException | None = None,
        evidence: Mapping[str, Any] | None = None,
        versions: Mapping[str, Any] | None = None,
        signature: Any = None,
        redaction_names: Iterable[str] | None = None,
    ) -> str:
        """Record one occurrence and return its incident id.

        ``redaction_names`` are the names of the model the failure is about
        that the caller knows (an empty list: it knows there are none). They
        join the record's ``redactionNames``; ``None`` adds nothing, and a
        record no caller ever gave names to has no such field.
        """

        redactor = self._redactor
        # Redacted first: the fingerprint and the cuts see placeholders, never
        # a temporary folder or a home directory.
        evidence = redactor.value(dict(evidence or {}))
        ids = redactor.value({key: value for key, value in dict(ids or {}).items() if value not in (None, "")})
        signature = redactor.value(signature) if signature is not None else None
        digest = fingerprint(
            kind, phase, exception=exception, evidence=evidence, signature=signature, redact=redactor.text,
        )
        identity = incident_id(kind, digest)
        moment = _iso(self._now())
        occurrence = {
            "at": moment,
            "ids": bounded(ids, limit=256),
            "evidence": bounded(evidence),
            "exception": _exception_record(exception, redactor.text) if exception is not None else None,
        }
        with self._lock:
            path = self.root / f"{identity}.json"
            current = self._read(path)
            if current is None or current.get("fingerprint") != digest:
                current = {
                    "schema": SCHEMA_VERSION,
                    "id": identity,
                    "kind": kind,
                    "phase": phase,
                    "fingerprint": digest,
                    "firstSeen": moment,
                    "count": 0,
                    "occurrences": [],
                    "jobLogs": {},
                }
            current["count"] = int(current.get("count") or 0) + 1
            current["lastSeen"] = moment
            current["versions"] = bounded({**self.versions(), **dict(versions or {})}, limit=512)
            current["latest"] = occurrence
            if redaction_names is not None or "redactionNames" in current:
                kept = current.get("redactionNames")
                current["redactionNames"] = _redaction_names(
                    kept if isinstance(kept, list) else (), redaction_names,
                )
            current["occurrences"] = (
                list(current.get("occurrences") or []) + [{"at": moment, "ids": occurrence["ids"]}]
            )[-MAX_OCCURRENCES:]
            self._write(path, current)
            self._prune_locked()
        return identity

    def retain_job_logs(self, job_ids: Iterable[str], log_path: Callable[[str], Path]) -> int:
        """Copy the tails of these jobs' logs into the records naming them.

        Called before the logs are deleted: the run list and its logs come and
        go with Clear failed, the evidence does not. Returns the records updated.
        """

        wanted = {str(job_id) for job_id in job_ids if job_id}
        if not wanted or not self.root.is_dir():
            return 0
        updated = 0
        with self._lock:
            for path in self._paths():
                record = self._read(path)
                if record is None:
                    continue
                named = [
                    str(item.get("ids", {}).get("job_id"))
                    for item in record.get("occurrences") or ()
                    if isinstance(item, Mapping) and str((item.get("ids") or {}).get("job_id")) in wanted
                ]
                if not named:
                    continue
                logs = dict(record.get("jobLogs") or {})
                for job_id in dict.fromkeys(named):
                    tail = read_log_tail(log_path(job_id))
                    if tail is None:
                        continue
                    text, truncated = tail
                    logs.pop(job_id, None)
                    logs[job_id] = {"capturedAt": _iso(self._now()), "truncated": truncated, "tail": text}
                record["jobLogs"] = dict(list(logs.items())[-MAX_JOB_LOGS:])
                self._write(path, record)
                updated += 1
        return updated

    def _write(self, path: Path, record: Mapping[str, Any]) -> None:
        """Write a record; its modification time becomes its last occurrence."""

        write_json_atomic(path, self._redactor.value(record))
        last = _parse(record.get("lastSeen"))
        if last is not None:
            try:
                os.utime(path, (last.timestamp(), last.timestamp()))
            except OSError:
                pass  # pruning then counts it from now, which only keeps it longer

    # -- reading ---------------------------------------------------------------

    def _paths(self) -> list[Path]:
        try:
            return sorted(path for path in self.root.glob("*.json") if path.is_file())
        except OSError:
            return []

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def list(self, *, since: datetime | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        """Every record, most recent first."""

        records = []
        for path in self._paths():
            record = self._read(path)
            if record is None:
                continue
            if kind is not None and record.get("kind") != kind:
                continue
            last = _parse(record.get("lastSeen"))
            if since is not None and (last is None or last < since):
                continue
            records.append(record)
        return sorted(records, key=lambda item: str(item.get("lastSeen") or ""), reverse=True)

    def get(self, incident_id: str) -> dict[str, Any] | None:
        """One record by id, or by a unique id prefix."""

        if not re.fullmatch(r"[a-z0-9-]+", incident_id or ""):
            return None
        exact = self.root / f"{incident_id}.json"
        if exact.is_file():
            return self._read(exact)
        matches = [path for path in self._paths() if path.stem.startswith(incident_id)]
        return self._read(matches[0]) if len(matches) == 1 else None

    def prune(self) -> int:
        with self._lock:
            return self._prune_locked()

    def _prune_locked(self) -> int:
        """Drop records past their age, then the oldest beyond each pool's size.

        Reads modification times and names only (``_write`` sets the time to
        the last occurrence): recording one failure never parses every record.
        """

        cutoff = (self._now() - timedelta(days=MAX_AGE_DAYS)).timestamp()
        limits = {INTERFACE_ERROR_KIND: MAX_INTERFACE_RECORDS, "pipeline": MAX_RECORDS}
        pools: dict[str, list[tuple[float, str, Path]]] = {}
        removed = 0
        for path in self._paths():
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if modified < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
                continue
            pools.setdefault(_pool(path.name), []).append((modified, path.name, path))
        for pool, entries in pools.items():
            entries.sort(reverse=True)
            for _modified, _name, path in entries[limits[pool]:]:
                path.unlink(missing_ok=True)
                removed += 1
        return removed


def summary(record: Mapping[str, Any], *, public: bool = False) -> dict[str, Any]:
    """One record as a line of a list: what, where, how often, and the first words.

    ``public``: for text a user may paste anywhere (a problem report's
    summary). The first words lose the record's ``redactionNames`` and every
    identifier, and a record that cannot be redacted (no ``redactionNames``)
    and a caught interface error (the interface's own words, whatever names
    they hold) show none.
    """

    latest = record.get("latest") or {}
    exception = latest.get("exception") or {}
    evidence = latest.get("evidence") or {}
    message = exception.get("message") or evidence.get("detail") or evidence.get("message") \
        or evidence.get("reason") or evidence.get("code") or ""
    if public:
        names = record.get("redactionNames")
        vouched = isinstance(names, list) and record.get("kind") != INTERFACE_ERROR_KIND
        message = redact_names(str(message), names) if vouched and message else ""
    return {
        "id": record.get("id"),
        "kind": record.get("kind"),
        "phase": record.get("phase"),
        "count": record.get("count"),
        "firstSeen": record.get("firstSeen"),
        "lastSeen": record.get("lastSeen"),
        "ids": latest.get("ids") or {},
        "message": str(message).splitlines()[0][:200] if message else None,
    }


# -- the process-wide store ----------------------------------------------------

_store: IncidentStore | None = None
_names: Callable[[Mapping[str, Any]], Iterable[str] | None] | None = None
_store_lock = threading.Lock()


def configure_incidents(
    data_dir: Path | str,
    *,
    app_root: Path | str | None = None,
    versions: Callable[[], Mapping[str, Any]] | Mapping[str, Any] | None = None,
    names: Callable[[Mapping[str, Any]], Iterable[str] | None] | None = None,
) -> IncidentStore:
    """Make ``record_incident`` write into this data directory, from now on.

    ``names`` turns an occurrence's ids into the names of the model they lead
    to (a run's snapshot, an ingest, a retained bundle), or ``None`` when they
    lead nowhere: what the record keeps to redact itself with.
    """

    global _store, _names
    store = IncidentStore(data_dir, app_root=app_root, versions=versions)
    with _store_lock:
        _store = store
        _names = names
    return store


def reset_incidents() -> None:
    """Record nothing until configured again (the suite's isolation hook)."""

    global _store, _names
    with _store_lock:
        _store = None
        _names = None


def configured_store() -> IncidentStore | None:
    return _store


def record_incident(
    kind: str,
    phase: str,
    *,
    ids: Mapping[str, Any] | None = None,
    exception: BaseException | None = None,
    evidence: Mapping[str, Any] | None = None,
    versions: Mapping[str, Any] | None = None,
    signature: Any = None,
    redaction_names: Iterable[str] | None = None,
) -> str | None:
    """Record one incident in the configured store; ``None`` when there is none.

    ``redaction_names`` are the names the caller knows (see
    :meth:`IncidentStore.record`); the configured ``names`` adds those the
    ids lead to. Never raises: the failure being recorded is already being
    handled, and recording it must not become a second one.
    """

    store, resolve = _store, _names
    if store is None:
        return None
    try:
        names = list(redaction_names) if redaction_names is not None else None
        if resolve is not None and ids:
            try:
                found = resolve(ids)
            except Exception:  # noqa: BLE001 - names help redaction; they never stop a record
                log.debug("Could not resolve an incident's names", exc_info=True)
                found = None
            if found is not None:
                names = [*(names or ()), *found]
        return store.record(
            kind, phase, ids=ids, exception=exception, evidence=evidence,
            versions=versions, signature=signature, redaction_names=names,
        )
    except Exception:  # noqa: BLE001 - see the docstring
        log.warning("Could not record a %s incident", kind, exc_info=True)
        return None


def retain_job_logs(job_ids: Iterable[str], log_path: Callable[[str], Path]) -> None:
    """Before job logs are deleted, keep their tails in the incidents naming them."""

    store = _store
    if store is None:
        return
    try:
        store.retain_job_logs(job_ids, log_path)
    except Exception:  # noqa: BLE001 - deleting runs must not fail over evidence
        log.warning("Could not keep job logs with their incidents", exc_info=True)


__all__ = [
    "GEOMETRY_KEYS",
    "IDENTIFIER_PATTERN",
    "INCIDENTS_DIRECTORY",
    "INTERFACE_ERROR_KIND",
    "IncidentStore",
    "MAX_AGE_DAYS",
    "MAX_JOB_LOGS",
    "MAX_JOB_LOG_TAIL_BYTES",
    "MAX_OCCURRENCES",
    "MAX_INTERFACE_RECORDS",
    "MAX_RECORDS",
    "MAX_REDACTION_NAMES",
    "MIN_NAME_WORD",
    "MIN_REDACTION_NAME",
    "NameMatcher",
    "Redactor",
    "account_name",
    "bounded",
    "comparable_text",
    "configure_incidents",
    "configured_store",
    "fingerprint",
    "incident_id",
    "incidents_dir",
    "read_log_tail",
    "record_incident",
    "redact_names",
    "redaction_rules",
    "reset_incidents",
    "retain_job_logs",
    "summary",
]
