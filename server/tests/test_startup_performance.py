"""Coverage for the startup and transfer costs paid on every cold load.

Two independent findings from the 2026-08-06 load review:

* the SPA and the results payloads were served uncompressed, and
* every mesher entry point imports lazily, so the first control a user touched
  paid for the whole import graph (``POST /api/design/symmetry`` measured at
  1152 ms first call against a running server, 57-150 ms after).
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import logging
from pathlib import Path
import sys
import threading
import time
import types
from typing import Any

import pytest

from server.app import create_app
from server.engines.registry import EngineInfo, EngineRegistry
from server.mesh import prewarm
from server.platform.warmup import BackgroundWarmup


class HeaderClient:
    """Dependency-free ASGI harness that keeps the response headers.

    ``test_app_batch_e.py`` has a sibling of this that drops them; content
    negotiation is exactly what these tests are about.
    """

    __test__ = False

    def __init__(self, app: Any) -> None:
        self.app = app

    def get(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        async def request() -> tuple[int, dict[str, str], bytes]:
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

            raw_headers = [(b"host", b"127.0.0.1:3100")]
            raw_headers.extend(
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in (headers or {}).items()
            )
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
            decoded = {
                name.decode("latin-1").lower(): value.decode("latin-1")
                for name, value in start.get("headers", [])
            }
            return start["status"], decoded, body

        return asyncio.run(request())


def test_spa_document_is_gzipped_for_clients_that_accept_it(tmp_path: Path) -> None:
    client = HeaderClient(create_app(data_dir=tmp_path))
    status, headers, body = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert status == 200
    assert headers.get("content-encoding") == "gzip"
    # The compressed bytes must still be the document, not merely smaller.
    assert b"<div id=\"root\">" in gzip.decompress(body)


def test_uncompressed_client_still_gets_the_plain_document(tmp_path: Path) -> None:
    client = HeaderClient(create_app(data_dir=tmp_path))
    status, headers, body = client.get("/")
    assert status == 200
    assert "content-encoding" not in headers
    assert b"<div id=\"root\">" in body


def test_api_json_over_the_floor_is_compressed_and_still_parses(tmp_path: Path) -> None:
    """The SPA is the biggest win, but result payloads run to hundreds of kB.

    ``/openapi.json`` is the fixture rather than a domain route because its size
    does not depend on the host: ``/api/capabilities`` is 423-620 bytes
    depending on how verbose the engine probe's reasons are on this machine,
    which straddles the 500-byte floor and makes the assertion a coin flip.
    """

    client = HeaderClient(create_app(data_dir=tmp_path))
    status, headers, body = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})
    assert status == 200
    assert headers.get("content-encoding") == "gzip"
    decompressed = gzip.decompress(body)
    assert len(decompressed) > 500
    assert "openapi" in json.loads(decompressed)


@pytest.mark.parametrize("path", ["/ws/preview", "/ws/jobs"])
def test_gzip_middleware_leaves_websockets_alone(tmp_path: Path, path: str) -> None:
    """A middleware that mishandled the websocket scope would kill the preview.

    These two sockets carry every live geometry frame and every job event, and
    they now traverse GZipMiddleware plus the two http-only middlewares on the
    way to their route. This is a passthrough guard, not a proof that GZip is
    installed -- removing GZip would leave it passing, which is the point.
    """

    application = create_app(data_dir=tmp_path)
    sent: list[dict[str, Any]] = []

    async def connect() -> None:
        incoming: list[dict[str, Any]] = [{"type": "websocket.connect"}]

        async def receive() -> dict[str, Any]:
            if incoming:
                return incoming.pop(0)
            return {"type": "websocket.disconnect", "code": 1000}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await application(
            {
                "type": "websocket",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "scheme": "ws",
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": b"",
                "root_path": "",
                "headers": [(b"host", b"127.0.0.1:3100")],
                "client": ("127.0.0.1", 12345),
                "server": ("127.0.0.1", 3100),
                "subprotocols": [],
            },
            receive,
            send,
        )

    asyncio.run(connect())
    kinds = [message["type"] for message in sent]
    assert "websocket.accept" in kinds, kinds
    # Accepted, not refused by a middleware that treated it as a request.
    assert kinds[0] == "websocket.accept"


def test_small_json_stays_uncompressed(tmp_path: Path) -> None:
    """Below the 500-byte floor, framing costs more than compression saves."""

    client = HeaderClient(create_app(data_dir=tmp_path))
    status, headers, body = client.get("/health", headers={"Accept-Encoding": "gzip"})
    assert status == 200
    assert "content-encoding" not in headers
    assert len(body) < 500
    assert json.loads(body)["uptime"] >= 0


def test_create_app_registers_every_prewarm(tmp_path: Path) -> None:
    application = create_app(data_dir=tmp_path)
    startup = {handler.__name__ for handler in application.router.on_startup}
    shutdown = {handler.__name__ for handler in application.router.on_shutdown}
    assert {"prewarm_gmsh_worker", "prewarm_mesher", "prewarm"} <= startup
    assert {"shutdown_mesher_prewarm", "shutdown_prewarm"} <= shutdown
    # The BEMPP worker is warmed at boot, so it must also be stopped with the
    # app: an atexit hook does not run when a launcher is TerminateProcess'd.
    assert "prewarm_bempp_worker" in startup
    assert "shutdown_bempp_worker" in shutdown


def test_the_bempp_worker_prewarm_is_registered_by_default(tmp_path: Path) -> None:
    """The largest gap between a server start and a first result closes itself.

    The BEMPP worker's own initialization measured 25.4 s (OpenCL) and 61.4 s
    (numba) on the reference Windows VM, and it is paid in a child the parent
    can kill outright -- so unlike the in-process warmup below, warming it
    costs shutdown nothing and does not need an opt-in.
    """

    startup = {
        handler.__name__ for handler in create_app(data_dir=tmp_path).router.on_startup
    }
    assert "prewarm_bempp_worker" in startup


def test_the_bempp_worker_prewarm_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.solver import bempp_process, warmup as solver_warmup

    monkeypatch.setattr(
        bempp_process, "prewarm_bempp_process", lambda: pytest.fail("must not warm")
    )
    monkeypatch.setenv("WG2_SOLVER_WARMUP", "0")

    assert solver_warmup.prewarm_bempp_worker_for_engine("bempp") is False


def test_the_bempp_worker_prewarm_skips_hosts_auto_solves_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Metal or GPU host must not spend a process on an unreachable fallback."""

    from server.solver import bempp_process, warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    prewarmed: list[str] = []
    monkeypatch.setattr(
        bempp_process, "prewarm_bempp_process", lambda: prewarmed.append("bempp")
    )

    assert solver_warmup.prewarm_bempp_worker_for_engine("metal") is False
    assert solver_warmup.prewarm_bempp_worker_for_engine(None) is False
    assert prewarmed == []

    assert solver_warmup.prewarm_bempp_worker_for_engine("bempp") is True
    assert prewarmed == ["bempp"]


