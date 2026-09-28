from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from server import app as app_module
from server.app import VERSION, create_app
from server.engines.registry import detect_engines
from server.jobs.store import JobStore


ROOT = Path(__file__).resolve().parents[2]
PINS = ROOT / "pins.json"
GENERATOR = ROOT / "scripts" / "gen_requirements.py"
ORACLE = ROOT / "spike" / "oracle" / "v1-manifest.json"


@dataclass
class Response:
    status_code: int
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.body)


class TestClient:
    """Dependency-free HTTP-sized ASGI harness for this test module."""

    __test__ = False

    def __init__(self, app) -> None:
        self.app = app

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> Response:
        return asyncio.run(
            self.request_async(method, path, headers=headers, body=body)
        )

    async def request_async(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> Response:
        """One request on the caller's event loop, for app state bound to it."""

        sent: list[dict[str, Any]] = []
        delivered = False

        async def receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        request_headers = {"host": "127.0.0.1:3100"}
        request_headers.update(
            {name.lower(): value for name, value in (headers or {}).items()}
        )
        raw_headers = [
            (name.encode("latin-1"), value.encode("latin-1"))
            for name, value in request_headers.items()
        ]
        await self.app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": b"",
                "root_path": "",
                "headers": raw_headers,
                "client": ("127.0.0.1", 12345),
                "server": ("127.0.0.1", 3100),
            },
            receive,
            send,
        )
        start = next(message for message in sent if message["type"] == "http.response.start")
        response_body = b"".join(
            message.get("body", b"")
            for message in sent
            if message["type"] == "http.response.body"
        )
        return Response(status_code=start["status"], body=response_body)

    def get(self, path: str, headers: dict[str, str] | None = None) -> Response:
        return self.request("GET", path, headers=headers)

    def post(
        self,
        path: str,
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> Response:
        return self.request("POST", path, headers=headers, body=body)


def test_health_and_placeholder_shell(tmp_path: Path) -> None:
    shared_version = json.loads((ROOT / "shared" / "version.json").read_text(encoding="utf-8"))["version"]
    assert VERSION == shared_version
    client = TestClient(create_app(data_dir=tmp_path))
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["version"] == VERSION
    assert health.json()["uptime"] >= 0
    assert health.json()["data_dir"] == str(tmp_path.absolute())

    shell = client.get("/")
    assert shell.status_code == 200
    # The dist/ content evolves (placeholder → real shell build); assert it serves
    # a non-empty HTML document rather than pinning wording.
    assert "<!doctype html>" in shell.text.lower()
    assert len(shell.text) > 100


def test_windows_acl_repair_status_is_published_after_startup(tmp_path: Path) -> None:
    expected = {
        "platform": "windows",
        "roots": [{
            "scope": "appData", "source": "current", "repaired": 3,
            "remaining": 0, "unreadable": 0, "failed": 0,
            "truncated": False, "administratorMayHelp": 0,
        }],
    }
    fake_os = SimpleNamespace(name="nt", environ=app_module.os.environ)
    with (
        patch.object(app_module, "os", fake_os),
        patch.object(app_module, "repair_legacy_acls", return_value={}) as repair,
        patch.object(
            app_module, "legacy_acl_repair_feedback", return_value=expected
        ) as feedback,
    ):
        application = create_app(data_dir=tmp_path)
        startup = next(
            handler for handler in application.router.on_startup
            if handler.__name__ == "_repair_legacy_acls"
        )
        asyncio.run(startup())

    response = TestClient(application).get("/api/acl-repair/status")
    assert response.status_code == 200
    assert response.json() == expected
    repair.assert_called_once()
    feedback.assert_called_once()


def test_explicit_workspace_default_is_separate_from_internal_data(
    tmp_path: Path, monkeypatch
) -> None:
    # A developer checkout may legitimately contain historical exports in its
    # ignored output/ directory. Keep this default-path test independent from
    # that migration input.
    monkeypatch.setattr(app_module, "LEGACY_WORKSPACE_DIR", tmp_path / "legacy-output")
    data_dir = tmp_path / "app-data"
    workspace_dir = tmp_path / "waveguide-generator" / "output"
    client = TestClient(create_app(data_dir=data_dir, workspace_dir=workspace_dir))

    response = client.get("/api/workspace/path")

    assert response.status_code == 200
    assert response.json() == {"path": str(workspace_dir.resolve()), "selected": False}
    assert workspace_dir.is_dir()
    assert not (data_dir / "workspace").exists()


def test_capabilities_and_dryrun_guard(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("WG2_ENABLE_DRYRUN", raising=False)
    client = TestClient(create_app(data_dir=tmp_path))
    engines = client.get("/api/capabilities").json()["engines"]
    # Real detection (batch Q) probes THIS machine, so availability values are
    # environment-dependent; assert the report contract, not the environment.
    names = [engine["name"] for engine in engines]
    assert {"metal", "bempp"}.issubset(set(names))
    # BEAT is advertised per execution backend, not as one entry.
    assert {"beat-cuda", "beat-rocm", "beat-metal", "beat-cpu"}.issubset(set(names))
    assert "beat" not in names
    assert "axisym" not in names
    assert "circsym" not in names
    assert "dryrun" not in names
    assert all(
        set(engine)
        == {
            "name",
            "label",
            "available",
            "reason",
            "version",
            "fast_paths",
            "formulations",
            "mountings",
            "ground_plane_axes",
            "ground_plane_composes_with_symmetry",
            "geometry_sources",
            "imported_features",
            "symmetry_domains",
            "field_traces",
            "di_sphere",
            "cancellation_granularity",
        }
        for engine in engines
    )
    assert all(engine["label"] for engine in engines)
    assert all(engine["reason"] for engine in engines if engine["available"] is False)

    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    enabled = detect_engines()
    assert enabled[0].name == "dryrun"
    assert enabled[0].available is True



def _removed_solver_request(engine: str, solver_mode: str) -> dict[str, Any]:
    return {
        "design": {
            "formula": "OSSE",
            "L": 120,
            "a": 45,
            "simulation": {"f1": 250, "f2": 8000, "num_frequencies": 5},
        },
        "options": {"engine": engine, "solver_mode": solver_mode, "stage_delay_ms": 0},
    }


@pytest.mark.parametrize(
    ("engine", "solver_mode"),
    [("auto", "circsym"), ("axisym", "full_3d"), ("circsym", "full_3d")],
)
def test_removed_solver_is_a_typed_422_at_plan_submit_and_retry(
    tmp_path: Path, engine: str, solver_mode: str
) -> None:
    """Every HTTP boundary refuses the removed mode with one stable code, not a 500."""

    application = create_app(data_dir=tmp_path)
    runtime = application.state.jobs_runtime
    client = TestClient(application)
    payload = _removed_solver_request(engine, solver_mode)
    now = "2026-09-28T12:00:00"
    seed = JobStore.for_data_dir(application.state.data_dir)
    seed.initialize()
    seed.create_job({
        "id": "historical-axisym",
        "status": "error",
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "started_at": now,
        "progress": 0.5,
        "stage": "solve",
        "stage_message": "Solving",
        "config_json": payload,
        "config_summary_json": {"formula_type": "OSSE"},
        "task_metadata": {},
    })
    seed.close()

    def assert_refused(response, stage: str) -> None:
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "removed_solver_mode"
        assert error["stage"] == stage
        assert "Axisymmetric solving has been removed" in error["message"]

    async def scenario() -> None:
        body = json.dumps(payload).encode()
        headers = {"content-type": "application/json"}
        try:
            assert_refused(
                await client.request_async("POST", "/api/solve/plan", headers=headers, body=body),
                "planning",
            )
            assert_refused(
                await client.request_async("POST", "/api/solve", headers=headers, body=body),
                "submission",
            )
            assert_refused(
                await client.request_async("POST", "/api/jobs/historical-axisym/retry"),
                "retry",
            )
            jobs, _total = await runtime.list_jobs(status=None, limit=10, offset=0)
            assert [job["id"] for job in jobs] == ["historical-axisym"]
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())

def _axial_imported_request() -> dict[str, Any]:
    return {
        "geometry": {
            "type": "imported",
            "ingest_id": "wgi_" + "0" * 26,
            "manifest_sha256": "sha256:" + "1" * 64,
            "artifact_sha256": "sha256:" + "2" * 64,
            "drive_channels": [{"id": "hf", "source_ids": ["source-hf"], "motion": "axial"}],
            "mesh": {
                "rigid_size_mm": 8.0,
                "transition_mm": 20.0,
                "source_size_mm": {"source-hf": 3.0},
            },
        },
        "options": {"engine": "metal", "frequencies_hz": [100.0, 500.0, 1000.0]},
    }


def test_removed_axial_channel_is_a_typed_422_at_imported_plan_submit_and_retry(
    tmp_path: Path,
) -> None:
    """A stored or new imported request with an axial channel is refused, never a 500 or normal."""

    application = create_app(data_dir=tmp_path)
    runtime = application.state.jobs_runtime
    client = TestClient(application)
    payload = _axial_imported_request()
    now = "2026-09-29T12:00:00"
    seed = JobStore.for_data_dir(application.state.data_dir)
    seed.initialize()
    seed.create_job({
        "id": "historical-axial",
        "status": "error",
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "started_at": now,
        "progress": 0.5,
        "stage": "solve",
        "stage_message": "Solving",
        "config_json": payload,
        "config_summary_json": {"geometry_type": "imported"},
        "task_metadata": {},
    })
    seed.close()

    async def scenario() -> None:
        body = json.dumps(payload).encode()
        headers = {"content-type": "application/json"}
        try:
            for method, path, stage, request_body in (
                ("POST", "/api/solve/imported-plan", "planning", body),
                ("POST", "/api/solve", "submission", body),
                ("POST", "/api/jobs/historical-axial/retry", "retry", None),
            ):
                kwargs = {"headers": headers, "body": request_body} if request_body else {}
                response = await client.request_async(method, path, **kwargs)
                assert response.status_code == 422, (path, response.text)
                error = response.json()["error"]
                assert error["code"] == "removed_source_motion", path
                assert error["stage"] == stage, path
                assert "Axial source motion has been removed" in error["message"]
            jobs, _total = await runtime.list_jobs(status=None, limit=10, offset=0)
            assert [job["id"] for job in jobs] == ["historical-axial"]
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_capabilities_reports_the_journal_mode_sqlite_actually_granted(tmp_path: Path) -> None:
    """A store degraded to a rollback journal is readable off the running app.

    ``PRAGMA journal_mode = WAL`` is a request, and a data directory on a share
    that cannot support it leaves the store in ``delete`` mode. It is reported
    beside the backend probes rather than refused, because the app is correct
    on a rollback journal -- only slower.
    """

    from server.platform.sqlite import reset_journal_mode_statuses

    reset_journal_mode_statuses()
    client = TestClient(create_app(data_dir=tmp_path))
    assert client.get("/api/jobs").status_code == 200

    storage = client.get("/api/capabilities").json()["storage"]
    assert all(
        set(item) == {"name", "path", "journalMode", "available", "reason"} for item in storage
    )
    jobs = next(item for item in storage if item["name"] == "Jobs database")
    assert jobs["journalMode"] == "wal"
    assert jobs["available"] is True
    assert jobs["reason"]


def test_origin_guard_requires_own_authority_or_allowlisted_loopback(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_EXTRA_WS_ORIGINS", "http://localhost:3101")
    client = TestClient(create_app(data_dir=tmp_path))
    rejected = client.get("/health", headers={"Origin": "https://example.com"})
    assert rejected.status_code == 403
    assert "Non-local Origin" in rejected.json()["detail"]

    rebound = client.get("/health", headers={"Host": "attacker.test:3100"})
    assert rebound.status_code == 403
    assert "Non-local Host" in rebound.json()["detail"]

    assert (
        client.get(
            "/health", headers={"Origin": "http://127.0.0.1:3100"}
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/health", headers={"Origin": "http://localhost:3101"}
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/health", headers={"Origin": "http://127.0.0.1:3102"}
        ).status_code
        == 403
    )
    for host in ("localhost:3100", "127.0.0.1:3100", "[::1]:3100"):
        assert client.get("/health", headers={"Host": host}).status_code == 200
    for host in ("localhost", "127.0.0.1:3101", "[::1]:3101"):
        assert client.get("/health", headers={"Host": host}).status_code == 403


def test_streaming_request_body_limit_returns_413(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(app_module, "MAX_REQUEST_BODY_BYTES", 5)
    application = create_app(data_dir=tmp_path)
    client = TestClient(application)

    headers = {"Content-Type": "application/json"}
    assert client.post("/api/design/symmetry", b"12345", headers).status_code != 413
    oversized = client.post("/api/design/symmetry", b"123456", headers)
    assert oversized.status_code == 413
    assert "64 MB" in oversized.json()["detail"]


def test_pins_preserve_oracle_inventory_and_render_deterministically(tmp_path: Path) -> None:
    pins = json.loads(PINS.read_text(encoding="utf-8"))["modules"]
    oracle = json.loads(ORACLE.read_text(encoding="utf-8"))["module_pins"]
    # The oracle records the exact dependency constellation used for the
    # historical parity capture. Current release pins may advance after that
    # evidence is frozen — and new owned modules may join (hornlab-sim,
    # Phase 3) — but no module from the capture may silently leave the pins.
    assert set(oracle) <= set(pins)

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    for output in (first, second):
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--output", str(output)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
    assert first.read_bytes() == second.read_bytes()
    assert first.read_text(encoding="utf-8").splitlines()[1:] == sorted(
        first.read_text(encoding="utf-8").splitlines()[1:]
    )


def test_pins_generator_check_mode_diffs_without_writing(tmp_path: Path) -> None:
    output = tmp_path / "requirements-pins.txt"
    generate = subprocess.run(
        [sys.executable, str(GENERATOR), "--output", str(output)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert generate.returncode == 0

    good_check = subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--output", str(output)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert good_check.returncode == 0

    output.write_text("stale\n", encoding="utf-8")
    bad_check = subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--output", str(output)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad_check.returncode == 1
    assert "-stale" in bad_check.stderr
    assert output.read_text(encoding="utf-8") == "stale\n"
