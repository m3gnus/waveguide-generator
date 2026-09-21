"""The stalled-``received`` class, reproduced from the add-in's real writer output.

M1 transfer contract, C4: before any fix, each way an accepted request can sit in
``received`` with nothing on screen is reproduced here, producer to consumer:

- the consumer is off (``WG2_CAD_DELIVERY=0``), by live delivery and by file;
- no WGLink folder is selected;
- an update restart is approved;
- a delivery pass hangs;
- a file arrives and nothing tells the page.

The request files are the add-in producer's fixtures
(``fixtures/wglink_inbox/``: written by ``write_solve_request_fields`` for v3
and the new writer for v4). Where a test needs a return WG can retain, the
fixture's bundle reference is pointed at a real ``.wgreturn`` written here;
every other field is the fixture's.

The first test is the root-cause question for operations ``093de99b`` and
``186d2953`` (never replayed here): both arrived live into a WG started with
``WG2_CAD_DELIVERY=0``. It runs the real delivery loop, off and on.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.app import create_app
from server.cadlink import preparation, solve_command
from server.cadlink.api import CAD_DELIVERY_ENV
from server.cadlink.fusion_delivery import CAPABILITIES_FILENAME, ipc_folder
from server.cadlink.operations import PREPARE_AND_SOLVE, RECEIVE_SNAPSHOT
from server.cadlink.solve_command import SOLVE_REQUESTS_DIRECTORY, collect_solve_deliveries
from server.tests.test_cad_preparation import _write_return

FIXTURES = Path(__file__).parent / "fixtures" / "wglink_inbox"
V3_SOLVE = "wg-request-v3-prepare-and-solve.json"
V4_SOLVE = "wg-request-v4-prepare-and-solve.json"
V4_SNAPSHOT = "wg-request-v4-receive-snapshot.json"
V4_NO_KIND = "wg-request-v4-invalid-missing-kind.json"


def fixture(name: str, **overrides: Any) -> dict[str, Any]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    payload.update(overrides)
    return payload


def drop(data_dir: Path, payload: dict[str, Any]) -> Path:
    """Put a request into WG's inbox as the add-in does: staged, then renamed."""

    folder = ipc_folder(data_dir, create=True) / SOLVE_REQUESTS_DIRECTORY
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{payload['commandId']}.json"
    staged = folder / f".{target.name}.tmp"
    staged.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    staged.replace(target)
    return target


def app_for(tmp_path: Path, *, select: bool = True):
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_dir.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    application = create_app(data_dir=data_dir)
    if select:
        application.state.cad_workspace.select(workspace)
    return application, data_dir, workspace


def run_loop(application, seconds: float, *, before=None) -> dict[str, Any] | None:
    """The real start-up delivery loop, for ``seconds``, then its shutdown.

    Returns the delivery status route's answer taken while the loop still runs,
    once that route exists.
    """

    async def main() -> dict[str, Any] | None:
        startup = {getattr(h, "__name__", ""): h for h in application.router.on_startup}
        shutdown = {getattr(h, "__name__", ""): h for h in application.router.on_shutdown}
        await startup["start_cad_delivery"]()
        try:
            if before is not None:
                await asyncio.to_thread(before)
            await asyncio.sleep(seconds)
            try:
                from server.cadlink.api import get_delivery_status
            except ImportError:
                return None
            return await get_delivery_status(SimpleNamespace(app=application))
        finally:
            await shutdown["abandon_cad_preparations_on_shutdown"]()

    return asyncio.run(main())


def delivery_status(observed: dict[str, Any] | None) -> dict[str, Any]:
    assert observed is not None, "WG has no delivery status route"
    return observed


# -- root cause: the consumer is off ------------------------------------------


@pytest.mark.parametrize("consumer", ["off", "on"])
def test_a_received_solve_waits_while_the_consumer_is_off_and_moves_once_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str
) -> None:
    """Confirms the hypothesis for 093de99b/186d2953, with a positive control.

    Delivered live (``deliver_live``, what the route calls) into a WG whose
    consumer is off, a solve is accepted and stays ``received``: nothing else
    ever lists it. The same delivery with the consumer on leaves ``received``
    within a pass.
    """

    if consumer == "off":
        monkeypatch.setenv(CAD_DELIVERY_ENV, "0")
    else:
        monkeypatch.delenv(CAD_DELIVERY_ENV, raising=False)
    application, data_dir, workspace = app_for(tmp_path)
    bundle_path, manifest = _write_return(workspace)
    item = solve_command.DeliveredItem(
        operation_id="op-live", kind=PREPARE_AND_SOLVE, bundle_path=bundle_path,
        manifest_sha256=manifest, return_id="wgr_1",
    )
    store = application.state.cadlink_store

    def deliver() -> None:
        solve_command.deliver_live(
            store, item,
            retain=lambda operation_id: preparation.retain_operation_snapshot(
                store, data_dir, workspace.resolve(), operation_id
            ),
        )

    run_loop(application, 2.5, before=deliver)
    state = store.get_operation("op-live")["state"]
    if consumer == "off":
        assert state == "received"
    else:
        assert state != "received"
    store.close()