def test_the_beat_worker_prewarm_is_registered_and_stopped_by_default(
    tmp_path: Path,
) -> None:
    """A GPU host's first solve waited through the whole Julia start-up.

    BEAT keeps one persistent Julia worker per server process, so the cost is
    paid once -- but nothing paid it until a user asked for a solve. Warming it
    is registered by default for the same reason as BEMPP's: the work is in a
    child process the app can terminate, so it cannot lengthen a Quit. Which
    also means it must be stopped with the app.
    """

    application = create_app(data_dir=tmp_path)
    startup = {handler.__name__ for handler in application.router.on_startup}
    shutdown = {handler.__name__ for handler in application.router.on_shutdown}
    assert "prewarm_beat_worker" in startup
    assert "shutdown_beat_worker" in shutdown


def _beat_quit_hook(application: Any) -> Any:
    """``shutdown_beat_worker`` itself, out of the router it is registered on.

    It is a closure over ``create_app``'s locals, so there is no other handle
    on it -- and the registration is what the test above pins, not the body.
    """

    return next(
        handler
        for handler in application.router.on_shutdown
        if handler.__name__ == "shutdown_beat_worker"
    )


def test_quitting_detaches_the_beat_worker_rather_than_killing_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A warm Julia runtime should survive Quit, not be thrown away by it.

    Since ``hornlab_beat_bem`` 94deec1 the worker lives in a persistent host
    process that outlives this one, and the next launch adopts it instead of
    paying the cold start again. That is opt-in at exactly one call site: the
    quit hook asks for ``detach_workers``, not ``shutdown_workers``. Calling
    the wrong sibling costs a user the whole Julia start-up on every launch
    and would be invisible -- both hooks quit cleanly.
    """

    application = create_app(data_dir=tmp_path)

    called: list[str] = []
    package = types.ModuleType("hornlab_beat_bem")
    package.detach_workers = lambda: called.append("detach")  # type: ignore[attr-defined]
    package.shutdown_workers = lambda **_: called.append("shutdown")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hornlab_beat_bem", package)

    asyncio.run(_beat_quit_hook(application)())

    assert called == ["detach"]


def test_quitting_survives_a_beat_package_that_predates_detach_workers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An older module pin must not turn Quit into a traceback.

    ``pins.json`` and the installed environment can disagree -- an editable
    development install is the ordinary way it happens -- and the failure mode
    is asymmetric: a missing name here fires while the app is already tearing
    down. ``from ... import detach_workers`` raises ``ImportError`` against a
    package that only has ``shutdown_workers``, which the hook's existing
    ``(ImportError, OSError)`` guard already catches; this pins that, because
    nothing else does.
    """

    application = create_app(data_dir=tmp_path)

    package = types.ModuleType("hornlab_beat_bem")
    package.shutdown_workers = lambda **_: pytest.fail(  # type: ignore[attr-defined]
        "an old package has no persistent host, so nothing should be stopped here"
    )
    monkeypatch.setitem(sys.modules, "hornlab_beat_bem", package)

    asyncio.run(_beat_quit_hook(application)())


