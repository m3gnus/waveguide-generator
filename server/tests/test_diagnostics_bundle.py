"""What a problem report contains, and -- more to the point -- what it does not."""

from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

import pytest

from server.diagnostics.bundle import (
    MAX_JOB_LOG_BYTES,
    SETTINGS_ALLOWLIST,
    build_bundle,
    build_summary,
    bundle_filename,
    collect_settings,
    collect_system,
    read_log_text,
    summary_text,
)
from server.diagnostics.scrub import scrub_rules
from server.platform.paths import data_paths


BUILD = {
    "version": "0.3.0",
    "label": "0.3.0+gabcd1234",
    "commit": "abcd1234" * 5,
    "commit_short": "abcd1234",
    "dirty": False,
    "source": "git",
}

CAPABILITIES = {
    "engines": [
        {"name": "bempp", "available": True, "reason": "", "version": "0.1.0"},
        {"name": "metal", "available": False, "reason": "Requires macOS.", "version": None},
    ],
    "dependencies": {"pinned": {"hornlab-sim": "aaa"}, "installed": {"hornlab-sim": "bbb"}, "drift": ["hornlab-sim"]},
    "storage": {"jobs": "wal"},
}


@pytest.fixture
def paths(tmp_path: Path):
    layout = data_paths(tmp_path)
    layout.logs.mkdir(parents=True, exist_ok=True)
    return layout


def rules():
    return scrub_rules(home="/home/ada", environ={"HOME": "/home/ada"}, system="Linux")


def summary_for(**overrides):
    arguments = {
        "build": BUILD,
        "version": "0.3.0",
        "data_dir": "/home/ada/.local/share/WaveguideGenerator",
        "capabilities": CAPABILITIES,
        "rules": rules(),
    }
    arguments.update(overrides)
    return build_summary(**arguments)


