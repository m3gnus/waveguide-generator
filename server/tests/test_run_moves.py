"""Synthetic owner-shaped trees, fault injection and byte-preserving relocation."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.cadlink.store import CadLinkStore
from server.jobs.store import JobStore
from server.workspace import archive_identity as identity
from server.workspace import run_moves as moves_module
from server.workspace.run_moves import MoveRefused, RunMoves, archive_write

KEY = "design:wgu_test"


@pytest.fixture(autouse=True)
def isolated_move_locks():
    yield
    moves_module._BLOCKED.clear()
    moves_module._WRITERS.clear()


@pytest.fixture(params=["native", "windows"])
def text_newlines(request, monkeypatch):
    """Optionally give text-mode writes Windows newline translation (\\n -> \\r\\n).

    WG journals each design.json by the checksum of the bytes it writes; on
    Windows a text-mode writer used to store different bytes than it hashed.
    os.fdopen and Path.open both open files through io.open.
    """
    if request.param == "windows":
        real = io.open

        def windows_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, *args, **kwargs):
            if "b" not in mode and any(flag in mode for flag in "wax+") and newline is None:
                newline = "\r\n"
            return real(file, mode, buffering, encoding, errors, newline, *args, **kwargs)

        monkeypatch.setattr(io, "open", windows_open)
    return request.param


def setup(tmp_path, key=KEY, name="250728solana"):
    root = tmp_path / "WG" / "runs"
    root.mkdir(parents=True)
    cad = CadLinkStore(tmp_path / "cadlink.db")
    cad.archive_root = lambda: root
    cad.initialize()
    identity.claim_project_folder(cad, key, name)
    jobs = JobStore(tmp_path / "jobs.db")
    jobs.initialize()
    manager = RunMoves(tmp_path / "data", jobs, cad, lambda: root)
    return manager, root, jobs, cad


def run(manager, root, number=1, *, name="250728solana", key=None, folder=None, status="complete", archived=True):
    label = f"{name}{number}"
    source = root / (folder or label) / f"{number}_{label}"
    source.mkdir(parents=True)
    row = {"id": f"job{number}", "label": label, "status": status, "created_at": "then", "updated_at": "then", "queued_at": "then",
           "config_json": {"design_name": name}, "config_summary_json": {},
           "task_metadata": {"archived_path": str(source), "exported_files": [str(source / "exports" / "one.csv"), f"{source.parent.name}/{source.name}/exports/one.csv", "/elsewhere/one.csv"]}}
    if archived:
        row["task_metadata"]["archived_at"] = "done"
    if key:
        row["task_metadata"]["archive"] = {"key": key, "folder": source.parent.name, "run": source.name}
    manager.jobs.create_job(row)
    (source / "run.json").write_text(json.dumps({"schemaVersion": 1, "run": {"jobId": row["id"]}, "design": {"name": name}, "cad": {"modelFile": "legacy.f3d"}}))
    (source / "result.bin").write_bytes(b"results\x00unchanged")
    (source / "empty").mkdir()
    return row["id"], source


def move_plan(manager, name="250728solana", key=KEY, **kwargs):
    initial = manager.preview(key, name, **kwargs)
    return manager.preview(key, name, selected=[c["job_id"] for c in initial["candidates"]], **kwargs)


def test_name_only_owner_shape_is_unticked_and_ten_folders_move_byte_exact(tmp_path, text_newlines):
    manager, root, jobs, cad = setup(tmp_path)
    originals = [run(manager, root, n) for n in range(1, 11)]
    before = {job: (path / "run.json").read_bytes() for job, path in originals}
    preview = manager.preview(KEY, "250728solana")
    assert len(preview["candidates"]) == 10
    assert not any(c["preselected"] for c in preview["candidates"])
    assert preview["operations"] == []
    with pytest.raises(MoveRefused, match="Confirm"):
        manager.execute(preview["id"])
    plan = move_plan(manager)
    assert plan["summary"]["runs"] == 10
    result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "done", result["error"]
    assert all(not path.exists() and not path.parent.exists() for _, path in originals)
    for job, source in originals:
        row = jobs.get_job_row(job)
        path = Path(row["task_metadata"]["archived_path"])
        assert path == root / "250728solana" / source.name
        assert (path / "run.json").read_bytes() == before[job]
        assert (path / "empty").is_dir()
        assert row["task_metadata"]["archive"]["key"] == KEY
        assert row["task_metadata"]["exported_files"] == [str(path / "exports" / "one.csv"), f"250728solana/{path.name}/exports/one.csv", "/elsewhere/one.csv"]
    assert identity.registered_folder(cad, KEY) == "250728solana"
    assert manager.undo(result["id"])["phase"] == "undone"
    for job, path in originals:
        assert (path / "run.json").read_bytes() == before[job]
        assert jobs.get_job_row(job)["task_metadata"]["archived_path"] == str(path)


def test_untitled_identity_is_never_preselected(tmp_path):
    manager, root, _, _ = setup(tmp_path, name="Untitled")
    for n in range(1, 11):
        run(manager, root, n, name="Untitled", key=KEY)
    plan = manager.preview(KEY, "Untitled")
    assert len(plan["candidates"]) == 10
    assert not any(c["preselected"] for c in plan["candidates"])


def test_identity_match_preselected_but_foreign_or_unknown_folders_are_never_candidates(tmp_path):
    manager, root, _, _ = setup(tmp_path)
    run(manager, root, key=KEY)
    run(manager, root, 2, key="design:foreign")
    unknown = root / "250728solana3" / "3_250728solana3"
    unknown.mkdir(parents=True)
    (unknown / "run.json").write_text('{"schemaVersion":1,"run":{"jobId":"unknown"}}')
    preview = manager.preview(KEY, "250728solana")
    assert [(c["job_id"], c["preselected"]) for c in preview["candidates"]] == [("job1", True)]


_STAT_SEQUENCE = {"st_mode", "st_ino", "st_dev", "st_nlink", "st_uid", "st_gid", "st_size"}


def _with_device(result: os.stat_result, offset: int) -> os.stat_result:
    """Shift st_dev, keeping every platform-only field (st_flags, st_file_attributes...).

    os.stat_result(sequence) alone sets those to None. Path.lstat is
    stat(follow_symlinks=False), so this fake also answers WG's reparse-point
    check, which on Windows reads st_file_attributes from that lstat.
    """
    fields = list(result)
    fields[2] += offset
    return os.stat_result(fields, {name: getattr(result, name) for name in dir(result)
                                   if name.startswith("st_") and name not in _STAT_SEQUENCE})


def cross_device(monkeypatch, sources):
    original = Path.stat
    source_set = set(sources)
    def fake(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if not source_set.isdisjoint({path, *path.parents}):
            return _with_device(result, 10)
        return result
    monkeypatch.setattr(Path, "stat", fake)


def test_cross_drive_verifies_whole_move_before_any_original_removal(tmp_path, monkeypatch):
    manager, root, jobs, _ = setup(tmp_path)
    old = tmp_path / "older-Runs"
    old.mkdir()
    originals = [run(manager, old, n, key=KEY)[1] for n in (1, 2)]
    result_file = originals[0] / "result.bin"
    result_file.chmod(0o640)
    os.utime(result_file, (946684800, 946684800))
    original_stat = result_file.stat()
    cross_device(monkeypatch, originals)
    plan = move_plan(manager)
    assert all(op["method"] == "copy_verify_remove" for op in plan["operations"])
    observed = []
    def step(phase):
        if phase == "verified":
            observed.append(all(p.exists() for p in originals))
            assert all(Path(op["dst"]).exists() for op in plan["operations"])
    manager.after_step = step
    result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "done", result["error"]
    assert observed == [True]
    assert all(not p.exists() for p in originals)
    assert len(list(root.rglob("result.bin"))) == 2
    copied_stat = (root / "250728solana" / originals[0].name / "result.bin").stat()
    assert copied_stat.st_mode == original_stat.st_mode
    assert copied_stat.st_mtime_ns == original_stat.st_mtime_ns
    with pytest.raises(MoveRefused, match="this drive"):
        manager.undo(result["id"])
    assert Path(jobs.get_job_row("job1")["task_metadata"]["archived_path"]).is_dir()


def test_failed_cross_drive_verification_keeps_every_original(tmp_path, monkeypatch):
    manager, root, _, _ = setup(tmp_path)
    originals = [run(manager, root, n, key=KEY)[1] for n in (1, 2)]
    cross_device(monkeypatch, originals)
    plan = move_plan(manager)
    def step(phase):
        if phase.startswith("copied:"):
            partial = Path(phase.removeprefix("copied:") + ".partial-" + plan["id"])
            (partial / "result.bin").write_bytes(b"BAD")
    manager.after_step = step
    result = manager.execute(plan["id"], confirmed=True)
    assert "Verification failed" in result["error"]
    assert all((p / "result.bin").read_bytes() == b"results\x00unchanged" for p in originals)


class Crash(BaseException):
    pass


@pytest.mark.parametrize("cross", [False, True])
def test_crash_after_every_durable_and_physical_step_recovers(tmp_path, monkeypatch, cross):
    # First record all steps from a representative move. Then kill each independent
    # fixture once at every step (including the gap between rename and journal write).
    def fixture(directory):
        manager, root, jobs, cad = setup(directory)
        _, source = run(manager, root, key=KEY)
        return manager, root, source, jobs, cad
    probe, _, source, _, _ = fixture(tmp_path / "probe")
    if cross:
        cross_device(monkeypatch, [tmp_path / "probe" / "WG" / "runs" / "250728solana1", *[tmp_path / str(i) / "WG" / "runs" / "250728solana1" for i in range(60)]])
    plan = move_plan(probe)
    phases = []
    probe.after_step = phases.append
    assert probe.execute(plan["id"], confirmed=True)["phase"] == "done"
    for index in range(len(phases)):
        moves_module._BLOCKED.clear()
        manager, root, source, jobs, _ = fixture(tmp_path / str(index))
        record = move_plan(manager)
        counter = 0
        def crash(phase):
            nonlocal counter
            counter += 1
            if counter == index + 1:
                raise Crash(phase)
        manager.after_step = crash
        with pytest.raises(Crash):
            manager.execute(record["id"], confirmed=True)
        manager.after_step = lambda _: None
        reports = manager.recover()
        final = manager.read(record["id"])
        assert final["phase"] == "done", (index, phases[index], reports)
        assert not source.exists()
        assert (Path(jobs.get_job_row("job1")["task_metadata"]["archived_path"]) / "result.bin").is_file()
        assert manager.recover() == []


def test_windows_open_file_failure_is_reported_and_retryable(tmp_path, monkeypatch):
    manager, root, jobs, _ = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    plan = move_plan(manager)
    rename = moves_module.rename_absent
    def locked(src, dst):
        if Path(src) == source:
            raise PermissionError(32, "File is open in another program")
        return rename(src, dst)
    monkeypatch.setattr(moves_module, "rename_absent", locked)
    result = manager.execute(plan["id"], confirmed=True)
    assert "File is open" in result["error"]
    assert source.exists() and jobs.get_job_row("job1")["task_metadata"]["archived_path"] == str(source)
    monkeypatch.setattr(moves_module, "rename_absent", rename)
    assert manager.recover()[0]["phase"] == "done"


@pytest.mark.parametrize("state,archived", [("running", True), ("queued", True), ("complete", False)])
def test_refuses_running_and_not_yet_archived(tmp_path, state, archived):
    manager, root, _, _ = setup(tmp_path)
    run(manager, root, key=KEY, status=state, archived=archived)
    with pytest.raises(MoveRefused, match="Wait"):
        move_plan(manager)


def test_move_and_export_are_mutually_exclusive(tmp_path):
    manager, root, _, _ = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    plan = move_plan(manager)
    with archive_write(source):
        with pytest.raises(MoveRefused, match="exporting"):
            manager.execute(plan["id"], confirmed=True)
    def inspect(phase):
        if phase == "prepared":
            with pytest.raises(MoveRefused, match="Runs are being moved"):
                with archive_write(source):
                    pass
    manager.after_step = inspect
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"


def test_dataless_refused_before_reading(tmp_path, monkeypatch):
    manager, root, _, _ = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    target = source / "result.bin"
    original = Path.lstat
    def dataless(path):
        value = original(path)
        if path == target:
            return SimpleNamespace(st_flags=0x40000000, st_mode=value.st_mode, st_file_attributes=0)
        return value
    monkeypatch.setattr(Path, "lstat", dataless)
    with pytest.raises(MoveRefused, match="Download them in Finder first"):
        move_plan(manager)
    assert target.exists()


def test_links_and_changed_preview_refused(tmp_path):
    manager, root, _, _ = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    (source / "link").symlink_to(source / "result.bin")
    with pytest.raises(MoveRefused, match="links"):
        move_plan(manager)
    (source / "link").unlink()
    plan = move_plan(manager)
    (source / "result.bin").write_bytes(b"new")
    result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "refused"
    assert source.exists()


def test_explicit_rename_updates_registry_aliases_mirrors_and_can_undo(tmp_path, text_newlines):
    manager, root, jobs, cad = setup(tmp_path, key="lineage:wgl_test", name="Original")
    key = "lineage:wgl_test"
    _, source = run(manager, root, key=key, name="Original", folder="Original")
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    cad.record_lineage_cad_names("wgl_test", archive_stem="Original")
    plan = manager.preview(key, "New name", rename=True)
    assert plan["target"] == str(root / "New_name")
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    assert not source.exists()
    assert identity.registered_folder(cad, key) == "New_name"
    assert cad.get_lineage_cad_names("wgl_test")["archive_stem"] == "New_name"
    assert json.loads((root / "New_name" / "design.json").read_text())["folder"] == "New_name"
    assert manager.undo(plan["id"])["phase"] == "undone"
    assert source.exists()
    assert identity.registered_folder(cad, key) == "Original"


def test_gather_fusion_cad_import_deduplicates_by_checksum_and_undo_restores(tmp_path):
    key = "lineage:wgl_test"
    manager, root, jobs, _ = setup(tmp_path, key=key, name="Untitled")
    (root / "Untitled" / "cad").mkdir(parents=True)
    sources = [run(manager, root, n, name="cad-import", key=key, folder="cad-import")[1] for n in (40, 44, 54)]
    manager.verify_document = lambda lineage, digest, document: lineage == "wgl_test" and digest == "sha256:test" and document.read_bytes() == b"fusion"
    for source in sources:
        (source / "version.f3d").write_bytes(b"fusion")
        (source / "version.cad.json").write_text(json.dumps({"returnStateHash": "sha256:test", "ingestId": source.name}))
    initial = manager.preview(key, "Untitled")
    assert not any(c["preselected"] for c in initial["candidates"])
    plan = move_plan(manager, name="Untitled", key=key)
    assert plan["summary"]["fusion_files"] == 3
    assert plan["summary"]["duplicates"] == 2
    before = [(source / "run.json").read_bytes() for source in sources]
    result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "done", result["error"]
    assert len(list(root.rglob("*.f3d"))) == 1
    assert len(list((root / "Untitled" / "cad").glob("*.cad.json"))) == 1
    assert [Path(jobs.get_job_row(f"job{n}")["task_metadata"]["archived_path"]).joinpath("run.json").read_bytes() for n in (40,44,54)] == before
    assert manager.undo(plan["id"])["phase"] == "undone"
    assert all((source / "version.f3d").read_bytes() == b"fusion" for source in sources)


def test_undo_refuses_changed_files_or_new_archive(tmp_path):
    manager, root, _, _ = setup(tmp_path)
    run(manager, root, key=KEY)
    plan = move_plan(manager)
    result = manager.execute(plan["id"], confirmed=True)
    target = Path(result["operations"][0]["dst"])
    (target / "result.bin").write_bytes(b"changed")
    with pytest.raises(MoveRefused, match="Verification"):
        manager.undo(plan["id"])


def test_recovery_reverifies_destination_after_verified_crash(tmp_path, monkeypatch):
    manager, root, _, _ = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    cross_device(monkeypatch, [source])
    plan = move_plan(manager)
    def crash(phase):
        if phase == "verified":
            raise Crash()
    manager.after_step = crash
    with pytest.raises(Crash):
        manager.execute(plan["id"], confirmed=True)
    (Path(plan["operations"][0]["dst"]) / "result.bin").write_bytes(b"corrupt")
    manager.after_step = lambda _: None
    report = manager.recover()[0]
    assert "Verification failed" in report["error"]
    assert (source / "result.bin").read_bytes() == b"results\x00unchanged"


def test_recovery_after_partial_original_removal(tmp_path, monkeypatch):
    manager, root, _, _ = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    cross_device(monkeypatch, [source])
    plan = move_plan(manager)
    def crash(phase):
        if phase.startswith("source_file_removed:"):
            raise Crash()
    manager.after_step = crash
    with pytest.raises(Crash):
        manager.execute(plan["id"], confirmed=True)
    assert source.exists()
    assert len(list(source.glob("*.*"))) == 1
    manager.after_step = lambda _: None
    assert manager.recover()[0]["phase"] == "done"
    assert not source.exists()


def test_new_file_during_source_removal_is_preserved(tmp_path, monkeypatch):
    manager, root, _, _ = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    cross_device(monkeypatch, [source])
    plan = move_plan(manager)
    def new_file(phase):
        if phase.startswith("source_file_removed:"):
            (source / "user-file.txt").write_text("keep me")
    manager.after_step = new_file
    result = manager.execute(plan["id"], confirmed=True)
    assert result["error"]
    assert (source / "user-file.txt").read_text() == "keep me"


def test_undo_refuses_linked_original_parent(tmp_path):
    manager, root, _, _ = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    plan = move_plan(manager)
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    external = tmp_path / "unowned"
    external.mkdir()
    source.parent.symlink_to(external, target_is_directory=True)
    with pytest.raises(MoveRefused, match="links"):
        manager.undo(plan["id"])
    assert list(external.iterdir()) == []


def test_distinct_return_states_share_one_fusion_file_and_both_lookup(tmp_path):
    from server.workspace.archive import captured_cad_document
    key = "lineage:wgl_test"
    manager, root, _, _ = setup(tmp_path, key=key, name="Model")
    for n in (1, 2):
        _, source = run(manager, root, n, name="cad-import", key=key)
        (source / "legacy.f3d").write_bytes(b"same Fusion bytes")
        (source / "legacy.cad.json").write_text(json.dumps({"returnStateHash": f"sha256:{n}", "ingestId": f"wgi_{n}"}))
    manager.verify_document = lambda lineage, digest, doc: doc.read_bytes() == b"same Fusion bytes"
    plan = move_plan(manager, name="Model", key=key)
    result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "done", result["error"]
    assert len(list(root.rglob("*.f3d"))) == 1
    files = [captured_cad_document(root, "Model", f"sha256:{n}", verify_run_document=lambda doc: True) for n in (1, 2)]
    assert files[0] is not None and files[0] == files[1]
    assert files[0].name.endswith(".pinned.f3d")
    assert len(list((root / "Model" / "cad").glob("*.cad.json"))) == 2
    # The protection callback can pin either alias even though only one state
    # has the primary evidence for these byte-identical models.
    from server.workspace.archive import pin_cad_document
    for n in (1, 2):
        assert pin_cad_document(root, "Model", f"sha256:{n}", verify_document=lambda doc: True) == f"cad/{files[0].name}"


def test_rename_and_ordinary_pin_with_gather(tmp_path):
    key = "lineage:wgl_test"
    manager, root, jobs, cad = setup(tmp_path, key=key, name="Model")
    _, source = run(manager, root, key=key, name="Model", folder="Model")
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    directory = root / "Model" / "cad"
    directory.mkdir()
    (directory / "existing.f3d").write_bytes(b"fusion")
    (directory / "existing.json").write_text('{"returnStateHash":"sha256:state"}')
    manager.verify_document = lambda lineage, digest, doc: doc.read_bytes() == b"fusion"
    plan = manager.preview(key, "Renamed", rename=True)
    result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "done", result["error"]
    assert (root / "Renamed" / "cad" / "existing.pinned.cad.json").is_file()
    assert manager.undo(plan["id"])["phase"] == "undone"
    assert (directory / "existing.json").is_file()
    assert source.exists()


def test_unknown_destination_and_unknown_rename_contents_are_refused(tmp_path):
    manager, root, _, _ = setup(tmp_path)
    run(manager, root, key=KEY)
    dest = root / "250728solana"
    dest.mkdir()
    (dest / "user.txt").write_text("not WG")
    with pytest.raises(MoveRefused, match="cannot identify"):
        move_plan(manager)
    assert (dest / "user.txt").read_text() == "not WG"


def test_gather_lookup_uses_real_retained_verifier_for_legacy_ingest_and_old_root(tmp_path):
    import asyncio
    from server.tests.test_cadlink_projects import app_with_runs, project
    from server.tests.cad_document_fixtures import retained_document_record
    from server.cadlink.api import _verified_run_document, list_project_documents
    from server.jobs.runtime import JobRuntime
    from server.workspace.archive import captured_cad_document
    app, root = app_with_runs(tmp_path)
    design_id, lineage = project(app)
    cad = app.state.cadlink_store
    cad.record_lineage_cad_names(lineage, archive_stem="Model")
    manager = app.state.run_moves
    manager.jobs.initialize()
    # Old CAD jobs recover lineage through ingest; they have no archive key.
    old_root = tmp_path / "earlier" / "Runs"
    old_root.mkdir(parents=True)
    request = SimpleNamespace(app=app)
    for n in (40, 44):
        digest = f"sha256:{n:064x}"
        retained = retained_document_record(app, b"one Fusion", digest, lineage)
        retained["anchor"] = {"design_id": design_id}
        with cad._transaction() as conn:
            conn.execute("UPDATE ingests SET record_json=? WHERE ingest_id=?", (json.dumps(retained), retained["ingest_id"]))
        job_id, source = run(manager, old_root, n, name="cad-import", folder="cad-import")
        with manager.jobs._transaction() as conn:
            config = {"geometry": {"type": "imported", "ingest_id": retained["ingest_id"]}}
            conn.execute("UPDATE simulation_jobs SET config_json=?, config_summary_json=? WHERE id=?", (json.dumps(config), json.dumps({"geometry_type":"imported"}), job_id))
        (source / "old-name.f3d").write_bytes(b"one Fusion")
        (source / "old-name.cad.json").write_text(json.dumps({"ingestId": retained["ingest_id"], "returnStateHash": digest}))
        assert JobRuntime._serialize_job(manager.jobs.get_job_row(job_id), cadlink_store=cad)["cad_source"]["lineage_id"] == lineage
    plan = manager.preview(f"lineage:{lineage}", "Model")
    assert len(plan["candidates"]) == 2 and all(c["preselected"] for c in plan["candidates"])
    result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "done", result["error"]
    for n in (40,44):
        digest = f"sha256:{n:064x}"
        document = captured_cad_document(root, "Model", digest, verify_run_document=lambda path: _verified_run_document(request, lineage, digest, path))
        assert document and document.read_bytes() == b"one Fusion"
    listing = asyncio.run(list_project_documents(lineage, request))
    assert len(listing["items"]) == 2 and all(item["filename"] for item in listing["items"])


@pytest.mark.parametrize("undo", [False, True])
def test_crash_matrix_gather_and_undo(tmp_path, undo):
    key = "lineage:wgl_test"
    def fixture(directory):
        manager, root, _, _ = setup(directory, key=key, name="Model")
        _, source = run(manager, root, key=key)
        (source / "legacy.f3d").write_bytes(b"fusion")
        # Exact CRLF bytes survive undo.
        (source / "legacy.cad.json").write_bytes(b'{"returnStateHash":"sha256:state"}\r\n')
        manager.verify_document = lambda lineage, digest, doc: doc.read_bytes() == b"fusion"
        plan = move_plan(manager, name="Model", key=key)
        return manager, source, plan
    probe, _, plan = fixture(tmp_path / "probe")
    if undo:
        assert probe.execute(plan["id"], confirmed=True)["phase"] == "done"
    phases = []
    probe.after_step = phases.append
    if undo:
        probe.undo(plan["id"])
    else:
        probe.execute(plan["id"], confirmed=True)
    for index in range(len(phases)):
        moves_module._BLOCKED.clear()
        manager, source, plan = fixture(tmp_path / str(index))
        if undo:
            manager.execute(plan["id"], confirmed=True)
        counter = 0
        def crash(phase):
            nonlocal counter
            counter += 1
            if counter == index + 1:
                raise Crash(phase)
        manager.after_step = crash
        with pytest.raises(Crash):
            if undo:
                manager.undo(plan["id"])
            else:
                manager.execute(plan["id"], confirmed=True)
        manager.after_step = lambda _: None
        manager.recover()
        final = manager.read(plan["id"])
        assert final["phase"] == ("undone" if undo else "done"), (index, phases[index], final["error"])
        if undo:
            assert (source / "legacy.cad.json").read_bytes().endswith(b"\r\n")


def test_legacy_run_without_recorded_path_is_found_in_known_earlier_runs_root(tmp_path):
    manager, root, jobs, _ = setup(tmp_path)
    old = tmp_path / "Earlier Runs"
    old.mkdir()
    manager.known_roots = lambda: [old]
    job_id, source = run(manager, old)
    row = jobs.get_job_row(job_id)
    metadata = dict(row["task_metadata"])
    metadata.pop("archived_path")
    with jobs._transaction() as conn:
        conn.execute("UPDATE simulation_jobs SET task_metadata_json=? WHERE id=?", (json.dumps(metadata), job_id))
    plan = move_plan(manager)
    assert plan["candidates"][0]["source"] == str(source)
    result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "done", result["error"]
    assert Path(jobs.get_job_row(job_id)["task_metadata"]["archived_path"]).parent == root / "250728solana"


def test_source_change_between_member_removals_is_not_deleted(tmp_path, monkeypatch):
    manager, root, _, _ = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    cross_device(monkeypatch, [source])
    plan = move_plan(manager)
    def edit_after_first(phase):
        if phase.startswith("source_file_removed:"):
            for remaining in source.glob("*.*"):
                remaining.write_bytes(b"new uncopied bytes")
    manager.after_step = edit_after_first
    result = manager.execute(plan["id"], confirmed=True)
    assert "original changed" in result["error"]
    assert any(member.read_bytes() == b"new uncopied bytes" for member in source.glob("*.*"))


def test_undo_recovery_rechecks_restored_original_before_deleting_shared_copy(tmp_path):
    key = "lineage:wgl_test"
    manager, root, _, _ = setup(tmp_path, key=key, name="Model")
    _, source = run(manager, root, key=key)
    (source / "old.f3d").write_bytes(b"fusion")
    (source / "old.cad.json").write_text('{"returnStateHash":"sha256:state"}')
    manager.verify_document = lambda lineage, digest, doc: doc.read_bytes() == b"fusion"
    plan = move_plan(manager, name="Model", key=key)
    result = manager.execute(plan["id"], confirmed=True)
    shared = Path(next(op["dst"] for op in result["operations"] if op["kind"] == "cad"))
    def crash(phase):
        if phase == "undone:0":
            raise Crash()
    manager.after_step = crash
    with pytest.raises(Crash):
        manager.undo(plan["id"])
    (source / "old.f3d").write_bytes(b"corrupt")
    manager.after_step = lambda _: None
    assert manager.recover()[0]["error"]
    assert shared.read_bytes() == b"fusion"


def test_exclusive_rename_preserves_existing_destination(tmp_path):
    from server.platform.rename import rename_absent
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "run.json").write_bytes(b"original")
    with pytest.raises(FileExistsError):
        rename_absent(source, destination)
    assert (source / "run.json").read_bytes() == b"original"
    assert list(destination.iterdir()) == []


def test_route_confirmation_write_lock_events_and_startup_order(tmp_path):
    import asyncio
    from fastapi import HTTPException
    from server.workspace.run_moves_api import create_run_moves_router, PreviewRunMove, ConfirmRunMove
    from server.workspace.api import create_workspace_router, WorkspaceState, WriteExportRequest
    from server.tests.test_cadlink_projects import app_with_runs
    manager, root, jobs, _ = setup(tmp_path)
    job_id, source = run(manager, root, key=KEY)
    jobs.mutate_job_metadata(job_id, {"imported_geometry": {"archive_stem": source.parent.name}})
    published = []
    routes = create_run_moves_router(manager, published.append).routes
    def endpoint(suffix):
        return next(route.endpoint for route in routes if route.path.endswith(suffix))
    plan = asyncio.run(endpoint('/preview')(PreviewRunMove(key=KEY, name="250728solana")))
    with pytest.raises(HTTPException) as refused:
        asyncio.run(endpoint('/{move_id}/execute')(plan["id"], ConfirmRunMove(confirmed=False)))
    assert refused.value.status_code == 409 and source.exists()
    state = WorkspaceState(tmp_path / "state", default_path=root)
    write = next(route.endpoint for route in create_workspace_router(state).routes if route.path.endswith('/write-export'))
    manager._block(plan)
    with pytest.raises(HTTPException) as locked:
        asyncio.run(write(WriteExportRequest(subdirectory=f"{source.parent.name}/{source.name}", members=[{"relative_path":"one.csv", "text":"x"}])))
    assert locked.value.status_code == 409 and locked.value.detail == "Runs are being moved"
    asyncio.run(write(WriteExportRequest(subdirectory="any/run", members=[{"relative_path":"one.csv", "text":"x"}])))
    assert (root / "any/run/one.csv").read_text() == "x"
    result = asyncio.run(endpoint('/{move_id}/execute')(plan["id"], ConfirmRunMove(confirmed=True)))
    assert result["phase"] == "done"
    assert published[-1]["payload"]["changed"]["archive_folder"] == "250728solana"
    assert jobs.get_job_row(job_id)["task_metadata"]["imported_geometry"]["archive_stem"] == "250728solana"
    app, _ = app_with_runs(tmp_path / "startup")
    handlers = [handler.__name__ for handler in app.router.on_startup]
    assert handlers.index("recover_run_moves") < handlers.index("start")


def test_auto_export_reservation_blocks_move_during_generation_and_releases(tmp_path):
    import asyncio
    from server.jobs.auto_export import AutoExportOwner
    from server.tests.test_auto_export import StoredRuntime
    manager, root, _, cad = setup(tmp_path)
    runtime = StoredRuntime(tmp_path / "separate-jobs.db")
    runtime.cadlink_store = cad
    manager.jobs = runtime.store
    job_id, _ = run(manager, root, key=KEY)
    plan = move_plan(manager)
    async def scenario():
        owner = AutoExportOwner(runtime)
        claim = await owner.claim(job_id, ["step"])
        assert claim["claimed"]
        with pytest.raises(MoveRefused, match="exporting"):
            manager.execute(plan["id"], confirmed=True)
        await owner.release(claim["token"])
        assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    asyncio.run(scenario())


def test_concurrent_one_off_export_index_entry_is_preserved(tmp_path):
    manager, root, jobs, _ = setup(tmp_path)
    job_id, _ = run(manager, root, key=KEY)
    plan = move_plan(manager)
    def add_export(phase):
        if phase == "verified":
            jobs.mutate_job_metadata(job_id, {"exported_files": ["/chosen-elsewhere/new.step"]})
    manager.after_step = add_export
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    assert "/chosen-elsewhere/new.step" in jobs.get_job_row(job_id)["task_metadata"]["exported_files"]
    manager.after_step = lambda _: None
    assert manager.undo(plan["id"])["phase"] == "undone"
    assert "/chosen-elsewhere/new.step" in jobs.get_job_row(job_id)["task_metadata"]["exported_files"]


def test_undo_allows_preexisting_destination_runs_but_refuses_new_exports(tmp_path):
    manager, root, jobs, cad = setup(tmp_path)
    job_id, old = run(manager, root, 1, key=KEY, folder="250728solana")
    identity.write_project_record(root, jobs.get_job_row(job_id), cad)
    _, source = run(manager, root, 2, key=KEY)
    plan = move_plan(manager)
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    extra = old / "new-export.csv"
    extra.write_text("new export after move")
    with pytest.raises(MoveRefused, match="Verification"):
        manager.undo(plan["id"])
    extra.unlink()
    assert manager.undo(plan["id"])["phase"] == "undone"
    assert old.is_dir() and source.is_dir()


def test_open_legacy_design_can_preview_before_its_first_new_solve(tmp_path):
    manager, root, _, cad = setup(tmp_path)
    _, source = run(manager, root)
    new_key = "design:wgu_opened_legacy"
    plan = manager.preview(new_key, "250728solana")
    assert len(plan["candidates"]) == 1 and not plan["candidates"][0]["preselected"]
    assert source.exists() and plan["phase"] == "planned"
    assert identity.registered_folder(cad, new_key)


@pytest.mark.parametrize("legacy_pin", [False, True])
def test_gather_pin_survives_actual_v035_capture_and_prune(tmp_path, legacy_pin):
    from server.tests.fixtures import archive_v035 as older
    from server.tests.test_run_archive import bundle_with_document, record_for
    from server.workspace.archive import archive_cad_document, captured_cad_document

    key = "lineage:wgl_test"
    manager, root, _, _ = setup(tmp_path, key=key, name="Model")
    bundle, record = bundle_with_document(tmp_path), record_for()
    ordinary = root / "Model" / archive_cad_document(bundle, record, root, "Model")
    if legacy_pin:
        ordinary.with_suffix(".json").rename(ordinary.with_suffix(".cad.json"))
    manager.verify_document = lambda lineage, digest, doc: doc.read_bytes() == b"f3d-bytes"
    plan = manager.preview(key, "Model")
    result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "done", result["error"]
    pinned = captured_cad_document(root, "Model", "sha256:abc123")
    assert pinned.name.endswith(".pinned.f3d")
    before = pinned.read_bytes(), pinned.with_suffix(".cad.json").read_bytes(), pinned.stat().st_mtime_ns
    older.archive_cad_document(bundle, record, root, "Model")
    assert captured_cad_document(root, "Model", "sha256:abc123") == pinned
    older._prune_captured_documents(pinned.parent, set(), [])
    assert (pinned.read_bytes(), pinned.with_suffix(".cad.json").read_bytes(), pinned.stat().st_mtime_ns) == before


@pytest.mark.parametrize("undo", [False, True])
def test_pin_and_alias_rename_crash_matrix_recovers_exact_bytes(tmp_path, undo):
    from server.workspace.archive import captured_cad_document

    key = "lineage:wgl_test"
    def fixture(directory):
        manager, root, jobs, cad = setup(directory, key=key, name="Model")
        job_id, _ = run(manager, root, key=key, folder="Model")
        identity.write_project_record(root, jobs.get_job_row(job_id), cad)
        directory = root / "Model" / "cad"
        directory.mkdir()
        (directory / "existing.f3d").write_bytes(b"fusion")
        originals = {"existing.cad.json": b'{"returnStateHash":"sha256:1"}\r\n',
                     "alias.cad.json": b'{"returnStateHash":"sha256:2","modelFile":"existing.f3d"}\r\n'}
        for name, content in originals.items():
            (directory / name).write_bytes(content)
        manager.verify_document = lambda lineage, digest, doc: doc.read_bytes() == b"fusion"
        plan = manager.preview(key, "Renamed", rename=True)
        return manager, root, plan, originals

    probe, _, plan, _ = fixture(tmp_path / "probe")
    if undo:
        assert probe.execute(plan["id"], confirmed=True)["phase"] == "done"
    phases = []
    probe.after_step = phases.append
    (probe.undo(plan["id"]) if undo else probe.execute(plan["id"], confirmed=True))
    for index in range(len(phases)):
        moves_module._BLOCKED.clear()
        manager, root, plan, originals = fixture(tmp_path / str(index))
        if undo:
            assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
        count = 0
        def crash(phase):
            nonlocal count
            count += 1
            if count == index + 1:
                raise Crash(phase)
        manager.after_step = crash
        with pytest.raises(Crash):
            (manager.undo(plan["id"]) if undo else manager.execute(plan["id"], confirmed=True))
        manager.after_step = lambda _: None
        manager.recover()
        final = manager.read(plan["id"])
        assert final["phase"] == ("undone" if undo else "done"), (index, phases[index], final["error"])
        if undo:
            directory = root / "Model" / "cad"
            assert (directory / "existing.f3d").read_bytes() == b"fusion"
            assert {p.name: p.read_bytes() for p in directory.iterdir() if p.suffix == ".json"} == originals
        else:
            documents = [captured_cad_document(root, "Renamed", f"sha256:{n}") for n in (1, 2)]
            assert documents[0] == documents[1] == root / "Renamed" / "cad" / "existing.pinned.f3d"


@pytest.mark.parametrize("target", ["directory", "sidecar", "model", "manifest"])
def test_move_preview_propagates_transient_cad_io_and_can_retry(tmp_path, monkeypatch, target):
    # Exercise app wiring as well as the strict retained-manifest verifier.
    from server.tests.test_cadlink_projects import app_with_runs, project
    from server.tests.cad_document_fixtures import retained_document_record

    app, root = app_with_runs(tmp_path)
    _, lineage = project(app)
    cad = app.state.cadlink_store
    identity.claim_project_folder(cad, f"lineage:{lineage}", "Model")
    directory = root / "Model" / "cad"
    directory.mkdir(parents=True)
    document = directory / "kept.pinned.f3d"
    document.write_bytes(b"fusion")
    digest = "sha256:" + "1" * 64
    record = retained_document_record(app, b"fusion", digest, lineage)
    sidecar = document.with_suffix(".cad.json")
    sidecar.write_text(json.dumps({"ingestId": record["ingest_id"], "returnStateHash": digest}))
    manager = app.state.run_moves
    manager.jobs.initialize()
    real_iterdir, real_open = Path.iterdir, Path.open
    def iterdir(path):
        if target == "directory" and path == directory:
            raise OSError("transient directory read")
        return real_iterdir(path)
    def open_file(path, *args, **kwargs):
        if ((target == "sidecar" and path == sidecar) or (target == "model" and path == document)
                or (target == "manifest" and path.name == "wgreturn.json")):
            raise OSError("transient file read")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(OSError, match="transient"):
        manager.preview(f"lineage:{lineage}", "Model")
    monkeypatch.undo()
    assert manager.preview(f"lineage:{lineage}", "Model")["phase"] == "planned"
    assert document.read_bytes() == b"fusion"


def test_inventory_refuses_unreadable_descendant_instead_of_truncating_move(tmp_path, monkeypatch):
    manager, root, _, _ = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    real_iterdir = Path.iterdir
    def iterdir(path):
        if path == source / "empty":
            raise OSError("transient child directory read")
        return real_iterdir(path)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    with pytest.raises(OSError, match="transient child"):
        move_plan(manager)
    assert source.exists()


@pytest.mark.parametrize("rename", [False, True])
def test_gather_prefers_pinned_original_over_old_writer_duplicate_and_undo_restores_all(tmp_path, rename):
    from server.workspace.archive import captured_cad_document
    key = "lineage:wgl_test"
    manager, root, jobs, cad = setup(tmp_path, key=key, name="Model")
    job_id, _ = run(manager, root, key=key, folder="Model")
    identity.write_project_record(root, jobs.get_job_row(job_id), cad)
    directory = root / "Model" / "cad"
    directory.mkdir()
    for stem in ("state", "state.pinned"):
        (directory / (stem + ".f3d")).write_bytes(b"fusion")
    (directory / "state.json").write_bytes(b'{"returnStateHash":"sha256:1"}\r\n')
    (directory / "state.pinned.cad.json").write_bytes(b'{"returnStateHash":"sha256:1"}\n')
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    manager.verify_document = lambda lineage, digest, doc: doc.read_bytes() == b"fusion"
    plan = manager.preview(key, "Renamed" if rename else "Model", rename=rename)
    result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "done", result["error"]
    target = root / ("Renamed" if rename else "Model") / "cad"
    assert list(target.glob("*.f3d")) == [target / "state.pinned.f3d"]
    assert captured_cad_document(root, target.parent.name, "sha256:1") == target / "state.pinned.f3d"
    assert manager.undo(plan["id"])["phase"] == "undone"
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_alias_lookup_prefers_protected_model_even_with_unrelated_sidecar_name(tmp_path):
    from server.workspace.archive import captured_cad_document, pin_cad_document
    directory = tmp_path / "runs" / "Model" / "cad"
    directory.mkdir(parents=True)
    ordinary, pinned = directory / "a.f3d", directory / "z.pinned.f3d"
    ordinary.write_bytes(b"fusion")
    pinned.write_bytes(b"fusion")
    ordinary.with_suffix(".json").write_text('{"returnStateHash":"sha256:1"}')
    alias = directory / "evidence.cad.json"
    alias.write_text('{"returnStateHash":"sha256:1","modelFile":"z.pinned.f3d"}')
    assert captured_cad_document(tmp_path / "runs", "Model", "sha256:1") == pinned
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert pin_cad_document(tmp_path / "runs", "Model", "sha256:1", verify_document=lambda doc: True) == "cad/z.pinned.f3d"
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


@pytest.mark.parametrize("recover", [False, True])
def test_undo_refuses_changed_cleanup_pointer_before_restoring_runs(tmp_path, recover):
    manager, root, jobs, cad = setup(tmp_path)
    job_id, source = run(manager, root, key=KEY)
    identity.write_project_record(root, jobs.get_job_row(job_id), cad)
    plan = move_plan(manager)
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    destination = Path(jobs.get_job_row(job_id)["task_metadata"]["archived_path"])
    if recover:
        def crash(phase):
            if phase == "undo_prepared":
                raise Crash(phase)
        manager.after_step = crash
        with pytest.raises(Crash):
            manager.undo(plan["id"])
        manager.after_step = lambda _: None
    source.parent.mkdir()
    pointer = source.parent / "design.json"
    pointer.write_text('{"schemaVersion":1,"designUid":"foreign"}')
    if recover:
        report = manager.recover()[0]
        assert report["phase"] != "undone" and "occupied or changed" in report["error"]
    else:
        with pytest.raises(MoveRefused, match="occupied or changed"):
            manager.undo(plan["id"])
    assert destination.is_dir() and not source.exists()
    assert jobs.get_job_row(job_id)["task_metadata"]["archived_path"] == str(destination)
    assert pointer.read_text() == '{"schemaVersion":1,"designUid":"foreign"}'



def test_existing_captures_with_distinct_states_share_model_but_keep_alias_evidence(tmp_path):
    from server.workspace.archive import captured_cad_document, pin_cad_document
    key = "lineage:wgl_test"
    manager, root, jobs, cad = setup(tmp_path, key=key, name="Model")
    job_id, _ = run(manager, root, key=key, folder="Model")
    identity.write_project_record(root, jobs.get_job_row(job_id), cad)
    directory = root / "Model" / "cad"
    directory.mkdir()
    (directory / "a.pinned.f3d").write_bytes(b"fusion")
    (directory / "a.pinned.cad.json").write_bytes(b'{"returnStateHash":"sha256:1"}\r\n')
    (directory / "b.f3d").write_bytes(b"fusion")
    (directory / "b.json").write_bytes(b'{"returnStateHash":"sha256:2"}\r\n')
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    manager.verify_document = lambda lineage, digest, doc: doc.read_bytes() == b"fusion"
    plan = manager.preview(key, "Renamed", rename=True)
    result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "done", result["error"]
    target = root / "Renamed" / "cad"
    assert list(target.glob("*.f3d")) == [target / "a.pinned.f3d"]
    for n in (1, 2):
        assert captured_cad_document(root, "Renamed", f"sha256:{n}") == target / "a.pinned.f3d"
        assert pin_cad_document(root, "Renamed", f"sha256:{n}", verify_document=lambda doc: True) == "cad/a.pinned.f3d"
    assert manager.undo(plan["id"])["phase"] == "undone"
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_undo_never_overwrites_cleanup_file_created_during_publication(tmp_path, monkeypatch):
    manager, root, jobs, cad = setup(tmp_path)
    job_id, source = run(manager, root, key=KEY)
    identity.write_project_record(root, jobs.get_job_row(job_id), cad)
    plan = move_plan(manager)
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    pointer = source.parent / "design.json"
    real_rename = moves_module.rename_absent
    def race(src, dst):
        if dst == pointer:
            dst.write_bytes(b"new foreign pointer")
        return real_rename(src, dst)
    monkeypatch.setattr(moves_module, "rename_absent", race)
    with pytest.raises(FileExistsError):
        manager.undo(plan["id"])
    assert pointer.read_bytes() == b"new foreign pointer"
    assert manager.read(plan["id"])["phase"] != "undone"


def gathered_fixture(tmp_path):
    key = "lineage:wgl_test"
    manager, root, jobs, cad = setup(tmp_path, key=key, name="Model")
    _, source = run(manager, root, key=key)
    (source / "old.f3d").write_bytes(b"fusion")
    (source / "old.cad.json").write_bytes(b'{"returnStateHash":"sha256:state"}\r\n')
    manager.verify_document = lambda lineage, digest, doc: doc.read_bytes() == b"fusion"
    plan = move_plan(manager, name="Model", key=key)
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    return manager, root, source, plan


def crash_at(manager, phase):
    def crash(current):
        if current == phase:
            raise Crash(current)
    manager.after_step = crash


@pytest.mark.parametrize("replacement", ["bytes", "directory"])
def test_recovered_undo_keeps_changed_gathered_deletion_target(tmp_path, replacement):
    manager, root, source, plan = gathered_fixture(tmp_path)
    shared = Path(next(op["dst"] for op in plan["operations"] if op["kind"] == "cad"))
    crash_at(manager, "undone:0")
    with pytest.raises(Crash):
        manager.undo(plan["id"])
    assert (source / "old.f3d").read_bytes() == b"fusion"
    if replacement == "bytes":
        shared.write_bytes(b"edited")  # Same size; hash must be checked.
    else:
        shared.unlink()
        shared.mkdir()
        (shared / "recovery.f3d").write_bytes(b"only copy")
    manager.after_step = lambda _: None
    report = manager.recover()[0]
    assert report["error"] and report["phase"] != "undone"
    assert (shared.read_bytes() if replacement == "bytes" else (shared / "recovery.f3d").read_bytes()) == (b"edited" if replacement == "bytes" else b"only copy")


def test_gathered_target_is_rechecked_at_unlink_after_undo_verification(tmp_path, monkeypatch):
    manager, _, _, plan = gathered_fixture(tmp_path)
    remove = moves_module._remove_members
    edited = []
    def race(path, manifest):
        if path.suffix == ".f3d":
            path.write_bytes(b"edited")
            edited.append(path)
        return remove(path, manifest)
    monkeypatch.setattr(moves_module, "_remove_members", race)
    with pytest.raises(MoveRefused, match="Verification"):
        manager.undo(plan["id"])
    assert edited and edited[0].read_bytes() == b"edited"


@pytest.mark.parametrize("change", ["extra", "changed", "source_missing", "unowned"])
def test_recovery_preserves_unexpected_partial_contents_and_requires_safe_source(tmp_path, monkeypatch, change):
    manager, root, _, _ = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    cross_device(monkeypatch, [source])
    plan = move_plan(manager)
    destination = Path(plan["operations"][0]["dst"])
    partial = destination.with_name(destination.name + ".partial-" + plan["id"])
    crash_at(manager, "copied:" + str(destination))
    with pytest.raises(Crash):
        manager.execute(plan["id"], confirmed=True)
    if change == "extra":
        protected = partial / "recovery-notes.txt"
        protected.write_bytes(b"user data")
    elif change == "changed":
        protected = partial / "result.bin"
        protected.write_bytes(b"user data")
    elif change == "source_missing":
        source.rename(partial / "last-original")
        protected = partial / "last-original" / "result.bin"
    else:
        record = manager.read(plan["id"])
        record["operations"][0]["created"] = False
        moves_module._save(manager._journal_path(plan["id"]), record)
        protected = partial / "result.bin"
    before = protected.read_bytes()
    manager.after_step = lambda _: None
    report = manager.recover()[0]
    assert report["error"] and report["phase"] != "done"
    assert protected.read_bytes() == before
    assert not destination.exists()
    if change == "source_missing":
        assert (partial / "result.bin").read_bytes() == b"results\x00unchanged"


def test_undo_partial_with_unexpected_contents_is_preserved(tmp_path):
    manager, _, _, plan = gathered_fixture(tmp_path)
    op = next(op for op in plan["operations"] if op["kind"] == "cad")
    source = Path(op["live_src"])
    partial = source.with_name(source.name + ".undo-partial-" + plan["id"])
    crash_at(manager, "undo_copied:" + str(source))
    with pytest.raises(Crash):
        manager.undo(plan["id"])
    partial.unlink()
    partial.mkdir()
    protected = partial / "last-copy.f3d"
    protected.write_bytes(b"only copy")
    manager.after_step = lambda _: None
    report = manager.recover()[0]
    assert report["error"] and protected.read_bytes() == b"only copy"


@pytest.mark.parametrize("change", ["run", "metadata_only", "replacement", "link"])
def test_recovered_undo_revalidates_before_reversing_project_rename(tmp_path, change):
    manager, root, jobs, cad = setup(tmp_path, name="Original")
    _, source = run(manager, root, key=KEY, name="Original", folder="Original")
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    plan = manager.preview(KEY, "Renamed", rename=True)
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    crash_at(manager, "undo_prepared")
    with pytest.raises(Crash):
        manager.undo(plan["id"])
    target = Path(plan["target"])
    if change in {"run", "metadata_only"}:
        _, added = run(manager, root, 2, key=KEY, name="Original", folder="Renamed")
        if change == "metadata_only":
            # A post-start assignment without files follows the recovered undo.
            import shutil
            shutil.rmtree(added)
    else:
        original = root / "retained-original"
        target.rename(original)
        if change == "replacement":
            import shutil
            shutil.copytree(original, target)  # Identical bytes, different folder.
        else:
            target.symlink_to(original, target_is_directory=True)
    manager.after_step = lambda _: None
    report = manager.recover()[0]
    if change == "metadata_only":
        assert report["phase"] == "undone", report["error"]
        assert jobs.get_job_row("job2")["task_metadata"]["archive"]["folder"] == "Original"
        return
    assert report["error"] and report["phase"] != "undone"
    assert target.exists() and not source.exists()
    assert jobs.get_job_row("job1")["task_metadata"]["archive"]["folder"] == "Renamed"


@pytest.mark.parametrize("recover", [False, True])
def test_submissions_return_during_rename_and_reconcile_at_publication(tmp_path, monkeypatch, recover):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from server.engines.registry import EngineInfo, EngineRegistry
    from server.jobs.runtime import JobRuntime
    from server.tests.test_archive_identity import UID
    from server.tests.test_jobs_runtime import _request

    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    key = f"design:{UID}"
    manager, root, jobs, cad = setup(tmp_path, key=key, name="Original")
    _, source = run(manager, root, key=key, name="Original", folder="Original")
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    plan = manager.preview(key, "Renamed", rename=True)
    rename = moves_module.rename_absent
    reached, release = threading.Event(), threading.Event()
    def locked(src, dst):
        if Path(src) == source.parent:
            if recover:
                raise PermissionError(32, "File is open")
            reached.set()
            assert release.wait(10), "test did not release rename"
        return rename(src, dst)
    monkeypatch.setattr(moves_module, "rename_absent", locked)
    if recover:
        assert "File is open" in manager.execute(plan["id"], confirmed=True)["error"]
    runtime = JobRuntime(jobs, cadlink_store=cad)
    runtime.engine_registry = EngineRegistry(detector=lambda: [EngineInfo("dryrun", True, "test", "test")])
    monkeypatch.setattr(runtime, "_ensure_scheduler", lambda: None)
    request = _request().model_copy(update={"design_uid": UID, "design_name": "Original", "label": "New solve"})
    other = request.model_copy(update={"design_uid": "wgu_other", "design_name": "Other", "label": "Other solve"})
    async def exercise():
        # More submissions than shared executor workers catches parked-wait starvation.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=4))
        moving = None
        try:
            if not recover:
                moving = asyncio.create_task(asyncio.to_thread(manager.execute, plan["id"], confirmed=True))
                assert await asyncio.to_thread(reached.wait, 5)
            other_id = await asyncio.wait_for(runtime.submit(other), 5)
            job_ids = await asyncio.wait_for(asyncio.gather(*(runtime.submit(request) for _ in range(48))), 10)
            assert all(jobs.get_job_row(job)["task_metadata"]["archive"]["folder"] == "Original" for job in job_ids)
            other_row = jobs.get_job_row(other_id)
            assert other_row["task_metadata"]["archive"]["folder"] == "Other"
            # Project-level exclusion also applies to a run's first write.
            if recover:
                # A failed attempt releases exclusion while awaiting recovery.
                with archive_write(root / "Original"):
                    pass
            else:
                with pytest.raises(MoveRefused, match="Runs are being moved"):
                    identity.claim_run_write_path(jobs, jobs.get_job_row(job_ids[0]), lambda: root)
            assert identity.claim_run_write_path(jobs, other_row, lambda: root).parent == root / "Other"
            with archive_write(root / "Other"):
                pass
            monkeypatch.setattr(moves_module, "rename_absent", rename)
            release.set()
            result = (await asyncio.wait_for(asyncio.to_thread(manager.recover), 10))[0] if recover else await asyncio.wait_for(moving, 10)
            assert result["phase"] == "done", result["error"]
            for job in job_ids:
                metadata = jobs.get_job_row(job)["task_metadata"]
                assert metadata["archive"]["folder"] == "Renamed"
                assert Path(metadata["archived_path"]).parent == root / "Renamed"
            assert jobs.get_job_row(other_id)["task_metadata"]["archive"]["folder"] == "Other"
            assert identity.registered_folder(cad, key) == "Renamed"
            assert not source.parent.exists()
        finally:
            release.set()
            if moving is not None:
                await moving
            await runtime.shutdown()
    asyncio.run(exercise())


def test_recovery_reconciles_queued_post_journal_jobs(tmp_path):
    manager, root, jobs, cad = setup(tmp_path, name="Original")
    _, source = run(manager, root, key=KEY, name="Original", folder="Original")
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    plan = manager.preview(KEY, "Renamed", rename=True)
    crash_at(manager, "prepared")
    with pytest.raises(Crash):
        manager.execute(plan["id"], confirmed=True)
    row = {"id": "late", "label": "late", "status": "queued", "created_at": "then", "updated_at": "then", "queued_at": "then",
           "config_json": {}, "config_summary_json": {}, "task_metadata": {
               "archive": {"key": KEY, "folder": "Original", "run": "2_late"},
               "imported_geometry": {"archive_stem": "Original"}}}
    jobs.create_job(row)
    manager.after_step = lambda _: None
    assert manager.recover()[0]["phase"] == "done"
    metadata = jobs.get_job_row("late")["task_metadata"]
    assert metadata["archive"]["folder"] == "Renamed"
    assert metadata["imported_geometry"]["archive_stem"] == "Renamed"


@pytest.mark.parametrize("change", ["changed", "unexpected", "finder"])
def test_cleanup_preserves_and_reports_leftovers_after_journal_write(tmp_path, change):
    manager, root, jobs, cad = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    pointer = source.parent / "design.json"
    if change == "finder":
        leftover = source.parent / ".DS_Store"
        leftover.write_bytes(b"Finder data, not WG data")
    else:
        leftover = pointer if change == "changed" else source.parent / "foreign.txt"
    plan = move_plan(manager)
    def edit(phase):
        if phase == "cleanup" and change != "finder":
            leftover.write_bytes(b"edited after journal fsync")
    manager.after_step = edit
    report = manager.execute(plan["id"], confirmed=True)
    assert leftover.read_bytes() == (b"Finder data, not WG data" if change == "finder" else b"edited after journal fsync")
    assert any(item["path"] == str(leftover) for item in report["leftovers"])
    if change == "finder":
        assert report["phase"] == "done", report["error"]
        assert report["error"] is None
        assert not pointer.exists()
        assert all(Path(item["path"]).name != ".DS_Store" for item in report["cleanup"])
    else:
        assert report["phase"] != "done"
        assert "Move needs attention" in report["error"]
        assert str(leftover) in report["error"]
    assert (root / "250728solana" / source.name / "result.bin").exists()


@pytest.mark.parametrize("phase", ["verified", "jobs_committed", "registry_committed", "pointers_committed"])
def test_recovery_preserves_edited_design_pointer(tmp_path, phase):
    manager, root, jobs, cad = setup(tmp_path, name="Original")
    run(manager, root, key=KEY, name="Original", folder="Original")
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    plan = manager.preview(KEY, "Renamed", rename=True)
    crash_at(manager, phase)
    with pytest.raises(Crash):
        manager.execute(plan["id"], confirmed=True)
    pointer = root / "Renamed" / "design.json"
    edited = json.loads(pointer.read_text())
    edited["name"] = "User edit while stopped"
    content = json.dumps(edited).encode()
    pointer.write_bytes(content)
    manager.after_step = lambda _: None
    report = manager.recover()[0]
    assert report["phase"] != "done"
    assert str(pointer) in report["error"]
    assert "kept" in report["error"]
    assert pointer.read_bytes() == content


def test_pointer_rechecked_after_replacement_intent_is_journaled(tmp_path):
    manager, root, jobs, cad = setup(tmp_path, name="Original")
    run(manager, root, key=KEY, name="Original", folder="Original")
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    plan = manager.preview(KEY, "Renamed", rename=True)
    pointer = root / "Renamed" / "design.json"
    def edit(phase):
        record = manager.read(plan["id"])
        if record.get("pointer_writes"):
            pointer.write_bytes(b"Changed after pointer intent fsync")
    manager.after_step = edit
    report = manager.execute(plan["id"], confirmed=True)
    assert report["phase"] != "done"
    assert "Changed design.json was kept" in report["error"]
    assert pointer.read_bytes() == b"Changed after pointer intent fsync"


def test_recovery_replays_reconciled_queued_jobs_after_reference_intent_crash(tmp_path):
    manager, root, jobs, cad = setup(tmp_path, name="Original")
    run(manager, root, key=KEY, name="Original", folder="Original")
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    plan = manager.preview(KEY, "Renamed", rename=True)
    crash_at(manager, "prepared")
    with pytest.raises(Crash):
        manager.execute(plan["id"], confirmed=True)
    row = {"id": "late", "label": "late", "status": "queued", "created_at": "then", "updated_at": "then", "queued_at": "then",
           "config_json": {}, "config_summary_json": {}, "task_metadata": {
               "archive": {"key": KEY, "folder": "Original", "run": "2_late"},
               "imported_geometry": {"archive_stem": "Original"}}}
    jobs.create_job(row)
    crash_at(manager, "reference_intent")
    with pytest.raises(Crash):
        manager.recover()
    manager.after_step = lambda _: None
    report = manager.recover()[0]
    assert report["phase"] == "done", report["error"]
    metadata = jobs.get_job_row("late")["task_metadata"]
    assert metadata["archive"]["folder"] == "Renamed"
    assert metadata["imported_geometry"]["archive_stem"] == "Renamed"


def test_rename_ignores_other_projects_busy_jobs_and_writers(tmp_path):
    manager, root, jobs, cad = setup(tmp_path, name="Original")
    run(manager, root, key=KEY, name="Original", folder="Original")
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    run(manager, root, 2, key="design:other", name="Other", folder="Other", status="queued", archived=False)
    with archive_write(root / "Other"):
        plan = manager.preview(KEY, "Renamed", rename=True)
        result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "done", result["error"]


def test_first_write_checks_fresh_project_after_reference_reconciliation(tmp_path):
    manager, root, jobs, cad = setup(tmp_path, name="Original")
    run(manager, root, key=KEY, name="Original", folder="Original")
    stale_row = jobs.get_job_row("job1")
    identity.write_project_record(root, stale_row, cad)
    plan = manager.preview(KEY, "Renamed", rename=True)
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    moves_module._BLOCKED["another-move"] = [root / "Renamed"]
    with pytest.raises(MoveRefused, match="Runs are being moved"):
        identity.claim_run_write_path(jobs, stale_row, lambda: root)


def test_undo_reconciles_submission_at_last_reverse_rename_boundary(tmp_path):
    manager, root, jobs, cad = setup(tmp_path, name="Original")
    run(manager, root, key=KEY, name="Original", folder="Original")
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    plan = manager.preview(KEY, "Renamed", rename=True)
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    def submit(phase):
        if phase == "undone:0":
            with moves_module.submission_location(cad, {"design_uid": "wgu_test", "design_name": "Original"}, metadata := {}):
                jobs.create_job({"id": "late", "label": "late", "status": "queued", "created_at": "then", "updated_at": "then", "queued_at": "then",
                                 "config_json": {}, "config_summary_json": {}, "task_metadata": metadata})
    manager.after_step = submit
    result = manager.undo(plan["id"])
    assert result["phase"] == "undone", result["error"]
    assert jobs.get_job_row("late")["task_metadata"]["archive"]["folder"] == "Original"


def test_cleanup_reports_unknown_file_added_while_unlinking_owned_pointer(tmp_path, monkeypatch):
    manager, root, jobs, cad = setup(tmp_path)
    _, source = run(manager, root, key=KEY)
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    pointer = source.parent / "design.json"
    foreign = source.parent / "new.txt"
    plan = move_plan(manager)
    unlink = Path.unlink
    def add_file(path, *args, **kwargs):
        result = unlink(path, *args, **kwargs)
        if path == pointer:
            foreign.write_bytes(b"new unowned contents")
        return result
    monkeypatch.setattr(Path, "unlink", add_file)
    report = manager.execute(plan["id"], confirmed=True)
    assert report["phase"] != "done"
    assert str(foreign) in report["error"]
    assert any(item["path"] == str(foreign) for item in report["leftovers"])
    assert foreign.read_bytes() == b"new unowned contents"


def submit_move_job(manager, cad, job_id, *, status="queued", key=KEY):
    """Use the admission lock and allocator, without starting a scheduler."""
    config = {"design_uid": "wgu_test", "design_name": "Original"}
    metadata = {"imported_geometry": {"archive_stem": "Original"}}
    if key.startswith("lineage:"):
        metadata["imported_geometry"]["anchor_lineage_id"] = key.removeprefix("lineage:")
    with moves_module.submission_location(cad, config, metadata):
        manager.jobs.create_job({"id": job_id, "label": job_id, "status": status,
                                 "created_at": "then", "updated_at": "then", "queued_at": "then",
                                 "config_json": config, "config_summary_json": {}, "task_metadata": metadata})


@pytest.mark.parametrize("kind,undo", [
    (kind, undo) for kind in ("move", "rename", "logical_rename", "move_and_logical_rename", "gather_rename")
    for undo in (False, True)
] + [("copy_move", False)])
def test_submission_assignment_invariant_at_every_crash_boundary(tmp_path, monkeypatch, kind, undo):
    # Enumerate the actual durable/physical hooks, including reference and pointer
    # publication. Admit jobs before the journal, at process death, while stopped,
    # and at a second crash during recovery. Every assignment must converge.
    key = "lineage:wgl_test" if kind == "gather_rename" else KEY
    rename = kind not in {"move", "copy_move"}
    if kind == "copy_move":
        cross_device(monkeypatch, [tmp_path / directory / "WG" / "runs" / "Earlier"
                                   for directory in ["probe", *map(str, range(200))]])
    def fixture(directory):
        manager, root, jobs, cad = setup(directory, key=key, name="Original")
        if kind != "logical_rename":
            _, source = run(manager, root, key=key, name="Original", folder="Original" if kind in {"rename", "gather_rename"} else "Earlier")
            if kind == "gather_rename":
                document = json.loads((source / "run.json").read_text())
                document["design"]["lineageId"] = "wgl_test"
                (source / "run.json").write_text(json.dumps(document))
                (source / "old.f3d").write_bytes(b"fusion")
                (source / "old.cad.json").write_bytes(b'{"returnStateHash":"sha256:state"}\r\n')
                captures = source.parent / "cad"
                captures.mkdir()
                (captures / "existing.f3d").write_bytes(b"fusion")
                (captures / "existing.cad.json").write_bytes(b'{"returnStateHash":"sha256:other"}\r\n')
                manager.verify_document = lambda lineage, digest, doc: doc.read_bytes() == b"fusion"
            if kind in {"rename", "gather_rename"}:
                identity.write_project_record(root, jobs.get_job_row("job1"), cad)
        submit_move_job(manager, cad, "before", status="cancelled", key=key)
        plan = move_plan(manager, name="Renamed" if rename else "Original", rename=rename, key=key)
        if kind == "logical_rename":
            assert not plan["operations"] and not (root / "Original").exists()
            assert plan["folder_change"]["after"] == str(root / "Renamed")
        if undo:
            assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
        return manager, root, jobs, cad, plan

    probe, _, _, _, plan = fixture(tmp_path / "probe")
    phases = []
    probe.after_step = phases.append
    assert (probe.undo(plan["id"]) if undo else probe.execute(plan["id"], confirmed=True))["phase"] == ("undone" if undo else "done")
    for index, boundary in enumerate(phases):
        moves_module._BLOCKED.clear()
        manager, root, jobs, cad, plan = fixture(tmp_path / str(index))
        count = 0
        def crash(phase):
            nonlocal count
            count += 1
            if count == index + 1:
                submit_move_job(manager, cad, "boundary", key=key)
                raise Crash(phase)
        manager.after_step = crash
        with pytest.raises(Crash):
            (manager.undo(plan["id"]) if undo else manager.execute(plan["id"], confirmed=True))
        submit_move_job(manager, cad, "stopped", key=key)
        if manager.read(plan["id"])["phase"] not in {"done", "undone"}:
            def crash_replay(phase):
                if phase == "reference_intent":
                    submit_move_job(manager, cad, "replay", key=key)
                    raise Crash(phase)
            manager.after_step = crash_replay
            with pytest.raises(Crash):
                manager.recover()
        manager.after_step = lambda _: None
        manager.recover()
        final = manager.read(plan["id"])
        assert final["phase"] == ("undone" if undo else "done"), (index, boundary, final["error"])
        folder = "Original" if undo or not rename else "Renamed"
        assert identity.registered_folder(cad, key) == folder, (index, boundary)
        if kind == "gather_rename":
            assert identity.registered_folder(cad, KEY) == folder
            assert cad.get_lineage_cad_names("wgl_test")["archive_stem"] == folder
        for job_id in ("before", "boundary", "stopped", "replay"):
            row = jobs.get_job_row(job_id)
            if row is None:
                continue
            metadata = row["task_metadata"]
            assert metadata["archive"]["folder"] == folder, (boundary, job_id, metadata)
            assert metadata["imported_geometry"]["archive_stem"] == folder
            assert identity.claim_run_write_path(jobs, row, lambda: root).parent == root / folder
        assert manager.recover() == []


@pytest.mark.parametrize("busy", ["running", "archiving", "auto_exporting"])
def test_undo_recovery_releases_busy_submission_and_retries_after_it_finishes(tmp_path, busy):
    manager, root, jobs, cad = setup(tmp_path, name="Original")
    run(manager, root, key=KEY, name="Original", folder="Original")
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    plan = manager.preview(KEY, "Renamed", rename=True)
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    crash_at(manager, "undo_prepared")
    with pytest.raises(Crash):
        manager.undo(plan["id"])
    submit_move_job(manager, cad, "late", status="running" if busy == "running" else "queued")
    if busy != "running":
        jobs.mutate_job_metadata("late", {busy: True})
    manager.after_step = lambda _: None
    report = manager.recover()[0]
    assert "Move needs attention" in report["error"]
    assert "Wait for it to finish" in report["error"]
    for folder in ("Original", "Renamed"):
        with archive_write(root / folder):
            pass
    jobs.update_job("late", status="cancelled")
    if busy != "running":
        jobs.mutate_job_metadata("late", {busy: False})
    report = manager.recover()[0]
    assert report["phase"] == "undone", report["error"]
    assert jobs.get_job_row("late")["task_metadata"]["archive"]["folder"] == "Original"


@pytest.mark.parametrize("legacy", [False, True])
def test_pending_undo_reconciles_first_submission_including_legacy_journal(tmp_path, legacy):
    manager, root, jobs, cad = setup(tmp_path, name="Original")
    run(manager, root, key=KEY, name="Original", folder="Original")
    identity.write_project_record(root, jobs.get_job_row("job1"), cad)
    plan = manager.preview(KEY, "Renamed", rename=True)
    assert manager.execute(plan["id"], confirmed=True)["phase"] == "done"
    crash_at(manager, "undo_prepared")
    with pytest.raises(Crash):
        manager.undo(plan["id"])
    if legacy:
        record = manager.read(plan["id"])
        for key in ("undo_jobs", "journal_jobs", "folder_change"):
            record.pop(key)
        moves_module._save(manager._journal_path(plan["id"]), record)
    submit_move_job(manager, cad, "late")
    manager.after_step = lambda _: None
    report = manager.recover()[0]
    assert report["phase"] == "undone", report["error"]
    assert jobs.get_job_row("late")["task_metadata"]["archive"]["folder"] == "Original"
    assert identity.registered_folder(cad, KEY) == "Original"


@pytest.mark.parametrize("recover", [False, True])
def test_logical_rename_reconciles_first_submission_without_physical_operations(tmp_path, recover):
    manager, root, jobs, cad = setup(tmp_path, name="Original")
    plan = manager.preview(KEY, "Renamed", rename=True)
    assert not plan["operations"] and not plan["references"]
    def submit(phase):
        if phase == "prepared":
            submit_move_job(manager, cad, "late")
            if recover:
                raise Crash(phase)
    manager.after_step = submit
    if recover:
        with pytest.raises(Crash):
            manager.execute(plan["id"], confirmed=True)
        manager.after_step = lambda _: None
        result = manager.recover()[0]
    else:
        result = manager.execute(plan["id"], confirmed=True)
    assert result["phase"] == "done", result["error"]
    metadata = jobs.get_job_row("late")["task_metadata"]
    assert metadata["archive"]["folder"] == metadata["imported_geometry"]["archive_stem"] == "Renamed"
    assert identity.registered_folder(cad, KEY) == "Renamed"
    assert not (root / "Original").exists()


def test_recovery_keeps_folder_created_during_logical_rename_and_releases_writers(tmp_path):
    manager, root, _, _ = setup(tmp_path, name="Original")
    plan = manager.preview(KEY, "Renamed", rename=True)
    crash_at(manager, "prepared")
    with pytest.raises(Crash):
        manager.execute(plan["id"], confirmed=True)
    original = root / "Original"
    original.mkdir()
    (original / "foreign.txt").write_bytes(b"keep")
    manager.after_step = lambda _: None
    report = manager.recover()[0]
    assert "Move needs attention" in report["error"]
    assert (original / "foreign.txt").read_bytes() == b"keep"
    with archive_write(original), archive_write(root / "Renamed"):
        pass