def test_the_beat_worker_prewarm_only_warms_the_engine_auto_chose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTO's order is metal, beat, bempp -- each prewarm claims exactly one.

    Both worker prewarms are registered unconditionally, so the thing that
    stops a Mac from booting a Julia process it will never solve on is this
    guard and nothing else.
    """

    from server.solver import warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    warmed: list[str] = []
    monkeypatch.setattr(
        solver_warmup, "_warm_beat", lambda status: warmed.append("beat")
    )

    assert solver_warmup.prewarm_beat_worker_for_engine("metal") is False
    assert solver_warmup.prewarm_beat_worker_for_engine("bempp") is False
    assert solver_warmup.prewarm_beat_worker_for_engine(None) is False
    assert warmed == []

    assert solver_warmup.prewarm_beat_worker_for_engine("beat") is True
    assert warmed == ["beat"]


def test_the_beat_worker_prewarm_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.solver import warmup as solver_warmup

    monkeypatch.setattr(
        solver_warmup, "_warm_beat", lambda backend: pytest.fail("must not warm")
    )
    monkeypatch.setenv("WG2_SOLVER_WARMUP", "0")

    assert solver_warmup.prewarm_beat_worker_for_engine("beat-cuda") is False


def test_the_beat_warmup_runs_a_solve_not_just_a_process_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``mode="worker"`` would leave the expensive half on the first real solve.

    Booting the Julia worker pays start-up and package loading; the JIT and the
    GPU kernel compilation are only paid by running something. ``tiny`` solves
    one frequency on a four-triangle tetrahedron, which is what makes the
    warmup worth a process at all.
    """

    from server.solver import warmup as solver_warmup

    calls: list[dict[str, object]] = []
    module = types.SimpleNamespace(
        warm_up=lambda **kwargs: calls.append(kwargs)
    )
    monkeypatch.setitem(sys.modules, "hornlab_beat_bem", module)

    solver_warmup._warm_beat("cuda")

    assert calls == [{"beat_backend": "cuda", "mode": "tiny"}]


@pytest.mark.parametrize("backend", ["cuda", "rocm", "metal", "cpu"])
def test_the_beat_warmup_warms_the_backend_the_solve_will_use(
    monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """The warmup and the solve must resolve one accelerator, not two.

    ``_warm_beat`` used to re-derive the backend from a probe result, and
    spelled its fallback ``"cuda"`` while the solve in ``server/solver/beat.py``
    spelled the same fallback ``"cpu"``. A probe that reported available without
    naming a backend therefore warmed a CUDA context -- on a host that need not
    have one -- and then solved on the CPU, so the warmup paid a device
    initialisation for a path the user's solve never took and left the path it
    did take cold.

    Now that each backend is its own engine, neither side derives anything: the
    engine name carries the backend, the prewarm reads it off that name, and the
    adapter is constructed with it. This asserts that chain end to end, which is
    the property that actually matters -- the two agree -- rather than the
    constant either one happens to use.
    """

    from server.engines.registry import create_engine
    from server.solver import warmup as solver_warmup

    calls: list[dict[str, object]] = []
    module = types.SimpleNamespace(warm_up=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setitem(sys.modules, "hornlab_beat_bem", module)
    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)

    engine = f"beat-{backend}"
    assert solver_warmup.prewarm_beat_worker_for_engine(engine) is True

    assert calls == [{"beat_backend": backend, "mode": "tiny"}]
    assert create_engine(engine).backend == backend


def test_the_beat_prewarm_ignores_engines_that_are_not_beat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.solver import warmup as solver_warmup

    monkeypatch.setattr(
        solver_warmup, "_warm_beat", lambda backend: pytest.fail("must not warm")
    )
    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)

    for engine in ("metal", "bempp", "axisym", "dryrun", None):
        assert solver_warmup.prewarm_beat_worker_for_engine(engine) is False


def test_the_beat_prewarm_still_answers_the_legacy_family_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored ``beat`` the registry could not map still warms something.

    ``resolve_prewarm_engine`` normally hands this hook a resolved variant, so
    the bare name only survives when no ``beat-*`` was available to map it to.
    Falling back to the package probe there is what the hook did for that name
    before the split, and it is better than warming nothing.
    """

    from server.solver import beat as beat_adapter
    from server.solver import warmup as solver_warmup

    calls: list[dict[str, object]] = []
    module = types.SimpleNamespace(warm_up=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setitem(sys.modules, "hornlab_beat_bem", module)
    monkeypatch.setattr(
        beat_adapter, "beat_status", lambda: {"available": True, "backend": "rocm"}
    )
    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)

    assert solver_warmup.prewarm_beat_worker_for_engine("beat") is True
    assert calls == [{"beat_backend": "rocm", "mode": "tiny"}]


def test_an_unnamed_beat_backend_falls_back_to_the_one_every_host_has() -> None:
    """A guess that costs a slow solve beats one that costs a failed device init."""

    from server.solver import beat as beat_adapter

    assert beat_adapter.BEAT_FALLBACK_BACKEND == "cpu"
    assert beat_adapter.resolve_beat_backend({"available": True}) == "cpu"
    assert beat_adapter.resolve_beat_backend({"backend": "metal"}) == "metal"


def test_the_bempp_worker_prewarm_takes_auto_from_the_registrys_one_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probing separately ran a second cold detect_engines() racing the first.

    ``EngineRegistry.prewarm`` and this handler started 2 ms apart on different
    threads, and neither ``lru_cache`` nor the uncached ``circsym_status``
    serialises a miss, so both did the full probe. Awaiting ``capabilities()``
    joins the one snapshot instead.
    """

    from server.app import bempp_worker_prewarm
    from server.engines.registry import EngineInfo, EngineRegistry
    from server.solver import warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    probes = 0

    def detector() -> list[EngineInfo]:
        nonlocal probes
        probes += 1
        return [EngineInfo("bempp", True, "test", "0.1.0")]

    engines: list[str | None] = []
    monkeypatch.setattr(
        solver_warmup,
        "prewarm_bempp_worker_for_engine",
        lambda engine: engines.append(engine) or True,
    )

    registry = EngineRegistry(detector=detector)

    async def exercise() -> None:
        await registry.prewarm()
        await bempp_worker_prewarm(registry)
        await registry.shutdown_prewarm()

    asyncio.run(exercise())

    assert engines == ["bempp"]
    assert probes == 1


