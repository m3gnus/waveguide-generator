"""A replay encodes one verified input generation, without reopening files."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from server.jobs import mesh_replay
from test_jobs_api import _complete_job
from test_mesh_import import register_mesh, registration, request
from test_mesh_replay import app_for
from test_operation_stages import _http


@pytest.mark.parametrize("change", ["none", "replace", "remove"])
def test_replay_captures_each_input_once_and_keeps_exact_package_bytes(tmp_path, monkeypatch, change):
    async def scenario():
        app, store, runtime, _ = app_for(tmp_path)
        try:
            record = register_mesh(store, tmp_path, registration())
            runtime.store.create_job(_complete_job("export", request=request(record), mesh_discarded=False))
            expected = mesh_replay.build_mesh_replay(runtime, store, "export")
            snapshot = Mock(wraps=runtime.store.get_archive_snapshot)
            monkeypatch.setattr(runtime.store, "get_archive_snapshot", snapshot)
            solver = Path(record["mesh_store_path"])
            original = solver.parent / "original.msh"
            reads = {solver: 0, original: 0}
            path_open = Path.open

            def opened(path, mode="r", *args, **kwargs):
                if path in reads and "r" in mode:
                    reads[path] += 1
                return path_open(path, mode, *args, **kwargs)

            monkeypatch.setattr(Path, "open", opened)
            prepare = mesh_replay.prepare_registration

            async def prepared(body, cancel_cb=None, **options):
                result = await prepare(body, cancel_cb, **options)
                for path in reads:
                    if change == "replace":
                        path.write_text("a different generation")
                    elif change == "remove":
                        path.unlink()
                return result

            monkeypatch.setattr(mesh_replay, "prepare_registration", prepared)
            status, _, payload = await _http(app, "GET", "/api/jobs/export/mesh-replay")
            assert status == 200, payload
            assert payload == json.dumps(expected, separators=(",", ":"), ensure_ascii=False).encode()
            assert snapshot.call_count == 1
            assert reads == {solver: 1, original: 1}
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["original", "solver"])
def test_replay_still_refuses_initial_input_tampering(tmp_path, target):
    async def scenario():
        app, store, runtime, _ = app_for(tmp_path)
        try:
            record = register_mesh(store, tmp_path, registration())
            runtime.store.create_job(_complete_job("export", request=request(record), mesh_discarded=False))
            path = Path(record["mesh_store_path"])
            if target == "original":
                path = path.parent / "original.msh"
            path.write_text("changed")
            status, _, payload = await _http(app, "GET", "/api/jobs/export/mesh-replay")
            assert status == 409
            assert "changed" in json.loads(payload)["detail"] or "digest mismatch" in json.loads(payload)["detail"]
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())
