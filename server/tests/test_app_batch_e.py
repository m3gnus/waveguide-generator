from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from server.app import VERSION, create_app
from server.engines.registry import detect_engines


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

    def get(self, path: str, headers: dict[str, str] | None = None) -> Response:
        async def request() -> Response:
            sent: list[dict[str, Any]] = []
            delivered = False

            async def receive() -> dict[str, Any]:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": b"", "more_body": False}
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
                    "method": "GET",
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
            body = b"".join(
                message.get("body", b"")
                for message in sent
                if message["type"] == "http.response.body"
            )
            return Response(status_code=start["status"], body=body)

        return asyncio.run(request())


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


def test_capabilities_and_dryrun_guard(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("WG2_ENABLE_DRYRUN", raising=False)
    client = TestClient(create_app(data_dir=tmp_path))
    engines = client.get("/api/capabilities").json()["engines"]
    # Real detection (batch Q) probes THIS machine, so availability values are
    # environment-dependent; assert the report contract, not the environment.
    names = [engine["name"] for engine in engines]
    assert {"metal", "bempp"}.issubset(set(names))
    assert "circsym" not in names
    assert "dryrun" not in names
    assert all(
        set(engine) == {"name", "available", "reason", "version", "fast_paths"}
        for engine in engines
    )
    assert all(engine["reason"] for engine in engines if engine["available"] is False)

    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    enabled = detect_engines()
    assert enabled[0].name == "dryrun"
    assert enabled[0].available is True


def test_origin_guard_rejects_remote_and_allows_loopback(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))
    rejected = client.get("/health", headers={"Origin": "https://example.com"})
    assert rejected.status_code == 403
    assert "Non-local Origin" in rejected.json()["detail"]

    rebound = client.get("/health", headers={"Host": "attacker.test:3100"})
    assert rebound.status_code == 403
    assert "Non-local Host" in rebound.json()["detail"]

    for origin in ("http://localhost:3100", "http://127.0.0.1:3100", "http://[::1]:3100"):
        assert client.get("/health", headers={"Origin": origin}).status_code == 200
    for host in ("localhost:3100", "127.0.0.1:3100", "[::1]:3100"):
        assert client.get("/health", headers={"Host": host}).status_code == 200
    for host in ("localhost", "127.0.0.1:3101", "[::1]:3101"):
        assert client.get("/health", headers={"Host": host}).status_code == 403


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
