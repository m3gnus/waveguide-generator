"""Incident records: bounded, deduplicated, privacy-safe and independent of the run list."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import re
import zipfile

import pytest

from server.platform import incidents
from server.platform.incidents import (
    MAX_JOB_LOG_TAIL_BYTES,
    MAX_LIST_ITEMS,
    MAX_OCCURRENCES,
    MAX_RECORDS,
    MAX_STRING_CHARS,
    IncidentStore,
    configure_incidents,
    record_incident,
)


class Clock:
    def __init__(self, start: datetime) -> None:
        self.moment = start

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **delta: float) -> None:
        self.moment += timedelta(**delta)


def _store(tmp_path: Path, **kwargs) -> IncidentStore:
    kwargs.setdefault("app_root", tmp_path / "app")
    kwargs.setdefault("home", tmp_path / "home")
    kwargs.setdefault("user", "ada-lovelace")
    kwargs.setdefault("versions", {"wg": "0.3.6", "build": {"commit_short": "abc1234"}})
    return IncidentStore(tmp_path / "data", **kwargs)


def _failure(message: str) -> Exception:
    try:
        raise RuntimeError(message)
    except RuntimeError as exc:
        return exc


def test_unconfigured_process_records_nothing(tmp_path: Path) -> None:
    assert incidents.configured_store() is None
    assert record_incident("internal-failure", "cad-preparation", evidence={"detail": "x"}) is None


def test_same_failure_is_one_record_with_count_and_occurrences(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
    store = _store(tmp_path, now=clock)
    first = store.record(
        "internal-failure", "cad-preparation",
        ids={"job_id": "job-1", "operation_id": "op-1"},
        exception=_failure("mesh 1234 failed at 0xdeadbeef00"),
        evidence={"code": "preparation_failed", "detail": "the mesh had 1234 triangles"},
    )
    clock.advance(minutes=5)
    second = store.record(
        "internal-failure", "cad-preparation",
        ids={"job_id": "job-2", "operation_id": "op-2"},
        exception=_failure("mesh 98 failed at 0xfeedface11"),
        evidence={"code": "preparation_failed", "detail": "the mesh had 98 triangles"},
    )
    assert first == second
    record = store.get(first)
    assert record["count"] == 2
    assert record["firstSeen"] == "2026-10-24T12:00:00+00:00"
    assert record["lastSeen"] == "2026-10-24T12:05:00+00:00"
    assert [item["ids"]["job_id"] for item in record["occurrences"]] == ["job-1", "job-2"]
    assert record["latest"]["ids"] == {"job_id": "job-2", "operation_id": "op-2"}
    assert "RuntimeError" in record["latest"]["exception"]["stack"]
    assert record["versions"]["build"]["commit_short"] == "abc1234"
    # A different failure is a different record.
    other = store.record("internal-failure", "cad-preparation", evidence={"code": "interrupted"})
    assert other != first
    assert {item["id"] for item in store.list()} == {first, other}
    assert store.list(kind="frame-gate") == []
    # Ids by unique prefix.
    assert store.get(first[:-3])["id"] == first


def test_occurrences_are_bounded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for index in range(MAX_OCCURRENCES + 5):
        incident = store.record("frame-gate", "cad-preparation", ids={"job_id": f"job-{index}"},
                                evidence={"reason": "mismatch"})
    record = store.get(incident)
    assert record["count"] == MAX_OCCURRENCES + 5
    assert len(record["occurrences"]) == MAX_OCCURRENCES
    assert record["occurrences"][-1]["ids"]["job_id"] == f"job-{MAX_OCCURRENCES + 4}"


def test_evidence_is_bounded_and_never_copies_geometry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    incident = store.record("domain", "cad-preparation", evidence={
        "message": "x" * (MAX_STRING_CHARS * 3),
        "vertices": [[0.0, 1.0, 2.0]] * 5000,
        "triangles": [[0, 1, 2]] * 5000,
        "axes": list(range(1000)),
        "nested": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}},
        "triangle_count": 5000,
    })
    evidence = store.get(incident)["latest"]["evidence"]
    assert len(evidence["message"]) < MAX_STRING_CHARS + 100
    assert evidence["vertices"] == evidence["triangles"] == "[withheld: geometry]"
    assert len(evidence["axes"]) == MAX_LIST_ITEMS + 1
    assert evidence["triangle_count"] == 5000
    assert "below depth" in json.dumps(evidence["nested"])
    assert (store.root / f"{incident}.json").stat().st_size < 64 * 1024


def test_paths_and_the_account_name_are_rewritten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    data, app, home = tmp_path / "data", tmp_path / "app", tmp_path / "home"
    incident = store.record(
        "internal-failure", "cad-preparation",
        exception=_failure(f"could not read {data}/imports/bundles/x.wgreturn"),
        evidence={
            "detail": f"{app}/server/mesh.py failed for {home}/Documents/Ada-Lovelace/horn.step",
            "owner": "ada-lovelace",
            f"{home}/key": "value",
        },
    )
    text = (store.root / f"{incident}.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in text
    assert "ada-lovelace" not in text.lower()
    record = json.loads(text)
    assert record["latest"]["evidence"]["detail"] == (
        "<wg>/server/mesh.py failed for ~/Documents/<user>/horn.step"
    )
    assert record["latest"]["evidence"]["owner"] == "<user>"
    assert "~/key" in record["latest"]["evidence"]
    assert "<data>/imports/bundles/x.wgreturn" in record["latest"]["exception"]["message"]


def test_pruning_keeps_thirty_days_and_two_hundred_records(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 9, 1, tzinfo=timezone.utc))
    store = _store(tmp_path, now=clock)
    old = store.record("frame-fallback", "cad-preparation", evidence={"reason_code": "old"})
    clock.advance(days=31)
    recent = store.record("frame-fallback", "cad-preparation", evidence={"reason_code": "new"})
    assert store.get(old) is None
    assert store.get(recent) is not None
    for index in range(MAX_RECORDS + 10):
        clock.advance(seconds=1)
        # Letters only: digits are volatile and fingerprint alike.
        code = chr(97 + index // 26) + chr(97 + index % 26)
        store.record("domain", "cad-preparation", evidence={"code": code})
    remaining = store.list()
    assert len(remaining) == MAX_RECORDS
    # The oldest go first; the newest stays.
    assert store.get(recent) is None
    assert remaining[0]["lastSeen"] == clock.moment.isoformat(timespec="seconds")


def test_preparation_diagnostic_records_an_incident_with_the_handled_stack(tmp_path: Path, caplog) -> None:
    from server.cadlink.intake.readings import record_preparation_diagnostic

    store = configure_incidents(tmp_path / "data", app_root=tmp_path / "app")
    with caplog.at_level("WARNING"):
        try:
            raise ValueError("synthetic preparation failure")
        except ValueError as exc:
            incident = record_preparation_diagnostic(
                "internal-failure",
                {"code": "preparation_failed", "job_id": "job-7", "operation_id": "op-7",
                 "detail": "synthetic preparation failure"},
                exception=exc,
            )
    record = store.get(incident)
    assert record["kind"] == "internal-failure" and record["phase"] == "cad-preparation"
    assert record["latest"]["ids"] == {"job_id": "job-7", "operation_id": "op-7"}
    assert "synthetic preparation failure" in record["latest"]["exception"]["stack"]
    # The log line stays, for the replay tool, and names the incident.
    logged = [entry.cad_diagnostic for entry in caplog.records if hasattr(entry, "cad_diagnostic")]
    assert logged[-1]["stage"] == "internal-failure"
    assert logged[-1]["incident"] == incident


def test_job_log_tails_survive_clear_failed(tmp_path: Path) -> None:
    from server.jobs.store import JobStore

    store = configure_incidents(tmp_path / "data", app_root=tmp_path / "app")
    jobs = JobStore.for_data_dir(tmp_path / "data")
    jobs.initialize()
    try:
        now = "2026-10-24T12:00:00Z"
        jobs.create_job({
            "id": "job-failed", "status": "error", "created_at": now, "updated_at": now,
            "queued_at": now, "config_json": {}, "config_summary_json": {},
        })
        log_path = jobs._job_log_path("job-failed")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("early line\n" * 4000 + "the failing stage\n", encoding="utf-8")
        incident = record_incident(
            "internal-failure", "cad-preparation", ids={"job_id": "job-failed"},
            evidence={"code": "preparation_failed"},
        )
        ids, _events = jobs.delete_jobs_by_status_with_events(["error"])
        assert ids == ["job-failed"]
        assert not log_path.exists()
        record = store.get(incident)
        kept = record["jobLogs"]["job-failed"]
        assert kept["tail"].endswith("the failing stage\n")
        assert kept["truncated"] is True
        assert len(kept["tail"].encode("utf-8")) <= MAX_JOB_LOG_TAIL_BYTES
        assert kept["tail"].startswith("early line\n")  # a whole line, never part of one
    finally:
        jobs.close()


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
def test_log_tails_read_the_same_whatever_line_endings_the_log_has(tmp_path: Path, newline: str) -> None:
    """A Windows text-mode log keeps ``\\n`` lines in a record, as the run's log view shows it."""

    log = tmp_path / "job.log"
    log.write_bytes(newline.join(["early line"] * 4000 + ["the failing stage", ""]).encode("utf-8"))
    whole = incidents.read_log_tail(log, limit=10**7)
    assert whole == ("early line\n" * 4000 + "the failing stage\n", False)
    text, truncated = incidents.read_log_tail(log)
    assert truncated is True
    assert "\r" not in text
    assert text.endswith("early line\nthe failing stage\n")
    assert text.startswith("early line\n")  # a whole line, never part of one


