"""Run archive members written by server reference, not relayed through the browser.

``GET /api/jobs/{id}/archive-snapshot?members=held`` keeps the mesh, pressure
bases and radiation matrix on the server, and write-export names them as
``member_source`` entries. The archive folder must be byte for byte the one the
retired inline (base64) snapshot wrote when the browser uploaded those bytes
back: the stored mesh text as UTF-8, each exported pressure basis, and the
stored radiation matrix.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import json as _json
from pathlib import Path
import shutil
import time

import pytest

from server.workspace.archive_holds import ArchiveSnapshotHolds, HoldTooLarge, HoldUnavailable


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` by its ``/``-separated relative path, on every OS."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _snapshot_fixture():
    from server.tests.test_pressure_basis import multichannel_basis
    from server.tests.test_radiation_impedance_presentation import artifact_bytes

    bases, results, motions = multichannel_basis()
    text = json.dumps(results)
    return {
        "job": {"status": "complete"}, "results_text": text,
        "results_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "mesh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n", "channel_bases": bases,
        "radiation_impedance": artifact_bytes(),
    }, motions


def _npz_digest(content: bytes) -> str:
    """sha256 over each array's name, dtype, shape and bytes, independent of zlib."""

    import io

    import numpy as np

    digest = hashlib.sha256()
    with np.load(io.BytesIO(content), allow_pickle=False) as archive:
        for name in sorted(archive.files):
            array = np.ascontiguousarray(archive[name])
            digest.update(f"{name}|{array.dtype.str}|{array.shape}|".encode())
            digest.update(array.tobytes())
    return digest.hexdigest()


def test_held_members_match_digests_frozen_from_the_retired_inline_form(monkeypatch):
    """Not circular: ``fixtures/archive-inline-member-digests.json`` was produced
    at WG dev 96e8e33c7 by the retired ``_archive_snapshot_presentation`` on this
    same fixture, base64-decoding each member it sent. The npz members are
    compared by their arrays, so a platform's zlib cannot move the digest."""

    from server.jobs.runtime import core

    frozen = json.loads((Path(__file__).parent / "fixtures" / "archive-inline-member-digests.json").read_text())
    snapshot, motions = _snapshot_fixture()
    monkeypatch.setattr(core, "_request_motions", lambda _job: motions)
    holds = ArchiveSnapshotHolds()
    held = core._held_archive_snapshot_presentation("run", snapshot, holds)
    members = [held["mesh_artifact"]["member"], *(base["member"] for base in held["pressure_bases"]),
               held["radiation_impedance"]["member"]]
    assert sorted(members) == sorted(frozen)
    for member in members:
        content = holds.lookup(held["hold"], "run", member)
        digest = hashlib.sha256(content).hexdigest() if member == "mesh" else _npz_digest(content)
        assert digest == frozen[member], member


def test_held_snapshot_holds_exactly_the_bytes_the_inline_form_carried(monkeypatch):
    from server.jobs.radiation_impedance import radiation_impedance_presentation
    from server.jobs.runtime import core
    from server.solver import pressure_basis

    snapshot, motions = _snapshot_fixture()
    monkeypatch.setattr(core, "_request_motions", lambda _job: motions)
    holds = ArchiveSnapshotHolds()
    held = core._held_archive_snapshot_presentation("run", snapshot, holds)

    # What the retired inline form sent, as base64, for the browser to upload.
    assert held["results"] == json.loads(snapshot["results_text"])
    assert held["results_sha256"] == snapshot["results_sha256"]
    mesh = holds.lookup(held["hold"], "run", held["mesh_artifact"]["member"])
    assert mesh == snapshot["mesh_text"].encode("utf-8")
    assert held["mesh_artifact"]["bytes"] == len(mesh)
    assert [base["channel_id"] for base in held["pressure_bases"]] == ["normal", "axial", "request"]
    for base in held["pressure_bases"]:
        content = holds.lookup(held["hold"], "run", base["member"])
        assert content == pressure_basis.export_pressure_basis(
            snapshot["channel_bases"], held["results"], base["channel_id"], motions,
        ).content
        assert base["bytes"] == len(content)
    radiation = holds.lookup(held["hold"], "run", held["radiation_impedance"]["member"])
    assert radiation == snapshot["radiation_impedance"]
    assert held["radiation_impedance"]["bytes"] == len(radiation)
    assert held["radiation_impedance"]["presentation"] == radiation_impedance_presentation(radiation)
    # A hold belongs to its run only, and names only the members it copied.
    with pytest.raises(HoldUnavailable):
        holds.lookup(held["hold"], "other-run", "mesh")
    with pytest.raises(KeyError):
        holds.lookup(held["hold"], "run", "nonsense")
    assert holds.release(held["hold"], "run") is True
    assert holds.release(held["hold"], "run") is False
    with pytest.raises(HoldUnavailable):
        holds.lookup(held["hold"], "run", "mesh")


