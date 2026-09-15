"""The CAD operation store: identity, delivery, fencing and the ledger fold-in.

Each test holds one rule of ``docs/architecture/CAD-OPERATIONS.md``.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from server.cadlink import store as store_module
from server.cadlink.operations import fusion_mutation_precheck, request_digest
from server.cadlink.solve_command import (
    LEDGER_FILENAME,
    SOLVE_REQUEST_FILENAME,
    PendingSolveCommand,
    collect_solve_deliveries,
    ledger_entry,
    read_ledger,
    solve_command_request,
)
from server.cadlink.store import CadLinkStore


SOLVE = "prepare_and_solve"
LEDGER = {
    "cmd-1": {"state": "accepted", "jobId": "job-7", "reason": None, "at": "2026-08-14T12:00:00Z"},
    "cmd-2": {
        "state": "refused",
        "jobId": None,
        "reason": "Superseded by a newer return from Fusion.",
        "at": "2026-08-14T12:05:00Z",
    },
}


def _command(**overrides: str) -> PendingSolveCommand:
    fields = {
        "marker_path": Path("unused"),
        "command_id": "cmd-1",
        "return_id": "wgr_1",
        "bundle_path": "wgreturn/speaker.wgreturn",
        "manifest_sha256": "sha256:" + "a" * 64,
        "requested_at": "2026-08-14T12:00:00Z",
    }
    fields.update(overrides)
    return PendingSolveCommand(**fields)  # type: ignore[arg-type]


def _solve_request(**overrides: str) -> tuple[dict, dict, str]:
    target, inputs = solve_command_request(_command(**overrides))
    return target, inputs, request_digest(SOLVE, target, inputs)


def _update(**overrides: object) -> dict[str, object]:
    target: dict[str, object] = {
        "document_id": "urn:adsk.wipprod:dm.lineage:doc-1",
        "design_id": "wgd_1",
        "instance_id": "wgi_1",
        "expected_baseline": {"kind": "document_signature_hash", "value": "sha256:base"},
    }
    target.update(overrides)
    return target


UPDATE_INPUTS = {"export_id": "wge_1"}


def _accept_update(store: CadLinkStore, operation_id: str) -> None:
    target = _update()
    store.accept_operation(
        operation_id,
        "update_link",
        request_digest("update_link", target, UPDATE_INPUTS),
        target,
        UPDATE_INPUTS,
    )


def _raw(db_path: Path, operation_id: str) -> tuple | None:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(
            "SELECT * FROM cad_operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()


def _count(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM cad_operations").fetchone()[0])


def _db(data_dir: Path) -> Path:
    return data_dir / "db" / "cadlink.db"


def _seed_ledger(
    data_dir: Path, commands: dict | None = None, *, raw: str | None = None
) -> Path:
    folder = data_dir / "ipc" / "wglink"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LEDGER_FILENAME
    text = raw
    if text is None:
        payload = {"schemaVersion": 1, "commands": LEDGER if commands is None else commands}
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path: Path):
    registry = CadLinkStore(tmp_path / "cadlink.db")
    yield registry
    registry.close()


# -- Identity -----------------------------------------------------------------


def test_the_digest_is_sha256_over_canonical_json_of_kind_target_and_inputs() -> None:
    target, inputs, digest = _solve_request(
        return_id="wgr_9", bundle_path="wgreturn/a.wgreturn", manifest_sha256="sha256:abc"
    )
    document = {
        "digestVersion": 1,
        "inputs": {
            "bundle_path": "wgreturn/a.wgreturn",
            "manifest_sha256": "sha256:abc",
            "return_id": "wgr_9",
        },
        "kind": "prepare_and_solve",
        "target": {},
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert digest == "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # Mapping order is not identity.
    assert request_digest(SOLVE, target, dict(reversed(list(inputs.items())))) == digest


def test_transport_fields_never_enter_the_digest() -> None:
    first = _command()
    later = replace(first, requested_at="2026-08-15T09:30:00Z", marker_path=Path("elsewhere"))
    assert request_digest(SOLVE, *solve_command_request(first)) == request_digest(
        SOLVE, *solve_command_request(later)
    )


def test_every_kind_has_exactly_one_shape() -> None:
    shapes = {
        "receive_snapshot": (
            {},
            {"bundle_path": "wgreturn/a.wgreturn", "manifest_sha256": "sha256:a"},
        ),
        "prepare_and_solve": (
            {},
            # A legacy single-slot marker may omit the return id.
            {"return_id": "", "bundle_path": "wgreturn/a.wgreturn", "manifest_sha256": "sha256:a"},
        ),
        "request_return": (_update(), {}),
        "insert_link": (
            {"destination": {"kind": "document", "value": "urn:doc"}, "export_id": "wge_1"},
            {},
        ),
        "update_link": (_update(), UPDATE_INPUTS),
    }
    for kind, (target, inputs) in shapes.items():
        assert request_digest(kind, target, inputs).startswith("sha256:")
    with pytest.raises(ValueError, match="kind"):
        request_digest("solve", {}, {})
    with pytest.raises(ValueError, match="export_id"):
        request_digest(
            "insert_link",
            {"destination": {"kind": "document", "value": "urn:doc"}},
            {},
        )


@pytest.mark.parametrize(
    ("kind", "inputs"), [("update_link", UPDATE_INPUTS), ("request_return", {})]
)
def test_update_and_return_targets_are_exact_and_carry_their_baseline(
    kind: str, inputs: dict
) -> None:
    for field in ("document_id", "design_id", "instance_id", "expected_baseline"):
        missing = _update()
        del missing[field]
        with pytest.raises(ValueError, match=field):
            request_digest(kind, missing, inputs)
        with pytest.raises(ValueError, match=field):
            request_digest(kind, _update(**{field: ""}), inputs)
    with pytest.raises(ValueError, match="expected_baseline"):
        request_digest(kind, _update(expected_baseline={"kind": "mtime", "value": "1"}), inputs)
    with pytest.raises(ValueError, match="expected_baseline"):
        request_digest(
            kind,
            _update(expected_baseline={"kind": "document_signature_hash", "value": ""}),
            inputs,
        )
    with pytest.raises(ValueError, match="session_token"):
        request_digest(kind, _update(session_token="refreshed"), inputs)
    # The baseline is part of the identity: a refreshed baseline is a new request.
    refreshed = _update(expected_baseline={"kind": "document_signature_hash", "value": "sha256:newer"})
    assert request_digest(kind, refreshed, inputs) != request_digest(kind, _update(), inputs)


def test_an_update_names_the_export_it_applies() -> None:
    with pytest.raises(ValueError, match="export_id"):
        request_digest("update_link", _update(), {})


# -- Delivery -----------------------------------------------------------------


def test_the_same_id_and_digest_recovers_one_operation(store: CadLinkStore, tmp_path: Path) -> None:
    target, inputs, digest = _solve_request()
    created, outcome = store.accept_operation("cmd-1", SOLVE, digest, target, inputs)
    assert outcome == "created"
    assert (
        created["state"],
        created["attempt_generation"],
        created["legacy"],
        created["digest_version"],
        created["request_digest"],
    ) == ("received", 0, 0, 1, digest)

    again, outcome = store.accept_operation("cmd-1", SOLVE, digest, target, inputs)
    assert outcome == "recovered"
    assert again == created

    # "Restartable": a restarted backend recovers the same operation.
    restarted = CadLinkStore(tmp_path / "cadlink.db")
    try:
        assert restarted.accept_operation("cmd-1", SOLVE, digest, target, inputs) == (
            created,
            "recovered",
        )
    finally:
        restarted.close()
    assert _count(tmp_path / "cadlink.db") == 1


def test_the_same_id_with_a_different_digest_is_a_conflict_and_leaves_the_original(
    store: CadLinkStore, tmp_path: Path
) -> None:
    target, inputs, digest = _solve_request(manifest_sha256="sha256:" + "a" * 64)
    original, _ = store.accept_operation("cmd-1", SOLVE, digest, target, inputs)
    before = _raw(tmp_path / "cadlink.db", "cmd-1")

    other_target, other_inputs, other_digest = _solve_request(manifest_sha256="sha256:" + "b" * 64)
    row, outcome = store.accept_operation("cmd-1", SOLVE, other_digest, other_target, other_inputs)

    assert outcome == "conflict"
    assert row == original
    assert _raw(tmp_path / "cadlink.db", "cmd-1") == before
    assert _count(tmp_path / "cadlink.db") == 1


def test_a_refreshed_baseline_is_a_new_request_never_a_rewrite(
    store: CadLinkStore, tmp_path: Path
) -> None:
    _accept_update(store, "op-1")
    before = _raw(tmp_path / "cadlink.db", "op-1")
    refreshed = _update(expected_baseline={"kind": "document_signature_hash", "value": "sha256:newer"})
    _row, outcome = store.accept_operation(
        "op-1",
        "update_link",
        request_digest("update_link", refreshed, UPDATE_INPUTS),
        refreshed,
        UPDATE_INPUTS,
    )
    assert outcome == "conflict"
    assert _raw(tmp_path / "cadlink.db", "op-1") == before
    stored = json.loads(store.get_operation("op-1")["target_json"])
    assert stored["expected_baseline"]["value"] == "sha256:base"


def test_a_digest_that_does_not_describe_the_request_is_refused(store: CadLinkStore) -> None:
    target, inputs, _digest = _solve_request()
    with pytest.raises(ValueError, match="digest"):
        store.accept_operation("cmd-1", SOLVE, "sha256:" + "0" * 64, target, inputs)
    assert store.get_operation("cmd-1") is None


# -- Fencing ------------------------------------------------------------------


def test_claiming_is_a_conditional_update_on_the_generation(store: CadLinkStore) -> None:
    target, inputs, digest = _solve_request()
    store.accept_operation("cmd-1", SOLVE, digest, target, inputs)

    assert store.claim("cmd-1", 0) == 1
    assert store.get_operation("cmd-1")["state"] == "processing"
    # A second consumer holding the same generation loses the race.
    assert store.claim("cmd-1", 0) is None
    # Recovery takes over through a new attempt.
    assert store.claim("cmd-1", 1) == 2
    assert store.claim("missing", 0) is None


def test_a_stale_generation_records_nothing(store: CadLinkStore, tmp_path: Path) -> None:
    target, inputs, digest = _solve_request()
    store.accept_operation("cmd-1", SOLVE, digest, target, inputs)
    attempt_a = store.claim("cmd-1", 0)
    attempt_b = store.claim("cmd-1", attempt_a)
    before = _raw(tmp_path / "cadlink.db", "cmd-1")

    assert store.record_outcome("cmd-1", attempt_a, "accepted", job_id="job-from-a") is None
    assert _raw(tmp_path / "cadlink.db", "cmd-1") == before

    row = store.record_outcome("cmd-1", attempt_b, "accepted", job_id="job-from-b")
    assert (row["state"], row["job_id"]) == ("accepted", "job-from-b")


@pytest.mark.parametrize("terminal", ["accepted", "rejected", "cancelled"])
def test_a_terminal_operation_is_never_revived_or_rewritten(
    store: CadLinkStore, tmp_path: Path, terminal: str
) -> None:
    target, inputs, digest = _solve_request()
    store.accept_operation("cmd-1", SOLVE, digest, target, inputs)
    generation = store.claim("cmd-1", 0)
    store.record_outcome("cmd-1", generation, terminal)
    before = _raw(tmp_path / "cadlink.db", "cmd-1")

    for state in ("accepted", "rejected", "cancelled", "needs_user_input", "recovery_required"):
        assert store.record_outcome("cmd-1", generation, state) is None
    assert store.claim("cmd-1", generation) is None
    assert _raw(tmp_path / "cadlink.db", "cmd-1") == before


def test_waiting_for_the_user_keeps_the_operation_claimable(store: CadLinkStore) -> None:
    target, inputs, digest = _solve_request()
    store.accept_operation("cmd-1", SOLVE, digest, target, inputs)
    generation = store.claim("cmd-1", 0)
    row = store.record_outcome(
        "cmd-1", generation, "needs_user_input", outcome={"message": "Review the source assignments."}
    )
    assert row["state"] == "needs_user_input"
    assert store.claim("cmd-1", generation) == generation + 1


def test_recovery_required_is_reserved_and_never_retried_automatically(
    store: CadLinkStore,
) -> None:
    _accept_update(store, "op-1")
    generation = store.claim("op-1", 0)
    row = store.record_outcome(
        "op-1",
        generation,
        "recovery_required",
        outcome={"message": "Update interrupted; recovery required."},
    )
    assert row["state"] == "recovery_required"
    assert store.claim("op-1", generation) is None
    # Reading the document can still settle it, with evidence and no new attempt.
    settled = store.record_outcome(
        "op-1",
        generation,
        "accepted",
        outcome={"reconciled": True, "evidence": {"operation_id": "op-1", "export_id": "wge_1"}},
    )
    assert settled["state"] == "accepted"


# -- Outcome vocabulary -------------------------------------------------------


def test_baseline_and_target_reason_codes_are_rejections(store: CadLinkStore) -> None:
    for index, code in enumerate(("baseline_conflict", "target_not_exact")):
        operation_id = f"op-{index}"
        _accept_update(store, operation_id)
        generation = store.claim(operation_id, 0)
        with pytest.raises(ValueError, match=code):
            store.record_outcome(operation_id, generation, "accepted", reason=code)
        row = store.record_outcome(operation_id, generation, "rejected", reason=code)
        assert (row["state"], row["reason"]) == ("rejected", code)

    _accept_update(store, "op-free-text")
    generation = store.claim("op-free-text", 0)
    with pytest.raises(ValueError, match="reason"):
        store.record_outcome("op-free-text", generation, "rejected", reason="the document moved")


def test_a_reconciled_outcome_carries_the_evidence_it_was_read_from(store: CadLinkStore) -> None:
    _accept_update(store, "op-1")
    generation = store.claim("op-1", 0)
    evidence = {"operation_id": "op-1", "export_id": "wge_7"}

    with pytest.raises(ValueError, match="reconciled"):
        store.record_outcome(
            "op-1",
            generation,
            "rejected",
            reason="baseline_conflict",
            outcome={"reconciled": True, "evidence": evidence},
        )
    with pytest.raises(ValueError, match="evidence"):
        store.record_outcome("op-1", generation, "accepted", outcome={"reconciled": True})
    with pytest.raises(ValueError, match="operation_id"):
        store.record_outcome(
            "op-1",
            generation,
            "accepted",
            outcome={"reconciled": True, "evidence": {"operation_id": "op-2", "export_id": "wge_7"}},
        )
    with pytest.raises(ValueError, match="unexpected"):
        store.record_outcome("op-1", generation, "accepted", outcome={"note": "x"})

    row = store.record_outcome(
        "op-1", generation, "accepted", outcome={"reconciled": True, "evidence": evidence}
    )
    assert json.loads(row["outcome_json"]) == {"reconciled": True, "evidence": evidence}


def test_reconciliation_is_read_before_the_baseline_and_never_mutates() -> None:
    # Evidence that this operation already applied wins over a changed
    # document: the change is the operation's own write.
    assert (
        fusion_mutation_precheck("op-1", applied_operation_id="op-1", baseline_matches=False)
        == "reconciled"
    )
    assert (
        fusion_mutation_precheck("op-1", applied_operation_id="op-1", baseline_matches=True)
        == "reconciled"
    )
    # Only without evidence does the baseline decide.
    assert (
        fusion_mutation_precheck("op-1", applied_operation_id=None, baseline_matches=False)
        == "baseline_conflict"
    )
    assert (
        fusion_mutation_precheck("op-1", applied_operation_id="op-0", baseline_matches=False)
        == "baseline_conflict"
    )
    assert (
        fusion_mutation_precheck("op-1", applied_operation_id=None, baseline_matches=True)
        == "apply"
    )


def test_unknown_vocabulary_is_refused(store: CadLinkStore) -> None:
    with pytest.raises(ValueError, match="kind"):
        store.accept_operation("op-1", "solve", "sha256:" + "0" * 64, {}, {})
    target, inputs, digest = _solve_request()
    store.accept_operation("cmd-1", SOLVE, digest, target, inputs)
    for state in ("received", "processing", "superseded"):
        with pytest.raises(ValueError, match="state"):
            store.record_outcome("cmd-1", 0, state)


# -- Folding in the JSON ledger -----------------------------------------------


def test_the_json_ledger_folds_into_the_store_exactly_once(tmp_path: Path) -> None:
    ledger = _seed_ledger(tmp_path)
    original = ledger.read_bytes()

    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        store.initialize()
        assert ledger_entry(store, "cmd-1") == LEDGER["cmd-1"]
        assert ledger_entry(store, "cmd-2") == LEDGER["cmd-2"]
        assert read_ledger(store) == LEDGER
        for command_id, state in (("cmd-1", "accepted"), ("cmd-2", "rejected")):
            row = store.get_operation(command_id)
            assert (row["kind"], row["state"], row["legacy"]) == (SOLVE, state, 1)
            # What history never recorded stays absent; nothing is manufactured.
            assert row["request_digest"] is None
            assert row["digest_version"] is None
            assert row["target_json"] is None
            assert row["inputs_json"] is None
    finally:
        store.close()

    assert not ledger.exists()
    assert ledger.with_name(LEDGER_FILENAME + ".migrated").read_bytes() == original

    reopened = CadLinkStore.for_data_dir(tmp_path)
    try:
        reopened.initialize()
        assert read_ledger(reopened) == LEDGER
    finally:
        reopened.close()
    assert _count(_db(tmp_path)) == 2


def test_a_folded_ledger_outcome_still_replays_to_a_redelivery(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    _seed_ledger(data_dir)
    bundle = workspace / "wgreturn" / "speaker.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "wgreturn.json").write_bytes(b"{}")
    marker = data_dir / "ipc" / "wglink" / SOLVE_REQUEST_FILENAME
    marker.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "target": "waveguide-generator",
                "commandId": "cmd-1",
                "returnId": "wgr_1",
                "bundlePath": "wgreturn/speaker.wgreturn",
                "manifestSha256": "sha256:" + hashlib.sha256(b"{}").hexdigest(),
                "requestedAt": "2026-08-14T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    # Never initialized before the first pass: the read itself must migrate.
    store = CadLinkStore.for_data_dir(data_dir)
    try:
        result = collect_solve_deliveries(data_dir, store)
    finally:
        store.close()

    assert result["command"]["commandId"] == "cmd-1"
    assert result["outcome"] == LEDGER["cmd-1"]
    assert not marker.exists()


def test_an_interrupted_import_reruns_cleanly_without_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _seed_ledger(tmp_path)
    real = store_module._legacy_solve_row
    calls: list[object] = []

    def failing(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("injected mid-import")
        return real(*args, **kwargs)

    monkeypatch.setattr(store_module, "_legacy_solve_row", failing)
    interrupted = CadLinkStore.for_data_dir(tmp_path)
    with pytest.raises(RuntimeError, match="injected"):
        interrupted.initialize()
    interrupted.close()

    # Nothing was half-imported and the ledger is still where it was, so it
    # remains the one source until an import commits.
    with closing(sqlite3.connect(_db(tmp_path))) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "cad_operations" not in tables
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert ledger.exists()

    monkeypatch.setattr(store_module, "_legacy_solve_row", real)
    rerun = CadLinkStore.for_data_dir(tmp_path)
    try:
        rerun.initialize()
        assert read_ledger(rerun) == LEDGER
    finally:
        rerun.close()
    assert _count(_db(tmp_path)) == 2
    assert not ledger.exists()


def test_a_ledger_that_cannot_be_renamed_is_retired_on_the_next_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _seed_ledger(tmp_path)

    def locked(_source, _target):
        raise PermissionError("held open by another process")

    monkeypatch.setattr(store_module.os, "replace", locked)
    first = CadLinkStore.for_data_dir(tmp_path)
    try:
        first.initialize()
        assert read_ledger(first) == LEDGER
    finally:
        first.close()
    assert ledger.exists()

    monkeypatch.undo()
    second = CadLinkStore.for_data_dir(tmp_path)
    try:
        second.initialize()
        assert read_ledger(second) == LEDGER
    finally:
        second.close()
    assert _count(_db(tmp_path)) == 2
    assert not ledger.exists()


def test_a_row_the_store_holds_is_never_overwritten_by_a_late_ledger(tmp_path: Path) -> None:
    store = CadLinkStore.for_data_dir(tmp_path)
    target, inputs, digest = _solve_request()
    try:
        store.accept_operation("cmd-1", SOLVE, digest, target, inputs)
        generation = store.claim("cmd-1", 0)
        store.record_outcome("cmd-1", generation, "accepted", job_id="job-new")
        before = _raw(_db(tmp_path), "cmd-1")
    finally:
        store.close()

    # An older build sharing this data directory wrote its own ledger meanwhile.
    _seed_ledger(
        tmp_path,
        {"cmd-1": {"state": "refused", "jobId": None, "reason": "stale", "at": "2026-08-01T00:00:00Z"}},
    )
    reopened = CadLinkStore.for_data_dir(tmp_path)
    try:
        reopened.initialize()
    finally:
        reopened.close()
    assert _raw(_db(tmp_path), "cmd-1") == before


def test_a_legacy_row_is_recovered_by_id_and_never_rewritten(tmp_path: Path) -> None:
    _seed_ledger(tmp_path)
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        store.initialize()
        before = _raw(_db(tmp_path), "cmd-1")
        target, inputs, digest = _solve_request()
        row, outcome = store.accept_operation("cmd-1", SOLVE, digest, target, inputs)
        assert outcome == "recovered"
        assert (row["legacy"], row["request_digest"]) == (1, None)

        update = _update()
        _row, outcome = store.accept_operation(
            "cmd-1",
            "update_link",
            request_digest("update_link", update, UPDATE_INPUTS),
            update,
            UPDATE_INPUTS,
        )
        assert outcome == "conflict"
        assert store.claim("cmd-1", 0) is None
    finally:
        store.close()
    assert _raw(_db(tmp_path), "cmd-1") == before


def test_malformed_ledger_entries_are_skipped_never_invented(tmp_path: Path) -> None:
    _seed_ledger(
        tmp_path,
        {
            "cmd-ok": {"state": "accepted", "jobId": "job-1", "reason": None, "at": "2026-08-14T12:00:00Z"},
            "cmd-blocked": {"state": "blocked", "jobId": None, "reason": None, "at": "2026-08-14T12:00:00Z"},
            "cmd-junk": "accepted",
            "cmd-odd-job": {"state": "accepted", "jobId": 7, "reason": None, "at": "2026-08-14T12:00:00Z"},
        },
    )
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        store.initialize()
        assert set(read_ledger(store)) == {"cmd-ok", "cmd-odd-job"}
        assert ledger_entry(store, "cmd-odd-job")["jobId"] is None
    finally:
        store.close()


def test_an_unreadable_ledger_is_left_in_place(tmp_path: Path) -> None:
    ledger = _seed_ledger(tmp_path, raw="{not json")
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        store.initialize()
        assert read_ledger(store) == {}
    finally:
        store.close()
    assert ledger.read_text(encoding="utf-8") == "{not json"


# -- Schema -------------------------------------------------------------------


def test_a_v11_registry_upgrades_to_v12_and_keeps_every_row(tmp_path: Path) -> None:
    db_path = tmp_path / "cadlink.db"
    store = CadLinkStore(db_path)
    saved = store.save(
        requested=None,
        design_hash="sha256:" + "1" * 64,
        filename="design.cfg",
        snapshot_builder=lambda identity: f"DesignId={identity.design_id}",
        saved_at="2026-08-10T14:22:31Z",
    )
    design_id = saved["identity"].design_id
    lineage_id = saved["identity"].lineage_id
    store.allocate_export(
        design_id=design_id,
        geometry_hash="sha256:geometry",
        artifact_sha256="sha256:artifact",
        manifest_json="{}",
        idempotency_key="export-row",
    )

    def fail(_facts):
        raise RuntimeError("bundle build failed")

    with pytest.raises(RuntimeError):
        store.allocate_export(design_id=design_id, idempotency_key="reserved-row", export_builder=fail)
    store.allocate_ingest(
        manifest_sha256="sha256:manifest",
        artifact_sha256="sha256:artifact",
        record_builder=lambda ingest_id, now: json.dumps({"ingest": ingest_id, "at": now}),
    )
    store.save_onshape_link(
        design_id=design_id,
        account_id="account",
        document_id="document",
        workspace_id="workspace",
        blob_element_id="blob",
        part_studio_element_id=None,
        variable_studio_element_id=None,
        document_name="Document",
        is_public=False,
        last_export_id=None,
        last_sequence=None,
        last_design_hash=None,
        last_geometry_hash=None,
    )
    store.record_lineage_cad_names(
        lineage_id, parameter_slug="slug", bundle_stem="stem", archive_stem="stem"
    )
    store.close()

    tables = (
        "designs",
        "exports",
        "export_reservations",
        "ingests",
        "onshape_links",
        "lineage_cad_names",
    )
    # A v11 registry is the current DDL without the operation table.
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("DROP TABLE IF EXISTS cad_operations")
        conn.execute("PRAGMA user_version = 11")
        conn.commit()
        before = {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in tables
        }
    assert all(before[table] for table in tables)

    upgraded = CadLinkStore(db_path)
    upgraded.initialize()
    upgraded.close()

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store_module.STORE_FORMAT_VERSION
        after = {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in tables
        }
        assert conn.execute("SELECT COUNT(*) FROM cad_operations").fetchone()[0] == 0
    assert after == before


def test_a_v11_registry_and_its_ledger_upgrade_in_one_step(tmp_path: Path) -> None:
    store = CadLinkStore.for_data_dir(tmp_path)
    saved = store.save(
        requested=None,
        design_hash="sha256:" + "2" * 64,
        filename="design.cfg",
        snapshot_builder=lambda identity: f"DesignId={identity.design_id}",
        saved_at="2026-08-10T14:22:31Z",
    )
    store.close()
    with closing(sqlite3.connect(_db(tmp_path))) as conn:
        conn.execute("DROP TABLE cad_operations")
        conn.execute("PRAGMA user_version = 11")
        conn.commit()
    ledger = _seed_ledger(tmp_path)

    upgraded = CadLinkStore.for_data_dir(tmp_path)
    try:
        upgraded.initialize()
        assert upgraded.get_design(saved["identity"].design_id)["snapshot_text"] == saved["text"]
        assert read_ledger(upgraded) == LEDGER
    finally:
        upgraded.close()
    with closing(sqlite3.connect(_db(tmp_path))) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store_module.STORE_FORMAT_VERSION
    assert not ledger.exists()


def test_an_earlier_migrated_copy_is_kept(tmp_path: Path) -> None:
    ledger = _seed_ledger(tmp_path)
    original = ledger.read_bytes()
    earlier = ledger.with_name(LEDGER_FILENAME + ".migrated")
    earlier.write_text("an earlier import's copy", encoding="utf-8")

    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        store.initialize()
    finally:
        store.close()

    assert not ledger.exists()
    assert earlier.read_text(encoding="utf-8") == "an earlier import's copy"
    copies = sorted(ledger.parent.glob(LEDGER_FILENAME + ".migrated-*"))
    assert [copy.read_bytes() for copy in copies] == [original]


def test_only_a_cad_mutation_can_require_recovery(store: CadLinkStore) -> None:
    target, inputs, digest = _solve_request()
    store.accept_operation("cmd-1", SOLVE, digest, target, inputs)
    generation = store.claim("cmd-1", 0)
    with pytest.raises(ValueError, match="recovery_required"):
        store.record_outcome("cmd-1", generation, "recovery_required")
    assert store.get_operation("cmd-1")["state"] == "processing"


def test_recovery_is_settled_only_by_reading_the_document_or_by_the_user(
    store: CadLinkStore, tmp_path: Path
) -> None:
    _accept_update(store, "op-1")
    generation = store.claim("op-1", 0)
    store.record_outcome("op-1", generation, "recovery_required")
    before = _raw(tmp_path / "cadlink.db", "op-1")

    with pytest.raises(ValueError, match="recovery_required"):
        store.record_outcome("op-1", generation, "rejected", reason="baseline_conflict")
    with pytest.raises(ValueError, match="recovery_required"):
        store.record_outcome("op-1", generation, "accepted")
    with pytest.raises(ValueError, match="recovery_required"):
        store.record_outcome("op-1", generation, "needs_user_input")
    assert _raw(tmp_path / "cadlink.db", "op-1") == before

    assert store.record_outcome("op-1", generation, "cancelled")["state"] == "cancelled"


def test_a_job_id_once_attached_is_never_cleared(store: CadLinkStore) -> None:
    target, inputs, digest = _solve_request()
    store.accept_operation("cmd-1", SOLVE, digest, target, inputs)
    first = store.claim("cmd-1", 0)
    store.record_outcome("cmd-1", first, "needs_user_input", job_id="job-1")
    second = store.claim("cmd-1", first)
    row = store.record_outcome("cmd-1", second, "accepted")
    assert row["job_id"] == "job-1"


def test_an_operation_id_is_any_non_empty_string(store: CadLinkStore) -> None:
    long_id = "cmd-" + "x" * 300
    target, inputs, digest = _solve_request()
    _row, outcome = store.accept_operation(long_id, SOLVE, digest, target, inputs)
    assert outcome == "created"
    with pytest.raises(ValueError, match="operation_id"):
        store.accept_operation("", SOLVE, digest, target, inputs)


def test_a_newer_registry_schema_is_refused(tmp_path: Path) -> None:
    db_path = tmp_path / "cadlink.db"
    store = CadLinkStore(db_path)
    store.initialize()
    store.close()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA user_version = 13")
        conn.commit()
    newer = CadLinkStore(db_path)
    try:
        with pytest.raises(RuntimeError, match="13"):
            newer.initialize()
    finally:
        newer.close()