def test_recording_never_raises(tmp_path: Path, monkeypatch) -> None:
    configure_incidents(tmp_path / "data")

    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(incidents, "write_json_atomic", broken)
    assert record_incident("domain", "cad-preparation", evidence={}) is None


# -- problem reports --------------------------------------------------------------


def test_report_zip_and_summary_carry_incidents(tmp_path: Path) -> None:
    from server.diagnostics.bundle import build_bundle, build_summary, summary_text
    from server.diagnostics.incidents import bundle_incidents, recent_incidents
    from server.diagnostics.scrub import scrub_rules
    from server.platform.paths import data_paths

    clock = Clock(datetime.now(timezone.utc))
    store = _store(tmp_path, now=clock)
    # Written where no name could be known: its first words never reach the clipboard.
    unknown = store.record("domain", "cad-preparation", evidence={"detail": "Kestrel horn is open"})
    clock.advance(seconds=1)
    incident = store.record(
        "internal-failure", "cad-preparation", ids={"job_id": "job-1"},
        evidence={"code": "preparation_failed",
                  "detail": "the mesh of Tritonia speaker failed (urn:adsk.wipprod:dm.lineage:SECRET, "
                            "design wgd_01JABCDEFGHJKMNPQRSTVWXYZ0)"},
        redaction_names=["Tritonia speaker"],
    )
    rules = scrub_rules(home="/home/nobody", environ={}, system="Linux")
    lines = recent_incidents(tmp_path / "data")
    summary = build_summary(build={"label": "x"}, version="0.3.6", data_dir=tmp_path / "data",
                            capabilities=None, rules=rules, incidents=lines)
    assert [line["id"] for line in summary["recentIncidents"]] == [incident, unknown]
    text = summary_text(summary)
    assert "Recent internal problems" in text and incident in text and "the mesh of <name> failed" in text
    for secret in ("Tritonia", "SECRET", "wgd_", "Kestrel", "redactionNames"):
        assert secret not in text and secret not in json.dumps(summary), secret
    payload = build_bundle(
        paths=data_paths(tmp_path / "data"), summary=summary, system={}, capabilities=None,
        settings_envelope=None, rules=rules, incidents=bundle_incidents(tmp_path / "data"),
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        assert f"incidents/{incident}.json" in names
        assert json.loads(archive.read("incidents.json"))[0]["id"] == incident
        manifest = json.loads(archive.read("manifest.json"))
    assert any(member["name"] == f"incidents/{incident}.json" and member["note"]
               for member in manifest["members"])


def test_bundle_endpoint_includes_incidents(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.diagnostics.api import create_diagnostics_router

    class Registry:
        async def capabilities(self):
            return ()

        def cpu_preparation_in_flight(self) -> bool:
            return False

    store = _store(tmp_path)
    incident = store.record("frame-gate", "cad-preparation", evidence={"reason": "mismatch"})
    application = FastAPI()
    application.include_router(create_diagnostics_router(
        data_dir=tmp_path / "data", version="0.3.6", build={"label": "x"}, engine_registry=Registry(),
    ))
    client = TestClient(application)
    response = client.get("/api/diagnostics/bundle")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert f"incidents/{incident}.json" in archive.namelist()
    assert client.get("/api/diagnostics/summary").json()["summary"]["recentIncidents"][0]["id"] == incident


def test_caught_interface_errors_with_an_operation_become_incidents(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.diagnostics.api import create_diagnostics_router

    store = configure_incidents(tmp_path / "data")
    application = FastAPI()
    router = create_diagnostics_router(
        data_dir=tmp_path / "data", version="0.3.6", build={}, engine_registry=object(),
    )
    application.include_router(router)
    client = TestClient(application)
    assert client.post("/api/diagnostics/client-log", json={
        "message": "Run archive failed: disk full", "stack": "Error: disk full\n at archive",
        "source": "caught", "operation": "run-archive", "context": {"job_id": "job-3"},
    }).json() == {"recorded": True}
    entry = router.client_errors.snapshot()[-1]
    assert entry["operation"] == "run-archive" and entry["context"] == {"job_id": "job-3"}
    [record] = store.list(kind="interface-error")
    assert record["phase"] == "run-archive"
    assert record["latest"]["ids"] == {"job_id": "job-3"}
    # An uncaught error without an operation stays in the session buffer only.
    client.post("/api/diagnostics/client-log", json={"message": "render loop", "source": "render"})
    assert len(store.list()) == 1


def test_a_diagnostic_never_takes_an_unrelated_outer_exception(tmp_path: Path) -> None:
    from server.cadlink.intake.readings import record_preparation_diagnostic

    store = configure_incidents(tmp_path / "data", app_root=tmp_path / "app")
    try:
        raise KeyError("an unrelated lookup further out")
    except KeyError:
        incident = record_preparation_diagnostic("frame-fallback", {"reason_code": "survey"})
    assert store.get(incident)["latest"]["exception"] is None


def test_repeats_in_different_temporary_folders_are_one_incident(tmp_path: Path) -> None:
    import tempfile

    store = _store(tmp_path)
    incidents_seen = set()
    for _ in range(2):
        scratch = Path(tempfile.mkdtemp(prefix="tmp"))
        try:
            incidents_seen.add(store.record(
                "internal-failure", "cad-preparation",
                exception=_failure(f"could not open {scratch}/mesh/part.msh"),
                evidence={"code": "preparation_failed", "detail": f"gmsh wrote nothing in {scratch}/mesh"},
            ))
        finally:
            scratch.rmdir()
    [incident] = incidents_seen
    assert store.get(incident)["count"] == 2
    detail = store.get(incident)["latest"]["evidence"]["detail"]
    # The folder's own separator (``\\`` on Windows), then the random name.
    assert re.fullmatch(r"gmsh wrote nothing in <tmp>[\\/]tmp\w+/mesh", detail), detail


#: Each host's real temporary folder, home and the paths WG writes under them.
#: The store redacts a record when it is recorded and again when it is written,
#: so every case is redacted twice: the rules must be idempotent.
_PLATFORM_TEMP_CASES = {
    "linux": dict(
        system="Linux", temp="/tmp", home="/home/runner", environ={"HOME": "/home/runner"},
        texts={
            "gmsh wrote nothing in /tmp/tmp4m_wb_tw/mesh": "gmsh wrote nothing in <tmp>/tmp4m_wb_tw/mesh",
            "/tmp/tmp/x and /home/runner/tmp/y": "<tmp>/tmp/x and ~/tmp/y",
            "a sibling /tmpdata/x is not the temporary folder": "a sibling /tmpdata/x is not the temporary folder",
            "/home/runnerup/x is not the home folder": "/home/runnerup/x is not the home folder",
        },
    ),
    "macos": dict(
        system="Darwin", temp="/var/folders/ab/cd12_ef/T", home="/Users/runner",
        environ={"HOME": "/Users/runner"},
        texts={
            "gmsh wrote nothing in /var/folders/ab/cd12_ef/T/tmpk3j9x/mesh":
                "gmsh wrote nothing in <tmp>/tmpk3j9x/mesh",
            "/Users/runner/Documents/horn.step": "~/Documents/horn.step",
        },
    ),
    "windows": dict(
        system="Windows", temp=r"C:\Users\runner\AppData\Local\Temp", home=r"C:\Users\runner",
        environ={"USERPROFILE": r"C:\Users\runner", "APPDATA": r"C:\Users\runner\AppData\Roaming"},
        texts={
            r"gmsh wrote nothing in C:\Users\runner\AppData\Local\Temp\tmp25q1jgz4/mesh":
                r"gmsh wrote nothing in <tmp>\tmp25q1jgz4/mesh",
            r"c:/users/RUNNER/appdata/local/temp/tmpab12/mesh": "<tmp>/tmpab12/mesh",
            r'{"path": "C:\\Users\\runner\\AppData\\Local\\Temp\\tmpq9\\x"}': r'{"path": "<tmp>\\tmpq9\\x"}',
            r"C:\Users\runner\Documents\tmp\x": r"~\Documents\tmp\x",
        },
    ),
    "windows-short-temp": dict(
        system="Windows", temp=r"C:\Users\RUNNER~1\AppData\Local\Temp", home=r"C:\Users\runner",
        environ={"USERPROFILE": r"C:\Users\runner"},
        texts={r"C:\Users\RUNNER~1\AppData\Local\Temp\tmpzz1\part.msh": r"<tmp>\tmpzz1\part.msh"},
    ),
}


@pytest.mark.parametrize("platform_name", sorted(_PLATFORM_TEMP_CASES))
def test_every_hosts_temporary_folder_is_redacted_once_and_idempotently(platform_name: str) -> None:
    case = _PLATFORM_TEMP_CASES[platform_name]
    rules = incidents.redaction_rules(
        data_dir=None, app_root=None, home=case["home"], temp_dir=case["temp"],
        environ=case["environ"], system=case["system"],
    )
    redactor = incidents.Redactor(rules, user="runner")
    for text, expected in case["texts"].items():
        once = redactor.text(text)
        assert once == expected, (platform_name, text)
        assert redactor.text(once) == once, (platform_name, text)


@pytest.mark.parametrize("platform_name", sorted(_PLATFORM_TEMP_CASES))
def test_every_hosts_repeats_in_different_temporary_folders_share_a_fingerprint(platform_name: str) -> None:
    case = _PLATFORM_TEMP_CASES[platform_name]
    redactor = incidents.Redactor(incidents.redaction_rules(
        data_dir=None, app_root=None, home=case["home"], temp_dir=case["temp"],
        environ=case["environ"], system=case["system"],
    ), user="runner")
    separator = "\\" if case["system"] == "Windows" else "/"
    digests = {
        incidents.fingerprint(
            "internal-failure", "cad-preparation", exception=None,
            evidence=redactor.value(
                redactor.value({"detail": f"gmsh wrote nothing in {case['temp']}{separator}{scratch}/mesh"})
            ),
        )
        for scratch in ("tmp4m_wb_tw", "tmpq81kz0az")
    }
    assert len(digests) == 1


def test_paths_are_redacted_before_a_field_is_cut(tmp_path: Path) -> None:
    store = _store(tmp_path)
    home = tmp_path / "home"
    detail = "x" * (MAX_STRING_CHARS - 20) + f" {home}/file"
    assert len(detail) > MAX_STRING_CHARS
    incident = store.record("domain", "cad-preparation", evidence={"detail": detail},
                            exception=_failure("y" * (MAX_STRING_CHARS - 20) + f" {home}/stack"))
    record = store.get(incident)
    assert record["latest"]["evidence"]["detail"].endswith(" ~/file")
    assert record["latest"]["exception"]["message"].endswith(" ~/stack")
    assert str(tmp_path)[:16] not in json.dumps(record)


def test_interface_errors_have_their_own_pool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(incidents, "MAX_RECORDS", 5)
    monkeypatch.setattr(incidents, "MAX_INTERFACE_RECORDS", 3)
    clock = Clock(datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
    store = _store(tmp_path, now=clock)
    pipeline = store.record("internal-failure", "cad-preparation", evidence={"code": "preparation_failed"})
    for index in range(10):
        clock.advance(seconds=1)
        store.record("interface-error", "run-archive", evidence={"message": chr(97 + index)})
    assert store.get(pipeline) is not None  # a noisy interface never pushes it out
    assert len(store.list(kind="interface-error")) == 3
    for index in range(10):
        clock.advance(seconds=1)
        store.record("domain", "cad-preparation", evidence={"code": chr(97 + index)})
    assert len([item for item in store.list() if item["kind"] != "interface-error"]) == 5
    assert len(store.list(kind="interface-error")) == 3


def test_recording_reads_only_its_own_record(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    for index in range(20):
        store.record("domain", "cad-preparation", evidence={"code": chr(97 + index)})
    reads = []
    original = IncidentStore._read

    def counting(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(IncidentStore, "_read", staticmethod(counting))
    store.record("frame-gate", "cad-preparation", evidence={"reason": "mismatch"})
    assert len(reads) == 1  # pruning reads file times, never the other records


def test_file_time_is_the_last_occurrence(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 1, 8, tzinfo=timezone.utc))
    store = _store(tmp_path, now=clock)
    incident = store.record("domain", "cad-preparation", evidence={"code": "open"})
    assert (store.root / f"{incident}.json").stat().st_mtime == clock.moment.timestamp()


def test_discarded_result_logs_are_kept_with_their_incidents(tmp_path: Path) -> None:
    from server.jobs.store import JobStore

    store = configure_incidents(tmp_path / "data", app_root=tmp_path / "app")
    jobs = JobStore.for_data_dir(tmp_path / "data")
    jobs.initialize()
    try:
        now = "2026-10-24T12:00:00Z"
        jobs.create_job({
            "id": "job-discarded", "status": "complete", "created_at": now, "updated_at": now,
            "queued_at": now, "completed_at": now, "config_json": {}, "config_summary_json": {},
            "task_metadata": {"results_discarded_at": now},
        })
        log_path = jobs._job_log_path("job-discarded")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("meshing\nthe solve diverged\n", encoding="utf-8")
        incident = record_incident("internal-failure", "solve", ids={"job_id": "job-discarded"},
                                   evidence={"code": "x"})
        jobs._delete_discarded_result_logs()
        assert not log_path.exists()
        assert store.get(incident)["jobLogs"]["job-discarded"]["tail"].endswith("the solve diverged\n")
    finally:
        jobs.close()


def test_report_keeps_log_tails_for_the_newest_and_the_selected_run(tmp_path: Path) -> None:
    from server.diagnostics.incidents import TAILED_INCIDENTS, bundle_incidents

    clock = Clock(datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
    store = _store(tmp_path, now=clock)
    logs = tmp_path / "logs"
    logs.mkdir()
    created = []
    for index in range(TAILED_INCIDENTS + 3):
        clock.advance(seconds=1)
        job_id = f"job-{chr(97 + index)}"
        (logs / f"{job_id}.log").write_text(f"log of {job_id}\n", encoding="utf-8")
        created.append((job_id, store.record("domain", "cad-preparation", ids={"job_id": job_id},
                                             evidence={"code": chr(97 + index)})))
    store.retain_job_logs([job_id for job_id, _ in created], lambda job_id: logs / f"{job_id}.log")
    oldest_job, oldest = created[0]
    records = {record["id"]: record for record in bundle_incidents(tmp_path / "data", job_id=oldest_job)}
    assert records[oldest]["jobLogs"][oldest_job]["tail"] == f"log of {oldest_job}\n"
    assert records[created[1][1]]["jobLogs"] == {} and records[created[1][1]]["jobLogsLeftOut"] == 1
    for job_id, incident in created[-TAILED_INCIDENTS:]:
        assert job_id in records[incident]["jobLogs"]


# -- the names a record redacts itself with -------------------------------------


def _name_sources(tmp_path: Path) -> str:
    """A run, its ingest and WG's retained copy of the return, each naming the model."""

    import sqlite3

    data = tmp_path / "data"
    digest = "c" * 64
    bundle = data / "imports" / "bundles" / f"{digest}.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "wgreturn.json").write_text(json.dumps({
        "document": {"name": "Kestrel horn"},
        "assembly": {"cut_provenance": [{"feature": {"kind": "split-body", "name": "Mirror split"}}]},
        "scope": {"included": [{"name": "Front baffle", "component": "Horn body", "object_id": "tok-1"}]},
        "instances": [{"occurrence_path": "Kestrel horn/Horn body:1", "parameter_prefix": "wg_kestrel_"}],
    }), encoding="utf-8")
    (data / "db").mkdir(parents=True)
    with sqlite3.connect(data / "db" / "simulations.db") as connection:
        connection.execute("CREATE TABLE simulation_jobs (id TEXT PRIMARY KEY, task_metadata_json TEXT)")
        connection.execute("INSERT INTO simulation_jobs VALUES (?, ?)", ("job-1", json.dumps({"cad": {
            "snapshot": {"document_name": "Kestrel horn v2"}, "preparation": {"ingest_id": "ingest-1"}}})))
    with sqlite3.connect(data / "db" / "cadlink.db") as connection:
        connection.execute("CREATE TABLE ingests (ingest_id TEXT PRIMARY KEY, manifest_sha256 TEXT, record_json TEXT)")
        connection.execute("INSERT INTO ingests VALUES (?, ?, ?)", ("ingest-1", f"sha256:{digest}", json.dumps({
            "document": {"name": "Kestrel horn"},
            "sources": [{"selectors": {"shell_names": ["Throat shell"]}, "observed": {"bodies": ["Front baffle"]}}]})))
    return digest


def test_names_lead_from_a_run_to_its_ingest_and_bundle(tmp_path: Path) -> None:
    from server.diagnostics.incidents import IncidentNames

    _name_sources(tmp_path)
    names, sources = IncidentNames(tmp_path / "data").find({"job_id": "job-1"})
    assert sources == ["run", "ingest", "bundle"]
    assert names[0] == "Kestrel horn v2"
    assert {"Kestrel horn", "Throat shell", "Front baffle", "Horn body", "Mirror split", "kestrel", "tok-1"} <= set(names)
    assert IncidentNames(tmp_path / "data")({"job_id": "job-gone"}) is None
    assert IncidentNames(tmp_path / "nothing")({"job_id": "job-1"}) is None
    assert not (tmp_path / "nothing").exists()  # read only


def test_records_keep_the_names_they_redact_with(tmp_path: Path) -> None:
    from server.cadlink.intake.readings import record_preparation_diagnostic
    from server.diagnostics.incidents import IncidentNames

    _name_sources(tmp_path)
    store = configure_incidents(tmp_path / "data", app_root=tmp_path / "app",
                                names=IncidentNames(tmp_path / "data"))
    incident = record_preparation_diagnostic(
        "domain", {"job_id": "job-1", "code": "open_half"}, names=["Caller named"])
    names = store.get(incident)["redactionNames"]
    assert names[0] == "Caller named" and {"Kestrel horn v2", "Mirror split", "Throat shell"} <= set(names)
    # A repeat adds its names to the record's; none is lost, none is doubled.
    again = record_preparation_diagnostic("domain", {"job_id": "job-2", "code": "open_half"}, names=["kestrel HORN", "Other"])
    assert again == incident
    kept = store.get(incident)["redactionNames"]
    assert kept[:len(names)] == names and kept[-1] == "Other" and len(kept) == len(set(map(str.lower, kept)))
    # A caller that knows there are none says so: the record can redact itself.
    interface = record_incident("interface-error", "run-archive", evidence={"message": "x"}, redaction_names=())
    assert store.get(interface)["redactionNames"] == []
    # Nothing known: no field, and a redacted export refuses it.
    unknown = record_incident("preparation-crash", "cad-preparation", evidence={"message": "y"})
    assert "redactionNames" not in store.get(unknown)
    assert "redactionNames" not in incidents.summary(store.get(incident))


def test_redaction_names_are_bounded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    incident = store.record("domain", "cad-preparation", redaction_names=[f"name {index}" for index in range(1000)]
                            + ["x" * 5000, "ab", ""])
    kept = store.get(incident)["redactionNames"]
    assert len(kept) == incidents.MAX_REDACTION_NAMES and all(len(name) >= 3 for name in kept)


def test_a_crash_after_shutdown_is_still_recorded_without_noise(tmp_path: Path, caplog) -> None:
    import asyncio
    from types import SimpleNamespace

    from server.cadlink.api import _track

    store = configure_incidents(tmp_path / "data", app_root=tmp_path / "app")
    refusals = []

    async def scenario() -> None:
        loop = asyncio.get_running_loop()

        def refused(*_args, **_kwargs):
            refusals.append(True)
            raise RuntimeError("cannot schedule new futures after shutdown")

        loop.run_in_executor = refused  # type: ignore[method-assign]

        async def crash() -> None:
            raise ValueError("lane crashed")

        task = asyncio.create_task(crash())
        _track(SimpleNamespace(), task)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    with caplog.at_level("ERROR"):
        asyncio.run(scenario())
    assert refusals == [True]
    [record] = store.list(kind="preparation-crash")
    assert "lane crashed" in record["latest"]["exception"]["message"]
    assert not any("Exception in callback" in entry.getMessage() for entry in caplog.records)