def members_of(payload: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def build(paths, **overrides) -> dict[str, bytes]:
    arguments = {
        "paths": paths,
        "summary": summary_for(),
        "system": collect_system(environ={"WG2_SOLVER_WARMUP": "0", "SECRET_TOKEN": "hunter2"}),
        "capabilities": CAPABILITIES,
        "settings_envelope": None,
        "rules": rules(),
    }
    arguments.update(overrides)
    return members_of(build_bundle(**arguments))


def test_a_report_is_produced_with_nothing_on_disk(paths) -> None:
    """The reporter's application is already broken; the report still builds."""

    members = build(paths)
    assert "manifest.json" in members
    assert "summary.json" in members
    assert not [name for name in members if name.startswith("logs/")]


def test_both_log_generations_are_included_and_a_missing_one_is_not_an_error(paths) -> None:
    # Bytes, not ``write_text``: on Windows that would translate the newline,
    # and these assertions are about what the bundle carried.
    (paths.logs / "server.log").write_bytes(b"current\n")
    assert "logs/server.log.1" not in build(paths)

    (paths.logs / "server.log.1").write_bytes(b"previous\n")
    members = build(paths)
    assert members["logs/server.log"] == b"current\n"
    assert members["logs/server.log.1"] == b"previous\n"


def test_log_text_is_scrubbed(paths) -> None:
    (paths.logs / "server.log").write_bytes(b"wg.workspace: adopted /home/ada/Documents/Horns\n")
    text = build(paths)["logs/server.log"].decode("utf-8")
    assert "ada" not in text
    assert text.endswith("adopted ~/Documents/Horns\n")


def test_the_design_stays_out_unless_it_is_asked_for(paths) -> None:
    """The single most important property of this bundle."""

    envelope = {
        "schemaVersion": 1,
        "namespaces": {
            "designDraft": json.dumps({"name": "Client X 1400"}),
            "driverLibrary": json.dumps([{"brand": "Acme"}]),
            "preferences": json.dumps({"chartCount": 2}),
        },
    }
    members = build(paths, settings_envelope=envelope)
    settings = json.loads(members["settings.json"])
    assert "Client X 1400" not in members["settings.json"].decode("utf-8")
    assert "Acme" not in members["settings.json"].decode("utf-8")
    assert settings["included"] == {"preferences": {"chartCount": 2}}
    assert set(settings["withheld"]) == {"designDraft", "driverLibrary"}
    assert settings["withheld"]["driverLibrary"]["bytes"] > 0
    assert not [name for name in members if name.startswith("design/")]


def test_the_design_is_included_when_the_box_is_ticked(paths) -> None:
    members = build(
        paths,
        design_draft=json.dumps({"name": "Client X 1400"}),
        include_design=True,
        job_id="run-7",
        job_log="log\n",
        job_request={"design": {"throat": 25.4}},
    )
    assert json.loads(members["design/design-draft.json"]) == {"name": "Client X 1400"}
    assert json.loads(members["design/job-run-7.request.json"]) == {"design": {"throat": 25.4}}
    assert json.loads(members["manifest.json"])["includesDesign"] is True


def test_an_unrecognised_settings_namespace_is_withheld(paths) -> None:
    """A namespace added later must not ship itself into every report."""

    envelope = {"schemaVersion": 1, "namespaces": {"somethingNew": json.dumps({"secret": 1})}}
    settings = json.loads(build(paths, settings_envelope=envelope)["settings.json"])
    assert settings["included"] == {}
    assert "somethingNew" in settings["withheld"]
    assert "somethingNew" not in SETTINGS_ALLOWLIST


def test_only_wg2_environment_variables_are_reported(paths) -> None:
    system = json.loads(build(paths)["system.json"])
    assert system["wg2_environment"] == {"WG2_SOLVER_WARMUP": "0"}
    assert "hunter2" not in build(paths)["system.json"].decode("utf-8")


def test_the_job_log_is_tail_capped(paths) -> None:
    """A long sweep's log is unbounded, and the failure is at the end of it."""

    oversized = ("noise\n" * 400_000) + "FINAL LINE\n"
    assert len(oversized) > MAX_JOB_LOG_BYTES
    members = build(paths, job_id="run-1", job_log=oversized[-MAX_JOB_LOG_BYTES:])
    assert members["logs/job-run-1.log"].endswith(b"FINAL LINE\n")
    assert len(members["logs/job-run-1.log"]) <= MAX_JOB_LOG_BYTES


def test_the_manifest_lists_every_member(paths) -> None:
    (paths.logs / "server.log").write_text("x\n", encoding="utf-8")
    members = build(paths, job_id="run-2", job_log="y\n")
    manifest = json.loads(members["manifest.json"])
    listed = {entry["name"] for entry in manifest["members"]}
    # The manifest names itself last, so it is the one member it cannot size.
    assert listed == set(members) - {"manifest.json"}
    assert all(entry["note"] for entry in manifest["members"])
    assert manifest["scrubbed"]


def test_read_log_text_survives_a_truncated_utf8_sequence(tmp_path: Path) -> None:
    path = tmp_path / "server.log"
    path.write_bytes("solver ran\n".encode("utf-8") + b"\xc3")
    assert read_log_text(path, limit=1024).startswith("solver ran\n")


def test_read_log_text_returns_none_for_a_file_that_is_not_there(tmp_path: Path) -> None:
    assert read_log_text(tmp_path / "absent.log", limit=1024) is None


def test_summary_text_leads_with_the_build_label() -> None:
    text = summary_text(summary_for())
    assert text.splitlines()[0] == "Waveguide Generator 0.3.0 (0.3.0+gabcd1234)"
    assert "Solvers available: bempp" in text
    assert "Module drift: hornlab-sim" in text


def test_summary_text_reports_a_probe_that_did_not_finish() -> None:
    text = summary_text(summary_for(capabilities=None))
    assert "Solvers: The solver probe did not complete." in text


def test_job_errors_are_scrubbed_into_the_summary() -> None:
    summary = summary_for(
        jobs=[
            {
                "id": "run-3",
                "run_number": 3,
                "status": "failed",
                "created_at": "2026-08-31T00:00:00Z",
                "error_message": "mesh write failed: /home/ada/runs/3",
                "config_summary_json": {"engine": "bempp"},
            }
        ]
    )
    assert summary["recentJobs"][0]["error"] == "mesh write failed: ~/runs/3"
    assert "~/runs/3" in summary_text(summary)


def test_the_filename_names_the_build() -> None:
    assert bundle_filename(summary_for()).startswith("wg-report-abcd1234-")
    assert bundle_filename(summary_for()).endswith(".zip")


def test_collect_settings_tolerates_a_missing_envelope() -> None:
    assert collect_settings(None)["included"] == {}


def test_the_log_is_flushed_before_it_is_read(paths, monkeypatch) -> None:
    """The lines about what just failed are often still in the handler."""

    import logging

    from server.diagnostics import bundle as bundle_module

    class Sink(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.flushed = 0

        def flush(self) -> None:
            self.flushed += 1

    sink = Sink()
    monkeypatch.setattr("server.platform.logging_setup.log_sinks", lambda: (sink,))
    build(paths)
    assert sink.flushed == 1
    assert bundle_module.flush_log_sinks is not None


def test_a_broken_logging_stack_does_not_stop_the_report(paths, monkeypatch) -> None:
    def explode():
        raise RuntimeError("logging is not configured")

    monkeypatch.setattr("server.platform.logging_setup.log_sinks", explode)
    assert "manifest.json" in build(paths)