def test_an_empty_stored_mesh_is_no_mesh(monkeypatch):
    from server.jobs.runtime import core

    snapshot, motions = _snapshot_fixture()
    monkeypatch.setattr(core, "_request_motions", lambda _job: motions)
    snapshot = {**snapshot, "mesh_text": ""}
    holds = ArchiveSnapshotHolds()
    held = core._held_archive_snapshot_presentation("run", snapshot, holds)
    assert held["mesh_artifact"] is None
    with pytest.raises(KeyError):
        holds.lookup(held["hold"], "run", "mesh")


class _Timers:
    """Scheduled expiries, fired by the test instead of a clock."""

    def __init__(self):
        self.pending = []

    def __call__(self, seconds, callback):
        entry = {"seconds": seconds, "callback": callback, "cancelled": False}
        self.pending.append(entry)
        return type("Timer", (), {"cancel": lambda _self: entry.update(cancelled=True)})()

    def fire(self, index):
        entry = self.pending[index]
        if not entry["cancelled"]:
            entry["callback"]()


def test_holds_expire_and_the_oldest_go_first():
    now = [0.0]
    timers = _Timers()
    holds = ArchiveSnapshotHolds(ttl_seconds=10, max_holds=2, max_bytes=10, clock=lambda: now[0], schedule=timers)
    first = holds.hold("a", {"mesh": b"12345"})
    second = holds.hold("b", {"mesh": b"12345"})
    assert holds.lookup(first, "a", "mesh") == b"12345"
    third = holds.hold("c", {"mesh": b"1"})
    with pytest.raises(HoldUnavailable):  # over two holds, and over ten bytes
        holds.lookup(first, "a", "mesh")
    assert timers.pending[0]["cancelled"] is True
    assert holds.lookup(second, "b", "mesh") == b"12345"
    now[0] = 10.0
    with pytest.raises(HoldUnavailable):
        holds.lookup(second, "b", "mesh")
    with pytest.raises(HoldUnavailable):
        holds.lookup(third, "c", "mesh")


def test_a_hold_expires_on_its_timer_with_nothing_else_touching_the_store():
    timers = _Timers()
    holds = ArchiveSnapshotHolds(ttl_seconds=10, schedule=timers)
    holds.hold("a", {"mesh": b"bytes"})
    assert len(holds) == 1 and timers.pending[0]["seconds"] == 10
    timers.fire(0)
    assert len(holds) == 0


def test_a_released_hold_cancels_its_timer():
    timers = _Timers()
    holds = ArchiveSnapshotHolds(schedule=timers)
    token = holds.hold("a", {"mesh": b"bytes"})
    assert holds.release(token, "a") and timers.pending[0]["cancelled"]


def test_the_default_timer_really_expires(monkeypatch):
    holds = ArchiveSnapshotHolds(ttl_seconds=0.05)
    holds.hold("a", {"mesh": b"bytes"})
    deadline = time.monotonic() + 5
    while len(holds) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(holds) == 0


