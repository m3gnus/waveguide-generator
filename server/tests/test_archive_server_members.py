"""Run archive members written by server reference, not relayed through the browser.

``GET /api/jobs/{id}/archive-snapshot?members=held`` keeps the mesh, pressure
bases and radiation matrix on the server, and write-export names them as
``member_source`` entries. The archive folder must be byte for byte the one the
inline (base64) snapshot writes when the browser uploads those bytes back.
"""

from __future__ import annotations

import asyncio
import base64
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
    return {
        str(path.relative_to(root)): path.read_bytes()
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


def test_held_snapshot_holds_exactly_the_inline_bytes(monkeypatch):
    from server.jobs.runtime import core

    snapshot, motions = _snapshot_fixture()
    monkeypatch.setattr(core, "_request_motions", lambda _job: motions)
    holds = ArchiveSnapshotHolds()
    inline = core._archive_snapshot_presentation("run", snapshot)
    held = core._held_archive_snapshot_presentation("run", snapshot, holds)

    assert held["results"] == inline["results"]
    assert held["results_sha256"] == inline["results_sha256"]
    mesh = holds.lookup(held["hold"], "run", held["mesh_artifact"]["member"])
    assert mesh == inline["mesh_artifact"].encode("utf-8")
    assert held["mesh_artifact"]["bytes"] == len(mesh)
    assert [base["channel_id"] for base in held["pressure_bases"]] == [
        base["channel_id"] for base in inline["pressure_bases"]
    ] == ["normal", "axial", "request"]
    for held_base, inline_base in zip(held["pressure_bases"], inline["pressure_bases"], strict=True):
        content = holds.lookup(held["hold"], "run", held_base["member"])
        assert content == base64.b64decode(inline_base["content_base64"])
        assert held_base["bytes"] == len(content)
    radiation = holds.lookup(held["hold"], "run", held["radiation_impedance"]["member"])
    assert radiation == base64.b64decode(inline["radiation_impedance"]["content_base64"])
    assert held["radiation_impedance"]["bytes"] == len(radiation)
    assert held["radiation_impedance"]["presentation"] == inline["radiation_impedance"]["presentation"]
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
    assert core._archive_snapshot_presentation("run", snapshot)["mesh_artifact"] == ""
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


def test_the_client_mirrors_the_write_export_byte_cap():
    # frontend/src/api/workspace.ts plans archive requests with this number.
    from server.workspace.api import MAX_EXPORT_BYTES

    source = (Path(__file__).resolve().parents[2] / "frontend/src/api/workspace.ts").read_text()
    assert f"WRITE_EXPORT_MAX_BYTES = {MAX_EXPORT_BYTES // (1024 * 1024)} * 1024 * 1024;" in source


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

    # Today's path: the base64 snapshot's bytes, uploaded back.
    inline = await client.get(f"/api/jobs/{job_id}/archive-snapshot")
    assert inline.status_code == 200, inline.text
    inline = inline.json()
    uploads = [("mesh.msh", inline["mesh_artifact"].encode("utf-8"))]
    uploads += [(f"{base['channel_id']}_pressure_basis.npz", base64.b64decode(base["content_base64"]))
                for base in inline["pressure_bases"]]
    uploads.append(("radiation.npz", base64.b64decode(inline["radiation_impedance"]["content_base64"])))
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
    assert held["results"] == inline["results"]
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
