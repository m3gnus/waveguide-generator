from __future__ import annotations

import os
import time

import pytest

from server.solver.beat_runtime import cleanup, host, ipc, registry as r, spawn
from server.tests.beat_runtime.fake_host_worker import EngineWorker, events, wait_until


def authenticated(record):
    connection = record.endpoint.connect(1)
    hello = r.hello_message(record)
    ipc.send_frame(connection, hello)
    reply = ipc.receive_frame(connection)
    r.validate_hello(record, reply, hello["nonce"])
    message = {**r.host_key({}), "op": "authenticate", "key": record.key,
               "key_id": record.identifier, "nonce": reply["client_nonce"],
               "proof": r.auth_proof(record, reply["client_nonce"], "client_auth")}
    ipc.send_frame(connection, message)
    accepted = ipc.receive_frame(connection)
    assert accepted["type"] == "authenticated"
    assert accepted["proof"] == r.auth_proof(record, reply["client_nonce"], "client_auth_ok")
    return connection


@pytest.mark.parametrize(("field", "value"), [
    ("provider", "foreign"), ("protocol", "beat-worker"), ("protocol_version", 1),
    ("protocol_version", True), ("key_id", "0" * 16), ("key", {}),
    ("key", "engine"),
])
def test_wrong_identity_refused_by_host(launch, field, value):
    key, directory, children = launch
    record = spawn.start_host(key, directory)
    with record.endpoint.connect(1) as connection:
        message = r.hello_message(record)
        message[field] = value
        ipc.send_frame(connection, message)
        assert ipc.receive_frame(connection)["type"] == "hello_refused"
    with cleanup.connect_authenticated(record, key, directory):
        pass
    assert len(children) == 1


def test_engine_identity_mismatch_and_token_refused(launch):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    for changes in ({"engine_fingerprint": "different"}, {"julia_threads": 99}):
        with record.endpoint.connect(1) as connection:
            message = r.hello_message(record)
            message["key"] = {**key, **changes}
            ipc.send_frame(connection, message)
            assert ipc.receive_frame(connection)["type"] == "hello_refused"
    impostor = r.HostRecord(key, record.pid, "wrong", record.endpoint, record.pid_start)
    with pytest.raises(r.RecordRefused, match="proof"):
        cleanup.connect_authenticated(impostor, key, directory)


def test_client_challenge_refuses_reflection_and_replay(launch):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    previous = None
    for mode in ("reflection", "wrong", "replay"):
        with record.endpoint.connect(1) as connection:
            message = r.hello_message(record)
            ipc.send_frame(connection, message)
            hello = ipc.receive_frame(connection)
            challenge = hello["client_nonce"]
            auth = {**r.host_key({}), "op": "authenticate", "key": key,
                    "key_id": record.identifier, "nonce": challenge,
                    "proof": hello["proof"] if mode == "reflection" else "wrong"}
            if mode == "replay":
                auth = previous
            previous = {**auth, "proof": r.auth_proof(record, challenge, "client_auth")}
            ipc.send_frame(connection, auth)
            assert ipc.receive_frame(connection)["type"] == "hello_refused"


def test_authenticated_lifetime_invalid_submission_and_shutdown(launch):
    key, directory, children = launch
    record = spawn.start_host(key, directory, idle_timeout=0.2)
    with authenticated(record) as connection:
        time.sleep(0.35)
        ipc.send_frame(connection, {"op": "ping"})
        assert ipc.receive_frame(connection)["host_pid"] == record.pid
        ipc.send_frame(connection, {"op": "submit"})
        assert ipc.receive_frame(connection)["type"] == "failed"
        ipc.send_frame(connection, {"op": "adopt"})
        report = ipc.receive_frame(connection)
        assert report["host_pid"] == record.pid
        assert report["worker_info"] is None
        request = r.hello_message(record, operation="shutdown")
        ipc.send_frame(connection, request)
        r.validate_hello(record, ipc.receive_frame(connection), request["nonce"], operation="shutdown")
    wait_until(lambda: children[0].returncode is not None)  # Wait thread reaps without caller poll/wait.
    assert r.read_record(record.identifier, directory) is None
    assert [event["type"] for event in events(key)] == ["built", "terminated"]
    if record.endpoint.kind == "unix":
        assert not record.endpoint.path.exists()


