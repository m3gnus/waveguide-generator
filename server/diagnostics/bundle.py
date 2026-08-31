"""Assemble the one file a bug report needs.

A report that is a screenshot costs a round trip to ask for the build label, a
second to ask which backend solved it, and a third for the log -- by which time
the user has usually stopped answering. This module produces all of it at once,
and produces it as an ordinary zip the reporter can open and read before they
send it, because "trust me, it is only logs" is not an argument anybody should
have to accept.

Everything here is synchronous and takes its inputs explicitly. The event loop
calls it through ``asyncio.to_thread``; the suite calls it directly with a
temporary data directory and no application at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import platform
import sys
from typing import Any
import zipfile

from server.platform.paths import DataPaths
from .scrub import ScrubRules, scrub_text, scrub_value


SCHEMA_VERSION = 1

#: The whole application log, both generations. ``RotatingFileHandler`` already
#: caps each at 5 MB (``server/platform/logging_setup.py``), so this ceiling is
#: a guard against a hand-placed file rather than a policy.
MAX_APP_LOG_BYTES = 6 * 1024 * 1024

#: Job logs have no rotation and no ceiling -- a long sweep with per-frequency
#: progress writes a lot -- so the bundle keeps the tail, which is where a
#: failure is.
MAX_JOB_LOG_BYTES = 2 * 1024 * 1024

#: What the reporter is told, verbatim, in the zip itself.
SCRUB_STATEMENT = (
    "Home directory and application-data roots are rewritten to ~ or %VAR% in "
    "every file above, so this report does not name the account it came from. "
    "Folder names below those roots are kept, because path shape is itself a "
    "cause of several defects."
)

#: Settings namespaces that describe how WG is configured. Everything outside
#: this list is reported by size only.
#:
#: This is an allowlist and not a blocklist on purpose. ``ui_settings.json``
#: holds the autosaved design draft and the user's whole driver library beside
#: the interface preferences (``frontend/src/stores/durableSettings.ts``), so a
#: rule that shipped everything it did not recognise would put the design in
#: the report the checkbox exists to keep out of it -- and would do so again the
#: next time a namespace is added.
SETTINGS_ALLOWLIST = (
    "preferences",
    "solveOptions",
    "viewer",
    "theme",
    "workspaceMode",
    "dockviewMode",
    "paramHelp",
    "paramSections",
    "crossoverView",
    "crossoverGainUnit",
)

#: Namespaces held back unless the reporter ticks "include my design".
DESIGN_NAMESPACES = ("designDraft",)

_MEMBER_NOTES = {
    "manifest.json": "This file: what the report contains and what was rewritten.",
    "summary.json": "Build, platform, solver availability, recent runs.",
    "system.json": "Operating system, Python, WG2_* environment variables.",
    "capabilities.json": "Which backends this machine can solve with, and module drift.",
    "settings.json": "Interface configuration; design and driver data withheld.",
    "workspace.json": "Export and CAD folder locations, scrubbed.",
    "frontend-errors.json": "Interface errors this session reported to the server.",
    "logs/server.log": "The application log.",
    "logs/server.log.1": "The previous application log, kept across rotation.",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def flush_log_sinks() -> None:
    """Push buffered records to disk before the log is read.

    The application log is written by a ``RotatingFileHandler`` behind a
    ``QueueListener``, so the most recent lines -- which are the ones about
    whatever just went wrong -- may still be in the handler's buffer when
    somebody asks for a report. Records still queued for the listener are not
    waited for: draining the queue would mean synchronising with a thread that
    is servicing the whole application, to gain the milliseconds between the
    failure and the click.
    """

    try:
        from server.platform.logging_setup import log_sinks

        for handler in log_sinks():
            try:
                handler.flush()
            except (OSError, ValueError):  # a closed or broken sink
                continue
    except Exception:
        # Reporting must not depend on logging being healthy; a report from a
        # process whose logging is broken is a report worth having.
        return


def read_log_text(path: Path, *, limit: int, tail: bool = False) -> str | None:
    """Read a log completely, or not at all.

    Read fully and close, rather than streaming into the zip from an open
    handle. ``RotatingFileHandler`` rolls over by renaming, and on Windows a
    rename fails outright while any other handle is open on the source -- so a
    lazily-consumed handle would turn "somebody asked for a report" into
    "logging stopped", which is precisely backwards.
    """

    try:
        raw = path.read_bytes()
    except (OSError, ValueError):
        return None
    if len(raw) > limit:
        raw = raw[-limit:] if tail else raw[:limit]
    # Log files are UTF-8 by handler configuration, but a crashed native
    # solver can leave a partial sequence at the end of one. Losing the report
    # over the last three bytes of it would be an absurd trade.
    return raw.decode("utf-8", errors="replace")


def collect_system(*, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Platform facts that cost nothing to gather.

    No memory or GPU probe. WG has no dependency that reports either, the
    solver capability list already discriminates the failures those numbers
    would explain, and a ctypes call written for a bug-report nicety is a
    ctypes call to maintain.
    """

    import os

    env = os.environ if environ is None else environ
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        # Only WG's own switches, by name and value. The rest of the
        # environment is the user's business and routinely holds credentials.
        "wg2_environment": {
            name: value for name, value in sorted(env.items()) if name.startswith("WG2_")
        },
    }


