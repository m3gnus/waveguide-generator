"""The one real pipeline the default suite runs: real mesher, real solver, pinned modules.

Every other WG-to-engine test in this suite stands something in for the engine,
the mesher or the ingest. Each of those stand-ins has hidden a real escape:

- ``FakeIngest`` hid backend preparation refusing every WG-design return;
- a stubbed mesher hid gmsh's 128-byte physical-name limit;
- the AUTO parallel sweep shipped as the default and never once worked.

So this file runs two small solves end to end through the entry points the
application itself uses, with nothing faked:

- **Parametric.** A tiny OSSE (about 200 triangles, two frequencies) submitted
  to ``POST /api/solve`` the way the SPA submits one, solved by the jobs runtime
  on BEMPP with the pinned mesher. BEMPP is the one engine the pinned
  requirements make available on every host, Linux, Windows and macOS: it
  assembles on OpenCL where a CPU device exists and on numba, which the lock
  file ships, everywhere else. So this solve never skips. An unavailable BEMPP
  fails, with the application's own reason.
- **CAD import.** The committed linked return ``round.wgreturn`` through the
  path a CAD Link solve takes: ``POST /api/cadlink/ingest``, a manual solve
  operation, a setup revision, and ``POST /api/cadlink/operations/{id}/prepare``
  -- which re-ingests the retained snapshot with the production ingest, holds
  the blocking findings for review, and submits to the jobs runtime once they
  are approved. Imported geometry is offered only by an engine that declares
  it: BEMPP on OpenCL (numba is never a shipping backend for imported
  geometry) or Metal. Hosted CI runners have neither, so there it is skipped
  with the registry's reasons. Where it is expected (``_imported_engine_expected``)
  a missing engine fails instead, and
  ``test_this_host_offers_an_imported_engine_where_one_is_expected`` says so on
  its own.

Both solves assert what came back, not that something came back: the requested
frequencies exactly, finite SPL and impedance of the right shape inside a loose
physical band, the engine that actually ran (the runtime silently substitutes
an unavailable named engine), the source and drive-channel metadata, and the
provenance block -- every pinned module installed at its ``pins.json`` commit,
with no drift. ``test_installed_modules_are_the_pinned_commits`` holds the
environment itself to the pins, with no editable checkout and no shadowing
import path.

This file must be green before any pin move (docs/DEVELOPMENT.md).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sys
from typing import Any

import pytest

from server.app import create_app
from server.cadlink import api as cadlink_api

from test_jobs_api import _request


REPO_ROOT = Path(__file__).resolve().parents[2]
PINS_FILE = REPO_ROOT / "pins.json"
#: The committed linked return the installed-candidate gate also imports; its
#: README says how it was made. A 60 mm round horn, one closed STEP body.
ROUND_RETURN = REPO_ROOT / "scripts" / "fixtures" / "imported-return" / "round.wgreturn"

#: Every host with the pinned requirements has BEMPP (numba ships in the lock).
PARAMETRIC_ENGINE = "bempp"
PARAMETRIC_FREQUENCIES = [500.0, 1000.0]
#: Engines that can solve imported geometry, in the order this file prefers
#: them: BEMPP on a CPU OpenCL device first, then Metal. BEAT is left out on
#: purpose: its worker is a persistent host that outlives this process in the
#: user's own registry, which a default suite run must not leave behind.
IMPORTED_ENGINES = ("bempp", "metal")
IMPORTED_FREQUENCIES = [1000.0, 2000.0]
#: The only blocking findings this return may raise on a fresh data directory,
#: as kind -> required verdict (None: any). The design registry is empty, so
#: freshness must say ``missing_design``; the fixture tags geometry rather than
#: paint, so ``source-paint-missing`` is expected. Anything else that blocks is
#: a regression, and approving it would hide one.
EXPECTED_BLOCKING = {"freshness": "missing_design", "source-paint-missing": None}
#: The wall-clock budget for either solve's job, generous for a cold numba JIT
#: on a hosted Windows runner; locally each solve takes well under 30 s.
JOB_TIMEOUT_S = 300.0
#: Set to 1 on a host that must run the imported solve, beyond the defaults
#: in ``_imported_engine_expected``.
REQUIRE_IMPORTED_ENV = "WG_REQUIRE_IMPORTED_PIPELINE"


# -- helpers ------------------------------------------------------------------


def _pins() -> dict[str, str]:
    modules = json.loads(PINS_FILE.read_text(encoding="utf-8"))["modules"]
    return {name: str(entry["sha"]) for name, entry in modules.items()}


async def _call(
    app: Any, method: str, path: str, body: Mapping[str, Any] | None = None, *, expect: int = 200
) -> Any:
    status, raw = await _request(app, method, path, body=dict(body) if body is not None else None)
    text = raw.decode("utf-8", "replace")
    assert status == expect, f"{method} {path} answered {status}, not {expect}: {text[:2000]}"
    return json.loads(text) if text else None


async def _finish(app: Any, job_id: str) -> dict[str, Any]:
    """Wait for ``job_id`` through the jobs runtime and answer its results."""

    await app.state.jobs_runtime.wait_idle(timeout=JOB_TIMEOUT_S)
    status = await _call(app, "GET", f"/api/status/{job_id}")
    assert status["status"] == "complete", (
        f"job {job_id} ended {status['status']!r}: {status.get('error_message')!r} "
        f"({status.get('stage_message')!r})"
    )
    return await _call(app, "GET", f"/api/results/{job_id}")


async def _close(app: Any) -> None:
    """Stop what the solve started, so nothing outlives the test.

    The lifespan hooks never ran (the requests go straight to the ASGI app), so
    their shutdown work is done here: the jobs runtime, the ingest's deferred
    background tasks, and the BEMPP worker child.
    """

    await app.state.jobs_runtime.shutdown()
    loop = asyncio.get_running_loop()
    # Module-level registries can still hold tasks another test's loop left;
    # only this loop's are this test's to wait for.
    background = [
        task
        for task in (
            *cadlink_api._DEFERRED_VIEWPORTS.values(),
            *cadlink_api._FRAME_SUGGESTIONS.values(),
            *getattr(app.state, "cad_preparations", set()),
        )
        if task.get_loop() is loop and not task.done()
    ]
    if background:
        await asyncio.wait(background, timeout=120.0)
    from server.solver.bempp_process import shutdown_bempp_process

    await asyncio.to_thread(shutdown_bempp_process)


def _finite_series(values: Any, where: str, count: int) -> list[float]:
    assert isinstance(values, list) and len(values) == count, (
        f"{where} has {values!r}, expected {count} values"
    )
    for value in values:
        assert isinstance(value, (int, float)) and not isinstance(value, bool), (
            f"{where} holds a non-number: {values!r}"
        )
        assert math.isfinite(value), f"{where} holds a non-finite value: {values!r}"
    return [float(value) for value in values]


def _assert_acoustics(payload: Mapping[str, Any], frequencies: list[float], where: str) -> None:
    """The numbers a user plots: right frequencies, right shape, finite and plausible."""

    count = len(frequencies)
    assert payload["frequencies"] == frequencies, (
        f"{where} answers frequencies {payload['frequencies']}, not the requested {frequencies}"
    )
    spl = payload["spl_on_axis"]
    assert spl["frequencies"] == frequencies, f"{where} SPL axis {spl['frequencies']}"
    levels = _finite_series(spl["spl"], f"{where} on-axis SPL", count)
    # Loose on purpose: a 60 mm horn driven at unit acceleration sits within a
    # few dB of 0 dB at 1 m. The band catches zeros (-inf), unit slips (x1000
    # is +60 dB) and garbage, not a changed resonance.
    assert all(-40.0 < level < 60.0 for level in levels), f"{where} on-axis SPL {levels} dB"
    _finite_series(spl["phase_degrees"], f"{where} on-axis phase", count)

    impedance = payload["impedance"]
    assert impedance["frequencies"] == frequencies, f"{where} impedance axis"
    real = _finite_series(impedance["real"], f"{where} impedance (real)", count)
    imaginary = _finite_series(impedance["imaginary"], f"{where} impedance (imaginary)", count)
    # Normalised specific radiation impedance of a small throat: resistance is
    # positive, below about 1.5 rho*c, and still rising with frequency this far
    # below cutoff. An all-zero result (a solver that computed nothing) fails
    # the first clause.
    assert all(0.0 < value < 1.5 for value in real), f"{where} radiation resistance {real}"
    assert real == sorted(real), f"{where} radiation resistance does not rise: {real}"
    assert all(abs(value) < 5.0 for value in imaginary), f"{where} reactance {imaginary}"
    di = payload["di"]
    assert di["frequencies"] == frequencies, f"{where} DI axis"
    _finite_series(di["di"], f"{where} directivity index", count)


def _assert_pinned_provenance(result: Mapping[str, Any], where: str) -> None:
    """The result says it came off the pinned stack, measured, not declared."""

    pins = _pins()
    provenance = result["provenance"]
    assert provenance["dependency_shas"] == pins, (
        f"{where}: the result declares {provenance['dependency_shas']}, pins.json {pins}"
    )
    installed = provenance["installed_dependency_shas"]
    drift = provenance["dependency_drift"]
    assert drift == [] and installed == pins, (
        f"{where} ran off a module that is not its pin: dependency_drift={drift}; installed "
        + ", ".join(
            f"{name}={installed.get(name)} (pin {sha})"
            for name, sha in sorted(pins.items())
            if installed.get(name) != sha
        )
        + ". Install the pins non-editable: pip install --no-deps -r server/requirements-pins.txt"
    )


def _imported_engine_expected() -> str | None:
    """Why this host must be able to run the imported solve, or None.

    Apple Silicon has Metal, and the pinned hornlab-metal-bem declares imported
    geometry, so a Mac that stops offering it has lost something. Hosted GitHub
    runners are VMs whose GPU is not a qualified Metal device, so they are not
    held to it (they still run the solve if the registry offers an engine).
    ``WG_REQUIRE_IMPORTED_PIPELINE=1`` requires it anywhere else, such as a host
    with a CPU OpenCL device.
    """

    if os.environ.get(REQUIRE_IMPORTED_ENV) == "1":
        return f"{REQUIRE_IMPORTED_ENV}=1"
    if (
        sys.platform == "darwin"
        and platform.machine() == "arm64"
        and os.environ.get("GITHUB_ACTIONS") != "true"
    ):
        return "this is Apple Silicon, where the pinned hornlab-metal-bem offers Metal"
    return None


async def _imported_engine(app: Any) -> tuple[str | None, str]:
    """The engine the CAD solve runs on here, or None and the registry's reasons."""

    engines = {info.name: info for info in await app.state.engine_registry.capabilities()}
    for name in IMPORTED_ENGINES:
        info = engines.get(name)
        if info is not None and info.available and "imported" in info.geometry_sources:
            return name, ""
    reasons = "; ".join(
        f"{name}: "
        + (
            "not registered"
            if engines.get(name) is None
            else f"available={engines[name].available}, geometry "
            f"{list(engines[name].geometry_sources)}, {engines[name].reason}"
        )
        for name in IMPORTED_ENGINES
    )
    return None, (
        f"no engine offers imported geometry on {sys.platform}/{platform.machine()} "
        f"({reasons})"
    )