def test_hello_alone_and_silent_peer_do_not_prevent_idle_exit(launch, monkeypatch):
    key, directory, children = launch
    expired = directory.parent / "expire-idle-clock"
    accepted = directory.parent / "accepted-peers"
    monkeypatch.setenv("BEAT_FAKE_HOST_SUSPEND_FILE", str(expired))
    monkeypatch.setenv("BEAT_FAKE_HOST_ACCEPTED_FILE", str(accepted))
    # Establish the peers before expiring the idle clock. A 0.3 s real-time
    # window can close between start_host's authenticated probe and connect.
    record = spawn.start_host(key, directory)
    with record.endpoint.connect(1), record.endpoint.connect(1) as connection:
        ipc.send_frame(connection, r.hello_message(record))
        assert ipc.receive_frame(connection)["type"] == "hello_ok"
        # One startup authentication probe plus both scenario peers. Socket
        # connect alone does not prove the silent peer entered host._pending.
        wait_until(lambda: accepted.read_text().splitlines() == ["accepted"] * 3)
        # The fake host advances only its idle clock a day; peer handshake
        # deadlines retain real monotonic time and the exit observation is 3 s.
        expired.touch()
        wait_until(lambda: children[0].returncode is not None)
    assert r.read_record(record.identifier, directory) is None
    assert events(key)[-1]["type"] == "terminated"


def test_shutdown_without_client_proof_refused(launch):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    with cleanup.connect_authenticated(record, key, directory) as connection:
        message = r.hello_message(record, operation="shutdown")
        message["proof"] = "wrong"
        ipc.send_frame(connection, message)
        assert ipc.receive_frame(connection)["type"] == "hello_refused"
    assert r.read_record(record.identifier, directory) == record
    assert cleanup.cleanup_host(record, key, directory, timeout=3)


def test_clean_disconnect_restarts_idle_window(launch):
    key, directory, children = launch
    record = spawn.start_host(key, directory, idle_timeout=0.3)
    connection = authenticated(record)
    time.sleep(0.2)
    connection.close()
    time.sleep(0.1)
    assert children[0].returncode is None
    wait_until(lambda: children[0].returncode is not None)


def test_partial_frame_deadline_and_malformed_peer(launch):
    key, directory, _ = launch
    record = spawn.start_host(key, directory)
    with record.endpoint.connect(1) as connection:
        connection.sendall(b"\x00")
        connection.settimeout(3)
        assert ipc.receive_frame(connection)["type"] == "hello_refused"
    with record.endpoint.connect(1) as connection:
        connection.sendall(b"\x00\x00\x00\x01[")
        assert ipc.receive_frame(connection)["type"] == "hello_refused"
    assert cleanup.cleanup_host(record, key, directory, timeout=3)


def test_close_retains_successor_and_is_idempotent(launch):
    key, directory, _ = launch
    owner = host.WorkerHost(key, directory, engine_factory=EngineWorker)
    with r.SpawnLock(r.spawn_lock_path(owner.identifier, directory)):
        record = owner.bind()
        r.write_record(record, directory)
        successor = r.HostRecord(key, record.pid, "successor", record.endpoint, record.pid_start)
        r.write_record(successor, directory)
    try:
        owner.close()
        owner.close()
        assert r.read_record(owner.identifier, directory) == successor
        assert [event["type"] for event in events(key)] == ["built", "terminated"]
    finally:
        r.unlink_record(r.record_path(owner.identifier, directory))
        if record.endpoint.kind == "unix":
            record.endpoint.path.unlink(missing_ok=True)


@pytest.mark.skipif(os.name != "posix", reason="Windows termination is Job Object governed")
def test_posix_termination_retires_engine_and_removes_own_record(launch):
    key, directory, children = launch
    record = spawn.start_host(key, directory)
    children[0].terminate()
    wait_until(lambda: children[0].returncode is not None)
    assert r.read_record(record.identifier, directory) is None
    assert events(key)[-1]["type"] == "terminated"


@pytest.mark.skipif(os.name != "posix", reason="Unix socket ownership")
def test_close_removes_own_unix_socket_and_record(launch, monkeypatch):
    key, directory, _ = launch
    directory = r.private_directory(directory)
    monkeypatch.chdir(directory)
    # Relative bind addresses exercise Unix lifecycle even with long macOS tmp_path roots.
    monkeypatch.setattr(ipc.Endpoint, "_address", lambda self: self.path.name)
    monkeypatch.setattr(host, "endpoint_for", lambda identifier, root:
                        ipc.Endpoint("unix", path=root / f"{identifier}.sock"))
    owner = host.WorkerHost(key, directory, engine_factory=EngineWorker)
    with r.SpawnLock(r.spawn_lock_path(owner.identifier, directory)):
        record = owner.bind()
        r.write_record(record, directory)
    assert record.endpoint.path.exists()
    owner.close()
    assert not record.endpoint.path.exists()
    assert r.read_record(record.identifier, directory) is None