def test_the_live_route_refuses_while_the_consumer_is_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A delivery the consumer will never run is refused, retryably, not parked."""

    from server.tests.test_cadlink_live_deliveries import _item, wg
    from server.tests.test_cadlink_live_session import _code

    monkeypatch.setenv(CAD_DELIVERY_ENV, "0")
    with wg(tmp_path, consumer=False) as app:
        bundle_path, manifest = _write_return(app.workspace)
        response = app.deliver(app.token(), _item(bundle_path, manifest))
        assert response.status_code == 409, response.text
        assert _code(response) == "delivery_consumer_disabled"
        assert response.json()["error"]["retryable"] is True
        assert app.ids() == []


@pytest.mark.parametrize("consumer", ["off", "on"])
def test_wg_advertises_solve_delivery_only_while_its_consumer_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str
) -> None:
    """With the consumer off the add-in must refuse at command time, so WG says nothing."""

    if consumer == "off":
        monkeypatch.setenv(CAD_DELIVERY_ENV, "0")
    else:
        monkeypatch.delenv(CAD_DELIVERY_ENV, raising=False)
    application, data_dir, _workspace = app_for(tmp_path)
    handler = {getattr(h, "__name__", ""): h for h in application.router.on_startup}[
        "advertise_fusion_delivery_on_startup"
    ]
    asyncio.run(handler())
    capabilities = json.loads((ipc_folder(data_dir) / CAPABILITIES_FILENAME).read_text(encoding="utf-8"))
    if consumer == "off":
        assert "solveCommandDelivery" not in capabilities
    else:
        assert capabilities["solveCommandDelivery"] == 4
    # Fusion-bound requests keep version 3 either way (contract C3, R1).
    assert capabilities["fusionRequestDelivery"] == 3
    application.state.cadlink_store.close()


# -- the consumer runs and declines -------------------------------------------


def test_no_wglink_folder_is_a_visible_reason_not_a_silent_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CAD_DELIVERY_ENV, raising=False)
    application, data_dir, _workspace = app_for(tmp_path, select=False)
    drop(data_dir, fixture(V3_SOLVE))
    status = delivery_status(run_loop(application, 1.5))
    assert status["consumer"] == "running"
    assert status["declined"] == preparation.NO_WORKSPACE_REASON
    assert status["lastPassCompletedAt"] is not None
    application.state.cadlink_store.close()


def test_an_approved_restart_is_a_visible_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CAD_DELIVERY_ENV, raising=False)
    application, data_dir, _workspace = app_for(tmp_path)
    drop(data_dir, fixture(V3_SOLVE))
    reason = "Waveguide Generator is about to restart to install 0.3.4."
    monkeypatch.setattr(application.state.update_restart, "refusal", lambda: reason)
    assert delivery_status(run_loop(application, 1.5))["declined"] == reason
    application.state.cadlink_store.close()


def test_a_hung_pass_is_distinguishable_from_an_idle_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason is only known when a pass returns; liveness says when one last did."""

    from server.cadlink import api as cadlink_api

    monkeypatch.delenv(CAD_DELIVERY_ENV, raising=False)
    application, _data_dir, _workspace = app_for(tmp_path)

    async def hang(*_args: Any, **_kwargs: Any) -> list[str]:
        await asyncio.Event().wait()
        return []

    monkeypatch.setattr(cadlink_api, "run_delivery_pass", hang)
    status = delivery_status(run_loop(application, 1.5))
    assert status["lastPassCompletedAt"] is None
    assert status["passStartedAt"] is not None
    application.state.cadlink_store.close()


