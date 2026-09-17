"""The live CAD Link session: endpoint file, mutual proof, tokens, origin rule.

``docs/reference/CADLINK-LIVE-PROTOCOL.md`` is the contract. These tests build
real applications with ``create_app`` and run the startup and shutdown work the
live session registers, the way ``test_cad_fusion_markers.py`` runs the CAD
Link startup: the whole startup also warms native workers and refreshes the
Fusion add-in, which a unit test must not do. A WG restart is a second
``create_app`` against the same data directory.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any

import pytest

from server.app import create_app
from server.cadlink import addin_update, fusion_delivery
from server.cadlink.live import endpoint as live_endpoint
from server.cadlink.live import proof as live_proof
from server.cadlink.live import registry as live_registry
from server.platform.paths import app_root
from server.tests.test_app_batch_e import Response, TestClient


PORT = 3100
LIVE = "/api/cadlink/live"
JSON = {"Content-Type": "application/json"}
INSTALLATION = "7c0e0a43-6a3e-4b43-9a55-1b0b1c9b0a11"
OTHER_INSTALLATION = "0f9b1d2e-2a8f-4c1e-8d0e-6f7a5b4c3d21"
STARTUP = ("advertise_fusion_delivery_on_startup", "start_live_session")
SHUTDOWN = ("stop_live_session",)


# -- harness -------------------------------------------------------------------


class Recorder:
    """A client that keeps every request and response it saw, verbatim."""

    def __init__(self, application) -> None:
        self.client = TestClient(application)
        self.wire: list[str] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> Response:
        self.wire.append(f"{method} {path} {json.dumps(headers or {})} {body.decode('latin-1')}")
        response = self.client.request(method, path, headers=headers, body=body)
        self.wire.append(f"{response.status_code} {response.body.decode('latin-1')}")
        return response


def _run_handlers(application, phase: str, names: tuple[str, ...]) -> None:
    handlers = application.router.on_startup if phase == "startup" else application.router.on_shutdown
    by_name = {getattr(handler, "__name__", ""): handler for handler in handlers}
    for name in names:
        assert name in by_name, f"{name} is not a registered {phase} handler"
        result = by_name[name]()
        if asyncio.iscoroutine(result):
            asyncio.run(result)


@contextmanager
def started(data_dir: Path, *, port: int | None = PORT) -> Iterator[Any]:
    application = create_app(data_dir=data_dir, advertised_port=port)
    _run_handlers(application, "startup", STARTUP)
    try:
        yield application
    finally:
        _run_handlers(application, "shutdown", SHUTDOWN)


def _endpoint_path(data_dir: Path) -> Path:
    return fusion_delivery.ipc_folder(data_dir) / "wg-endpoint.json"


def _secret(application) -> str:
    return application.state.live_registry.secret


def _instance(application) -> str:
    return application.state.live_registry.instance_id


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _mac(secret: str, label: str, nonce: str, instance: str, installation: str) -> str:
    message = f"{label}\n{nonce}\n{instance}\n{installation}".encode("utf-8")
    return _b64(hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest())


def _identity(**overrides: Any) -> dict[str, Any]:
    identity = {
        "source": "managed",
        "sourceCommit": addin_update.pinned_commit(app_root()),
        "addinVersion": "0.3.3",
        "managedBy": "waveguide-generator",
        "waveguideGeneratorRoot": None,
        "loadedAt": "2026-09-17T10:00:00Z",
    }
    identity.update(overrides)
    return identity


def _registration(
    application,
    *,
    secret: str | None = None,
    nonce: str | None = None,
    installation: str = INSTALLATION,
    **overrides: Any,
) -> dict[str, Any]:
    nonce = nonce or _b64(secrets.token_bytes(32))
    body = {
        "cadApplication": "fusion360",
        "liveProtocol": 1,
        "deliveryVersion": 3,
        "installationId": installation,
        "adapterSessionId": "fusion-session-1",
        "adapterVersion": "0.3.3",
        "clientNonce": nonce,
        "clientProof": _mac(
            secret or _secret(application), "wglink-client", nonce, _instance(application), installation
        ),
        "loadedIdentity": _identity(),
    }
    body.update(overrides)
    return body


def _register(
    client: Recorder,
    body: dict[str, Any],
    *,
    installation: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    headers = {**JSON, "X-WGLink-Installation": installation or body["installationId"]}
    headers.update(extra_headers or {})
    return client.request("POST", f"{LIVE}/sessions", headers=headers, body=json.dumps(body).encode())


def _session_headers(token: str | None, installation: str | None = INSTALLATION) -> dict[str, str]:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if installation is not None:
        headers["X-WGLink-Installation"] = installation
    return headers


def _code(response: Response) -> str:
    return response.json()["error"]["code"]


class Clock:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    # One fake time for validity (monotonic) and for the reported deadlines (wall).
    monkeypatch.setattr(live_registry, "_now", fake)
    monkeypatch.setattr(live_registry, "_wall", fake)
    return fake


# -- the endpoint file ---------------------------------------------------------


def test_startup_writes_the_endpoint_file_atomically_with_its_port_and_instance(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        document = json.loads(_endpoint_path(tmp_path).read_text(encoding="utf-8"))
        assert set(document) == {
            "schemaVersion", "producer", "instanceId", "pid", "baseUrl",
            "liveProtocol", "startedAt", "registrationSecret",
        }
        assert document["schemaVersion"] == 1
        assert document["producer"] == "waveguide-generator"
        assert document["instanceId"] == _instance(application)
        assert len(document["instanceId"]) == 32 and int(document["instanceId"], 16) >= 0
        assert document["pid"] == os.getpid()
        assert document["baseUrl"] == f"http://127.0.0.1:{PORT}"
        assert document["liveProtocol"] == 1 and type(document["liveProtocol"]) is int
        assert document["startedAt"].endswith("Z")
        assert document["registrationSecret"] == _secret(application)
        assert len(document["registrationSecret"]) >= 43
        leftovers = [p.name for p in fusion_delivery.ipc_folder(tmp_path).iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []
        # The capability file advertises the live protocol beside it.
        capabilities = json.loads(
            (fusion_delivery.ipc_folder(tmp_path) / fusion_delivery.CAPABILITIES_FILENAME).read_text()
        )
        assert capabilities["liveProtocol"] == 1
        assert capabilities["sourceIdentity"] == 1


def test_the_endpoint_file_is_written_through_the_atomic_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[str] = []
    real = fusion_delivery._write_json

    def spy(path: Path, payload, **kwargs):
        written.append(path.name)
        return real(path, payload, **kwargs)

    monkeypatch.setattr(fusion_delivery, "_write_json", spy)
    with started(tmp_path):
        assert "wg-endpoint.json" in written


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits; Windows relies on the profile ACL")
def test_the_endpoint_file_is_private_to_the_user(tmp_path: Path) -> None:
    with started(tmp_path):
        mode = stat.S_IMODE(_endpoint_path(tmp_path).stat().st_mode)
        assert mode & 0o077 == 0, oct(mode)
        assert _endpoint_path(tmp_path).stat().st_uid == os.getuid()


def test_no_advertised_port_writes_no_endpoint_file(tmp_path: Path) -> None:
    with started(tmp_path, port=None) as application:
        assert not _endpoint_path(tmp_path).exists()
        # The registry still exists: the session routes work without the file.
        assert live_registry.registry_for(tmp_path) is application.state.live_registry


def test_clean_shutdown_removes_only_its_own_endpoint_file_and_registry(tmp_path: Path) -> None:
    first = create_app(data_dir=tmp_path, advertised_port=PORT)
    _run_handlers(first, "startup", STARTUP)
    first_instance = _instance(first)
    second = create_app(data_dir=tmp_path, advertised_port=PORT + 1)
    _run_handlers(second, "startup", STARTUP)
    try:
        # The older app stopping must not remove what the newer one published.
        _run_handlers(first, "shutdown", SHUTDOWN)
        document = json.loads(_endpoint_path(tmp_path).read_text(encoding="utf-8"))
        assert document["instanceId"] == _instance(second) != first_instance
        assert live_registry.registry_for(tmp_path) is second.state.live_registry
    finally:
        _run_handlers(second, "shutdown", SHUTDOWN)
    assert not _endpoint_path(tmp_path).exists()
    assert live_registry.registry_for(tmp_path) is None


def test_endpoint_hello_never_contains_the_secret(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        response = client.request("GET", f"{LIVE}/endpoint")
        assert response.status_code == 200
        assert response.json() == {
            "schemaVersion": 1,
            "producer": "waveguide-generator",
            "instanceId": _instance(application),
            "liveProtocol": 1,
            "deliveryVersion": 3,
        }
        assert _secret(application) not in response.text


# -- registration and proof ----------------------------------------------------


def test_registration_with_a_valid_client_proof_returns_a_verifiable_server_proof(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        body = _registration(application)
        response = _register(client, body)
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["serverProof"] == _mac(
            _secret(application), "wglink-server", body["clientNonce"], _instance(application), INSTALLATION
        )
        assert payload["serverProof"] != body["clientProof"]
        assert payload["instanceId"] == _instance(application)
        assert payload["liveProtocol"] == 1
        assert payload["idleTimeoutSeconds"] == 60
        assert payload["heartbeatIntervalSeconds"] == 4
        assert payload["longPollSeconds"] == 25
        assert payload["capabilities"] == fusion_delivery.capabilities()
        assert payload["sessionToken"] and payload["liveSessionId"]
        assert payload["expiresAt"].endswith("Z") and payload["refreshAfter"].endswith("Z")
        assert payload["loadedIdentity"] == {**body["loadedIdentity"], "matchesPin": True}


def test_the_secret_never_appears_in_any_request_or_response(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        secret = _secret(application)
        client = Recorder(application)
        client.request("GET", f"{LIVE}/endpoint")
        registered = _register(client, _registration(application)).json()
        token = registered["sessionToken"]
        refreshed = client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token))
        assert refreshed.status_code == 200
        _register(client, _registration(application, clientProof=_b64(b"x" * 32)))
        client.request("DELETE", f"{LIVE}/sessions/current", headers=_session_headers(refreshed.json()["sessionToken"]))
        assert len(client.wire) == 10
        assert all(secret not in line for line in client.wire)


def test_a_wrong_proof_or_a_reused_nonce_is_401(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        wrong = _register(client, _registration(application, secret="not-the-secret"))
        assert wrong.status_code == 401 and _code(wrong) == "registration_proof_invalid"

        body = _registration(application)
        assert _register(client, body).status_code == 201
        replay = _register(client, body)
        assert replay.status_code == 401 and _code(replay) == "registration_proof_invalid"

        for malformed in ("", "not base64!", _b64(b"x" * 31), _b64(b"x" * 33)):
            response = _register(client, _registration(application, clientProof=malformed))
            assert response.status_code == 401 and _code(response) == "registration_proof_invalid"
        short_nonce = _b64(b"n" * 16)
        response = _register(client, _registration(application, nonce=short_nonce))
        assert response.status_code == 401 and _code(response) == "registration_proof_invalid"


def test_proofs_match_the_documented_encoding() -> None:
    secret = "Zm9vYmFyLXNlY3JldC0wMTIzNDU2Nzg5LWFiY2RlZmdoaWo"
    nonce = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    instance = "0123456789abcdef0123456789abcdef"
    installation = "inst-A_1"
    client = "wwUPXItpbKryw4TTit3YBX8tryoIOyuUIKPaaM7DJUY"
    server = "ncNzsZnz_77enGRnmE95tK87NH1DGnuBluYff-AEII0"

    assert live_proof.client_proof(secret, nonce, instance, installation) == client
    assert live_proof.server_proof(secret, nonce, instance, installation) == server
    assert live_proof.verify_client_proof(secret, nonce, client, instance, installation)
    # Unpadded base64url only, for the proof and the nonce alike.
    assert not live_proof.verify_client_proof(secret, nonce, client + "=", instance, installation)
    # The server vector holds "-" and "_", so its standard-alphabet spelling differs.
    standard = base64.b64encode(base64.urlsafe_b64decode(server + "=")).decode().rstrip("=")
    assert standard != server
    assert live_proof.decode_32(server) is not None
    assert live_proof.decode_32(standard) is None
    assert live_proof.decode_32(server + "=") is None
    assert not live_proof.verify_client_proof(secret, nonce + "=", client, instance, installation)
    assert not live_proof.verify_client_proof(secret, nonce, server, instance, installation)
    assert not live_proof.verify_client_proof(secret + " ", nonce, client, instance, installation)


def test_a_bad_proof_does_not_burn_the_nonce(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        nonce = _b64(secrets.token_bytes(32))
        bad = _register(client, _registration(application, nonce=nonce, secret="wrong"))
        assert bad.status_code == 401
        good = _register(client, _registration(application, nonce=nonce))
        assert good.status_code == 201, good.text


def test_the_old_secret_is_refused_after_a_restart(tmp_path: Path) -> None:
    with started(tmp_path) as first:
        old_secret = _secret(first)
        old_instance = _instance(first)
    with started(tmp_path) as second:
        assert _instance(second) != old_instance
        assert _secret(second) != old_secret
        document = json.loads(_endpoint_path(tmp_path).read_text(encoding="utf-8"))
        assert document["registrationSecret"] == _secret(second)
        client = Recorder(second)
        # A proof made with the old secret, for the new instance or the old one.
        for instance in (_instance(second), old_instance):
            nonce = _b64(secrets.token_bytes(32))
            body = _registration(second, nonce=nonce)
            body["clientProof"] = _mac(old_secret, "wglink-client", nonce, instance, INSTALLATION)
            response = _register(client, body)
            assert response.status_code == 401 and _code(response) == "registration_proof_invalid"


def test_a_wg_restart_forgets_every_session(tmp_path: Path) -> None:
    with started(tmp_path) as first:
        token = _register(Recorder(first), _registration(first)).json()["sessionToken"]
        assert Recorder(first).request(
            "POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token)
        ).status_code == 200
    with started(tmp_path) as second:
        client = Recorder(second)
        response = client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token))
        assert response.status_code == 401 and _code(response) == "session_unknown"
        assert addin_update.loaded_addin_identity(tmp_path) is None

        # The client's recovery: read the endpoint file again, register with the
        # new secret, check the server proof, and carry on with the new token.
        document = json.loads(_endpoint_path(tmp_path).read_text(encoding="utf-8"))
        assert document["instanceId"] == _instance(second)
        nonce = _b64(secrets.token_bytes(32))
        body = _registration(second, nonce=nonce)
        body["clientProof"] = _mac(
            document["registrationSecret"], "wglink-client", nonce, document["instanceId"], INSTALLATION
        )
        registered = _register(client, body)
        assert registered.status_code == 201, registered.text
        assert registered.json()["serverProof"] == _mac(
            document["registrationSecret"], "wglink-server", nonce, document["instanceId"], INSTALLATION
        )
        refreshed = client.request(
            "POST", f"{LIVE}/sessions/refresh", headers=_session_headers(registered.json()["sessionToken"])
        )
        assert refreshed.status_code == 200


def test_a_registration_below_delivery_version_3_is_refused_addin_outdated(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        response = _register(Recorder(application), _registration(application, deliveryVersion=2))
        assert response.status_code == 409 and _code(response) == "addin_outdated"


def test_an_unknown_live_protocol_is_refused_protocol_unsupported(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        response = _register(Recorder(application), _registration(application, liveProtocol=2))
        assert response.status_code == 409 and _code(response) == "protocol_unsupported"


def test_a_commit_other_than_the_pin_is_accepted_and_reported_not_matching(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        other = "f" * 40
        assert other != addin_update.pinned_commit(app_root())
        body = _registration(application, loadedIdentity=_identity(source="devSync", sourceCommit=other))
        response = _register(Recorder(application), body)
        assert response.status_code == 201, response.text
        assert response.json()["loadedIdentity"]["matchesPin"] is False
        assert addin_update.loaded_addin_identity(tmp_path)["matchesPin"] is False
        assert addin_update.loaded_addin_identity(tmp_path)["sourceCommit"] == other


def test_header_and_body_installation_ids_must_match_at_registration(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        response = _register(client, _registration(application), installation=OTHER_INSTALLATION)
        assert response.status_code == 400 and _code(response) == "installation_mismatch"
        body = _registration(application)
        missing = client.request(
            "POST", f"{LIVE}/sessions", headers=JSON, body=json.dumps(body).encode()
        )
        assert missing.status_code == 400 and _code(missing) == "installation_mismatch"


# -- origin, host, validation, ordering ----------------------------------------


def test_any_origin_header_on_a_live_route_is_403_even_a_local_one(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        # The application's own origin passes the global guard ...
        assert client.request("GET", "/health", headers={"Origin": f"http://127.0.0.1:{PORT}"}).status_code == 200
        # ... and is still refused on every live route.
        origin = {"Origin": f"http://127.0.0.1:{PORT}"}
        registered = _register(client, _registration(application), extra_headers=origin)
        assert registered.status_code == 403 and _code(registered) == "origin_not_allowed"
        token = _register(client, _registration(application)).json()["sessionToken"]
        for method, path in (("POST", "/sessions/refresh"), ("DELETE", "/sessions/current")):
            response = client.request(method, f"{LIVE}{path}", headers={**_session_headers(token), **origin})
            assert response.status_code == 403 and _code(response) == "origin_not_allowed"
        # An invalid JSON body does not get past the Origin rule either.
        response = client.request(
            "POST", f"{LIVE}/sessions",
            headers={**JSON, **origin, "X-WGLink-Installation": INSTALLATION}, body=b"{not json",
        )
        assert response.status_code == 403 and _code(response) == "origin_not_allowed"


def test_origin_null_on_a_live_route_is_403(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        response = _register(client, _registration(application), extra_headers={"Origin": "null"})
        assert response.status_code == 403
        assert client.request("GET", f"{LIVE}/endpoint", headers={"Origin": "null"}).status_code == 403


def test_get_endpoint_with_an_origin_is_403(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        # The application's own origin passes the global guard; the live rule refuses it.
        response = client.request("GET", f"{LIVE}/endpoint", headers={"Origin": f"http://127.0.0.1:{PORT}"})
        assert response.status_code == 403 and _code(response) == "origin_not_allowed"
        assert _instance(application) not in response.text
        # Any other origin is already refused by the global guard.
        for origin in (f"http://localhost:{PORT}", "https://example.com"):
            response = client.request("GET", f"{LIVE}/endpoint", headers={"Origin": origin})
            assert response.status_code == 403
            assert _instance(application) not in response.text


def test_a_non_loopback_host_is_still_refused_by_the_global_guard(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        response = client.request("GET", f"{LIVE}/endpoint", headers={"Host": "attacker.test:3100"})
        assert response.status_code == 403
        assert "Non-local Host" in response.json()["detail"]


def test_a_validation_error_on_a_live_route_is_400_without_echoing_input(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        marker = "tok-" + secrets.token_urlsafe(24)
        body = _registration(application, adapterVersion={"sessionToken": marker}, unexpected=marker)
        response = _register(client, body)
        assert response.status_code == 400, response.text
        payload = response.json()
        assert payload["error"]["code"] == "invalid_request"
        assert payload["error"]["stage"] == "cadlink-live"
        assert marker not in response.text
        assert '"input"' not in response.text and '"ctx"' not in response.text
        errors = payload["error"]["details"]["errors"]
        assert errors and all(set(error) == {"loc", "type"} for error in errors)

        # An unknown key's name is client input too; it is reported at its parent.
        key = "SECRETKEYNAME-" + secrets.token_hex(6)
        body = _registration(application, loadedIdentity={**_identity(), key: 1})
        body[key + "-top"] = 1
        response = _register(client, body)
        assert response.status_code == 400, response.text
        assert key not in response.text
        errors = response.json()["error"]["details"]["errors"]
        assert {"loc": ["body", "loadedIdentity"], "type": "extra_forbidden"} in errors
        assert {"loc": ["body"], "type": "extra_forbidden"} in errors

        invalid_json = client.request(
            "POST", f"{LIVE}/sessions",
            headers={**JSON, "X-WGLink-Installation": INSTALLATION},
            body=f'{{"clientProof": "{marker}"'.encode(),
        )
        assert invalid_json.status_code == 400 and _code(invalid_json) == "invalid_request"
        assert marker not in invalid_json.text


def test_a_validation_error_elsewhere_keeps_fastapis_422(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        response = Recorder(application).request(
            "POST", "/api/design/symmetry", headers=JSON, body=b'{"formula": "not-a-formula"}'
        )
        assert response.status_code == 422
        assert "detail" in response.json() and "error" not in response.json()


def test_an_unauthenticated_malformed_body_is_401_not_400(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        for body in (b"{not json", b'{"unexpected": 1}', b"[]"):
            response = client.request(
                "POST", f"{LIVE}/sessions/refresh",
                headers={**JSON, **_session_headers(None)}, body=body,
            )
            assert response.status_code == 401 and _code(response) == "session_unknown"
        # With an installation header missing, the header rule answers first.
        response = client.request(
            "POST", f"{LIVE}/sessions/refresh", headers={**JSON, **_session_headers(None, None)}, body=b"{"
        )
        assert response.status_code == 401 and _code(response) == "installation_mismatch"


def test_the_session_route_class_checks_before_fastapi_reads_the_body(tmp_path: Path) -> None:
    """The mechanism behind the order: any route on the authenticated router,
    including the body-carrying ones later protocol work adds, refuses an
    unauthenticated request before its body is decoded or validated."""

    from fastapi import APIRouter
    from pydantic import BaseModel

    from server.cadlink.live import api as live_api

    class Probe(BaseModel):
        value: int

    application = create_app(data_dir=tmp_path, advertised_port=PORT)
    router = APIRouter(route_class=live_api.SessionRoute)

    @router.post("/probe-body")
    async def probe(body: Probe) -> dict[str, int]:  # pragma: no cover - never reached
        return {"value": body.value}

    application.include_router(router, prefix=LIVE)
    # Ahead of the SPA mounted at "/", which would otherwise answer first.
    application.router.routes.insert(0, application.router.routes.pop())
    _run_handlers(application, "startup", STARTUP)
    try:
        client = Recorder(application)
        for body in (b"{not json", b'{"value": "x"}'):
            response = client.request(
                "POST", f"{LIVE}/probe-body", headers={**JSON, **_session_headers("nope")}, body=body
            )
            assert response.status_code == 401 and _code(response) == "session_unknown"
        token = _register(client, _registration(application)).json()["sessionToken"]
        response = client.request(
            "POST", f"{LIVE}/probe-body", headers={**JSON, **_session_headers(token)}, body=b"{not json"
        )
        assert response.status_code == 400 and _code(response) == "invalid_request"
    finally:
        _run_handlers(application, "shutdown", SHUTDOWN)


def test_live_routes_authenticate_before_the_restart_latch(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        application.state.update_restart.refusal = lambda: "An update restart is pending."
        client = Recorder(application)
        response = client.request(
            "POST", f"{LIVE}/sessions/refresh", headers={**JSON, **_session_headers("nope")}, body=b"{}"
        )
        assert response.status_code == 401 and _code(response) == "session_unknown"
        # The registration route is not latched either (it starts no work).
        assert _register(client, _registration(application)).status_code == 201


def test_live_routes_are_not_in_the_global_restart_latch() -> None:
    from server.app import RESTART_GATED_POSTS

    assert not any(path.startswith(LIVE) for path in RESTART_GATED_POSTS)


# -- tokens and sessions -------------------------------------------------------


def test_a_token_without_or_with_another_installation_header_is_401(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _register(client, _registration(application)).json()["sessionToken"]
        for installation in (None, OTHER_INSTALLATION, "bad header!"):
            response = client.request(
                "POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token, installation)
            )
            assert response.status_code == 401 and _code(response) == "installation_mismatch"
        wrong = client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers("x" + token))
        assert wrong.status_code == 401 and _code(wrong) == "session_unknown"
        for authorization in (token, f"Basic {token}", "Bearer"):
            response = client.request(
                "POST", f"{LIVE}/sessions/refresh",
                headers={"Authorization": authorization, "X-WGLink-Installation": INSTALLATION},
            )
            assert response.status_code == 401 and _code(response) == "session_unknown"
        ok = client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token))
        assert ok.status_code == 200


def test_tokens_are_stored_only_as_digests(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _register(client, _registration(application)).json()["sessionToken"]
        refreshed = client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token))
        new_token = refreshed.json()["sessionToken"]
        registry = application.state.live_registry

        seen: list[Any] = []

        def walk(value: Any, depth: int = 0) -> None:
            if depth > 6:
                return
            seen.append(value)
            if isinstance(value, dict):
                for key, item in value.items():
                    walk(key, depth + 1)
                    walk(item, depth + 1)
            elif isinstance(value, (list, tuple, set, frozenset)):
                for item in value:
                    walk(item, depth + 1)
            elif hasattr(value, "__dict__") and not isinstance(value, type):
                walk(vars(value), depth + 1)
            elif hasattr(value, "__slots__"):
                walk({name: getattr(value, name, None) for name in value.__slots__}, depth + 1)

        walk(registry)
        for plaintext in (token, new_token):
            assert not any(isinstance(v, str) and plaintext in v for v in seen)
            assert not any(isinstance(v, bytes) and plaintext.encode() in v for v in seen)
        digests = [v for v in seen if isinstance(v, bytes) and len(v) == 32]
        assert hashlib.sha256(new_token.encode()).digest() in digests
        assert hashlib.sha256(token.encode()).digest() in digests


def test_a_token_is_compared_in_constant_time_after_the_digest_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compared: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a, b):
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(live_registry.hmac, "compare_digest", spy)
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _register(client, _registration(application)).json()["sessionToken"]
        compared.clear()
        # Authentication itself, not the refresh that follows it.
        assert application.state.live_registry.authenticate(token, INSTALLATION) is not None
        digest = hashlib.sha256(token.encode()).digest()
        assert any(digest in pair for pair in compared)


def test_a_token_expires_and_refresh_issues_a_new_one_with_a_grace_for_the_old(
    tmp_path: Path, clock: Clock
) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        registered = _register(client, _registration(application)).json()
        token = registered["sessionToken"]

        clock.now += 50
        refreshed = client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token)).json()
        new_token = refreshed["sessionToken"]
        assert new_token != token
        assert refreshed["liveSessionId"] == registered["liveSessionId"]
        assert refreshed["expiresAt"] != registered["expiresAt"]
        assert set(refreshed) == {"liveSessionId", "sessionToken", "expiresAt", "refreshAfter"}

        # The old token keeps working for 30 s, and not after.
        registry = application.state.live_registry
        clock.now += 29
        assert registry.authenticate(token, INSTALLATION) is not None
        clock.now += 2
        stale = client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token))
        assert stale.status_code == 401 and _code(stale) == "token_expired"
        assert client.request(
            "POST", f"{LIVE}/sessions/refresh", headers=_session_headers(new_token)
        ).status_code == 200

    # Lifetime: a token never refreshed expires after 15 minutes, however active.
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _register(client, _registration(application)).json()["sessionToken"]
        registry = application.state.live_registry
        for _ in range(17):
            clock.now += 50
            assert registry.authenticate(token, INSTALLATION) is not None
        clock.now += 60  # 910 s > 900 s, never idle for more than 60 s
        response = client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token))
        assert response.status_code == 401 and _code(response) == "token_expired"


def test_the_grace_token_expires_exactly_after_30_seconds(tmp_path: Path, clock: Clock) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _register(client, _registration(application)).json()["sessionToken"]
        new_token = client.request(
            "POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token)
        ).json()["sessionToken"]
        registry = application.state.live_registry
        clock.now += 30
        assert registry.authenticate(token, INSTALLATION) is not None
        clock.now += 1
        with pytest.raises(live_registry.LiveAuthError) as refused:
            registry.authenticate(token, INSTALLATION)
        assert refused.value.code == "token_expired"
        assert registry.authenticate(new_token, INSTALLATION) is not None


def test_an_idle_session_expires(tmp_path: Path, clock: Clock) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _register(client, _registration(application)).json()["sessionToken"]
        clock.now += 61
        response = client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token))
        assert response.status_code == 401 and _code(response) == "token_expired"
        assert addin_update.loaded_addin_identity(tmp_path) is None


def test_a_second_registration_supersedes_the_first_session(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        first = _register(client, _registration(application)).json()
        second = _register(client, _registration(application)).json()
        assert first["liveSessionId"] != second["liveSessionId"]
        response = client.request(
            "POST", f"{LIVE}/sessions/refresh", headers=_session_headers(first["sessionToken"])
        )
        assert response.status_code == 401 and _code(response) == "session_superseded"
        assert client.request(
            "POST", f"{LIVE}/sessions/refresh", headers=_session_headers(second["sessionToken"])
        ).status_code == 200
        # Another installation has its own session and supersedes nothing.
        other = _register(client, _registration(application, installation=OTHER_INSTALLATION)).json()
        assert client.request(
            "POST", f"{LIVE}/sessions/refresh",
            headers=_session_headers(other["sessionToken"], OTHER_INSTALLATION),
        ).status_code == 200


def test_ending_a_session_refuses_its_token_and_clears_the_identity(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _register(client, _registration(application)).json()["sessionToken"]
        assert addin_update.loaded_addin_identity(tmp_path) is not None
        ended = client.request("DELETE", f"{LIVE}/sessions/current", headers=_session_headers(token))
        assert ended.status_code == 204
        response = client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token))
        assert response.status_code == 401 and _code(response) == "session_unknown"
        assert addin_update.loaded_addin_identity(tmp_path) is None


def test_two_apps_on_different_data_dirs_share_no_live_state(tmp_path: Path) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    with started(left) as a, started(right) as b:
        assert _secret(a) != _secret(b)
        token = _register(Recorder(a), _registration(a)).json()["sessionToken"]
        response = Recorder(b).request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token))
        assert response.status_code == 401 and _code(response) == "session_unknown"
        # A proof for A's instance and secret is no good at B.
        foreign = _registration(a)
        assert _register(Recorder(b), foreign).status_code == 401
        assert addin_update.loaded_addin_identity(left) is not None
        assert addin_update.loaded_addin_identity(right) is None


def test_a_stopped_app_leaves_no_registry_or_identity_behind(tmp_path: Path) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        _register(client, _registration(application))
        assert addin_update.loaded_addin_identity(tmp_path) is not None
    assert live_registry.registry_for(tmp_path) is None
    assert addin_update.loaded_addin_identity(tmp_path) is None
    assert application.state.live_registry is None
    response = Recorder(application).request("GET", f"{LIVE}/endpoint")
    assert response.status_code == 503 and _code(response) == "store_busy"


# -- logs, disk, status, OpenAPI -----------------------------------------------


def test_tokens_and_proofs_never_reach_the_request_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        with started(tmp_path) as application:
            client = Recorder(application)
            body = _registration(application)
            registered = _register(client, body).json()
            token = registered["sessionToken"]
            refreshed = client.request(
                "POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token)
            ).json()["sessionToken"]
            client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers("bogus-" + token))
            _register(client, _registration(application, secret="wrong"))
            client.request("DELETE", f"{LIVE}/sessions/current", headers=_session_headers(refreshed))
            sensitive = (
                _secret(application), token, refreshed, body["clientProof"], body["clientNonce"],
                registered["serverProof"],
            )
    text = "\n".join(
        f"{record.getMessage()} {record.args!r} {record.exc_text or ''}" for record in caplog.records
    )
    assert "/api/cadlink/live/sessions" in text  # the request log did run
    for value in sensitive:
        assert value not in text


def test_no_token_proof_or_nonce_is_persisted_under_the_data_dir(tmp_path: Path) -> None:
    """Transport fields are never digest inputs, database values or files."""

    with started(tmp_path) as application:
        client = Recorder(application)
        body = _registration(application)
        registered = _register(client, body).json()
        refreshed = client.request(
            "POST", f"{LIVE}/sessions/refresh", headers=_session_headers(registered["sessionToken"])
        ).json()
        values = [
            registered["sessionToken"], refreshed["sessionToken"], body["clientProof"],
            body["clientNonce"], registered["serverProof"], registered["liveSessionId"],
        ]
        for path in tmp_path.rglob("*"):
            if not path.is_file():
                continue
            content = path.read_bytes()
            for value in values:
                assert value.encode() not in content, (path.name, value)
            if path.name != "wg-endpoint.json":
                assert _secret(application).encode() not in content, path.name


def test_loaded_identity_is_reported_live_through_fusion_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.cadlink import api as cadlink_api

    stored = {"verdict": "activated", "detail": "", "superseded": None, "registration": None,
              "loadedIdentity": None}
    monkeypatch.setattr(cadlink_api, "last_refresh", lambda: dict(stored))

    async def no_activation(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(cadlink_api, "poll_activation", no_activation)
    monkeypatch.setattr(cadlink_api, "fusion_process_running", lambda: False)
    status_body = b'{"design": {"formula": "OSSE"}}'
    with started(tmp_path) as application:
        client = Recorder(application)

        def status() -> dict[str, Any]:
            response = client.request("POST", "/api/cadlink/fusion-status", headers=JSON, body=status_body)
            assert response.status_code == 200, response.text
            return response.json()

        assert status()["addinRefresh"]["loadedIdentity"] is None
        body = _registration(application)
        token = _register(client, body).json()["sessionToken"]
        assert status()["addinRefresh"]["loadedIdentity"] == {**body["loadedIdentity"], "matchesPin": True}
        # The stored activation snapshot was computed earlier and is untouched.
        assert stored["loadedIdentity"] is None
        client.request("DELETE", f"{LIVE}/sessions/current", headers=_session_headers(token))
        assert status()["addinRefresh"]["loadedIdentity"] is None


def test_the_activation_report_without_a_data_dir_still_has_no_identity() -> None:
    assert addin_update.loaded_addin_identity() is None
    assert addin_update.loaded_addin_identity(None) is None


def test_every_live_route_runs_the_pre_body_checks(tmp_path: Path) -> None:
    """A route added under the live prefix without the live route class would
    skip the Origin, installation and token checks that run before the body."""

    from fastapi.routing import APIRoute

    from server.cadlink.live.api import LiveRoute

    live = [
        route for route in create_app(data_dir=tmp_path).routes
        if getattr(route, "path", "").startswith(LIVE)
    ]
    assert len(live) == 6
    assert all(isinstance(route, APIRoute) and isinstance(route, LiveRoute) for route in live)


def test_refresh_with_a_token_in_its_grace_window_is_refused(tmp_path: Path, clock: Clock) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _register(client, _registration(application)).json()["sessionToken"]
        new_token = client.request(
            "POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token)
        ).json()["sessionToken"]
        clock.now += 5
        # Still authenticates (grace), but cannot mint another token.
        assert application.state.live_registry.authenticate(token, INSTALLATION) is not None
        response = client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token))
        assert response.status_code == 401 and _code(response) == "token_expired"
        assert client.request(
            "POST", f"{LIVE}/sessions/refresh", headers=_session_headers(new_token)
        ).status_code == 200


def test_validity_uses_the_monotonic_clock_and_reports_wall_time() -> None:
    import time

    assert live_registry._now is time.monotonic
    assert live_registry._wall is time.time


def test_the_reported_deadlines_are_wall_clock_while_validity_is_monotonic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monotonic, wall = Clock(100.0), Clock(1_800_000_000.0)
    monkeypatch.setattr(live_registry, "_now", monotonic)
    monkeypatch.setattr(live_registry, "_wall", wall)
    with started(tmp_path) as application:
        client = Recorder(application)
        registered = _register(client, _registration(application)).json()
        assert registered["expiresAt"] == "2027-01-15T08:15:00Z"
        assert registered["refreshAfter"] == "2027-01-15T08:10:00Z"
        # A wall-clock jump neither expires nor extends the session.
        wall.now += 7 * 24 * 3600
        assert application.state.live_registry.authenticate(registered["sessionToken"], INSTALLATION)
        monotonic.now += 61
        response = client.request(
            "POST", f"{LIVE}/sessions/refresh", headers=_session_headers(registered["sessionToken"])
        )
        assert response.status_code == 401 and _code(response) == "token_expired"


def test_a_live_request_body_over_64_kib_is_413_with_an_envelope(tmp_path: Path) -> None:
    from server.app import MAX_LIVE_REQUEST_BODY_BYTES

    assert MAX_LIVE_REQUEST_BODY_BYTES == 64 * 1024
    with started(tmp_path) as application:
        client = Recorder(application)
        body = _registration(application)
        padded = json.dumps({**body, "adapterVersion": "x" * (64 * 1024)}).encode()
        headers = {**JSON, "X-WGLink-Installation": INSTALLATION, "Content-Length": str(len(padded))}
        response = client.request("POST", f"{LIVE}/sessions", headers=headers, body=padded)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "request_too_large"
        assert response.json()["error"]["stage"] == "cadlink-live"
        # Without a Content-Length the body is cut off at the same limit as it is read.
        headers.pop("Content-Length")
        response = client.request("POST", f"{LIVE}/sessions", headers=headers, body=padded)
        assert response.status_code == 413 and _code(response) == "request_too_large"
        # A declared oversize body is refused before any live check runs.
        response = client.request(
            "POST", f"{LIVE}/sessions/refresh",
            headers={**_session_headers("nope"), "Content-Length": str(64 * 1024 + 1)}, body=b" " * (64 * 1024 + 1),
        )
        assert response.status_code == 413 and _code(response) == "request_too_large"
        # Under the limit the ordinary checks answer.
        assert _register(client, body).status_code == 201


def test_live_routes_are_in_the_openapi_document_without_examples(tmp_path: Path) -> None:
    schema = create_app(data_dir=tmp_path).openapi()
    live_paths = {path: item for path, item in schema["paths"].items() if path.startswith(LIVE)}
    assert set(live_paths) == {
        f"{LIVE}/endpoint", f"{LIVE}/sessions", f"{LIVE}/sessions/refresh", f"{LIVE}/sessions/current",
        f"{LIVE}/heartbeat", f"{LIVE}/deliveries",
    }
    rendered = json.dumps(live_paths) + json.dumps(schema.get("components", {}))
    assert '"example"' not in rendered and '"examples"' not in rendered
    for item in live_paths.values():
        for operation in item.values():
            assert "422" not in operation["responses"]
            assert "400" in operation["responses"] and "4XX" in operation["responses"]


def test_serve_passes_its_reserved_port_to_the_app() -> None:
    source = (Path(__file__).resolve().parents[2] / "launch" / "serve.py").read_text(encoding="utf-8")
    assert "advertised_port=port" in source


def test_the_endpoint_document_helpers_refuse_a_foreign_instance(tmp_path: Path) -> None:
    document = live_endpoint.endpoint_document(
        instance_id="a" * 32, pid=1, port=PORT, started_at="2026-09-17T10:00:00Z", secret="s"
    )
    live_endpoint.write_endpoint(tmp_path, document)
    live_endpoint.remove_endpoint_if_ours(tmp_path, "b" * 32)
    assert _endpoint_path(tmp_path).exists()
    live_endpoint.remove_endpoint_if_ours(tmp_path, "a" * 32)
    assert not _endpoint_path(tmp_path).exists()
