"""Developer replay uses real inbox preparation and keeps live inputs untouched."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from server.tools import cad_replay


@pytest.fixture
def bundle(tmp_path):
    pytest.importorskip("gmsh")
    pytest.importorskip("meshio")
    import cad_frame_fixtures as fx
    from test_cad_frame_infer_real import _bundle

    path, _manifest = _bundle(tmp_path, "replay", fx.horn_box, "+z")
    return path


def invoke(capsys, *args):
    code = cad_replay.main([*(str(arg) for arg in args), "--json"])
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


def test_real_bundle_prepares_green(bundle, tmp_path, capsys):
    code, report = invoke(capsys, bundle, "--data-dir", tmp_path / "scratch")
    assert code == 0, report
    assert report["outcome"] == "ok"
    stages = report["stages"]
    assert all(stage["seconds"] >= 0 for stage in stages)
    assert any(stage["stage"] == "solver mesh build" for stage in stages)
    inference = next(stage["evidence"] for stage in stages if stage["stage"] == "frame inference")
    assert inference["axis"] == "+z"
    assert inference["status"] == "automatic"
    assert inference["evidence"]["votes"]
    assert any(stage["stage"] == "domain decision" for stage in stages)
    assert any(stage["stage"] == "domain plan" for stage in stages)
    assert report["preparation_id"]
    assert (tmp_path / "scratch" / "db" / "cadlink.db").exists()


def test_refused_bundle_reports_validation_reason(tmp_path, capsys):
    bundle = tmp_path / "invalid.wgreturn"
    bundle.mkdir()
    (bundle / "wgreturn.json").write_text('{"wgreturn_version":"99"}')
    code, report = invoke(capsys, bundle, "--data-dir", tmp_path / "scratch")
    assert code == 1
    assert report["outcome"] == "refused"
    stage = next(stage for stage in report["stages"] if stage["stage"] == "bundle validation")
    assert stage["outcome"] == "refused"
    assert "wgreturn_version" in stage["exception"]["message"]
    assert "Traceback" in stage["exception"]["traceback"]


def test_pipeline_exception_keeps_traceback(bundle, tmp_path, capsys, monkeypatch):
    from server.cadlink.intake import ingest

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic worker failure")

    monkeypatch.setattr(ingest, "build_imported_mesh_isolated", broken)
    code, report = invoke(capsys, bundle, "--data-dir", tmp_path / "scratch")
    assert code == 1
    assert report["refusal"]["code"] == "preparation_failed"
    failed = next(stage for stage in report["stages"] if stage["stage"] == "solver mesh build")
    assert failed["outcome"] == "error"
    assert "synthetic worker failure" in failed["exception"]["traceback"]
    assert "broken" in failed["exception"]["traceback"]
    assert ingest.build_imported_mesh_isolated is broken  # taps restore even after failure


@pytest.mark.parametrize("location", [
    "Library/WG-Development/data", "Library/WG-Development/data/child",
    "Library/Application Support/WaveguideGenerator",
    "Library/Application Support/WaveguideGenerator2", "Library",
])
def test_live_dir_protection(tmp_path, monkeypatch, location):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    destination = tmp_path / location
    with pytest.raises(ValueError, match="overlaps"):
        cad_replay.scratch_directory(destination, source=None, live=None)
    assert not destination.exists()


def test_symlink_to_live_dir_is_refused(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(live, target_is_directory=True)
    with pytest.raises(ValueError, match="overlaps"):
        cad_replay.scratch_directory(alias, source=None, live=live)
    assert list(live.iterdir()) == []


def test_nonempty_destination_is_refused(tmp_path):
    (tmp_path / "sentinel").write_bytes(b"keep")
    with pytest.raises(ValueError, match="new or empty"):
        cad_replay.scratch_directory(tmp_path, source=None, live=None)
    assert (tmp_path / "sentinel").read_bytes() == b"keep"


def test_environment_never_selects_live_destination(tmp_path, monkeypatch, capsys):
    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setenv("WG2_DATA_DIR", str(live))
    code, report = invoke(capsys, tmp_path / "missing.wgreturn")
    assert code == 1
    assert Path(report["data_dir"]) != live
    assert list(live.iterdir()) == []
    assert os.environ["WG2_DATA_DIR"] == str(live)


def _live_request(bundle, tmp_path):
    from server.cadlink.intake.ingest import retain_snapshot
    from server.cadlink.operations import PREPARE_AND_SOLVE, prepare_and_solve_request, request_digest
    from server.cadlink.store import CadLinkStore

    live = tmp_path / "live"
    retained = retain_snapshot(bundle, live)
    store = CadLinkStore.for_data_dir(live)
    target, inputs = prepare_and_solve_request(
        return_id="wgr_1", bundle_path="wgreturn/replay.wgreturn",
        manifest_sha256=retained["manifest_sha256"],
    )
    store.accept_operation("request-1", PREPARE_AND_SOLVE,
                           request_digest(PREPARE_AND_SOLVE, target, inputs), target, inputs)
    store.close()
    return live


def test_from_request_replays_read_only(bundle, tmp_path, capsys):
    live = _live_request(bundle, tmp_path)
    before = {path.relative_to(live): path.read_bytes() for path in live.rglob("*") if path.is_file()}
    code, report = invoke(capsys, "--from-request", "request-1", "--live-data-dir", live,
                          "--data-dir", tmp_path / "scratch")
    assert code == 0, report
    after = {path.relative_to(live): path.read_bytes() for path in live.rglob("*") if path.is_file()}
    assert before == after
    assert report["request_id"] == "request-1"


def test_missing_request_does_not_create_live_database(tmp_path):
    live = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        cad_replay.request_bundle("request-1", live)
    assert not live.exists()


@pytest.mark.parametrize("request_id", ["../bad", "x/y", "", "x' OR 1=1"])
def test_request_id_is_not_a_path(tmp_path, request_id):
    with pytest.raises(ValueError, match="invalid request"):
        cad_replay.request_bundle(request_id, tmp_path)


def test_human_report_includes_evidence_and_traceback(capsys):
    cad_replay.render({"outcome": "refused", "stages": [
        {"stage": "frame inference", "depth": 0, "seconds": 0.1, "outcome": "error",
         "evidence": {"votes": {"+z": 1}}, "exception": {"traceback": "Traceback: test"}},
    ]}, __import__("sys").stdout)
    output = capsys.readouterr().out
    assert "frame inference: error" in output and '"votes"' in output and "Traceback: test" in output


def test_configured_live_dir_child_is_refused(tmp_path, monkeypatch):
    live = tmp_path / "configured-live"
    monkeypatch.setenv("WG2_DATA_DIR", str(live))
    with pytest.raises(ValueError, match="overlaps"):
        cad_replay.scratch_directory(live / "child", source=None, live=None)
    assert not live.exists()


def test_request_copy_includes_wal_without_touching_live_files(bundle, tmp_path):
    import sqlite3
    from server.cadlink.intake.ingest import retained_snapshot_path

    live = _live_request(bundle, tmp_path)
    # Keep a writer open so the new row exists only in the WAL.
    database = live / "db" / "cadlink.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("UPDATE cad_operations SET operation_id = 'wal-request'")
    connection.commit()
    try:
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in database.parent.iterdir() if path.is_file()}
        result, digest = cad_replay.request_bundle("wal-request", live)
        assert result.is_dir()
        assert result == retained_snapshot_path(live, digest)
        after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                 for path in database.parent.iterdir() if path.is_file()}
        assert after == before
    finally:
        connection.close()


@pytest.mark.parametrize("status", ["complete", "error"])
def test_optional_solve_waits_and_preserves_failure(tmp_path, monkeypatch, status):
    import asyncio
    from server.jobs import runtime

    class Runtime:
        def __init__(self, *args, **kwargs):
            pass

        async def submit(self, request):
            assert request == "prepared-request"
            return "solve-job"

        async def get_job(self, job_id):
            return {"status": status, "error_message": "engine failed" if status == "error" else None}

        async def shutdown(self):
            pass

    monkeypatch.setattr(runtime, "JobRuntime", Runtime)
    monkeypatch.setattr("server.jobs.store.JobStore.get_job_log", lambda *args: "engine diagnostic")
    report = cad_replay.Report()
    if status == "error":
        with pytest.raises(RuntimeError, match="engine failed"):
            asyncio.run(cad_replay._solve("prepared-request", tmp_path, None, report))
    else:
        asyncio.run(cad_replay._solve("prepared-request", tmp_path, None, report))
    stage = report.payload["stages"][0]
    assert stage["evidence"]["status"] == status
    assert stage["evidence"]["log"] == "engine diagnostic"
    assert stage["outcome"] == ("ok" if status == "complete" else "error")


def test_from_request_refuses_a_replaced_valid_manifest(bundle, tmp_path, capsys):
    live = _live_request(bundle, tmp_path)
    retained, _digest = cad_replay.request_bundle("request-1", live)
    manifest_path = retained / "wgreturn.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["document"]["name"] = "Changed snapshot"
    manifest_path.write_text(json.dumps(manifest))
    code, report = invoke(capsys, "--from-request", "request-1", "--live-data-dir", live,
                          "--data-dir", tmp_path / "scratch")
    assert code == 1
    assert report["outcome"] == "refused"
    assert "digest" in report["exception"]["message"]
    assert not any(stage["stage"] == "solver mesh build" for stage in report["stages"])


def test_valid_bundle_with_unresolvable_source_reports_internal_stage(bundle, tmp_path, capsys):
    manifest_path = bundle / "wgreturn.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sources"][0]["selectors"]["advanced_face_indices"] = [999999]
    manifest_path.write_text(json.dumps(manifest))
    code, report = invoke(capsys, bundle, "--data-dir", tmp_path / "scratch")
    assert code == 1
    assert report["outcome"] == "refused"
    assert report["refusal"]["code"] == "preparation_failed"
    assert "stage 5" in report["refusal"]["message"]
    ingest = next(stage for stage in report["stages"] if stage["stage"] == "ingest")
    assert ingest["exception"]["stage"] == "stage 5 role resolution"
    assert ingest["exception"]["traceback"]


def test_automatic_axis_retry_uses_real_preparation(tmp_path, capsys):
    pytest.importorskip("gmsh")
    pytest.importorskip("meshio")
    import cad_frame_fixtures as fx
    from test_cad_frame_infer_real import _bundle

    bundle, _manifest = _bundle(tmp_path, "rotated", fx.horn_box, "-x")
    code, report = invoke(capsys, bundle, "--data-dir", tmp_path / "scratch", "--engine", "dryrun")
    assert code == 0, report
    ingestions = [stage for stage in report["stages"] if stage["stage"] == "ingest"]
    assert len(ingestions) == 2
    assert ingestions[-1]["evidence"]["normalisation"]["solver_frame"]["axis"] == "-x"
    preparation = next(stage for stage in report["stages"] if stage["stage"] == "preparation")
    assert preparation["evidence"]["request"]["options"]["engine"] == "dryrun"


def test_default_temp_root_cannot_be_inside_live_data(tmp_path, monkeypatch):
    import tempfile

    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setenv("WG2_DATA_DIR", str(live))
    monkeypatch.setattr(tempfile, "tempdir", str(live))
    with pytest.raises(ValueError, match="overlaps"):
        cad_replay.scratch_directory(None, source=None, live=None)
    assert list(live.iterdir()) == []


def test_handled_exception_log_retains_original_traceback():
    import logging

    report = cad_replay.Report()
    with report.capture_logs():
        try:
            raise RuntimeError("engine detail")
        except RuntimeError:
            logging.getLogger("server.jobs.runtime").exception("solve failed")
    message = report.payload["logs"][0]
    assert message["message"] == "solve failed"
    assert "RuntimeError: engine detail" in message["traceback"]