# -- the environment -----------------------------------------------------------


def test_installed_modules_are_the_pinned_commits() -> None:
    """Every pinned module is installed from its pin, non-editable, and is what imports.

    PEP 610's ``direct_url.json`` is the only record of the commit pip
    installed. It is not enough on its own: an editable ``.pth`` or a
    ``PYTHONPATH`` entry can shadow the installed copy while the metadata still
    reads correct, so the module that actually imports must live inside the
    distribution that was measured.
    """

    problems: list[str] = []
    for name, sha in sorted(_pins().items()):
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            problems.append(f"{name}: not installed")
            continue
        raw = dist.read_text("direct_url.json")
        document = json.loads(raw) if raw else {}
        if document.get("dir_info", {}).get("editable"):
            problems.append(f"{name}: an editable install of {document.get('url')}")
            continue
        commit = document.get("vcs_info", {}).get("commit_id")
        if commit != sha:
            problems.append(f"{name}: installed commit {commit!r} (from {document.get('url')!r}), pin {sha}")
            continue
        top_level = (dist.read_text("top_level.txt") or name.replace("-", "_")).split()
        root = Path(dist.locate_file("")).resolve()
        for module in top_level:
            spec = importlib.util.find_spec(module)
            origin = Path(spec.origin).resolve() if spec is not None and spec.origin else None
            if origin is None or not origin.is_relative_to(root):
                problems.append(
                    f"{name}: `import {module}` resolves to {origin}, not the installed copy in {root}"
                )
    assert not problems, (
        "The environment does not run the pinned modules:\n  "
        + "\n  ".join(problems)
        + "\nInstall them non-editable from the pins: "
        "pip install --no-deps -r server/requirements-pins.txt"
    )


