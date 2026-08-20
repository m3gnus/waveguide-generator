from __future__ import annotations

import hashlib
import json
import os

from server.cadlink.api import _pending_solve_command
from server.cadlink.solve_command import (
    SOLVE_REQUEST_FILENAME,
    clear_solve_command,
    ledger_entry,
    read_solve_command,
    record_outcome,
)


def _write_command(data_dir, bundle_path: str, manifest_sha256: str, command_id="cmd-1"):
    folder = data_dir / "ipc" / "wglink"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SOLVE_REQUEST_FILENAME).write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "target": "waveguide-generator",
                "commandId": command_id,
                "returnId": "wgr_1",
                "bundlePath": bundle_path,
                "manifestSha256": manifest_sha256,
                "requestedAt": "2026-08-14T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )


def _write_bundle(workspace, name="speaker.wgreturn", body=b'{"document": {}}') -> str:
    bundle = workspace / "wgreturn" / name
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "wgreturn.json").write_bytes(body)
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def test_a_matching_command_is_actionable(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    digest = _write_bundle(workspace)
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest)

    result = _pending_solve_command(data_dir, workspace.resolve())

    assert result["outcome"] is None
    assert result["command"]["commandId"] == "cmd-1"
    assert result["command"]["bundlePath"] == "wgreturn/speaker.wgreturn"


def test_a_bundle_that_changed_after_the_command_is_refused(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    digest = _write_bundle(workspace)
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest)
    # Fusion published, then something rewrote the evidence underneath it.
    _write_bundle(workspace, body=b'{"document": {"name": "changed"}}')

    result = _pending_solve_command(data_dir, workspace.resolve())

    assert result["outcome"]["state"] == "refused"
    assert "changed after Fusion asked" in result["outcome"]["reason"]
    # The marker is one-shot, while the ledger keeps the terminal answer.
    assert ledger_entry(data_dir, "cmd-1")["state"] == "refused"
    assert read_solve_command(data_dir) is None


def test_a_command_for_an_older_return_is_refused_and_cleared(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    digest = _write_bundle(workspace, name="older.wgreturn")
    older = workspace / "wgreturn" / "older.wgreturn"
    newer = workspace / "wgreturn" / "newer.wgreturn"
    _write_bundle(workspace, name="newer.wgreturn")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    _write_command(data_dir, "wgreturn/older.wgreturn", digest)

    result = _pending_solve_command(data_dir, workspace.resolve())

    assert result["outcome"]["state"] == "refused"
    assert result["outcome"]["reason"] == "Superseded by a newer return from Fusion."
    assert ledger_entry(data_dir, "cmd-1")["state"] == "refused"
    assert read_solve_command(data_dir) is None


def test_a_command_pointing_outside_the_workspace_is_refused(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "wgreturn").mkdir(parents=True)
    data_dir = tmp_path / "data"
    _write_command(data_dir, "../../elsewhere/evil.wgreturn", "sha256:whatever")

    result = _pending_solve_command(data_dir, workspace.resolve())

    assert result["outcome"]["state"] == "refused"
    assert read_solve_command(data_dir) is None


def test_an_accepted_command_replays_its_job_instead_of_submitting_again(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    digest = _write_bundle(workspace)
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest)
    record_outcome(data_dir, "cmd-1", state="accepted", job_id="job-7")

    result = _pending_solve_command(data_dir, workspace.resolve())

    assert result["outcome"]["state"] == "accepted"
    assert result["outcome"]["jobId"] == "job-7"
    assert read_solve_command(data_dir) is None


def test_clearing_only_removes_the_command_it_names(tmp_path) -> None:
    data_dir = tmp_path / "data"
    _write_command(data_dir, "wgreturn/speaker.wgreturn", "sha256:a", command_id="cmd-1")

    # A newer command must survive an acknowledgement for the older one.
    assert clear_solve_command(data_dir, "cmd-other") is False
    assert read_solve_command(data_dir).command_id == "cmd-1"
    assert clear_solve_command(data_dir, "cmd-1") is True
    assert read_solve_command(data_dir) is None


def test_a_blocked_command_is_not_written_to_the_ledger(tmp_path) -> None:
    data_dir = tmp_path / "data"
    # Only terminal outcomes are recordable; the route maps 'blocked' to a
    # no-op so the user can satisfy the gate and run the same request.
    assert ledger_entry(data_dir, "cmd-1") is None
    try:
        record_outcome(data_dir, "cmd-1", state="blocked")
    except ValueError as exc:
        assert "blocked" in str(exc)
    else:  # pragma: no cover - the guard is the point of the test
        raise AssertionError("a blocked outcome must not be recordable")
