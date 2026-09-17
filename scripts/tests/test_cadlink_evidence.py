"""Contracts for the read-only CAD Link evidence collector.

``scripts/cadlink_evidence.py`` is read by the release owner's clean-room
checklist (``docs/validation/CADLINK-LIVE-ACCEPTANCE.md``) after each step, so
its two promises matter more than its convenience: it must never write to the
data directory it is reading, and it must never crash on a step where
something -- the add-in has not run yet, no operation has been published --
has not happened.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from scripts import cadlink_evidence
from server.cadlink.fusion_delivery import CAPABILITIES_FILENAME, RETURN_REQUESTS, ipc_folder
from server.cadlink.fusion_status import FUSION_STATUS_FILENAME
from server.platform.paths import data_paths


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _ipc_folder(data_dir: Path) -> Path:
    """The real, production ipc/wglink location -- rooted at the app data
    directory itself (``server/cadlink/fusion_delivery.py:ipc_folder``), not
    the user-chosen CAD-link exchange folder. Deliberately calls the
    production function rather than reconstructing the path by hand, so a
    fixture built with this helper can never silently drift from where the
    collector (or WG itself) actually looks.
    """

    return ipc_folder(data_dir)


def _make_operations_db(data_dir: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    db_path = data_paths(data_dir).db / "cadlink.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute(
            "CREATE TABLE cad_operations ("
            "operation_id TEXT PRIMARY KEY, kind TEXT, state TEXT, updated_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO cad_operations (operation_id, kind, state, updated_at) VALUES (?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return db_path


# --- redaction ---------------------------------------------------------


def test_redact_hides_token_secret_password_keys_recursively() -> None:
    payload = {
        "deliveryVersion": 3,
        "auth": {"apiToken": "abc123", "nested": {"client_secret": "xyz"}},
        "credentials": [{"password": "hunter2", "username": "ada"}],
        "documentName": "Tritonia speaker",
    }
    redacted = cadlink_evidence.redact(payload)
    assert redacted["deliveryVersion"] == 3
    assert redacted["documentName"] == "Tritonia speaker"
    assert redacted["auth"]["apiToken"] == cadlink_evidence._REDACTED_PLACEHOLDER
    assert redacted["auth"]["nested"]["client_secret"] == cadlink_evidence._REDACTED_PLACEHOLDER
    assert redacted["credentials"][0]["password"] == cadlink_evidence._REDACTED_PLACEHOLDER
    assert redacted["credentials"][0]["username"] == "ada"


def test_ipc_files_are_read_from_the_data_directory_not_the_cadlink_exchange_folder(
    tmp_path: Path,
) -> None:
    """``ipc/wglink`` is rooted at the app data directory, never at
    ``cadLinkPath``.

    Every real caller of ``advertise_fusion_delivery``, ``publish_return_request``
    and ``publish_fusion_handoff`` (``server/cadlink/api.py``,
    ``server/exports/api.py``) passes ``application.state.data_dir`` -- the WG
    application data directory -- not the user's chosen CAD-link exchange
    folder. A collector that looked under the exchange folder instead would
    report every one of these files "missing" against a real installation.
    """

    data_dir = tmp_path / "data"
    # The real location: <data_dir>/ipc/wglink/wg-capabilities.json.
    real_path = data_paths(data_dir).root / "ipc" / "wglink" / CAPABILITIES_FILENAME
    _write_json(real_path, {"producer": "waveguide-generator"})
    # A decoy at the CAD-link exchange folder -- where an earlier, wrong
    # implementation of this collector looked -- must not fool it either way.
    decoy_path = data_paths(data_dir).root / "cadlink" / "ipc" / "wglink" / CAPABILITIES_FILENAME
    _write_json(decoy_path, {"producer": "decoy-should-not-be-read"})

    evidence = cadlink_evidence.collect_evidence(data_dir)

    capabilities = evidence["ipc"]["wgCapabilities"]
    assert capabilities["present"] is True
    assert capabilities["path"] == str(real_path)
    assert capabilities["content"]["producer"] == "waveguide-generator"


def test_collect_evidence_redacts_ipc_file_contents(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    ipc = _ipc_folder(data_dir)
    _write_json(
        ipc / FUSION_STATUS_FILENAME,
        {"documentName": "Tritonia", "link": {"refreshToken": "should-not-leak"}},
    )
    _write_json(
        ipc / CAPABILITIES_FILENAME,
        {"producer": "waveguide-generator", "authSecret": "should-not-leak"},
    )

    evidence = cadlink_evidence.collect_evidence(data_dir)

    status_content = evidence["ipc"]["fusionStatus"]["content"]
    assert status_content["documentName"] == "Tritonia"
    assert status_content["link"]["refreshToken"] == cadlink_evidence._REDACTED_PLACEHOLDER

    capabilities_content = evidence["ipc"]["wgCapabilities"]["content"]
    assert capabilities_content["producer"] == "waveguide-generator"
    assert capabilities_content["authSecret"] == cadlink_evidence._REDACTED_PLACEHOLDER

    # And the redaction survives being written into the zip, not just the
    # in-memory evidence dict.
    destination = tmp_path / "evidence.zip"
    cadlink_evidence.write_zip(evidence, destination)
    with zipfile.ZipFile(destination) as archive:
        written = json.loads(archive.read("fusion-status.json"))
    assert written["content"]["link"]["refreshToken"] == cadlink_evidence._REDACTED_PLACEHOLDER
    assert b"should-not-leak" not in destination.read_bytes()


def test_the_endpoint_file_is_collected_with_its_secret_redacted(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    ipc = _ipc_folder(data_dir)
    _write_json(
        ipc / "wg-endpoint.json",
        {
            "schemaVersion": 1,
            "producer": "waveguide-generator",
            "instanceId": "a" * 32,
            "baseUrl": "http://127.0.0.1:3100",
            "liveProtocol": 1,
            "registrationSecret": "endpoint-secret-should-not-leak",
        },
    )

    evidence = cadlink_evidence.collect_evidence(data_dir)

    endpoint = evidence["ipc"]["wgEndpoint"]
    assert endpoint["present"] is True
    assert endpoint["content"]["instanceId"] == "a" * 32
    assert endpoint["content"]["registrationSecret"] == cadlink_evidence._REDACTED_PLACEHOLDER

    destination = tmp_path / "evidence.zip"
    cadlink_evidence.write_zip(evidence, destination)
    with zipfile.ZipFile(destination) as archive:
        written = json.loads(archive.read("wg-endpoint.json"))
        manifest = json.loads(archive.read("manifest.json"))
    assert written["content"]["baseUrl"] == "http://127.0.0.1:3100"
    assert {"name": "wg-endpoint.json", "present": True} in manifest["members"]
    with zipfile.ZipFile(destination) as archive:
        for name in archive.namelist():
            assert b"endpoint-secret-should-not-leak" not in archive.read(name)


def test_session_token_and_registration_secret_keys_are_redacted() -> None:
    redacted = cadlink_evidence.redact(
        {"registrationSecret": "s", "sessionToken": "t", "live": [{"sessionToken": "u"}], "instanceId": "i"}
    )
    placeholder = cadlink_evidence._REDACTED_PLACEHOLDER
    assert redacted == {
        "registrationSecret": placeholder,
        "sessionToken": placeholder,
        "live": [{"sessionToken": placeholder}],
        "instanceId": "i",
    }


# --- live heartbeat transport summary -------------------------------------


def test_evidence_notes_the_live_heartbeat_transport_is_unknown_offline(tmp_path: Path) -> None:
    """The collector cannot see WG's in-memory ``LiveRegistry`` (CL11b): it
    runs offline, out of process, so it can never know whether the freshest
    heartbeat came by the live HTTP transport or the file one. That must be
    recorded, not silently omitted -- the bundle's ``notes`` says so and
    points at ``/fusion-status``'s ``heartbeatTransport`` field.
    """

    data_dir = tmp_path / "data"
    evidence = cadlink_evidence.collect_evidence(data_dir)
    assert any("heartbeatTransport" in note for note in evidence["notes"])
    assert any("live" in note and "file" in note for note in evidence["notes"])


def test_manifest_carries_the_notes_without_unzipping_every_member(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    evidence = cadlink_evidence.collect_evidence(data_dir)
    destination = tmp_path / "evidence.zip"
    cadlink_evidence.write_zip(evidence, destination)
    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["notes"] == evidence["notes"]
    assert any("heartbeatTransport" in note for note in manifest["notes"])


# --- the database is opened read-only -----------------------------------


def test_the_database_is_opened_read_only(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = _make_operations_db(
        data_dir,
        [("op-1", "request_return", "accepted", "2026-09-15T09:00:00Z")],
    )
    before = db_path.read_bytes()
    db_path.chmod(0o444)
    try:
        result = cadlink_evidence.collect_operations(data_dir, operation_id=None)
        assert result["present"] is True
        assert [row["operation_id"] for row in result["rows"]] == ["op-1"]

        # The collector's own connection mode refuses a write outright, not
        # merely happens not to attempt one: prove the mechanism, not just
        # the outcome, by trying the identical mode=ro connection directly.
        readonly = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            with pytest.raises(sqlite3.OperationalError):
                readonly.execute(
                    "INSERT INTO cad_operations (operation_id, kind, state, updated_at) "
                    "VALUES ('op-2', 'update_link', 'accepted', '2026-09-15T09:00:01Z')"
                )
        finally:
            readonly.close()

        # And the file on disk is byte-for-byte unchanged: no journal, no
        # WAL, no row written.
        assert db_path.read_bytes() == before
        assert not db_path.with_name("cadlink.db-journal").exists()
        assert not db_path.with_name("cadlink.db-wal").exists()
    finally:
        db_path.chmod(0o644)


def test_the_collector_asks_sqlite_for_a_read_only_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin the mechanism, not only the outcome.

    A writable connection can still happen to read a chmod'd-read-only file
    without erroring (SQLite's unix VFS falls back to read-only on its own),
    so the file-permission test above is not, by itself, proof that the
    collector *asked* for read-only. This intercepts the actual
    ``sqlite3.connect`` call and checks the URI it was given.
    """

    data_dir = tmp_path / "data"
    _make_operations_db(data_dir, [("op-1", "request_return", "accepted", "2026-09-15T09:00:00Z")])

    calls: list[tuple[str, bool]] = []
    real_connect = sqlite3.connect

    def spy_connect(database, *args, **kwargs):
        calls.append((str(database), bool(kwargs.get("uri"))))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(cadlink_evidence.sqlite3, "connect", spy_connect)
    cadlink_evidence.collect_operations(data_dir, operation_id=None)

    assert calls, "collect_operations must open the database"
    (database, used_uri) = calls[0]
    assert used_uri is True
    assert database.startswith("file:")
    assert "mode=ro" in database