# -- parametric ------------------------------------------------------------------


PARAMETRIC_BODY: dict[str, Any] = {
    "design": {
        "formula": "OSSE",
        "L": 60,
        "a": 30,
        "a0": 10,
        "r0": 10,
        "k": 1,
        "n": 4,
        "q": 0.99,
        "s": 0.8,
        "mesh": {
            "angular_segments": 12,
            "length_segments": 4,
            "throat_resolution": 8,
            "mouth_resolution": 15,
            "quadrants": 1,
            "wall_thickness": 2,
        },
        "source": {"shape": 2, "radius": -1, "curvature": 0, "velocity": 1},
        "simulation": {"f1": 500, "f2": 1000, "num_frequencies": 2, "sim_type": "freestanding"},
    },
    "options": {"engine": PARAMETRIC_ENGINE, "frequencies_hz": PARAMETRIC_FREQUENCIES},
    "label": "real pipeline",
    "client_request_id": "real-pipeline-parametric",
}


def test_a_parametric_design_solves_on_the_real_mesher_and_bempp(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(data_dir=tmp_path / "data")
        try:
            engines = {info.name: info for info in await app.state.engine_registry.capabilities()}
            bempp = engines.get(PARAMETRIC_ENGINE)
            assert bempp is not None and bempp.available, (
                "BEMPP is unavailable, and the pinned requirements install it (numba "
                f"included) on every host: {getattr(bempp, 'reason', 'not registered')}"
            )
            accepted = await _call(app, "POST", "/api/solve", PARAMETRIC_BODY)
            result = await _finish(app, accepted["job_id"])
        finally:
            await _close(app)

        _assert_acoustics(result, PARAMETRIC_FREQUENCIES, "the parametric result")
        metadata = result["metadata"]
        # The engine that ran, not the one asked for: a named engine the
        # runtime finds unavailable is replaced without failing the job.
        assert (metadata["engine"], metadata["solver_backend"]) == (
            "hornlab-bempp-bem", "bempp",
        ), (metadata["engine"], metadata["solver_backend"])
        assert metadata["solve_execution"]["engine"] == PARAMETRIC_ENGINE
        # The request's own metadata and the source it described.
        assert result["client_request_id"] == "real-pipeline-parametric"
        assert metadata["source_motion"] == "normal"
        assert metadata["impedance_drive"] == "unit_acceleration"
        mesh = metadata["mesh_stats"]
        assert mesh["generated_by"] == "hornlab-waveguide-mesher", mesh.get("generated_by")
        assert 150 <= mesh["triangle_count"] <= 600, mesh["triangle_count"]
        # Tag 2 is the driven throat disc; without it nothing radiates.
        assert mesh["tag_counts"].get("2", 0) > 0, mesh["tag_counts"]
        assert metadata["failures"] == [] and metadata["partial_success"] is False
        _assert_pinned_provenance(result, "the parametric result")

    asyncio.run(scenario())


# -- CAD import ------------------------------------------------------------------


def test_this_host_offers_an_imported_engine_where_one_is_expected(tmp_path: Path) -> None:
    """The CAD solve below may skip only on a host that is not expected to run it."""

    expected = _imported_engine_expected()
    if expected is None:
        pytest.skip(
            "no imported-geometry engine is expected on "
            f"{sys.platform}/{platform.machine()} (hosted runner or no Metal); set "
            f"{REQUIRE_IMPORTED_ENV}=1 to require one"
        )

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path / "data")
        try:
            engine, reason = await _imported_engine(app)
        finally:
            await app.state.jobs_runtime.shutdown()
        assert engine is not None, f"expected here because {expected}, but {reason}"

    asyncio.run(scenario())