def test_the_bempp_worker_prewarm_never_blocks_startup(tmp_path: Path) -> None:
    """The handler only schedules; the registry probe it needs is not awaited here."""

    application = create_app(data_dir=tmp_path)
    handler = next(
        item
        for item in application.router.on_startup
        if item.__name__ == "prewarm_bempp_worker"
    )

    async def exercise() -> float:
        started = time.perf_counter()
        await handler()
        elapsed = time.perf_counter() - started
        task = application.state.bempp_prewarm_task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return elapsed

    assert asyncio.run(exercise()) < 0.05


def test_the_bempp_warmup_targets_the_process_that_actually_solves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression this whole path exists to close.

    warmup.py used to run its BEMPP warmup solve in the API process. Every
    BEMPP solve runs in the worker child, and bempp-cl's hot numba kernels
    carry no ``cache=True``, so the JIT ran again in the child regardless:
    measured on the reference Windows VM, an in-parent warmup spent 24.2 s and
    left the first child solve at 24.3 s, unchanged.
    """

    from server.solver import bempp_process, warmup as solver_warmup

    calls: list[str] = []
    monkeypatch.setattr(
        bempp_process, "prewarm_bempp_process", lambda: calls.append("worker")
    )
    monkeypatch.setattr(
        solver_warmup,
        "warm_bempp_in_this_process",
        lambda _status: calls.append("in-parent"),
    )

    solver_warmup._warm_bempp({"available": True, "assembly_backend": "opencl"})

    assert calls == ["worker"]


def test_the_solver_warmup_is_opt_in(tmp_path: Path) -> None:
    """The explicit app API remains available without making it a default."""

    default_startup = {
        handler.__name__ for handler in create_app(data_dir=tmp_path).router.on_startup
    }
    assert "prewarm_solver" not in default_startup

    warmed = create_app(data_dir=tmp_path, solver_warmup=True)
    assert "prewarm_solver" in {handler.__name__ for handler in warmed.router.on_startup}


def test_the_solver_warmup_handler_never_blocks_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup runs before the listen socket exists, so this must only schedule."""

    from server.solver import warmup as solver_warmup

    started = 0

    def fake_start() -> None:
        nonlocal started
        started += 1

    monkeypatch.setattr(solver_warmup, "start_solver_warmup", fake_start)
    from server.app import prewarm_solver

    asyncio.run(prewarm_solver())
    assert started == 1


def test_the_solver_warmup_can_be_switched_off() -> None:
    from server.solver import warmup as solver_warmup

    import os

    previous = os.environ.get("WG2_SOLVER_WARMUP")
    os.environ["WG2_SOLVER_WARMUP"] = "0"
    try:
        assert solver_warmup.start_solver_warmup() is None
    finally:
        if previous is None:
            os.environ.pop("WG2_SOLVER_WARMUP", None)
        else:
            os.environ["WG2_SOLVER_WARMUP"] = previous


def test_solver_warmup_follows_auto_engine_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Metal host must not spend seconds warming an unused BEMPP fallback."""

    from server.solver import metal, warmup

    calls: list[str] = []
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True})
    monkeypatch.setattr(warmup, "_warm_metal", lambda: calls.append("metal"))
    monkeypatch.setattr(
        warmup,
        "_warm_bempp",
        lambda _status: calls.append("bempp"),
    )

    warmup._run_warmup()

    assert calls == ["metal"]


def test_solver_warmup_falls_back_to_bempp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metal and BEAT both absent, so AUTO's next stop is BEMPP.

    ``beat_status`` must be stubbed unavailable here, not left to the host: on
    any machine where BEAT reports available (an Apple Silicon Mac included),
    the real probe would route ``_run_warmup`` into the BEAT branch instead of
    the BEMPP one this test is asserting, and the test would fail for a reason
    that has nothing to do with the fallback logic under test.
    """

    from server.solver import beat as beat_adapter
    from server.solver import bempp, metal, warmup

    calls: list[tuple[str, object]] = []
    status = {"available": True, "assembly_backend": "numba"}
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": False})
    monkeypatch.setattr(beat_adapter, "beat_status", lambda: {"available": False})
    monkeypatch.setattr(bempp, "bempp_status", lambda: status)
    monkeypatch.setattr(warmup, "_warm_metal", lambda: calls.append(("metal", None)))
    monkeypatch.setattr(warmup, "_warm_bempp", lambda value: calls.append(("bempp", value)))

    warmup._run_warmup()

    assert calls == [("bempp", status)]