def test_operation_id_filters_rows(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _make_operations_db(
        data_dir,
        [
            ("op-1", "request_return", "accepted", "2026-09-15T09:00:00Z"),
            ("op-2", "update_link", "processing", "2026-09-15T09:05:00Z"),
        ],
    )
    result = cadlink_evidence.collect_operations(data_dir, operation_id="op-2")
    assert [row["operation_id"] for row in result["rows"]] == ["op-2"]


# --- missing inputs are reported, not a crash ---------------------------


def test_missing_files_are_reported_not_raised(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"  # nothing under it exists yet

    evidence = cadlink_evidence.collect_evidence(data_dir, operation_id="op-missing")

    assert evidence["ipc"]["fusionStatus"]["present"] is False
    assert evidence["ipc"]["wgCapabilities"]["present"] is False
    assert evidence["requestDirectories"]["returnRequests"]["present"] is False
    assert evidence["requestDirectories"]["handoffs"]["present"] is False
    assert evidence["operations"]["present"] is False
    assert evidence["operations"]["rows"] == []
    assert evidence["serverLog"]["present"] is False

    destination = tmp_path / "evidence.zip"
    cadlink_evidence.write_zip(evidence, destination)
    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    presence = {entry["name"]: entry["present"] for entry in manifest["members"]}
    assert presence["fusion-status.json"] is False
    assert presence["wg-capabilities.json"] is False
    assert presence["cad-operations.json"] is False
    assert presence["server.log"] is False


def test_a_malformed_json_member_is_reported_not_raised(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    path = _ipc_folder(data_dir) / FUSION_STATUS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    evidence = cadlink_evidence.collect_evidence(data_dir)
    assert evidence["ipc"]["fusionStatus"]["present"] is True
    assert evidence["ipc"]["fusionStatus"]["content"] is None


# --- request directories: names/sizes/mtimes, never contents -----------


def test_request_directory_listing_is_metadata_only(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    requests_dir = _ipc_folder(data_dir) / RETURN_REQUESTS.directory
    requests_dir.mkdir(parents=True)
    secret_body = "this request body must never appear in the evidence zip"
    (requests_dir / "req-1.json").write_text(secret_body, encoding="utf-8")

    evidence = cadlink_evidence.collect_evidence(data_dir)
    listing = evidence["requestDirectories"]["returnRequests"]
    assert listing["present"] is True
    [entry] = listing["entries"]
    assert entry["name"] == "req-1.json"
    assert entry["bytes"] == len(secret_body.encode("utf-8"))
    assert "modifiedAt" in entry
    assert set(entry) == {"name", "bytes", "modifiedAt"}

    destination = tmp_path / "evidence.zip"
    cadlink_evidence.write_zip(evidence, destination)
    assert secret_body.encode("utf-8") not in destination.read_bytes()


# --- server.log filtering ------------------------------------------------


def test_server_log_filters_to_the_operation_id(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    log_path = data_paths(data_dir).logs / "server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "2026-09-15T09:00:00Z INFO accepted operation op-1\n"
        "2026-09-15T09:00:01Z INFO accepted operation op-2\n"
        "2026-09-15T09:00:02Z INFO settled operation op-1\n",
        encoding="utf-8",
    )

    evidence = cadlink_evidence.collect_evidence(data_dir, operation_id="op-1")
    lines = evidence["serverLog"]["text"].splitlines()
    assert len(lines) == 2
    assert all("op-1" in line for line in lines)


# --- workspace root resolution -------------------------------------------


def test_cadlink_workspace_root_reads_the_persisted_setting(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    custom = tmp_path / "elsewhere" / "cad-exchange"
    _write_json(
        data_paths(data_dir).root / cadlink_evidence.CADLINK_SETTINGS_FILENAME,
        {"schemaVersion": 1, "cadLinkPath": str(custom)},
    )
    assert cadlink_evidence.cadlink_workspace_root(data_dir) == custom


def test_cadlink_workspace_root_falls_back_when_unset(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    resolved = cadlink_evidence.cadlink_workspace_root(data_dir)
    assert resolved == data_paths(data_dir).root / "cadlink"