def test_a_cad_return_ingests_prepares_and_solves_through_the_operation(tmp_path: Path) -> None:
    manifest = json.loads((ROUND_RETURN / "wgreturn.json").read_text(encoding="utf-8"))
    design_id = manifest["instances"][0]["design_id"]
    source = manifest["sources"][0]
    source_id, channel_id = str(source["id"]), str(source["default_drive_channel_id"])
    source_size = float(source["suggested_resolution_mm"])
    workspace = tmp_path / "workspace"
    shutil.copytree(ROUND_RETURN, workspace / "wgreturn" / "round.wgreturn")
    mesh_request = {"rigid_size_mm": 20.0, "transition_mm": 30.0, "source_size_mm": {source_id: source_size}}

    async def scenario() -> tuple[str, str, dict[str, Any]]:
        app = create_app(data_dir=tmp_path / "data")
        try:
            engine, reason = await _imported_engine(app)
            if engine is None:
                expected = _imported_engine_expected()
                if expected is not None:
                    pytest.fail(f"the imported solve is expected here because {expected}, but {reason}")
                pytest.skip(f"skipped with the registry's reasons: {reason}")

            await _call(app, "POST", "/api/cad-workspace/select", {"path": str(workspace)})
            record = await _call(
                app,
                "POST",
                "/api/cadlink/ingest",
                {
                    "bundlePath": "wgreturn/round.wgreturn",
                    "mesh": {
                        "rigidSizeMm": mesh_request["rigid_size_mm"],
                        "transitionMm": mesh_request["transition_mm"],
                        "sourceSizeMm": mesh_request["source_size_mm"],
                    },
                    "expectedDesignId": design_id,
                },
            )
            # The real mesher tagged the source, under a gmsh physical name it
            # wrote and read back whole.
            assert record["tag_namespace"] == "wg-import-v1", record["tag_namespace"]
            assert source_id in record["source_tags"], record["source_tags"]

            operation = (
                await _call(
                    app,
                    "POST",
                    "/api/cadlink/operations",
                    {"operation_id": "real-pipeline-cad", "ingest_id": record["ingest_id"]},
                )
            )["operation"]
            assert operation["state"] == "received", operation
            revision = await _call(
                app,
                "POST",
                "/api/cadlink/setup-revisions",
                {
                    "setup": {
                        "geometry": {
                            "drive_channels": [{"id": channel_id, "source_ids": [source_id]}],
                            "mesh": mesh_request,
                        },
                        "options": {"engine": engine, "frequencies_hz": IMPORTED_FREQUENCIES},
                    }
                },
            )
            prepare = f"/api/cadlink/operations/{operation['operationId']}/prepare"
            held = (
                await _call(app, "POST", prepare, {"setupRevisionId": revision["revisionId"], "wait": True})
            )["operation"]
            # Prepared through the production ingest, then held: this design is
            # unknown to a fresh data directory, which the user has to review.
            assert (held["state"], held["stage"], held["reason"]) == (
                "needs_user_input", "ready", "findings_need_review",
            ), held
            prepared = app.state.cadlink_store.get_ingest(held["preparationId"])
            findings = json.loads(prepared["record_json"])["findings"]
            blocking = [item for item in findings if item.get("blocking")]
            kinds = {item["kind"]: item.get("verdict") for item in blocking}
            assert set(kinds) == set(EXPECTED_BLOCKING) and all(
                EXPECTED_BLOCKING[kind] in (None, verdict) for kind, verdict in kinds.items()
            ), f"blocking findings {kinds}, expected exactly {EXPECTED_BLOCKING}"
            submitted = (
                await _call(
                    app,
                    "POST",
                    prepare,
                    {
                        "setupRevisionId": revision["revisionId"],
                        "wait": True,
                        "approvals": {
                            "preparationId": held["preparationId"],
                            "findingIds": [item["id"] for item in blocking],
                        },
                    },
                )
            )["operation"]
            assert (submitted["state"], submitted["stage"]) == ("accepted", "submitted"), submitted
            assert submitted["preparationId"] == held["preparationId"], submitted
            return engine, held["preparationId"], await _finish(app, submitted["jobId"])
        finally:
            await _close(app)

    engine, ingest_id, result = asyncio.run(scenario())

    assert result["result_kind"] == "multi_channel", result["result_kind"]
    assert result["client_request_id"] == "cad-solve:real-pipeline-cad"
    assert result["frequencies"] == IMPORTED_FREQUENCIES, result["frequencies"]
    metadata = result["metadata"]
    assert metadata["geometry_type"] == "imported"
    assert metadata["ingest_id"] == ingest_id
    # The engine that ran is the one the setup named.
    assert metadata["solver_engine"]["engine"] == engine, metadata["solver_engine"]
    # The source survives into the solve: its tag maps back to its id and role.
    tags = {
        entry["source_id"]: (tag, entry["role"])
        for tag, entry in metadata["tag_map"].items()
        if entry.get("source_id")
    }
    assert set(tags) == {source_id} and tags[source_id][1] == source["role"], metadata["tag_map"]
    assert metadata["mesh_stats"]["tag_counts"].get(tags[source_id][0], 0) > 0, metadata["mesh_stats"]
    assert metadata["cad_identity"]["solver_anchor_instance_id"] == manifest["coordinate_system"][
        "solver_anchor_instance_id"
    ]
    # And so does the drive channel: exactly the one submitted, driving that source.
    assert result["channel_order"] == [channel_id], result["channel_order"]
    assert list(result["channels"]) == [channel_id], list(result["channels"])
    channel = result["channels"][channel_id]
    channel_metadata = channel["metadata"]
    assert channel_metadata["drive_channel_id"] == channel_id, channel_metadata.get("drive_channel_id")
    assert channel_metadata["source_ids"] == [source_id], channel_metadata.get("source_ids")
    assert channel_metadata["role"] == source["role"], channel_metadata.get("role")
    assert channel_metadata["source_motion"] == "normal"
    _assert_acoustics(channel, IMPORTED_FREQUENCIES, f"channel {channel_id!r}")
    _assert_pinned_provenance(result, "the imported result")
