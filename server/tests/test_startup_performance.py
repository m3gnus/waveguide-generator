"""Coverage for the startup and transfer costs paid on every cold load.

Two independent findings from the 2026-08-06 load review:

* the SPA and the results payloads were served uncompressed, and
* every mesher entry point imports lazily, so the first control a user touched
  paid for the whole import graph (``POST /api/design/symmetry`` measured at
  1152 ms first call against a running server, 57-150 ms after).
"""

from __future__ import annotations

import asyncio
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
        solver_warmup, "_warm_beat", lambda status: pytest.fail("must not warm")
    )
    monkeypatch.setenv("WG2_SOLVER_WARMUP", "0")

    assert solver_warmup.prewarm_beat_worker_for_engine("beat") is False


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

    solver_warmup._warm_beat({"available": True, "backend": "cuda"})

    assert calls == [{"beat_backend": "cuda", "mode": "tiny"}]


@pytest.mark.parametrize(
    "status",
    [
        {"available": True, "backend": "cuda"},
        {"available": True, "backend": "metal"},
        {"available": True, "backend": "rocm"},
        {"available": True, "backend": "cpu"},
        # The case the two sides used to answer differently.
        {"available": True},
        {"available": True, "backend": None},
        {"available": True, "backend": ""},
    ],
)
def test_the_beat_warmup_warms_the_backend_the_solve_will_use(
    monkeypatch: pytest.MonkeyPatch, status: dict[str, object]
) -> None:
    """The warmup and the solve must resolve one accelerator, not two.

    ``_warm_beat`` spelled its fallback ``"cuda"`` while the solve in
    ``server/solver/beat.py`` spelled the same fallback ``"cpu"``. A probe that
    reported available without naming a backend therefore warmed a CUDA context
    -- on a host that need not have one -- and then solved on the CPU, so the
    warmup paid a device initialisation for a path the user's solve never took
    and left the path it did take cold.

    No shipped pin produces that state; at ``hornlab-beat-bem`` ``88487d8``
    every available branch names a backend. The invariant lives in a different
    repository, though, and nothing on this side held it, so this asserts the
    property that actually matters -- the two agree -- rather than the constant
    either one happens to use.
    """

    from server.solver import beat as beat_adapter
    from server.solver import warmup as solver_warmup

    calls: list[dict[str, object]] = []
    module = types.SimpleNamespace(warm_up=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setitem(sys.modules, "hornlab_beat_bem", module)

    solver_warmup._warm_beat(status)

    assert len(calls) == 1
    assert calls[0]["beat_backend"] == beat_adapter.resolve_beat_backend(status)


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
    from server.solver import bempp, metal, warmup

    calls: list[tuple[str, object]] = []
    status = {"available": True, "assembly_backend": "numba"}
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": False})
    monkeypatch.setattr(bempp, "bempp_status", lambda: status)
    monkeypatch.setattr(warmup, "_warm_metal", lambda: calls.append(("metal", None)))
    monkeypatch.setattr(warmup, "_warm_bempp", lambda value: calls.append(("bempp", value)))

    warmup._run_warmup()

    assert calls == [("bempp", status)]


def test_solver_warmup_skips_when_no_physical_engine_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.solver import bempp, metal, warmup

    calls: list[str] = []
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": False, "reason": "no Metal"})
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
            EngineInfo("beat", True, "test", "0.1.0"),
        ]
    )

    # AUTO prefers Metal on this host, so the old code warmed Metal and left
    # the engine the user actually solves with cold.
    assert asyncio.run(registry.resolve("auto", solver_mode=None)) == "metal"

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
