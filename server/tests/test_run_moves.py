"""Synthetic owner-shaped trees, fault injection and byte-preserving relocation."""
from __future__ import annotations

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


def test_name_only_owner_shape_is_unticked_and_ten_folders_move_byte_exact(tmp_path):
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


def cross_device(monkeypatch, sources):
    original = Path.stat
    source_set = set(sources)
    def fake(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if not source_set.isdisjoint({path, *path.parents}):
            fields = list(result)
            fields[2] += 10
            return os.stat_result(fields)
        return result
    monkeypatch.setattr(Path, "stat", fake)


def test_cross_drive_verifies_whole_move_before_any_original_removal(tmp_path, monkeypatch):
    manager, root, jobs, _ = setup(tmp_path)
    old = tmp_path / "older-Runs"
    old.mkdir()
    originals = [run(manager, old, n, key=KEY)[1] for n in (1, 2)]
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


def test_explicit_rename_updates_registry_aliases_mirrors_and_can_undo(tmp_path):
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
        asyncio.run(write(WriteExportRequest(subdirectory="any/run", members=[{"relative_path":"one.csv", "text":"x"}])))
    assert locked.value.status_code == 409 and locked.value.detail == "Runs are being moved"
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