def test_solver_warmup_skips_when_no_physical_engine_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every engine unavailable, so the warmup must warm nothing.

    Same host-dependence as the sibling test above: without stubbing
    ``beat_status`` unavailable too, a BEAT-capable host would take the BEAT
    branch and call the unstubbed ``_warm_beat``, not the "skip" path this
    test means to exercise.
    """

    from server.solver import beat as beat_adapter
    from server.solver import bempp, metal, warmup

    calls: list[str] = []
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": False, "reason": "no Metal"})
    monkeypatch.setattr(beat_adapter, "beat_status", lambda: {"available": False, "reason": "no BEAT"})
    monkeypatch.setattr(bempp, "bempp_status", lambda: {"available": False, "reason": "no BEMPP"})
    monkeypatch.setattr(warmup, "_warm_metal", lambda: calls.append("metal"))
    monkeypatch.setattr(warmup, "_warm_bempp", lambda _status: calls.append("bempp"))

    warmup._run_warmup()

    assert calls == []


def test_warmup_solver_chatter_is_filtered_but_our_own_line_survives() -> None:
    """A hundred lines of assembler timings must not land in the user's log.

    Filtering on the thread rather than on the library keeps a real solve's
    diagnostics intact if an explicitly requested warmup overlaps it.
    """

    from server.solver.warmup import WARMUP_THREAD_NAME, _QuietWarmupFilter

    quiet = _QuietWarmupFilter()

    def record(name: str, thread: str) -> logging.LogRecord:
        made = logging.LogRecord(name, logging.INFO, __file__, 1, "m", None, None)
        made.threadName = thread
        return made

    assert not quiet.filter(record("bempp", WARMUP_THREAD_NAME))
    assert not quiet.filter(record("hornlab_bempp_bem.bie", WARMUP_THREAD_NAME))
    assert quiet.filter(record("wg.solver.warmup", WARMUP_THREAD_NAME))
    # A real solve logs from a different thread and is never touched.
    assert quiet.filter(record("bempp", "asyncio_0"))


def test_hashed_assets_are_immutable_and_the_document_is_not(tmp_path: Path) -> None:
    """The SPA re-downloads three multi-hundred-kB chunks without this.

    Vite content-hashes everything under /assets/, so a changed build is a
    changed URL; index.html is the one name that never changes and must never
    be cached, or the app pins itself to a build that may be gone.
    """

    client = HeaderClient(create_app(data_dir=tmp_path))
    _status, headers, _body = client.get("/")
    assert headers.get("cache-control") == "no-cache"

    assets = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "assets"
    entry = next((item for item in assets.glob("*.js")), None)
    if entry is None:  # pragma: no cover - only when the SPA is not built
        pytest.skip("frontend/dist/assets is not built")
    status, headers, _body = client.get(f"/assets/{entry.name}")
    assert status == 200
    assert headers.get("cache-control") == "public, max-age=31536000, immutable"


def test_import_mesher_modules_reports_only_what_imported() -> None:
    imported = prewarm.import_mesher_modules(
        ["json", "server.mesh.prewarm_does_not_exist", "gzip"]
    )
    assert imported == ["json", "gzip"]


def test_a_broken_optional_mesher_never_fails_startup() -> None:
    """The lazy import at the real call site owns the useful error message."""

    assert prewarm.import_mesher_modules(["hornlab_mesher_absent.nothing"]) == []


def test_prewarm_runs_in_the_background_and_shuts_down_cleanly() -> None:
    async def scenario() -> None:
        await prewarm.prewarm_mesher()
        task = prewarm.mesher_warmup.task
        assert task is not None
        # Startup must not block on the import graph.
        assert not task.done()
        await prewarm.shutdown_mesher_prewarm()
        assert task.done()
        assert prewarm.mesher_warmup.task is None

    asyncio.run(scenario())


def test_prewarm_does_not_stack_duplicate_tasks() -> None:
    async def scenario() -> None:
        await prewarm.prewarm_mesher()
        first = prewarm.mesher_warmup.task
        await prewarm.prewarm_mesher()
        assert prewarm.mesher_warmup.task is first
        await prewarm.shutdown_mesher_prewarm()

    asyncio.run(scenario())


def test_shutdown_without_a_prewarm_is_a_no_op() -> None:
    asyncio.run(prewarm.shutdown_mesher_prewarm())
    assert prewarm.mesher_warmup.task is None


def test_engine_probe_is_warmed_off_the_request_path() -> None:
    """The probe imports Metal and BEMPP: 500-950 ms on the first request."""

    probes = 0

    def detector() -> list[EngineInfo]:
        nonlocal probes
        probes += 1
        return [EngineInfo(name="dryrun", available=True, reason="test", version="builtin")]

    async def scenario() -> None:
        registry = EngineRegistry(detector=detector)
        await registry.prewarm()
        assert probes == 0, "startup must not block on the probe"
        await asyncio.sleep(0)
        while registry.warmup.task is not None and not registry.warmup.task.done():
            await asyncio.sleep(0)
        assert probes == 1
        # The request that follows reads the cache the warmup filled.
        assert (await registry.capabilities())[0].name == "dryrun"
        assert probes == 1
        await registry.shutdown_prewarm()

    asyncio.run(scenario())


def test_shutdown_drains_thread_work_instead_of_abandoning_it() -> None:
    """`task.cancel()` does not stop `asyncio.to_thread`, so stop() waits."""

    finished = threading.Event()

    def slow_work() -> None:
        time.sleep(0.05)
        finished.set()

    async def scenario() -> None:
        warmup = BackgroundWarmup("slow", lambda: asyncio.to_thread(slow_work))
        await warmup.start()
        assert not finished.is_set()
        await warmup.stop()
        # Quiescence, not just a reaped task: the thread really is done.
        assert finished.is_set()
        assert warmup.task is None

    asyncio.run(scenario())


def test_a_wedged_warmup_cannot_block_shutdown_forever() -> None:
    """Past the drain timeout the task is cancelled and shutdown proceeds."""

    async def never_finishes() -> None:
        await asyncio.Event().wait()

    async def scenario() -> None:
        warmup = BackgroundWarmup("wedged", never_finishes)
        await warmup.start()
        began = time.monotonic()
        await warmup.stop(drain_timeout=0.05)
        elapsed = time.monotonic() - began
        assert 0.05 <= elapsed < 2.0, f"drained in {elapsed:.3f}s"
        assert warmup.task is None

    asyncio.run(scenario())


def test_a_wedged_thread_releases_the_loop_but_keeps_running() -> None:
    """The honest limit of the drain: `stop()` bounds the *loop*, not the thread.

    Both real warmups work inside ``asyncio.to_thread``, and no timeout can
    reach into a running executor thread. So past the drain timeout `stop()`
    returns on schedule while the thread is still going -- which is the
    behaviour to know about, because the loop's own executor shutdown then
    waits for it. The 39-260 ms these warmups actually take is why that is
    tolerable rather than a hazard.
    """

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def wedged() -> None:
        entered.set()
        release.wait(timeout=10.0)
        finished.set()

    async def scenario() -> None:
        warmup = BackgroundWarmup("wedged-thread", lambda: asyncio.to_thread(wedged))
        await warmup.start()
        await asyncio.to_thread(entered.wait, 5.0)
        began = time.monotonic()
        await warmup.stop(drain_timeout=0.05)
        assert time.monotonic() - began < 2.0, "stop() must not wait on the thread"
        assert not finished.is_set(), "the thread is still running, as documented"
        # Let it go before the loop closes, or shutdown_default_executor blocks.
        release.set()
        await asyncio.to_thread(finished.wait, 5.0)

    asyncio.run(scenario())
    assert finished.is_set()


def test_a_failing_probe_never_escapes_the_warmup() -> None:
    def detector() -> list[EngineInfo]:
        raise RuntimeError("probe exploded")

    async def scenario() -> None:
        registry = EngineRegistry(detector=detector)
        await registry.prewarm()
        task = registry.warmup.task
        assert task is not None
        await task
        # Swallowed here; the real /api/capabilities call raises it where the
        # message is useful.
        assert task.exception() is None

    asyncio.run(scenario())


class _StubSettings:
    """The one method ``persisted_engine_preference`` uses of ``SettingsStore``."""

    def __init__(self, stored: object) -> None:
        self._stored = stored

    def get(self, namespace: str) -> object | None:
        return self._stored if namespace == "solveOptions" else None


def _persisted(engine: str) -> _StubSettings:
    """A settings store holding what the frontend actually writes.

    Zustand persists under a ``state`` key, and ``SettingsStore`` keeps the
    namespace payload as the opaque JSON *string* the frontend PUT, so the
    real value is doubly encoded. A test that skipped either layer would pass
    against a reader that cannot parse the file on disk.
    """

    return _StubSettings(json.dumps({"state": {"engine": engine, "solverMode": "full_3d"}}))


def test_the_persisted_engine_preference_is_read_through_both_encodings() -> None:
    from server.solver.warmup import persisted_engine_preference

    assert persisted_engine_preference(_persisted("beat")) == "beat"
    assert persisted_engine_preference(_persisted("AUTO")) == "auto"


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "not json at all",
        json.dumps({"state": {}}),
        json.dumps({"state": {"engine": 7}}),
        json.dumps({"engine": "beat"}),  # no Zustand envelope
        json.dumps([]),
    ],
)
def test_an_unreadable_preference_is_no_preference_rather_than_an_error(
    stored: object,
) -> None:
    """The frontend owns this schema, so every other shape must cost a warmup.

    ``server/settings/store.py`` stores namespaces opaquely precisely so the
    two sides do not have to agree; a reader that raised on an unexpected
    shape would turn a frontend schema change into a failed boot.
    """

    from server.solver.warmup import persisted_engine_preference

    assert persisted_engine_preference(_StubSettings(stored)) is None
    assert persisted_engine_preference(None) is None


def test_the_beat_prewarm_follows_the_users_choice_over_autos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Mac first-solve stall this fixes.

    Both prewarm hooks used to ask AUTO unconditionally. On a Mac AUTO answers
    ``metal`` -- correctly, it is faster there -- so a user who had explicitly
    selected BEAT got no warmup, and paid BEAT's Julia startup, package load
    and JIT on their first solve of every session. Measured against the CPU
    backend on the reference Windows box: 40.2 s the first time, 0.1 s the
    second.
    """

    from server.app import beat_worker_prewarm
    from server.engines.registry import EngineInfo, EngineRegistry
    from server.solver import warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    warmed: list[str | None] = []
    monkeypatch.setattr(
        solver_warmup,
        "prewarm_beat_worker_for_engine",
        lambda engine: warmed.append(engine) or True,
    )

    registry = EngineRegistry(
        detector=lambda: [
            EngineInfo("metal", True, "test", "0.1.0"),
            EngineInfo("beat-metal", True, "test", "0.1.0"),
            EngineInfo("beat-cpu", True, "test", "0.1.0"),
        ]
    )

    # AUTO prefers Metal on this host, so the old code warmed Metal and left
    # the engine the user actually solves with cold.
    assert asyncio.run(registry.resolve("auto", solver_mode=None)) == "metal"

    asyncio.run(beat_worker_prewarm(registry, _persisted("beat-cpu")))

    assert warmed == ["beat-cpu"]

    # A preference stored before BEAT's backends were separately selectable
    # still reaches this hook rather than falling through to AUTO's Metal. It
    # arrives as the bare name because the head start does not wait for the
    # capability probe that would have resolved it to a variant --
    # ``prewarm_beat_worker_for_engine`` lets the package's own probe pick the
    # backend for that name, which is what it did before the split.
    warmed.clear()
    asyncio.run(beat_worker_prewarm(registry, _persisted("beat")))

    assert warmed == ["beat"]