def test_an_idle_pass_reports_liveness_and_no_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control for the three above: a running, idle consumer says nothing is wrong."""

    monkeypatch.delenv(CAD_DELIVERY_ENV, raising=False)
    application, _data_dir, _workspace = app_for(tmp_path)
    status = delivery_status(run_loop(application, 1.5))
    assert status["consumer"] == "running"
    assert status["declined"] is None
    assert status["lastPassCompletedAt"] is not None
    application.state.cadlink_store.close()


# -- arrival by file ----------------------------------------------------------


def test_a_file_arrival_is_published_as_it_is_accepted(tmp_path: Path) -> None:
    """Gap (i): the inbox reader publishes the row it creates, so the page hears of it."""

    application, data_dir, workspace = app_for(tmp_path)
    bundle_path, manifest = _write_return(workspace)
    drop(data_dir, fixture(V3_SOLVE, bundlePath=bundle_path, manifestSha256=manifest))
    published: list[dict[str, Any]] = []
    collect_solve_deliveries(data_dir, application.state.cadlink_store, publish=published.append)
    assert [row["operation_id"] for row in published] == [fixture(V3_SOLVE)["commandId"]]
    assert published[0]["state"] == "received"
    application.state.cadlink_store.close()


def test_a_v4_snapshot_file_is_taken_as_a_snapshot_not_left_on_disk(tmp_path: Path) -> None:
    application, data_dir, workspace = app_for(tmp_path)
    bundle_path, manifest = _write_return(workspace)
    path = drop(data_dir, fixture(V4_SNAPSHOT, bundlePath=bundle_path, manifestSha256=manifest))
    collect_solve_deliveries(data_dir, application.state.cadlink_store)
    row = application.state.cadlink_store.get_operation(fixture(V4_SNAPSHOT)["commandId"])
    assert row is not None and row["kind"] == RECEIVE_SNAPSHOT
    assert not path.exists()
    application.state.cadlink_store.close()


def test_an_identified_but_invalid_v4_file_is_refused_visibly_and_removed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    application, data_dir, _workspace = app_for(tmp_path)
    path = drop(data_dir, fixture(V4_NO_KIND))
    refused: list[dict[str, Any]] = []
    with caplog.at_level(logging.WARNING):
        collect_solve_deliveries(data_dir, application.state.cadlink_store, refuse=refused.append)
    assert not path.exists()
    assert not any(p.name.startswith(solve_command.CLAIM_PREFIX) for p in path.parent.iterdir())
    assert [item["operationId"] for item in refused] == [fixture(V4_NO_KIND)["commandId"]]
    assert "kind" in refused[0]["reason"]
    assert application.state.cadlink_store.get_operation(fixture(V4_NO_KIND)["commandId"]) is None
    application.state.cadlink_store.close()


def test_the_real_v3_solve_file_is_still_a_solve(tmp_path: Path) -> None:
    """Positive control on the reader: the pinned add-in's file keeps working."""

    application, data_dir, workspace = app_for(tmp_path)
    bundle_path, manifest = _write_return(workspace)
    drop(data_dir, fixture(V3_SOLVE, bundlePath=bundle_path, manifestSha256=manifest))
    collect_solve_deliveries(data_dir, application.state.cadlink_store)
    row = application.state.cadlink_store.get_operation(fixture(V3_SOLVE)["commandId"])
    assert row is not None and row["kind"] == PREPARE_AND_SOLVE and row["state"] == "received"
    application.state.cadlink_store.close()


# -- the consumer's state reaches the page without a clock (review F4) ---------


def _pushes(application) -> list[dict[str, Any]]:
    pushed: list[dict[str, Any]] = []
    application.state.jobs_runtime.events.publish = pushed.append
    return pushed


def test_a_pass_that_hangs_after_passes_completed_is_pushed_as_hung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.cadlink import api as cadlink_api

    monkeypatch.delenv(CAD_DELIVERY_ENV, raising=False)
    monkeypatch.setattr(cadlink_api, "DELIVERY_PASS_HUNG_S", 0.3)
    application, _data_dir, _workspace = app_for(tmp_path)
    real = cadlink_api.run_delivery_pass
    calls = {"n": 0}

    async def hang_on_the_third(*args: Any, **kwargs: Any) -> list[str]:
        calls["n"] += 1
        if calls["n"] >= 3:
            await asyncio.Event().wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(cadlink_api, "run_delivery_pass", hang_on_the_third)
    pushed = _pushes(application)
    status = delivery_status(run_loop(application, 3.2))

    assert status["lastPassCompletedAt"] is not None  # earlier passes did complete
    assert status["passHung"] is True
    hung = [m["status"] for m in pushed if m.get("kind") == "cadDeliveryStatus" and m["status"]["passHung"]]
    assert len(hung) == 1
    application.state.cadlink_store.close()


def test_a_new_declined_reason_is_pushed_once_and_an_idle_pass_pushes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CAD_DELIVERY_ENV, raising=False)
    # No WGLink folder: every pass declines with the same reason.
    application, _data_dir, _workspace = app_for(tmp_path, select=False)
    pushed = _pushes(application)
    run_loop(application, 2.5)
    declined = [m for m in pushed if m.get("kind") == "cadDeliveryStatus"]
    assert len(declined) == 1
    assert declined[0]["status"]["declined"] == preparation.NO_WORKSPACE_REASON
    application.state.cadlink_store.close()

    # The zero: an idle consumer, several passes, no status pushed at all.
    (tmp_path / "idle").mkdir()
    idle, _d, _w = app_for(tmp_path / "idle")
    quiet = _pushes(idle)
    run_loop(idle, 2.5)
    assert [m for m in quiet if m.get("kind") == "cadDeliveryStatus"] == []
    idle.state.cadlink_store.close()