def collect_settings(envelope: Mapping[str, Any] | None) -> dict[str, Any]:
    """Split the settings envelope into what is reported and what is counted."""

    namespaces = {}
    if isinstance(envelope, Mapping):
        raw = envelope.get("namespaces")
        if isinstance(raw, Mapping):
            namespaces = raw

    included: dict[str, Any] = {}
    withheld: dict[str, Any] = {}
    for name, value in sorted(namespaces.items()):
        if name in SETTINGS_ALLOWLIST:
            included[name] = _decoded_setting(value)
        else:
            withheld[name] = {"bytes": len(str(value)), "reported": False}
    return {
        "schemaVersion": (envelope or {}).get("schemaVersion"),
        "included": included,
        "withheld": withheld,
        "note": (
            "Namespaces under 'withheld' hold your design, driver library, CAD "
            "project identity or window layout. Only their size is reported."
        ),
    }


def _decoded_setting(value: Any) -> Any:
    """Settings payloads are opaque strings; show them as what they are.

    ``SettingsStore`` stores each namespace exactly as the frontend wrote it,
    which is a JSON string inside JSON. Left alone it reads as one long escaped
    line and nobody diagnoses anything from it.
    """

    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return value


def build_summary(
    *,
    build: Mapping[str, Any],
    version: str,
    data_dir: Path | str,
    capabilities: Mapping[str, Any] | None,
    jobs: Sequence[Mapping[str, Any]] = (),
    frontend_error_count: int = 0,
    rules: ScrubRules,
    system: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The short form: what a maintainer reads first, and what pastes anywhere."""

    resolved_system = dict(system) if system is not None else collect_system()
    engines: Any
    dependencies: Any
    storage: Any
    if capabilities is None:
        engines = {"status": "unavailable", "reason": "The solver probe did not complete."}
        dependencies = None
        storage = None
    else:
        engines = [
            {
                "name": engine.get("name"),
                "available": engine.get("available"),
                "reason": engine.get("reason"),
                "version": engine.get("version"),
            }
            for engine in capabilities.get("engines", ())
            if isinstance(engine, Mapping)
        ]
        raw_dependencies = capabilities.get("dependencies") or {}
        dependencies = {
            "drift": list(raw_dependencies.get("drift") or ()),
            "pinned": raw_dependencies.get("pinned") or {},
            "installed": raw_dependencies.get("installed") or {},
        }
        storage = capabilities.get("storage")

    return {
        "schema": SCHEMA_VERSION,
        "createdAt": _now_iso(),
        "version": version,
        "build": dict(build),
        "system": {
            "platform": resolved_system.get("platform"),
            "machine": resolved_system.get("machine"),
            "python": (resolved_system.get("python") or "").split()[0] or None,
            "cpuCount": resolved_system.get("cpu_count"),
        },
        "dataDir": scrub_text(str(data_dir), rules),
        "engines": engines,
        "dependencies": dependencies,
        "storage": storage,
        "recentJobs": [_job_digest(job, rules) for job in jobs],
        "frontendErrors": frontend_error_count,
    }


def _job_digest(job: Mapping[str, Any], rules: ScrubRules) -> dict[str, Any]:
    """One run, reduced to what identifies a failure without describing a design."""

    summary = job.get("config_summary_json")
    engine = None
    if isinstance(summary, Mapping):
        engine = summary.get("engine") or summary.get("solver")
    return {
        "id": job.get("id"),
        "run": job.get("run_number"),
        "status": job.get("status"),
        "engine": engine,
        "stage": job.get("stage"),
        "createdAt": job.get("created_at"),
        "error": scrub_text(str(job["error_message"]), rules) if job.get("error_message") else None,
    }


def summary_text(summary: Mapping[str, Any]) -> str:
    """The clipboard form, and the body of a prefilled issue.

    Plain text rather than the JSON above because its destination is a forum
    post or an issue written by somebody who should not have to explain a
    payload they did not choose to paste.
    """

    build = summary.get("build") or {}
    system = summary.get("system") or {}
    lines = [
        f"Waveguide Generator {summary.get('version')} ({build.get('label') or build.get('commit') or 'unknown build'})",
        f"Source: {build.get('source') or 'unknown'}",
        f"Platform: {system.get('platform')} · {system.get('machine')} · Python {system.get('python')}",
    ]

    engines = summary.get("engines")
    if isinstance(engines, Mapping):
        lines.append(f"Solvers: {engines.get('reason') or 'unavailable'}")
    elif engines:
        available = [str(engine.get("name")) for engine in engines if engine.get("available")]
        blocked = [
            f"{engine.get('name')} ({engine.get('reason')})"
            for engine in engines
            if not engine.get("available") and engine.get("reason")
        ]
        lines.append(f"Solvers available: {', '.join(available) or 'none'}")
        if blocked:
            lines.append(f"Solvers unavailable: {'; '.join(blocked)}")

    dependencies = summary.get("dependencies") or {}
    drift = dependencies.get("drift") if isinstance(dependencies, Mapping) else None
    if drift:
        lines.append(f"Module drift: {', '.join(str(item) for item in drift)}")

    jobs = summary.get("recentJobs") or []
    failed = [job for job in jobs if job.get("status") == "failed"]
    if failed:
        lines.append("")
        lines.append("Recent failures:")
        for job in failed[:3]:
            lines.append(f"  run {job.get('run')} · {job.get('engine') or 'engine unknown'} · {job.get('error') or 'no message'}")

    if summary.get("frontendErrors"):
        lines.append(f"Interface errors this session: {summary['frontendErrors']}")
    return "\n".join(lines)


def build_bundle(
    *,
    paths: DataPaths,
    summary: Mapping[str, Any],
    system: Mapping[str, Any],
    capabilities: Mapping[str, Any] | None,
    settings_envelope: Mapping[str, Any] | None,
    rules: ScrubRules,
    job_id: str | None = None,
    job_log: str | None = None,
    job_request: Any = None,
    design_draft: Any = None,
    include_design: bool = False,
    frontend_errors: Sequence[Mapping[str, Any]] = (),
) -> bytes:
    """Zip every available member, and never fail because one was missing.

    A report is produced by somebody whose application is already misbehaving.
    Every member is therefore optional: a machine with no job logs, no settings
    file, a solver probe that timed out and a log that was deleted a moment ago
    still gets a report, and the manifest says which of those happened.
    """

    members: dict[str, bytes] = {}

    def add_json(name: str, value: Any) -> None:
        members[name] = (
            json.dumps(scrub_value(value, rules), indent=2, sort_keys=True, default=str) + "\n"
        ).encode("utf-8")

    add_json("summary.json", summary)
    add_json("system.json", system)
    add_json(
        "capabilities.json",
        capabilities
        if capabilities is not None
        else {"status": "unavailable", "reason": "The solver probe did not complete in time."},
    )
    add_json("settings.json", collect_settings(settings_envelope))
    add_json("workspace.json", _workspace_state(paths))
    add_json("frontend-errors.json", list(frontend_errors))

    flush_log_sinks()
    for name, path, tail in (
        ("logs/server.log", paths.logs / "server.log", False),
        ("logs/server.log.1", paths.logs / "server.log.1", False),
    ):
        text = read_log_text(path, limit=MAX_APP_LOG_BYTES, tail=tail)
        if text is not None:
            members[name] = scrub_text(text, rules).encode("utf-8")

    if job_id and job_log is not None:
        members[f"logs/job-{job_id}.log"] = scrub_text(job_log, rules).encode("utf-8")

    if include_design:
        if job_id and job_request is not None:
            add_json(f"design/job-{job_id}.request.json", job_request)
        if design_draft is not None:
            add_json("design/design-draft.json", _decoded_setting(design_draft))

    members["manifest.json"] = (
        json.dumps(
            {
                "schema": SCHEMA_VERSION,
                "createdAt": _now_iso(),
                "generator": "Waveguide Generator problem report",
                "build": dict(summary.get("build") or {}),
                "includesDesign": bool(include_design),
                "scrubbed": SCRUB_STATEMENT,
                "members": [
                    {
                        "name": name,
                        "bytes": len(payload),
                        "note": _member_note(name),
                    }
                    for name, payload in sorted(members.items())
                ],
            },
            indent=2,
        )
        + "\n"
    ).encode("utf-8")

    buffer = io.BytesIO()
    # Level 6, not 9: logs are the bulk of this and they are text. Measured
    # across the two application logs the last three levels buy under a percent
    # for several times the CPU, and this runs while somebody waits for a
    # download to start.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, payload in sorted(members.items()):
            archive.writestr(name, payload)
    return buffer.getvalue()


def _member_note(name: str) -> str:
    if name in _MEMBER_NOTES:
        return _MEMBER_NOTES[name]
    if name.startswith("logs/job-"):
        return "The log of the run selected in the report dialog."
    if name.startswith("design/"):
        return "Your design, included because the report dialog's design box was ticked."
    return ""


def _workspace_state(paths: DataPaths) -> dict[str, Any]:
    """The export and CAD folder settings, which are paths and nothing else."""

    state: dict[str, Any] = {}
    for key, name in (("workspace", "workspace_settings.json"), ("cadlink", "cadlink_settings.json")):
        path = paths.root / name
        try:
            state[key] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            state[key] = None
    return state


def bundle_filename(summary: Mapping[str, Any]) -> str:
    """A name that says which build produced it, without a space in it."""

    build = summary.get("build") or {}
    label = str(build.get("commit_short") or build.get("label") or summary.get("version") or "unknown")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    safe = "".join(character if character.isalnum() or character in "-._" else "-" for character in label)
    return f"wg-report-{safe[:32]}-{stamp}.zip"


__all__ = [
    "DESIGN_NAMESPACES",
    "MAX_APP_LOG_BYTES",
    "MAX_JOB_LOG_BYTES",
    "SCHEMA_VERSION",
    "SCRUB_STATEMENT",
    "SETTINGS_ALLOWLIST",
    "build_bundle",
    "build_summary",
    "bundle_filename",
    "collect_settings",
    "collect_system",
    "flush_log_sinks",
    "read_log_text",
    "summary_text",
]