def test_a_saved_engine_this_host_cannot_run_falls_back_to_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GPU that has gone must cost the optimisation, not the whole warmup."""

    from server.app import bempp_worker_prewarm
    from server.engines.registry import EngineInfo, EngineRegistry
    from server.solver import warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    warmed: list[str | None] = []
    monkeypatch.setattr(
        solver_warmup,
        "prewarm_bempp_worker_for_engine",
        lambda engine: warmed.append(engine) or True,
    )

    registry = EngineRegistry(detector=lambda: [EngineInfo("bempp", True, "test", "0.1.0")])

    asyncio.run(bempp_worker_prewarm(registry, _persisted("beat")))

    assert warmed == ["bempp"]


def test_leaving_the_picker_on_auto_still_warms_autos_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-existing behaviour, which the change above must not disturb."""

    from server.app import bempp_worker_prewarm
    from server.engines.registry import EngineInfo, EngineRegistry
    from server.solver import warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    warmed: list[str | None] = []
    monkeypatch.setattr(
        solver_warmup,
        "prewarm_bempp_worker_for_engine",
        lambda engine: warmed.append(engine) or True,
    )

    registry = EngineRegistry(detector=lambda: [EngineInfo("bempp", True, "test", "0.1.0")])

    asyncio.run(bempp_worker_prewarm(registry, _persisted("auto")))
    asyncio.run(bempp_worker_prewarm(registry, None))

    assert warmed == ["bempp", "bempp"]


