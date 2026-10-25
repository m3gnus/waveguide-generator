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
from server.cadlink.intake import solve_command
from server.cadlink.intake.preflight import admission_refusal, blocking_findings
from server.cadlink.intake.solve_command import InvalidRequest, _command_from_payload, collect_solve_deliveries
from server.cadlink.joblane.preparation import retain_operation_snapshot, settle_snapshot_operation
from server.cadlink.operations import (
    PREFLIGHT_BLOCKED,
    PREPARE_AND_SOLVE,
    REASON_CODES,
    REJECTED,
    TERMINAL_STATES,
    prepare_and_solve_request,
    request_digest,
)
from server.jobs.cad_intent import intent_of
from server.mesh.builder import MAX_SOLVER_MESH_ARTIFACT_TRIANGLES, _enforce_artifact_triangle_ceiling

from cad_backends import JobsHarness
from test_cad_preparation import _collect, _manifest, _revision, _setup, _write_return
from test_cad_request_acknowledgement import _ack
from test_cad_solve_delivery import _file, _payload

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


def test_wg_owns_the_ceiling_the_addin_falls_back_to():
    assert wglink_protocol.DEFAULT_SOLVED_TRIANGLE_LIMIT == MAX_SOLVER_MESH_ARTIFACT_TRIANGLES
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
    admitted = []

    def admit(operation_id):
        admitted.append(operation_id)
        return h.store.get_operation(operation_id)["state"] in TERMINAL_STATES

    collect_solve_deliveries(
        h.data_dir, h.store, accept_solve=admit,
        retain=lambda op: retain_operation_snapshot(h.store, h.data_dir, h.workspace.resolve(), op),
    )
    row = h.store.get_operation("cmd-1")
    assert (row["state"], row["reason"]) == ("rejected", PREFLIGHT_BLOCKED)
    ack = _ack(h.data_dir, "cmd-1")
    assert ack["outcome"] == "refused"
    assert ack["reason"] == _expected_refusal() == "2 sources share the role HF. Use one source per role."
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
    import server.cadlink.joblane.preparation as preparation

    def broken(_manifest):
        raise RuntimeError("rule crashed")

    monkeypatch.setattr(preparation, "admission_refusal", broken)
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