def test_one_hold_over_the_byte_cap_is_refused():
    holds = ArchiveSnapshotHolds(max_bytes=4, schedule=_Timers())
    with pytest.raises(HoldTooLarge, match="Use Export on the run"):
        holds.hold("a", {"mesh": b"12345"})
    assert len(holds) == 0
    gib = 1024 ** 3
    oversize = ArchiveSnapshotHolds(schedule=_Timers())
    with pytest.raises(HoldTooLarge) as refused:
        oversize.hold("a", {"mesh": _Sized(gib + gib // 2)})
    assert str(refused.value) == (
        "This run’s mesh and pressure data come to 1.5 GB, more than the 1.0 GB WG can "
        "archive, so WG cannot archive it. Use Export on the run to save the files you need."
    )


class _Sized(bytes):
    """A member that reports a size without allocating it."""

    def __new__(cls, size):
        member = super().__new__(cls, b"")
        member.size = size
        return member

    def __len__(self):
        return self.size


@dataclass
class _Response:
    status_code: int
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content)


def _multipart(data: dict, files) -> tuple[bytes, bytes]:
    boundary = b"wg-archive-boundary"
    parts = []

    def part(name: str, value: bytes, filename: str | None = None, content_type: str | None = None):
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        head = disposition.encode() + b"\r\n"
        if content_type:
            head += f"Content-Type: {content_type}\r\n".encode()
        parts.append(b"--" + boundary + b"\r\n" + head + b"\r\n" + value + b"\r\n")

    for name, value in data.items():
        for item in value if isinstance(value, list) else [value]:
            part(name, item.encode("utf-8"))
    for name, (filename, content, content_type) in files:
        part(name, content, filename, content_type)
    return b"".join(parts) + b"--" + boundary + b"--\r\n", b"multipart/form-data; boundary=" + boundary


class _Client:
    """Raw ASGI requests on the event loop the jobs runtime lives on."""

    def __init__(self, app) -> None:
        self.app = app

    async def _call(self, method: str, path: str, *, query: str = "", body: bytes = b"",
                    content_type: bytes | None = None) -> _Response:
        sent: list[dict] = []
        delivered = False

        async def receive() -> dict:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            # As in test_jobs_api: block, so a StreamingResponse is not cancelled.
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(message: dict) -> None:
            sent.append(message)

        headers = [(b"host", b"127.0.0.1:3100")]
        if content_type is not None:
            headers.append((b"content-type", content_type))
        await self.app({
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode("ascii"), "query_string": query.encode("ascii"),
            "root_path": "", "headers": headers, "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 3100),
        }, receive, send)
        start = next(item for item in sent if item["type"] == "http.response.start")
        return _Response(start["status"], b"".join(
            item.get("body", b"") for item in sent if item["type"] == "http.response.body"))

    async def get(self, path: str, params: dict | None = None) -> _Response:
        query = "&".join(f"{key}={value}" for key, value in (params or {}).items())
        return await self._call("GET", path, query=query)

    async def delete(self, path: str) -> _Response:
        return await self._call("DELETE", path)

    async def post(self, path: str, *, json: dict | None = None, data: dict | None = None,
                   files=None) -> _Response:
        if json is not None:
            return await self._call("POST", path, body=_json.dumps(json).encode(),
                                    content_type=b"application/json")
        body, content_type = _multipart(data or {}, files or [])
        return await self._call("POST", path, body=body, content_type=content_type)


def _with_archived_run(tmp_path, monkeypatch, scenario):
    """Run ``scenario(client, job_id, workspace, job)`` against a finished run
    that kept a mesh, three pressure bases and a radiation matrix."""

    from server.app import create_app
    from server.jobs.runtime import core
    from server.tests.test_jobs_api import _solve_body
    from server.tests.test_pressure_basis import multichannel_basis
    from server.tests.test_radiation_impedance_presentation import artifact_bytes

    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    workspace = tmp_path / "runs"
    workspace.mkdir()

    async def run() -> None:
        app = create_app(data_dir=tmp_path / "data", workspace_dir=workspace)
        runtime = app.state.jobs_runtime
        try:
            client = _Client(app)
            accepted = await client.post("/api/solve", json=_solve_body())
            assert accepted.status_code == 200, accepted.text
            job_id = accepted.json()["job_id"]
            await runtime.wait_idle()
            job = (await client.get(f"/api/status/{job_id}")).json()
            assert job["status"] == "complete", job
            bases, results, motions = multichannel_basis()
            # The dryrun request names no drive channels; the basis does.
            monkeypatch.setattr(core, "_request_motions", lambda _job: motions)
            runtime.store.store_results(job_id, results)
            runtime.store.store_channel_bases(job_id, bases)
            runtime.store.store_radiation_impedance(job_id, artifact_bytes())
            assert runtime.store.get_mesh_artifact(job_id)
            await scenario(client, job_id, workspace, job)
        finally:
            await runtime.shutdown()

    asyncio.run(run())


async def _write(client, data, files=()):
    return await client.post("/api/workspace/write-export", data=data, files=list(files) or None)


def test_archive_written_by_reference_is_byte_identical_to_the_uploaded_archive(tmp_path, monkeypatch):
    _with_archived_run(tmp_path, monkeypatch, _byte_identical_archive)


async def _byte_identical_archive(client, job_id, workspace, job):
    run_folder = f"{job['archive_folder']}/{job['archive_run']}"

    # The retired path: the bytes the inline snapshot carried, uploaded back.
    uploads = _inline_bytes(client, job_id)
    uploads.append(("run.json", b'{"built": "in the browser"}\n'))
    uploaded = await _write(client, {
        "subdirectory": run_folder, "existing": "confirm", "job_id": job_id,
        "relative_path": [name for name, _ in uploads],
    }, [("file", (name, content, "application/octet-stream")) for name, content in uploads])
    assert uploaded.status_code == 200, uploaded.text
    expected = _tree(workspace)
    expected_files = uploaded.json()["files"]
    assert len(expected) == len(uploads)
    for path in list(workspace.iterdir()):
        shutil.rmtree(path)

    # By reference: the same members, in the same order, only run.json uploaded.
    held = await client.get(f"/api/jobs/{job_id}/archive-snapshot", params={"members": "held"})
    assert held.status_code == 200, held.text
    held = held.json()
    assert "content_base64" not in json.dumps(held)
    hold = held["hold"]
    sources = [json.dumps({"hold": hold, "member": held["mesh_artifact"]["member"]})]
    sources += [json.dumps({"hold": hold, "member": base["member"]}) for base in held["pressure_bases"]]
    sources.append(json.dumps({"hold": hold, "member": held["radiation_impedance"]["member"]}))
    sources.append("upload")
    referenced = await _write(client, {
        "subdirectory": run_folder, "existing": "confirm", "job_id": job_id,
        "relative_path": [name for name, _ in uploads], "member_source": sources,
    }, [("file", ("run.json", uploads[-1][1], "application/json"))])
    assert referenced.status_code == 200, referenced.text
    assert referenced.json()["files"] == expected_files
    assert _tree(workspace) == expected

    # A retry is the byte-identical no-op it always was.
    again = await _write(client, {
        "subdirectory": run_folder, "existing": "confirm", "job_id": job_id,
        "relative_path": [name for name, _ in uploads], "member_source": sources,
    }, [("file", ("run.json", uploads[-1][1], "application/json"))])
    assert again.status_code == 200 and again.json()["replaced"] == []
    assert _tree(workspace) == expected


def _inline_bytes(client, job_id):
    """The members the retired inline snapshot carried for this run, in its order."""

    from server.jobs.runtime import core
    from server.solver import pressure_basis

    store = client.app.state.jobs_runtime.store
    snapshot = store.get_archive_snapshot(job_id)
    results = json.loads(snapshot["results_text"])
    motions = core._request_motions(snapshot["job"])
    uploads = [("mesh.msh", snapshot["mesh_text"].encode("utf-8"))]
    uploads += [(f"{base.channel_id}_pressure_basis.npz", base.content)
                for base in pressure_basis.export_pressure_bases(snapshot["channel_bases"], results, request_motions=motions)]
    uploads.append(("radiation.npz", snapshot["radiation_impedance"]))
    return uploads


def test_held_members_do_not_count_toward_the_upload_cap(tmp_path, monkeypatch):
    async def scenario(client, job_id, workspace, job):
        from server.workspace import api as workspace_api

        run_folder = f"{job['archive_folder']}/{job['archive_run']}"
        uploads = _inline_bytes(client, job_id) + [("run.json", b"{}\n")]
        # A cap smaller than any held member, but not than run.json.
        monkeypatch.setattr(workspace_api, "MAX_EXPORT_BYTES", 64)
        assert all(len(content) > 64 for _name, content in uploads[:-1])
        everything = await _write(client, {
            "subdirectory": run_folder, "existing": "confirm", "job_id": job_id,
            "relative_path": [name for name, _ in uploads],
        }, [("file", (name, content, "application/octet-stream")) for name, content in uploads])
        assert everything.status_code == 422 and "size limit" in everything.text
        assert _tree(workspace) == {}
        held = (await client.get(f"/api/jobs/{job_id}/archive-snapshot")).json()
        sources = [json.dumps({"hold": held["hold"], "member": held["mesh_artifact"]["member"]})]
        sources += [json.dumps({"hold": held["hold"], "member": base["member"]}) for base in held["pressure_bases"]]
        sources += [json.dumps({"hold": held["hold"], "member": held["radiation_impedance"]["member"]}), "upload"]
        referenced = await _write(client, {
            "subdirectory": run_folder, "existing": "confirm", "job_id": job_id,
            "relative_path": [name for name, _ in uploads], "member_source": sources,
        }, [("file", ("run.json", b"{}\n", "application/json"))])
        assert referenced.status_code == 200, referenced.text
        assert _tree(workspace) == {f"{run_folder}/{name}": content for name, content in uploads}

    _with_archived_run(tmp_path, monkeypatch, scenario)


def test_the_retired_inline_snapshot_form_is_refused(tmp_path, monkeypatch):
    async def scenario(client, job_id, workspace, job):
        refused = await client.get(f"/api/jobs/{job_id}/archive-snapshot", params={"members": "inline"})
        assert refused.status_code == 422
        assert refused.json()["detail"] == (
            "This WG window is older than its server and asked for a retired archive form. "
            "Reload or reopen WG, or update it, then archive again."
        )
        default = await client.get(f"/api/jobs/{job_id}/archive-snapshot")
        assert default.status_code == 200 and "hold" in default.json()

    _with_archived_run(tmp_path, monkeypatch, scenario)


@pytest.mark.parametrize("named", ["held", "mesh"])
def test_a_server_member_named_100_times_is_refused_without_writing(tmp_path, monkeypatch, named):
    async def scenario(client, job_id, workspace, job):
        run_folder = f"{job['archive_folder']}/{job['archive_run']}"
        hold = (await client.get(f"/api/jobs/{job_id}/archive-snapshot")).json()["hold"]
        source = {"hold": hold, "member": "radiation_impedance"} if named == "held" else {"member": "mesh"}
        response = await _write(client, {
            "subdirectory": run_folder, "existing": "merge_identical", "job_id": job_id,
            "relative_path": [f"copy-{index}.bin" for index in range(100)],
            "member_source": [json.dumps(source)] * 100,
        })
        assert response.status_code == 422
        assert response.json()["detail"] == (
            f"Each server-held file can be written once per request; {source['member']} "
            "is named more than once."
        )
        assert _tree(workspace) == {}
        row = client.app.state.jobs_runtime.store.get_job_row(job_id)
        assert "archived_path" not in (row.get("task_metadata") or {})

    _with_archived_run(tmp_path, monkeypatch, scenario)


def test_held_files_past_the_hold_limit_in_one_request_are_refused(tmp_path, monkeypatch):
    async def scenario(client, job_id, workspace, job):
        from server.workspace import archive_holds

        run_folder = f"{job['archive_folder']}/{job['archive_run']}"
        first = (await client.get(f"/api/jobs/{job_id}/archive-snapshot")).json()
        second = (await client.get(f"/api/jobs/{job_id}/archive-snapshot")).json()
        size = first["radiation_impedance"]["bytes"]
        # Two holds' copies of one file: each fits the limit, together they do not.
        monkeypatch.setattr(archive_holds, "MAX_HELD_BYTES", size * 2 - 1)
        response = await _write(client, {
            "subdirectory": run_folder, "existing": "merge_identical", "job_id": job_id,
            "relative_path": ["one.npz", "two.npz"],
            "member_source": [json.dumps({"hold": snapshot["hold"], "member": "radiation_impedance"})
                              for snapshot in (first, second)],
        })
        assert response.status_code == 422
        assert "exceed the" in response.json()["detail"] and "separate requests" in response.json()["detail"]
        assert _tree(workspace) == {}

    _with_archived_run(tmp_path, monkeypatch, scenario)


def test_retained_mesh_is_written_by_reference_like_the_download(tmp_path, monkeypatch):
    _with_archived_run(tmp_path, monkeypatch, _retained_mesh)


async def _retained_mesh(client, job_id, workspace, job):
    run_folder = f"{job['archive_folder']}/{job['archive_run']}"
    downloaded = await client.get(f"/api/mesh-artifact/{job_id}")
    assert downloaded.status_code == 200
    written = await _write(client, {
        "subdirectory": run_folder, "existing": "merge_identical", "job_id": job_id,
        "relative_path": "run.msh", "member_source": json.dumps({"member": "mesh"}),
    })
    assert written.status_code == 200, written.text
    assert Path(written.json()["files"][0]).read_bytes() == downloaded.content


@pytest.mark.parametrize("case", [
    "expired", "released", "other-run", "unknown-held-member", "no-job", "traversal", "unknown-mesh-name",
    "empty-mesh", "count",
])
def test_server_members_are_refused_safely(tmp_path, monkeypatch, case):
    async def scenario(client, job_id, workspace, job):
        await _refused(client, job_id, workspace, job, case)

    _with_archived_run(tmp_path, monkeypatch, scenario)


async def _refused(client, job_id, workspace, job, case):
    run_folder = f"{job['archive_folder']}/{job['archive_run']}"
    hold = (await client.get(f"/api/jobs/{job_id}/archive-snapshot", params={"members": "held"})).json()["hold"]
    data = {
        "subdirectory": run_folder, "existing": "merge_identical", "job_id": job_id,
        "relative_path": "run.msh", "member_source": json.dumps({"hold": hold, "member": "mesh"}),
    }
    if case == "expired":
        data["member_source"] = json.dumps({"hold": "not-a-hold", "member": "mesh"})
    elif case == "released":
        released = await client.delete(f"/api/jobs/{job_id}/archive-holds/{hold}")
        assert released.status_code == 200 and released.json() == {"released": True}
        again = await client.delete(f"/api/jobs/{job_id}/archive-holds/{hold}")
        assert again.status_code == 200 and again.json() == {"released": False}
    elif case == "unknown-held-member":
        data["member_source"] = json.dumps({"hold": hold, "member": "nonsense"})
    elif case == "empty-mesh":
        client.app.state.jobs_runtime.store.store_mesh_artifact(job_id, "")
        data["member_source"] = json.dumps({"member": "mesh"})
    elif case == "other-run":
        other = client.app.state.jobs_runtime.archive_holds.hold("another-run", {"mesh": b"x"})
        data["member_source"] = json.dumps({"hold": other, "member": "mesh"})
    elif case == "no-job":
        data.pop("job_id")
        data["subdirectory"] = "loose"
    elif case == "traversal":
        data["relative_path"] = "../escape.msh"
    elif case == "unknown-mesh-name":
        data["member_source"] = json.dumps({"member": "pressure_basis/normal"})
    elif case == "count":
        data["relative_path"] = ["one.msh", "two.msh"]
    response = await _write(client, data)
    if case in {"expired", "released", "other-run", "empty-mesh"}:
        assert response.status_code == 409
        assert response.json()["code"] == "server_member_unavailable"
    else:
        assert response.status_code == 422, response.text
    assert _tree(workspace) == {}
    if case != "traversal":
        # Refused before the run's folder was claimed: nothing recorded.
        row = client.app.state.jobs_runtime.store.get_job_row(job_id)
        assert "archived_path" not in (row.get("task_metadata") or {})