def test_a_saved_engine_warms_without_waiting_for_the_capability_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The head start, and the reason the boot prewarm was late.

    ``detect_engines()`` measured 2.7-6.6 s per boot on the reference Mac, and
    every prewarm used to wait out all of it before it could begin -- so on the
    packaged 0.3.1 app the BEAT warmup was still compiling at T+19 s while the
    user solved at T+3-8 s, and whoever won the single Julia worker paid the
    compile. A saved engine name needs none of that probe: it *is* the engine
    the first solve will use.

    The detector here refuses to answer until the warmup has started, so a
    prewarm that awaits the probe first deadlocks the test rather than passing
    it slowly.
    """

    from server.app import beat_worker_prewarm
    from server.solver import warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    warming = threading.Event()
    warmed: list[str | None] = []

    def warm(engine: str | None) -> bool:
        warmed.append(engine)
        warming.set()
        return True

    def detector() -> list[EngineInfo]:
        assert warming.wait(10.0), "the prewarm waited for the probe"
        return [
            EngineInfo("metal", True, "test", "0.1.0"),
            EngineInfo("beat", True, "test", "0.1.0"),
        ]

    monkeypatch.setattr(solver_warmup, "prewarm_beat_worker_for_engine", warm)

    asyncio.run(beat_worker_prewarm(EngineRegistry(detector=detector), _persisted("beat")))

    assert warmed == ["beat"]


def test_a_saved_beat_backend_gets_the_head_start_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The head start claims the BEAT family, not the single name "beat".

    Each of BEAT's execution backends is its own selectable engine, so a user
    who chose one has ``beat-metal`` saved, never ``beat``. The head start used
    an equality test against the hook's own engine name, which would have
    withheld it from every one of them -- and BEAT's warmup is the long one
    this was measured for, so that would have quietly removed the fix for
    exactly the users it was written for.

    The detector refuses to answer until the warmup has started, so a prewarm
    that awaits the probe first deadlocks the test rather than passing slowly.
    """

    from server.app import beat_worker_prewarm
    from server.solver import warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)

    for saved in ("beat-metal", "beat-cpu", "beat"):
        warming = threading.Event()
        warmed: list[str | None] = []

        def warm(engine: str | None) -> bool:
            warmed.append(engine)
            warming.set()
            return True

        def detector() -> list[EngineInfo]:
            assert warming.wait(10.0), "the prewarm waited for the probe"
            return [
                EngineInfo("metal", True, "test", "0.1.0"),
                EngineInfo("beat-metal", True, "test", "0.1.0"),
                EngineInfo("beat-cpu", True, "test", "0.1.0"),
            ]

        monkeypatch.setattr(solver_warmup, "prewarm_beat_worker_for_engine", warm)
        asyncio.run(
            beat_worker_prewarm(EngineRegistry(detector=detector), _persisted(saved))
        )

        assert warmed == [saved], saved


