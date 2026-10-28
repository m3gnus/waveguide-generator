"""WG's ingress half of the shared preflight rules, and Solve anyway from Fusion.

The rules are the vendored ``wglink_protocol``'s; the cases are the add-in's
own (``fixtures/wglink-shared-protocol/preflight-v1``), so Fusion's dialog and
WG's admission give the same codes in the same words.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from server.cadlink import wglink_protocol
from server.cadlink.fusion.fusion_delivery import capabilities
from server.cadlink.intake import solve_command
from server.cadlink.intake.preflight import admission_refusal, blocking_findings
from server.cadlink.intake.solve_command import InvalidRequest, _command_from_payload, collect_solve_deliveries
from server.cadlink.joblane.preparation import settle_snapshot_operation
from server.cadlink.operations import (
    PREFLIGHT_BLOCKED,
    PREPARE_AND_SOLVE,
    REASON_CODES,
    REJECTED,
    prepare_and_solve_request,
    request_digest,
)
from server.jobs.cad_intent import intent_of
from server.mesh.builder import (
    LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES,
    MAX_SOLVER_MESH_ARTIFACT_TRIANGLES,
    _enforce_artifact_triangle_ceiling,
)

from cad_backends import JobsHarness
from test_cad_preparation import _collect, _manifest, _revision, _setup, _write_return
from test_cad_request_acknowledgement import _ack
from test_cad_solve_delivery import _file, _payload
from test_wglink_packaged_pair import CORPUS, PIN, _from_package, packaged  # noqa: F401 - fixture

SHARED = Path(__file__).resolve().parent / "fixtures/wglink-shared-protocol"
CASES = json.loads((SHARED / "preflight-v1/cases.json").read_text("utf-8"))


@pytest.fixture(autouse=True)
def _fresh_state():
    names = ("_retention_waits", "_admission_waits", "_unreadable_waits", "_refused_claims",
             "_conflict_refusals", "_pending_refusal_acks", "_ack_failures")
    for name in names:
        getattr(solve_command, name).clear()
    yield
    for name in names:
        getattr(solve_command, name).clear()


def _edited(base: dict, edits: dict) -> dict:
    manifest = deepcopy(base)
    for dotted, value in edits.items():
        *parents, last = dotted.split(".")
        node = manifest
        for part in parents:
            node = node[int(part)] if isinstance(node, list) else node.setdefault(part, {})
        if isinstance(node, list):
            index = int(last)
            if index == len(node):
                node.append(deepcopy(value))
            else:
                node[index] = deepcopy(value)
        else:
            node[last] = deepcopy(value)
    return manifest


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda case: case["name"])
def test_wg_ingress_gives_the_addins_codes(case):
    base = json.loads((SHARED / CASES["base"]).read_text("utf-8"))
    manifest = _edited(base, case["edits"])
    findings = wglink_protocol.preflight_findings(wglink_protocol.manifest_draft(manifest))
    assert [[finding["code"], finding["severity"]] for finding in findings] == case["expected"]
    blocks = [finding for finding in findings if finding["severity"] == "block"]
    assert [finding["code"] for finding in blocking_findings(manifest)] == [finding["code"] for finding in blocks]
    refusal = admission_refusal(manifest)
    if blocks:
        assert refusal == " ".join(wglink_protocol.finding_text(finding) for finding in blocks)
    else:
        # A question or a note never stops WG: an unanswered one is WG's own hold.
        assert refusal is None


@pytest.mark.parametrize("case", CASES["draft_cases"], ids=lambda case: case["name"])
def test_wg_reads_the_addins_draft_cases_alike(case):
    findings = wglink_protocol.preflight_findings(case["draft"])
    assert [[finding["code"], finding["severity"]] for finding in findings] == case["expected"]


def test_a_manifest_never_claims_a_body_was_declared():
    """Declarations stay in Fusion: WG reads every surface body as undeclared, and only asks never stop it."""
    base = json.loads((SHARED / CASES["base"]).read_text("utf-8"))
    manifest = _edited(base, {"scope.included.0.body_kind": "surface"})
    assert wglink_protocol.manifest_draft(manifest)["open_bodies"] == [{"name": "Synthetic enclosure", "declared": None}]
    assert admission_refusal(manifest) is None


def test_wg_advertises_its_limits_and_the_addin_uses_only_valid_advertisements():
    limits = capabilities()["meshLimits"]
    assert limits == {"solvedTriangles": MAX_SOLVER_MESH_ARTIFACT_TRIANGLES,
                      "fullDomainWarning": LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES}
    assert wglink_protocol.solved_triangle_limit(limits) == MAX_SOLVER_MESH_ARTIFACT_TRIANGLES
    assert wglink_protocol.advertised_limit(limits, "fullDomainWarning") == LARGE_MESH_WARNING_FULL_DOMAIN_TRIANGLES
    assert wglink_protocol.ceiling_finding(MAX_SOLVER_MESH_ARTIFACT_TRIANGLES + 1, limits=limits)["code"] == "mesh_ceiling"
    for unknown in (None, {}, {"solvedTriangles": True}, {"solvedTriangles": 0},
                    {"solvedTriangles": "22000"}):
        assert wglink_protocol.solved_triangle_limit(unknown) is None
        assert wglink_protocol.ceiling_finding(MAX_SOLVER_MESH_ARTIFACT_TRIANGLES + 1, limits=unknown) is None
    assert wglink_protocol.ACKNOWLEDGES["mesh_ceiling"] == "mesh_too_large"
    assert REASON_CODES["mesh_too_large"] != REJECTED  # The hold the answer settles.


def test_a_refusal_at_admission_is_a_rejection_in_the_rules_words():
    assert REASON_CODES[PREFLIGHT_BLOCKED] == REJECTED


# -- the request field --------------------------------------------------------

def _solve_payload(**extra):
    return _payload("cmd-1", "wgreturn/a.wgreturn", "sha256:" + "0" * 64, schema=4,
                    kind="prepare_and_solve", **extra)


def test_a_solve_request_names_the_holds_it_answered():
    command = _command_from_payload(_solve_payload(acknowledged=["mesh_too_large", "a_future_hold"]),
                                    Path("cmd-1.json"), schema_versions=frozenset({4}))
    # A hold this WG does not know is not a refusal; it is not asked here.
    assert command.acknowledged == ("mesh_too_large",)
    assert command.payload()["acknowledged"] == ["mesh_too_large"]
    plain = _command_from_payload(_solve_payload(), Path("cmd-1.json"), schema_versions=frozenset({4}))
    assert plain.acknowledged == () and "acknowledged" not in plain.payload()


@pytest.mark.parametrize("payload", [
    _solve_payload(acknowledged="mesh_too_large"),
    _solve_payload(acknowledged=[1]),
    {**_solve_payload(acknowledged=["mesh_too_large"]), "kind": "receive_snapshot", "returnId": None},
])
def test_a_malformed_or_misplaced_answer_is_an_invalid_request(payload):
    if payload.get("returnId") is None:
        payload.pop("returnId", None)
    with pytest.raises(InvalidRequest):
        _command_from_payload(payload, Path("cmd-1.json"), schema_versions=frozenset({4}))


def test_the_answer_is_part_of_the_request_digest_and_absent_changes_nothing():
    fields = dict(return_id="wgr_1", bundle_path="wgreturn/a.wgreturn", manifest_sha256="sha256:" + "0" * 64)
    target, inputs = prepare_and_solve_request(**fields)
    assert set(inputs) == {"return_id", "bundle_path", "manifest_sha256"}
    # The digest every request before this field had, unchanged.
    assert request_digest(PREPARE_AND_SOLVE, target, inputs) == request_digest(PREPARE_AND_SOLVE, {}, dict(fields))
    target, answered = prepare_and_solve_request(**fields, acknowledged=("mesh_too_large",))
    assert answered["acknowledged"] == ["mesh_too_large"]
    assert request_digest(PREPARE_AND_SOLVE, target, answered) != request_digest(PREPARE_AND_SOLVE, target, inputs)
    for bad in ([], ["b", "a"], ["a", "a"], "a"):
        with pytest.raises(ValueError):
            request_digest(PREPARE_AND_SOLVE, {}, {**fields, "acknowledged": bad})


# -- admission ------------------------------------------------------------------

def _blocked_return(workspace: Path, name: str = "speaker.wgreturn") -> tuple[str, str]:
    """A structurally valid return two painted sources share a role in."""

    step = b"STEP"
    manifest = _manifest(step)
    linked = manifest["sources"][0]
    for index in (1, 2):
        manifest["sources"].append({
            **deepcopy(linked), "id": f"paint-hf-{index}", "instance_id": None,
            "default_drive_channel_id": f"drive-paint-{index}", "patch_policy": "explicit-disconnected",
            "selectors": {"appearance_labels": [f"HF{index}"]},
        })
    bundle = workspace / "wgreturn" / name
    bundle.mkdir(parents=True)
    (bundle / "assembly.step").write_bytes(step)
    body = json.dumps(manifest).encode("utf-8")
    (bundle / "wgreturn.json").write_bytes(body)
    return f"wgreturn/{name}", "sha256:" + hashlib.sha256(body).hexdigest()


def _expected_refusal() -> str:
    [finding] = [f for f in wglink_protocol.preflight_findings({
        "sources": [{"role": "HF"}, {"role": "HF"}]}) if f["code"] == "source_role_duplicate"]
    return wglink_protocol.finding_text(finding)


@pytest.fixture
def h(tmp_path: Path):
    harness = JobsHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def test_a_blocked_solve_is_refused_at_admission_never_prepared(h: JobsHarness):
    bundle_path, manifest = _blocked_return(h.workspace)
    _file(h.data_dir, "cmd-1", bundle_path, manifest, schema=4, kind="prepare_and_solve")
    h.delivery_pass()  # Production retention and admission.
    row = h.store.get_operation("cmd-1")
    assert (row["state"], row["reason"]) == ("rejected", PREFLIGHT_BLOCKED)
    ack = _ack(h.data_dir, "cmd-1")
    assert ack["outcome"] == "refused"
    assert ack["reason"] == _expected_refusal() == "2 drivers are HF – high-frequency driver. Keep one per role."
    assert h.jobs_store.latest_cad_job("cmd-1") is None


def test_a_blocked_send_is_refused_in_the_same_words(h: JobsHarness):
    bundle_path, manifest = _blocked_return(h.workspace)
    path = _file(h.data_dir, "cmd-1", bundle_path, manifest, schema=4, kind="receive_snapshot")
    payload = json.loads(path.read_text("utf-8"))
    del payload["returnId"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    collect_solve_deliveries(
        h.data_dir, h.store,
        retain=lambda op: settle_snapshot_operation(h.store, h.data_dir, h.workspace.resolve(), op),
    )
    row = h.store.get_operation("cmd-1")
    assert (row["state"], row["reason"]) == ("rejected", PREFLIGHT_BLOCKED)
    assert _ack(h.data_dir, "cmd-1")["reason"] == _expected_refusal()


def test_a_failure_of_the_rules_is_recorded_and_the_return_admitted(h: JobsHarness, monkeypatch, tmp_path: Path):
    from server.platform.incidents import configure_incidents

    incidents = configure_incidents(tmp_path / "wg-data", app_root=tmp_path / "app")
    import server.cadlink.intake.preflight as preflight

    def broken(_manifest):
        raise RuntimeError("rule crashed")

    monkeypatch.setattr(preflight, "admission_refusal", broken)
    bundle_path, manifest = _blocked_return(h.workspace)
    _file(h.data_dir, "cmd-1", bundle_path, manifest, schema=4, kind="prepare_and_solve")
    _collect(h)
    assert h.store.get_operation("cmd-1")["state"] != "rejected"
    assert h.jobs_store.latest_cad_job("cmd-1") is not None
    [record] = incidents.list(kind="preflight-rules-failed")
    assert record["latest"]["ids"]["operation_id"] == "cmd-1"


@pytest.mark.parametrize("answered", [True, False])
def test_solve_anyway_from_fusion_is_not_asked_again(h: JobsHarness, answered: bool):
    bundle_path, manifest = _write_return(h.workspace)
    extra = {"acknowledged": ["mesh_too_large"]} if answered else {}
    _file(h.data_dir, "cmd-1", bundle_path, manifest, schema=4, kind="prepare_and_solve", **extra)
    _collect(h)
    inputs = json.loads(h.store.get_operation("cmd-1")["inputs_json"])
    assert inputs.get("acknowledged") == (["mesh_too_large"] if answered else None)
    job = h.latest_job()
    intent = intent_of(job)
    assert intent.mesh_limit_override is answered
    # The jobs system's key replays the operation's own request digest.
    assert intent.delivery_digest() == h.store.get_operation("cmd-1")["request_digest"]
    seen = []
    original = h.ingest

    def mesh(*args, **kwargs):
        override = kwargs["prep_options"].get("mesh_limit_override", False)
        seen.append(override)
        _enforce_artifact_triangle_ceiling(28_944, mesh_limit_override=override)
        return original(*args, **kwargs)

    summary = h.prepare(setup_revision_id=_revision(h.store, _setup()), ingest=mesh)
    assert seen == [answered]
    row = h.jobs_store.get_job_row(h.latest_job()["id"])
    if answered:
        assert row["status"] == "queued"
        assert row["task_metadata"]["cad"]["mesh_limit_override"] == {"accepted": True, "source": "fusion"}
    else:
        assert summary["reason"] == "mesh_too_large"


# -- the refusal is one transaction, whoever reaches it ----------------------------

def _delivered_blocked_solve(h: JobsHarness) -> None:
    bundle_path, manifest = _blocked_return(h.workspace)
    _file(h.data_dir, "cmd-1", bundle_path, manifest, schema=4, kind="prepare_and_solve")


def test_a_refusal_that_cannot_be_recorded_is_retried_never_admitted(h: JobsHarness, monkeypatch, tmp_path: Path):
    from server.platform.incidents import configure_incidents

    incidents = configure_incidents(tmp_path / "wg-data", app_root=tmp_path / "app")
    _delivered_blocked_solve(h)
    real = h.store.reject_received
    failures = []

    def fails_once(*args, **kwargs):
        if not failures:
            failures.append(1)
            raise RuntimeError("store write lost")
        return real(*args, **kwargs)

    monkeypatch.setattr(h.store, "reject_received", fails_once)
    # The production delivery pass and admission (run_delivery_pass, accept_operation_solve).
    h.delivery_pass()
    assert h.store.get_operation("cmd-1")["state"] == "received"  # Kept, not admitted.
    h.delivery_pass()
    row = h.store.get_operation("cmd-1")
    assert (row["state"], row["reason"]) == ("rejected", PREFLIGHT_BLOCKED)
    assert h.jobs_store.latest_cad_job("cmd-1") is None
    assert _ack(h.data_dir, "cmd-1")["outcome"] == "refused"
    [record] = incidents.list(kind="preflight-refusal-not-recorded")
    assert record["latest"]["ids"]["operation_id"] == "cmd-1"


def test_two_admissions_racing_record_one_refusal_and_no_job(h: JobsHarness):
    import asyncio

    from server.cadlink.joblane.job_shims import accept_operation_solve

    bundle_path, manifest = _blocked_return(h.workspace)
    target, inputs = prepare_and_solve_request(return_id="wgr_1", bundle_path=bundle_path, manifest_sha256=manifest)
    h.store.accept_operation("cmd-1", PREPARE_AND_SOLVE, request_digest(PREPARE_AND_SOLVE, target, inputs),
                             target, inputs)
    recorded = []
    real = h.store.reject_received
    h.store.reject_received = lambda *a, **k: (recorded.append(real(*a, **k)), recorded[-1])[1]

    async def both():
        return await asyncio.gather(*(
            accept_operation_solve(h.context(), "cmd-1", schedule=False, delivery_released=True)
            for _ in range(2)
        ))

    assert h._loop.run(both()) == [None, None]
    assert len([row for row in recorded if row is not None]) == 1
    row = h.store.get_operation("cmd-1")
    assert (row["state"], row["reason"]) == ("rejected", PREFLIGHT_BLOCKED)
    assert h.jobs_store.latest_cad_job("cmd-1") is None


def test_the_pinned_writers_bundle_is_admitted(packaged, h: JobsHarness):  # noqa: F811
    """The frozen packaged writer (``integrations/wglink/source.json``) passes the rules."""

    assert PIN["addinVersion"] == "0.3.3"
    with _from_package(packaged, "wglink_return.py") as writer:
        fixture = CORPUS / "automatic-full.wgreturn"
        fields = deepcopy(json.loads((fixture / "wgreturn.json").read_text()))
        fields["return_record"] = fields.pop("return")
        fields.pop("acoustics")
        fields["wgreturn_version"] = "1.1"
        fields["required_features"].remove("domain-automatic-v1")
        fields["assembly"].pop("domain")
        fields["assembly"]["signature_hash"] = "sha256:" + "4" * 64
        fields["scope"]["included"][0].update(component="Body1", external_reference="none")
        manifest = writer.build_return_manifest(**fields)
        body = writer.dumps_return_manifest(manifest).encode("utf-8")
    assert admission_refusal(json.loads(body)) is None
    bundle = h.workspace / "wgreturn/pinned.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "assembly.step").write_bytes((fixture / "assembly.step").read_bytes())
    (bundle / "wgreturn.json").write_bytes(body)
    _file(h.data_dir, "cmd-1", "wgreturn/pinned.wgreturn", "sha256:" + hashlib.sha256(body).hexdigest(),
          schema=4, kind="prepare_and_solve", returnId=manifest["return"]["id"])
    _collect(h)
    assert h.store.get_operation("cmd-1")["state"] != "rejected"
    assert h.jobs_store.latest_cad_job("cmd-1") is not None


def test_a_fusion_answer_holds_only_for_the_preparation_it_was_given_for(h: JobsHarness):
    from server.cadlink.joblane.job_shims import accept_operation_solve

    bundle_path, manifest = _write_return(h.workspace)
    _file(h.data_dir, "cmd-1", bundle_path, manifest, schema=4, kind="prepare_and_solve",
          acknowledged=["mesh_too_large"])
    _collect(h)
    job = h.latest_job()
    assert intent_of(job).mesh_limit_override is True
    other = _revision(h.store, _setup(rigid=12.0))
    # A manual-waiting delivery (Prepare in WG first) carries the answer only
    # through a press that keeps the delivery's preparation.
    bundle_path, manifest = _write_return(h.workspace, "second.wgreturn")
    target, inputs = prepare_and_solve_request(return_id="wgr_1", bundle_path=bundle_path, manifest_sha256=manifest,
                                               acknowledged=("mesh_too_large",))
    h.store.accept_operation("cmd-2", PREPARE_AND_SOLVE, request_digest(PREPARE_AND_SOLVE, target, inputs),
                             target, inputs)
    job_id = h._loop.run(accept_operation_solve(h.context(), "cmd-2", manual=True, schedule=False,
                                                delivery_released=True))
    row = h.jobs_store.get_job_row(job_id)
    assert row["task_metadata"]["cad"].get("manual_waiting") is True
    assert intent_of(row).mesh_limit_override is True
    h._loop.run(h.runtime.prepare_cad_solve(job_id, setup_revision_id=other))
    assert intent_of(h.jobs_store.get_job_row(job_id)).mesh_limit_override is False


def _accepted_blocked_solve(h: JobsHarness) -> None:
    bundle_path, manifest = _blocked_return(h.workspace)
    target, inputs = prepare_and_solve_request(return_id="wgr_1", bundle_path=bundle_path, manifest_sha256=manifest)
    h.store.accept_operation("cmd-1", PREPARE_AND_SOLVE, request_digest(PREPARE_AND_SOLVE, target, inputs),
                             target, inputs)


def test_the_fence_refuses_when_retention_did_not(h: JobsHarness, monkeypatch):
    """Admission judges again under the operation fence, whatever retention said."""
    import server.cadlink.joblane.job_shims as job_shims
    import server.cadlink.joblane.preparation as preparation

    _accepted_blocked_solve(h)
    real = preparation.refuse_blocked_return
    monkeypatch.setattr(preparation, "refuse_blocked_return", lambda *a, **k: False)
    preparation.retain_operation_snapshot(h.store, h.data_dir, h.workspace.resolve(), "cmd-1")
    monkeypatch.setattr(preparation, "refuse_blocked_return", real)
    monkeypatch.setattr(job_shims, "retain_operation_snapshot", lambda *a, **k: "retained")
    assert h.store.get_operation("cmd-1")["state"] == "received"
    job = h._loop.run(job_shims.accept_operation_solve(h.context(), "cmd-1", schedule=False, delivery_released=True))
    row = h.store.get_operation("cmd-1")
    assert job is None and (row["state"], row["reason"]) == ("rejected", PREFLIGHT_BLOCKED)
    assert h.jobs_store.latest_cad_job("cmd-1") is None


def test_a_delivery_admitted_before_wg_held_its_return_is_judged_by_its_first_preparation(
    h: JobsHarness, monkeypatch,
):
    """Its retention wait ran out, so admission never saw the manifest: preparation refuses it, calmly."""
    import server.cadlink.joblane.job_shims as job_shims
    import server.cadlink.joblane.preparation as preparation

    _accepted_blocked_solve(h)
    # Admission without the return held: nothing was there to judge.
    monkeypatch.setattr(preparation, "refuse_blocked_return", lambda *a, **k: False)
    monkeypatch.setattr(job_shims, "refuse_blocked_return", lambda *a, **k: False)
    job_id = h._loop.run(job_shims.accept_operation_solve(h.context(), "cmd-1", schedule=False,
                                                          delivery_released=True))
    assert job_id is not None
    monkeypatch.undo()
    meshed = []
    h.prepare(setup_revision_id=_revision(h.store, _setup()), ingest=lambda *a, **k: meshed.append(1))
    row = h.jobs_store.get_job_row(job_id)
    assert row["status"] == "error" and meshed == []
    refusal = row["task_metadata"]["cad"]["refusal"]
    assert refusal == {"code": PREFLIGHT_BLOCKED, "message": _expected_refusal()}


def test_a_malformed_snapshot_record_stays_an_invalid_retention(h: JobsHarness, monkeypatch):
    import sqlite3
    from contextlib import closing

    from server.cadlink.joblane.preparation import RETAIN_INVALID, retain_operation_snapshot

    _accepted_blocked_solve(h)
    with closing(sqlite3.connect(h.store.db_path)) as conn:
        conn.execute("UPDATE cad_operations SET snapshot_json = '{not json' WHERE operation_id = 'cmd-1'")
        conn.commit()
    assert retain_operation_snapshot(h.store, h.data_dir, h.workspace.resolve(), "cmd-1") == RETAIN_INVALID


def test_an_invalid_refusal_is_not_retried_forever(h: JobsHarness, monkeypatch):
    from server.cadlink.joblane.preparation import RETAIN_INVALID, retain_operation_snapshot

    _accepted_blocked_solve(h)

    def invalid(*_a, **_k):
        raise ValueError("not an outcome this store records")

    monkeypatch.setattr(h.store, "reject_received", invalid)
    assert retain_operation_snapshot(h.store, h.data_dir, h.workspace.resolve(), "cmd-1") == RETAIN_INVALID


def test_a_fusion_answer_does_not_carry_to_an_admission_press_naming_another_setup(h: JobsHarness):
    from server.cadlink.joblane.job_shims import accept_operation_solve
    from server.cadlink.joblane.preparation import PreparationInput

    other = _revision(h.store, _setup(rigid=12.0))
    for command, press, expected in (("cmd-1", None, True),
                                     ("cmd-2", PreparationInput(setup_revision_id=other), False)):
        bundle_path, manifest = _write_return(h.workspace, f"{command}.wgreturn")
        target, inputs = prepare_and_solve_request(return_id="wgr_1", bundle_path=bundle_path,
                                                   manifest_sha256=manifest, acknowledged=("mesh_too_large",))
        h.store.accept_operation(command, PREPARE_AND_SOLVE, request_digest(PREPARE_AND_SOLVE, target, inputs),
                                 target, inputs)
        job_id = h._loop.run(accept_operation_solve(h.context(), command, press, schedule=False,
                                                    delivery_released=True))
        assert intent_of(h.jobs_store.get_job_row(job_id)).mesh_limit_override is expected, command


# -- what Fusion's progress panel reads (the request-status contract) ------------------

def test_a_return_refused_at_admission_reads_the_rules_words_in_its_status(h: JobsHarness, monkeypatch):
    from test_cad_request_status import _publisher, _status

    from server.cadlink.joblane import request_status

    recorded = []
    monkeypatch.setattr(request_status, "record_incident", lambda *a, **k: recorded.append(a))
    _delivered_blocked_solve(h)
    h.delivery_pass()
    assert _ack(h.data_dir, "cmd-1")["reason"] == _expected_refusal()
    _publisher(h).tick(h.jobs_store)
    record = _status(h)
    assert record["state"] == "refused"
    assert record["failure"]["recorded"] is False and record["failure"]["message"] == _expected_refusal()
    text = wglink_protocol.request_status_text(wglink_protocol.validate_request_status(record, request_id="cmd-1"))
    assert text["detail"] == _expected_refusal()
    assert wglink_protocol.user_text_violations(" ".join([text["headline"], text["detail"]])) == []
    assert recorded == []  # A user matter: nothing for the developers.


def test_a_return_refused_at_its_first_preparation_says_what_to_change_not_recorded(
    h: JobsHarness, monkeypatch,
):
    import server.cadlink.joblane.job_shims as job_shims
    import server.cadlink.joblane.preparation as preparation
    from test_cad_request_status import _publisher, _receipt, _status

    from server.cadlink.joblane import request_status

    _accepted_blocked_solve(h)
    monkeypatch.setattr(preparation, "refuse_blocked_return", lambda *a, **k: False)
    monkeypatch.setattr(job_shims, "refuse_blocked_return", lambda *a, **k: False)
    h._loop.run(job_shims.accept_operation_solve(h.context(), "cmd-1", schedule=False, delivery_released=True))
    monkeypatch.undo()
    recorded = []
    monkeypatch.setattr(request_status, "record_incident", lambda *a, **k: recorded.append(a))
    h.prepare(setup_revision_id=_revision(h.store, _setup()))
    _receipt(h)
    _publisher(h).tick(h.jobs_store)
    record = _status(h)
    assert record["state"] == "couldNotFinish" and record["phase"] == "preparing"
    assert record["failure"]["recorded"] is False
    assert record["failure"]["message"] == _expected_refusal()
    text = wglink_protocol.request_status_text(wglink_protocol.validate_request_status(record, request_id="cmd-1"))
    assert text["headline"] == _expected_refusal()
    assert recorded == []
