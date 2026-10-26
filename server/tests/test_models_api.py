"""Imported models: a file is one model however often it is imported or prepared."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import FastAPI
import pytest

from server.cadlink.roles import inventory_sha256, prepared_inventory_sha256
from server.cadlink.store import CadLinkStore
from server.jobs.imported_setup import setup_content, setup_digest, validate_setup
from server.jobs.models_api import mount_models_api
from server.jobs.runtime import ImportedSolveRefusal, JobRuntime
from server.jobs.store import JobStore
from test_imported_jobs import _AlwaysRegistry
from test_jobs_api import _request
from test_mesh_import import register_mesh, registration, request


def test_file_inspection_and_repreparation_share_axes_roles_and_declared_domain(tmp_path):
    from server.mesh.file_import import mesh_text
    from test_file_mesh_roles_symmetry import box_mesh

    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            original = {"filename": "case-00101.msh", "msh_text": mesh_text(box_mesh(planes=("x0", "y0")))}
            summary = await call(app, "POST", "/api/models/files/inspect", original)
            assert summary["suggested_units"] == "m"
            assert summary["detected_planes"] == ["x0", "y0"]
            assert summary["kept_sides"] == {"x0": "positive", "y0": "positive"}
            assert {g["tag"]: g["suggested_role"] for g in summary["groups"]} == {1: "RIGID", 101: "HF", 102: "MF"}
            settings = {"units": "m", "forward": "+z", "up": "+y", "roles": {101: "HF", 102: "MF"},
                        "domain_planes": ["x0", "y0"]}
            registered = await call(app, "POST", "/api/models/files", {**original, **settings})
            record = registered["prepared"]
            assert record["symmetry"]["domain_planes"] == ["x0", "y0"]
            assert record["polar_grid_derivation"]["axes"]["diagonal"]["minimum_deg"] == 0
            model_id = registered["model"]["model_id"]
            rebuilt = await call(app, "POST", f"/api/models/{model_id}/prepare", settings)
            assert rebuilt["prepared"]["ingest_id"] == record["ingest_id"]
            transformed = await call(app, "POST", "/api/models/files/inspect", {
                "filename": "half.msh", "msh_text": mesh_text(box_mesh(planes=("x0",))),
                "units": "m", "forward": "+z", "up": "+x",
            })
            assert transformed["detected_planes"] == ["y0"]
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())


def app_for(tmp_path, store):
    runtime = JobRuntime(
        JobStore.for_data_dir(tmp_path),
        cadlink_store=store,
        engine_registry=_AlwaysRegistry(SimpleNamespace(name="metal")),
    )
    runtime.store.initialize()
    app = FastAPI()
    app.state.cadlink_store, app.state.jobs_runtime, app.state.data_dir = store, runtime, tmp_path
    mount_models_api(app)
    return app, runtime


async def call(app, method, path, body=None, expected=200):
    path, _, query = path.partition("?")
    status, raw = await _request(app, method, path, body=body, query=query)
    assert status == expected, (status, raw)
    return json.loads(raw)


def setup_for(record):
    geometry = request(record).geometry.model_dump(mode="json", exclude_none=True)
    for field in ("type", "ingest_id", "manifest_sha256", "artifact_sha256", "acknowledged_findings"):
        geometry.pop(field, None)
    return {
        "geometry": geometry,
        "options": {
            "engine": "metal",
            "frequency_range": [250, 8000],
            "num_frequencies": 17,
            "polar_config": {"angle_range": [-180, 180, 73]},
        },
    }


@pytest.mark.parametrize("findings", [False, True])
def test_saved_file_setup_survives_restart_without_a_job(tmp_path, findings):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        record = register_mesh(store, tmp_path, registration())
        if findings:
            original = record
            row = store.allocate_ingest(
                manifest_sha256=record["manifest_sha256"],
                artifact_sha256=record["artifact_sha256"],
                record_builder=lambda ingest_id, created_at: json.dumps(
                    {
                        **original,
                        "ingest_id": ingest_id,
                        "created_at": created_at,
                        "report_sha256": "sha256:review",
                        "findings": [{"id": "review", "blocking": True}],
                    }
                ),
            )
            record = json.loads(row["record_json"])
        app, runtime = app_for(tmp_path, store)
        model_id = "mesh-file:" + record["artifact_sha256"]
        setup = setup_for(record)
        path = f"/api/models/{model_id}/setup"
        body = {"prepared_id": record["ingest_id"], "setup": setup}
        try:
            saved = await call(app, "PUT", path, body)
            assert await call(app, "PUT", path, body) == saved
            expected = setup_digest(validate_setup(setup))
            inventory = prepared_inventory_sha256(record)
            assert saved["inventory_sha256"] == inventory
            revision = store.get_setup_revision(store.get_model_setup(model_id, inventory)["revision_id"])
            assert revision["content_sha256"] == expected == saved["content_sha256"]
            assert runtime.store.list_jobs()[1] == 0
            if findings:
                with pytest.raises(ImportedSolveRefusal, match="unacknowledged_findings"):
                    await runtime._prepare_imported_submission(request(record))
            store.close()
            store.initialize()
            read = await call(app, "GET", f"{path}?prepared_id={record['ingest_id']}")
            assert read == {"setup": json.loads(revision["setup_json"]), "revision_id": revision["revision_id"]}
            invalid = {
                "prepared_id": record["ingest_id"],
                "setup": {**setup, "geometry": {
                    **setup["geometry"], "drive_channels": [{"id": "wrong", "source_ids": ["wrong"]}],
                }},
            }
            refused = await call(app, "PUT", path, invalid, expected=422)
            assert refused["error"]["code"] and refused["detail"]
            assert store.get_setup_revision(
                store.get_model_setup(model_id, inventory)["revision_id"]
            )["content_sha256"] == expected
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_reimporting_or_repreparing_a_file_finds_its_saved_setup(tmp_path):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            first = await call(app, "POST", "/api/models/files", registration().model_dump())
            model_id = first["model"]["model_id"]
            prepared = first["prepared"]
            assert model_id == "mesh-file:" + prepared["artifact_sha256"] == prepared["model_id"]
            saved = await call(app, "PUT", f"/api/models/{model_id}/setup",
                               {"prepared_id": prepared["ingest_id"], "setup": setup_for(prepared)})
            # Imported again, under another name and in other units: the same
            # model, a new preparation, and the setup saved for its sources.
            again = await call(app, "POST", "/api/models/files",
                               registration(filename="renamed.msh", units="cm").model_dump())
            assert again["model"]["model_id"] == model_id
            assert again["model"]["display_name"] == "renamed.msh"
            assert again["prepared"]["ingest_id"] != prepared["ingest_id"]
            found = await call(app, "GET", f"/api/models/{model_id}/setup?prepared_id={again['prepared']['ingest_id']}")
            assert found["revision_id"] == saved["revision_id"]
            # With no preparation named, the latest is read.
            assert (await call(app, "GET", f"/api/models/{model_id}/setup"))["revision_id"] == saved["revision_id"]
            # Another file is another model, with no setup.
            other = await call(app, "POST", "/api/models/files",
                               registration(msh_text=registration().msh_text + "\n").model_dump())
            assert other["model"]["model_id"] != model_id
            assert (await call(app, "GET", f"/api/models/{other['model']['model_id']}/setup"))["setup"] is None
            # A preparation of one model cannot read or save another's setup.
            mismatch = await call(
                app, "GET", f"/api/models/{model_id}/setup?prepared_id={other['prepared']['ingest_id']}",
                expected=409,
            )
            assert mismatch["error"]["code"] == "model_mismatch"
            await call(app, "GET", "/api/models/mesh-file:sha256:none", expected=404)
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_preparing_twice_is_one_ingest_even_concurrently(tmp_path):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            body = registration().model_dump()
            first, second = await asyncio.gather(
                call(app, "POST", "/api/models/files", body), call(app, "POST", "/api/models/files", body),
            )
            assert first["prepared"]["ingest_id"] == second["prepared"]["ingest_id"]
            again = await call(app, "POST", "/api/models/files", body)
            assert again["prepared"] == first["prepared"]
            model_id = first["model"]["model_id"]
            assert len(store.list_model_ingests(model_id)) == 1
            assert [path.name for path in (tmp_path / "imports" / "files").iterdir()] == [
                first["prepared"]["ingest_id"]
            ]
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_a_concurrent_commit_wins_and_the_loser_leaves_no_files(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        with ThreadPoolExecutor(4) as pool:
            records = list(pool.map(lambda _: register_mesh(store, tmp_path, registration()), range(4)))
        assert len({record["ingest_id"] for record in records}) == 1
        assert len(list((tmp_path / "imports" / "files").iterdir())) == 1
    finally:
        store.close()


def test_a_damaged_preparation_is_not_reused(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        record = register_mesh(store, tmp_path, registration())
        Path(record["mesh_store_path"]).write_text("changed")
        replacement = register_mesh(store, tmp_path, registration())
        assert replacement["ingest_id"] != record["ingest_id"]
        assert register_mesh(store, tmp_path, registration()) == replacement
    finally:
        store.close()


def test_a_restored_file_is_prepared_again_from_its_retained_original(tmp_path):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            first = (await call(app, "POST", "/api/models/files", registration().model_dump()))["prepared"]
            model_id = first["model_id"]
            # A restored file has no original in the browser: the server has it.
            change = {"units": "cm", "forward": "+x", "up": "+z", "source_tags": [2]}
            prepared = (await call(app, "POST", f"/api/models/{model_id}/prepare", change))["prepared"]
            assert prepared["model_id"] == model_id
            assert prepared["ingest_id"] != first["ingest_id"]
            assert prepared["source_file"]["transform"]["units"] == "cm"
            assert prepared["source_file"]["filename"] == "tetra.msh"
            assert prepared["msh_text"] != first["msh_text"]
            # The same preparation again is the same mesh.
            assert (await call(app, "POST", f"/api/models/{model_id}/prepare", change))["prepared"] == prepared
            read = await call(app, "GET", f"/api/models/{model_id}?prepared_id={first['ingest_id']}")
            assert read["model"]["latest_prepared_id"] == prepared["ingest_id"]
            assert read["prepared"]["ingest_id"] == first["ingest_id"]
            assert read["original_available"] is True
            bad = await call(app, "POST", f"/api/models/{model_id}/prepare",
                             {**change, "source_tags": [7]}, expected=422)
            assert "driven groups" in bad["detail"]
            for ingest in (first, prepared):
                retained = json.loads(store.get_ingest(ingest["ingest_id"])["record_json"])
                (Path(retained["mesh_store_path"]).parent / "original.msh").unlink()
            assert (await call(app, "GET", f"/api/models/{model_id}"))["original_available"] is False
            gone = await call(app, "POST", f"/api/models/{model_id}/prepare",
                              {**change, "units": "m"}, expected=409)
            assert gone["error"]["code"] == "original_unavailable"
            await call(app, "POST", "/api/models/wgl_cad/prepare", change, expected=404)
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_a_cad_project_setup_is_recorded_for_its_return_inventory(tmp_path, monkeypatch):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        setup = {
            "geometry": {"drive_channels": [{"id": "source-hf", "source_ids": ["source-hf"]}]},
            "options": {"engine": "metal", "frequencies_hz": [100, 200]},
        }
        inventory = [{"id": "source-hf", "role": "hf", "required": True}]
        # A CAD-authored document's project, known before anything is prepared.
        project = store.claim_cad_document_lineage("urn:fusion:box", "Box")
        try:
            saved = await call(app, "PUT", f"/api/models/{project}/setup", {"inventory": inventory, "setup": setup})
            # Roles are canonical, as the returns listing states them.
            assert saved["inventory_sha256"] == inventory_sha256([{**inventory[0], "role": "HF"}])
            row = store.get_model_setup(project, saved["inventory_sha256"])
            assert row["revision_id"] == saved["revision_id"]
            assert store.get_setup_revision(saved["revision_id"])["setup_json"] == setup_content(
                validate_setup(setup)
            )
            query = quote(json.dumps(inventory))
            found = await call(app, "GET", f"/api/models/{project}/setup?inventory={query}")
            assert found["cad_claimed"] is True
            unclaimed = await call(app, "GET", f"/api/models/wgl_unclaimed_design/setup?inventory={query}")
            assert unclaimed["cad_claimed"] is False
            assert found["revision_id"] == saved["revision_id"]
            assert found["setup"] == json.loads(store.get_setup_revision(saved["revision_id"])["setup_json"])
            # The Fusion job reads the very revision the UI inventory read shows.
            from server.cadlink.joblane import preparation_inputs
            from server.jobs.cad_preparation import _load_setup
            monkeypatch.setattr(preparation_inputs, "read_snapshot", lambda *a, **k: SimpleNamespace(manifest={"sources": inventory}))
            monkeypatch.setattr(preparation_inputs, "snapshot_project", lambda *a: project)
            loaded, revision_id = _load_setup(SimpleNamespace(store=store), None, {"retained_path": "retained"})
            assert revision_id == found["revision_id"]
            assert json.loads(setup_content(loaded)) == found["setup"]
            other = quote(json.dumps([{**inventory[0], "role": "LF"}]))
            assert (await call(app, "GET", f"/api/models/{project}/setup?inventory={other}"))["setup"] is None
            for invalid in ("not-json", "[]", json.dumps(inventory * 2)):
                await call(app, "GET", f"/api/models/{project}/setup?inventory={quote(invalid)}", expected=422)
            await call(app, "GET", f"/api/models/{project}/setup?inventory={query}&prepared_id=wgi_x", expected=422)
            # A file always names its prepared mesh; both at once is refused.
            await call(app, "PUT", "/api/models/mesh-file:sha256:x/setup",
                       {"inventory": inventory, "setup": setup}, expected=422)
            await call(app, "PUT", f"/api/models/{project}/setup",
                       {"inventory": inventory, "prepared_id": "wgi_x", "setup": setup}, expected=422)
            # An id no model, design or document carries gets no setup row.
            unknown = await call(app, "PUT", "/api/models/wgl_nobody/setup",
                                 {"inventory": inventory, "setup": setup}, expected=404)
            assert unknown["error"]["code"] == "model_not_found"
            assert store.get_model_setup("wgl_nobody", saved["inventory_sha256"]) is None
            # A saved WG design's lineage is a project too.
            design = store.save(
                requested=None, design_hash="sha256:" + "3" * 64, filename="horn.cfg",
                snapshot_builder=lambda identity: f"DesignId={identity.design_id}",
            )["identity"]
            await call(app, "PUT", f"/api/models/{design.lineage_id}/setup", {"inventory": inventory, "setup": setup})
            # The CAD route this replaces is gone.
            assert (await _request(app, "PUT", "/api/cadlink/project-setups", body={}))[0] == 404
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_shared_setup_keeps_legacy_schema_bytes_and_imports():
    from server.cadlink.joblane.setup import CadSolveSetup as LegacySetup
    from server.jobs.imported_setup import ImportedSolveSetup

    assert LegacySetup is ImportedSolveSetup
    setup = validate_setup(
        {
            "geometry": {"drive_channels": [{"id": "source", "source_ids": ["source"]}]},
            "options": {"frequencies_hz": [100, 200]},
            "label": "Métré",
        }
    )
    content = setup_content(setup)
    assert '"origin"' not in content and "Métré" in content
    assert (
        setup_digest(setup)
        == "sha256:" + __import__("hashlib").sha256(content.encode()).hexdigest()
    )


def test_a_pending_restart_refuses_preparation_but_never_a_setup_save(tmp_path):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            first = await call(app, "POST", "/api/models/files", registration().model_dump())
            prepared = first["prepared"]
            model_id = prepared["model_id"]
            project = store.claim_cad_document_lineage("urn:fusion:box", "Box")
            runtime._restart_pending = lambda: True
            refused = await call(app, "POST", "/api/models/files",
                                 registration(units="cm").model_dump(), expected=409)
            assert refused["error"]["code"] == "update_restart_pending"
            change = {"units": "cm", "forward": "+z", "up": "+y", "source_tags": [2]}
            await call(app, "POST", f"/api/models/{model_id}/prepare", change, expected=409)
            # The editor's last edits, file and CAD alike, still land.
            saved = await call(app, "PUT", f"/api/models/{model_id}/setup",
                               {"prepared_id": prepared["ingest_id"], "setup": setup_for(prepared)})
            assert store.get_model_setup(model_id, saved["inventory_sha256"])["revision_id"] == saved["revision_id"]
            cad_setup = {"geometry": {"drive_channels": [{"id": "hf", "source_ids": ["hf"]}]},
                         "options": {"frequencies_hz": [100]}}
            await call(app, "PUT", f"/api/models/{project}/setup",
                       {"inventory": [{"id": "hf", "role": "HF", "required": True}], "setup": cad_setup})
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_a_prepared_mesh_names_its_model_and_a_damaged_record_is_not_a_refused_mesh(tmp_path):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            prepared = (await call(app, "POST", "/api/models/files", registration().model_dump()))["prepared"]
            found = await call(app, "GET", f"/api/models?prepared_id={prepared['ingest_id']}")
            assert found == {"model_id": prepared["model_id"], "prepared_id": prepared["ingest_id"]}
            await call(app, "GET", "/api/models?prepared_id=wgi_gone", expected=404)
            with store._lock, store._transaction() as conn:
                conn.execute("UPDATE ingests SET record_json = '{damaged' WHERE ingest_id = ?",
                             (prepared["ingest_id"],))
            for path in (f"/api/models/{prepared['model_id']}",
                         f"/api/models/{prepared['model_id']}/setup"):
                damaged = await call(app, "GET", path, expected=500)
                assert damaged["error"]["code"] == "record_unreadable"
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_return_listing_resolves_the_same_project_as_fusion_before_preparation(tmp_path, monkeypatch):
    from server.cadlink import api
    store = CadLinkStore.for_data_dir(tmp_path)
    project = store.claim_cad_document_lineage("urn:fusion:box", "Box")
    manifest = {"document": {"native_id": "urn:fusion:box"}, "sources": []}
    monkeypatch.setattr(api, "return_inventory", lambda root: [({"readable": True}, manifest, root)])
    try:
        assert api._return_listing(tmp_path, store)[0]["projectLineageId"] == project
        manifest["document"]["native_id"] = "urn:fusion:new"
        assert api._return_listing(tmp_path, store)[0]["projectLineageId"] is None
    finally:
        store.close()


def test_pagehide_setup_cannot_be_overwritten_by_an_older_write(tmp_path):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        project = store.claim_cad_document_lineage("urn:fusion:ordered", "Ordered")
        inventory = [{"id": "hf", "role": "HF", "required": True}]

        async def write(sequence, count, writer="window-a"):
            async def with_headers(scope, receive, send):
                scope = {**scope, "headers": [*scope["headers"],
                    (b"x-wg-setup-writer", writer.encode()),
                    (b"x-wg-setup-seq", str(sequence).encode())]}
                await app(scope, receive, send)
            return await _request(with_headers, "PUT", f"/api/models/{project}/setup", body={
                "inventory": inventory,
                "setup": {"geometry": {"drive_channels": [{"id": "hf", "source_ids": ["hf"]}]},
                          "options": {"engine": "metal", "frequency_range": [100, 1000], "num_frequencies": count}},
            })

        try:
            newer, raw = await write(2, 23)
            assert newer == 200
            revision = json.loads(raw)["revision_id"]
            assert (await write(1, 17))[0] == 409
            query = quote(json.dumps(inventory))
            assert (await call(app, "GET", f"/api/models/{project}/setup?inventory={query}"))["revision_id"] == revision
            assert (await write(1, 31, "window-b"))[0] == 200
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())


def test_setup_write_ordering_evicts_the_oldest_writer(tmp_path, monkeypatch):
    from server.jobs import models_api

    monkeypatch.setattr(models_api, "MAX_ACCEPTED_SETUP_WRITES", 3)

    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        project = store.claim_cad_document_lineage("urn:fusion:bounded", "Bounded")

        async def write(writer, sequence):
            async def with_headers(scope, receive, send):
                scope = {**scope, "headers": [*scope["headers"],
                    (b"x-wg-setup-writer", writer.encode()),
                    (b"x-wg-setup-seq", str(sequence).encode())]}
                await app(scope, receive, send)
            status, _ = await _request(with_headers, "PUT", f"/api/models/{project}/setup", body={
                "inventory": [{"id": "hf", "role": "HF", "required": True}],
                "setup": {"geometry": {"drive_channels": [{"id": "hf", "source_ids": ["hf"]}]},
                          "options": {"engine": "metal", "frequency_range": [100, 1000], "num_frequencies": 23}},
            })
            return status

        try:
            for writer in ("oldest", "active", "third"):
                assert await write(writer, 2) == 200
            assert await write("oldest", 3) == 200  # Refresh its LRU position.
            assert await write("fourth", 2) == 200
            assert await write("oldest", 2) == 409
            assert await write("active", 1) == 200  # Only the least recent writer was evicted.
            assert await write("fourth", 1) == 409
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_inspection_reads_retained_original_with_the_chosen_axes(tmp_path):
    from server.mesh.file_import import mesh_text
    from test_file_mesh_roles_symmetry import box_mesh

    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            original = mesh_text(box_mesh())
            registered = await call(app, "POST", "/api/models/files", {
                "filename": "box.msh", "msh_text": original, "units": "m",
                "forward": "+z", "up": "+y", "roles": {101: "HF", 102: "MF"},
            })
            frame = {"units": "m", "forward": "+z", "up": "+x"}
            uploaded = await call(app, "POST", "/api/models/files/inspect", {
                "filename": "box.msh", "msh_text": original, **frame,
            })
            retained = await call(app, "POST", "/api/models/files/inspect", {
                "model_id": registered["model"]["model_id"], **frame,
            })
            assert uploaded.pop("known_model")["model_id"] == registered["model"]["model_id"]
            uploaded.pop("previous_setup")
            uploaded.pop("previous_roles")
            assert retained == uploaded
            assert retained["lids"]["y0"]["candidate"] is True
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())


def test_inspect_recognises_identical_bytes_and_the_latest_preparation(tmp_path):
    from server.mesh.file_import import mesh_text
    from test_file_mesh_roles_symmetry import box_mesh

    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            body = {"filename": "speaker.msh", "msh_text": mesh_text(box_mesh()), "units": "m",
                    "forward": "+z", "up": "+y", "roles": {101: "LF", 102: "MF"}}
            first = await call(app, "POST", "/api/models/files", body)
            latest = await call(app, "POST", "/api/models/files", {**body, "units": "cm"})
            inspected = await call(app, "POST", "/api/models/files/inspect", {
                "filename": "renamed.msh", "msh_text": body["msh_text"],
            })
            assert inspected["known_model"] == {
                "model_id": first["model"]["model_id"], "latest_prepared_id": latest["prepared"]["ingest_id"],
            }
            assert inspected["previous_setup"]["preparation"]["units"] == "cm"
            assert len(store.list_model_ingests(first["model"]["model_id"])) == 2
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())


def test_changed_file_restores_settings_by_group_name_and_partial_roles(tmp_path):
    from dataclasses import replace
    import numpy as np
    from server.mesh.file_import import mesh_text
    from test_file_mesh_roles_symmetry import box_mesh

    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            mesh = box_mesh(descriptors=False)
            body = {"filename": "speaker.msh", "msh_text": mesh_text(mesh), "units": "cm",
                    "forward": "+z", "up": "+y", "roles": {101: "LF", 102: "MF"}}
            registered = await call(app, "POST", "/api/models/files", body)
            record = registered["prepared"]
            setup = setup_for(record)
            setup["geometry"]["drive_channels"] = [
                {"id": source["id"], "source_ids": [source["id"]], "motion": "axial"}
                for source in record["sources"]
            ]
            model_id = registered["model"]["model_id"]
            await call(app, "PUT", f"/api/models/{model_id}/setup", {"prepared_id": record["ingest_id"], "setup": setup})
            changed = replace(mesh, points=mesh.points * 1.01,
                              tags=np.where(mesh.tags == 101, 201, mesh.tags),
                              names={1: "Walls", 201: "Tweeter", 102: "Mid"})
            original = {"filename": "speaker.msh", "msh_text": mesh_text(changed)}
            inspected = await call(app, "POST", "/api/models/files/inspect", original)
            previous = inspected["previous_setup"]
            assert "known_model" not in inspected
            assert previous["filename"] == "speaker.msh"
            assert previous["updated_at"]
            assert previous["preparation"] == {
                "units": "cm", "forward": "+z", "up": "+y", "roles": {"1": "RIGID", "201": "LF", "102": "MF"},
                "domain_planes": [], "reflect_planes": None, "remove_lid_planes": [],
            }
            assert previous["setup"]["geometry"]["drive_channels"][0]["source_ids"] == ["group_201"]
            assert previous["setup"]["geometry"]["drive_channels"][0]["motion"] == "axial"
            remeshed = await call(app, "POST", "/api/models/files", {**original, **previous["preparation"]})
            await call(app, "PUT", f"/api/models/{remeshed['model']['model_id']}/setup", {
                "prepared_id": remeshed["prepared"]["ingest_id"], "setup": previous["setup"],
            })
            changed.names[102] = "New mid"
            partial = await call(app, "POST", "/api/models/files/inspect", {
                "filename": "speaker.msh", "msh_text": mesh_text(changed),
            })
            assert "previous_setup" not in partial
            assert partial["previous_roles"] == {"1": "RIGID", "201": "LF"}
            unrelated = await call(app, "POST", "/api/models/files/inspect", {
                "filename": "another.msh", "msh_text": mesh_text(changed),
            })
            assert "previous_roles" not in unrelated
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())


def test_previous_preparation_keeps_symmetry_lids_axes_and_the_most_recent_choices(tmp_path):
    from server.mesh.file_import import mesh_text
    from test_file_mesh_roles_symmetry import box_mesh

    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            text = mesh_text(box_mesh(planes=("x0", "y0"), capped=("x0",), negative=("y0",), descriptors=False))
            original = {"filename": "speaker.msh", "msh_text": text}
            settings = {"units": "m", "forward": "+z", "up": "+y", "roles": {101: "HF", 102: "MF"},
                        "domain_planes": ["x0", "y0"], "remove_lid_planes": ["x0"], "reflect_planes": None}
            await call(app, "POST", "/api/models/files", {**original, **settings})
            latest = {**settings, "units": "cm", "roles": {101: "LF", 102: "MF"}}
            await call(app, "POST", "/api/models/files", {**original, "msh_text": text + "\n", **latest})
            inspected = await call(app, "POST", "/api/models/files/inspect", {**original, "msh_text": text + "\n\n"})
            assert inspected["previous_setup"]["preparation"] == {
                **latest, "roles": {"1": "RIGID", "101": "LF", "102": "MF"},
            }
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("count", [20_000, 20_001])
def test_inspect_refuses_large_mesh_before_symmetry_work(tmp_path, monkeypatch, count):
    from dataclasses import replace
    import numpy as np
    from server.mesh.file_import import mesh_text
    from test_file_mesh_roles_symmetry import box_mesh

    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            mesh = box_mesh()
            large = replace(mesh, triangles=np.resize(mesh.triangles, (count, 3)), tags=np.resize(mesh.tags, count))
            def inspected(*args):
                assert count == 20_000, "oversized files must stop before symmetry inspection"
                return mesh.summary()
            monkeypatch.setattr("server.jobs.models_api.inspect_file_mesh", inspected)
            answer = await call(app, "POST", "/api/models/files/inspect", {
                "filename": "large.msh", "msh_text": mesh_text(large),
            }, expected=200 if count == 20_000 else 422)
            if count > 20_000:
                assert answer["error"]["code"] == "mesh_too_large"
                assert answer["error"]["message"] == (
                    "This mesh has 20,001 triangles; Waveguide Generator accepts up to 20,000. "
                    "Export a coarser mesh, or export only the half or quarter of a symmetric model."
                )
            assert store.ingest_records() == []
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())


def test_identical_reimport_uses_a_reused_preparation_instead_of_newest_creation(tmp_path, monkeypatch):
    from server.mesh.file_import import mesh_text
    from test_file_mesh_roles_symmetry import box_mesh

    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            body = {"filename": "speaker.msh", "msh_text": mesh_text(box_mesh()), "units": "m",
                    "forward": "+z", "up": "+y", "roles": {101: "HF", 102: "MF"}}
            first = await call(app, "POST", "/api/models/files", body)
            await call(app, "POST", "/api/models/files", {**body, "units": "cm"})
            monkeypatch.setattr("server.cadlink.store.utc_now", lambda: "2099-01-01T00:00:00Z")
            reused = await call(app, "POST", "/api/models/files", body)
            assert reused["prepared"]["ingest_id"] == first["prepared"]["ingest_id"]
            inspected = await call(app, "POST", "/api/models/files/inspect", {
                "filename": "speaker.msh", "msh_text": body["msh_text"],
            })
            assert inspected["known_model"]["latest_prepared_id"] == first["prepared"]["ingest_id"]
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())


def test_group_name_remap_keeps_channel_identity_and_driver_physics():
    from server.jobs.models_api import _remap_file_setup

    setup = {
        "geometry": {
            "drive_channels": [{"id": "group_101", "source_ids": ["group_101", "group_102"],
                                "patch_weights": {"group_101": 0.4, "group_102": 0.6},
                                "driver": {"driver_id": "retained-driver", "parameters": {"Re": 6.2}}, "motion": "axial"}],
            "skipped_source_ids": ["group_103"], "mesh": {"source_size_mm": {"group_101": 2, "group_102": 3}},
            "combine": {"members": ["group_101"]}, "drive_voltage_v": 4.2,
        },
        "options": {"frequencies_hz": [137, 211, 3987], "crossover": {"kept": True}},
        "driver_references": {"group_101": {"driver_id": "retained-driver"}},
    }
    mapped = _remap_file_setup(setup, {"group_101": "group_201", "group_102": "group_202", "group_103": "group_203"})
    expected = json.loads(json.dumps(setup))
    expected["geometry"]["drive_channels"][0].update({"source_ids": ["group_201", "group_202"],
                                                       "patch_weights": {"group_201": 0.4, "group_202": 0.6}})
    expected["geometry"]["skipped_source_ids"] = ["group_203"]
    expected["geometry"]["mesh"]["source_size_mm"] = {"group_201": 2, "group_202": 3}
    assert mapped == expected
    assert setup["geometry"]["drive_channels"][0]["source_ids"] == ["group_101", "group_102"]


def test_inspection_keeps_settings_available_to_repair_a_missing_prepared_mesh(tmp_path):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            body = registration().model_dump()
            record = register_mesh(store, tmp_path, registration())
            setup = setup_for(record)
            model_id = "mesh-file:" + record["artifact_sha256"]
            await call(app, "PUT", f"/api/models/{model_id}/setup", {"prepared_id": record["ingest_id"], "setup": setup})
            Path(record["mesh_store_path"]).unlink()
            inspected = await call(app, "POST", "/api/models/files/inspect", {
                "filename": body["filename"], "msh_text": body["msh_text"],
            })
            assert inspected["known_model"]["model_id"] == model_id
            read = await call(app, "GET", f"/api/models/{model_id}?prepared_id={record['ingest_id']}", expected=409)
            assert read["error"]["code"] == "prepared_unavailable"
            memory = inspected["previous_setup"]
            assert memory["setup"] == json.loads(setup_content(validate_setup(setup)))
            repaired = await call(app, "POST", "/api/models/files", {
                "filename": body["filename"], "msh_text": body["msh_text"], **memory["preparation"],
            })
            assert repaired["prepared"]["ingest_id"] != record["ingest_id"]
            assert repaired["prepared"]["model_id"] == model_id
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("planes", [(), ("x0",), ("y0",), ("x0", "y0")])
def test_retained_mesh_inspection_keeps_pre_limit_models_editable(tmp_path, monkeypatch, planes):
    from dataclasses import replace
    import numpy as np
    from server.mesh.file_import import mesh_text
    from test_file_mesh_roles_symmetry import box_mesh

    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        try:
            mesh = box_mesh(planes=planes)
            count = 20_001
            large = replace(mesh, triangles=np.resize(mesh.triangles, (count, 3)), tags=np.resize(mesh.tags, count))
            original = mesh_text(large)
            # The retained original predates the limit. Inspection still uses the
            # normal parser; mock geometric evidence to avoid testing repeated faces.
            monkeypatch.setattr("server.jobs.models_api.read_original", lambda *_: ("old.msh", original))
            inspected = []
            def evidence(parsed, *_):
                inspected.append(parsed)
                return {**parsed.summary(), "detected_planes": list(planes)}
            monkeypatch.setattr("server.jobs.models_api.inspect_file_mesh", evidence)
            retained = await call(app, "POST", "/api/models/files/inspect", {
                "model_id": "mesh-file:old", "units": "m", "forward": "+z", "up": "+y",
            })
            assert retained["triangle_count"] == count
            assert retained["detected_planes"] == list(planes)
            fresh = await call(app, "POST", "/api/models/files/inspect", {
                "filename": "old.msh", "msh_text": original,
            }, expected=422)
            assert fresh["error"]["code"] == "mesh_too_large"
            assert len(inspected) == 1
        finally:
            await runtime.shutdown()
            store.close()
    asyncio.run(scenario())