def test_a_saved_metal_engine_does_not_take_beats_head_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``beat-metal`` and ``metal`` are different engines, and only one is BEAT's.

    A prefix test rather than a family test would have handed the Metal engine
    to BEAT's hook, and a substring one would have done the reverse.
    """

    from server.app import beat_worker_prewarm
    from server.solver import warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    warmed: list[str | None] = []

    def detector() -> list[EngineInfo]:
        assert warmed == [], "Metal is not BEAT's to warm before the probe"
        return [EngineInfo("metal", True, "test", "0.1.0")]

    monkeypatch.setattr(
        solver_warmup,
        "prewarm_beat_worker_for_engine",
        lambda engine: warmed.append(engine) or False,
    )

    asyncio.run(
        beat_worker_prewarm(EngineRegistry(detector=detector), _persisted("metal"))
    )

    # Reached only through AUTO's resolved answer, after the probe, and
    # refused there because it is not a BEAT engine.
    assert warmed == ["metal"]


def test_the_probe_still_gates_a_prewarm_with_no_saved_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTO's answer is only knowable from the snapshot, so that path waits."""

    from server.app import bempp_worker_prewarm
    from server.solver import warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    warmed: list[str | None] = []

    def detector() -> list[EngineInfo]:
        assert warmed == [], "nothing may be warmed before AUTO has an answer"
        return [EngineInfo("bempp", True, "test", "0.1.0")]

    monkeypatch.setattr(
        solver_warmup,
        "prewarm_bempp_worker_for_engine",
        lambda engine: warmed.append(engine) or True,
    )

    registry = EngineRegistry(detector=detector)
    asyncio.run(bempp_worker_prewarm(registry, _persisted("auto")))

    assert warmed == ["bempp"]


def test_a_head_start_that_could_not_warm_still_falls_back_to_autos_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guessing early must cost a failed warmup, never a missing one.

    ``prewarm_*_for_engine`` catches its own failures and returns False -- a
    GPU that has gone, a package this machine never installed -- so the
    reconciliation after the probe is what keeps the pre-existing behaviour:
    whatever AUTO resolved to is warmed instead.
    """

    from server.app import beat_worker_prewarm
    from server.solver import warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    warmed: list[str | None] = []
    monkeypatch.setattr(
        solver_warmup,
        "prewarm_beat_worker_for_engine",
        lambda engine: bool(warmed.append(engine)),
    )

    registry = EngineRegistry(
        detector=lambda: [EngineInfo("metal", True, "test", "0.1.0")]
    )

    asyncio.run(beat_worker_prewarm(registry, _persisted("beat")))

    assert warmed == ["beat", "metal"]


def test_a_cancelled_prewarm_does_not_leave_its_head_start_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``shutdown_beat_worker`` cancels this task, and awaiting one does not cancel it."""

    from server.app import beat_worker_prewarm
    from server.solver import warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    warming = threading.Event()
    release = threading.Event()

    def warm(engine: str | None) -> bool:
        warming.set()
        return release.wait(10.0)

    monkeypatch.setattr(solver_warmup, "prewarm_beat_worker_for_engine", warm)

    async def scenario() -> asyncio.Task[bool]:
        task = asyncio.create_task(
            beat_worker_prewarm(
                EngineRegistry(detector=lambda: [EngineInfo("beat", True, "t", "0.1.0")]),
                _persisted("beat"),
            )
        )
        while not warming.is_set():
            await asyncio.sleep(0.01)
        head_start = next(
            item
            for item in asyncio.all_tasks()
            if item is not asyncio.current_task() and item is not task
        )
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        release.set()
        with contextlib.suppress(asyncio.CancelledError):
            await head_start
        return head_start

    head_start = asyncio.run(scenario())
    assert head_start.cancelled()


def test_the_beat_prewarm_records_every_outcome_in_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Six consecutive boots of ``server.log`` said nothing about this step.

    PLAN calls the prewarm a hard requirement, and it logged nothing on success
    and DEBUG on skip -- so the only way to tell whether it had run was a
    packaged app's ``job_events``. Engine, backend, start, duration and failure
    reason are all INFO now.
    """

    from server.solver import beat as beat_adapter
    from server.solver import warmup as solver_warmup

    monkeypatch.delenv("WG2_SOLVER_WARMUP", raising=False)
    monkeypatch.setattr(
        beat_adapter, "beat_status", lambda: {"available": True, "backend": "metal"}
    )
    monkeypatch.setattr(solver_warmup, "_warm_beat", lambda status: None)

    with caplog.at_level(logging.INFO, logger="wg.solver.warmup"):
        assert solver_warmup.prewarm_beat_worker_for_engine("beat") is True
        assert solver_warmup.prewarm_beat_worker_for_engine("metal") is False

        def explode(status: object) -> None:
            raise RuntimeError("no Julia here")

        monkeypatch.setattr(solver_warmup, "_warm_beat", explode)
        assert solver_warmup.prewarm_beat_worker_for_engine("beat") is False

    messages = [record.getMessage() for record in caplog.records]
    assert any("starting on the metal backend" in message for message in messages)
    assert any("prewarm finished in" in message for message in messages)
    assert any("skipped: the first solve uses metal" in message for message in messages)
    assert any("failed after" in message and "no Julia here" in message for message in messages)
    assert {record.levelno for record in caplog.records} == {logging.INFO}
