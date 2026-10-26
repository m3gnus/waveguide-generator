"""Request-local BEAT admission across the selector and its remedies."""

import asyncio
from types import SimpleNamespace

import pytest

from server.engines.registry import EngineInfo
from server.jobs.runtime import core
from server.jobs.runtime.imported_refusals import ImportedPreflightCache, _imported_preflight_refusal
from server.solver import beat, beat_imported
from server.solver.beat_adapter import request as compiler
from server.tests import test_imported_beat as cad


class ReadyBeatRegistry:
    def __init__(self):
        self.infos = [
            EngineInfo(f"beat-{backend}", True, "ready", "test",
                       geometry_sources=("parametric", "imported"),
                       symmetry_domains=("full", "half-yz", "quarter"))
            for backend in beat.BEAT_BACKENDS
        ]

    async def capabilities(self):
        return self.infos

    async def get_engine(self, name):
        return beat.BeatEngine(beat.beat_engine_backend(name))

    async def unavailable_reason(self, name):
        raise AssertionError(name)


@pytest.mark.parametrize("invalid", [False, True])
def test_plan_compiles_once_across_backends_resolution_and_remedies(monkeypatch, invalid):
    async def no_common_refusal(*args, **kwargs):
        return None

    monkeypatch.setattr(core, "_imported_request_refusal_async", no_common_refusal)
    builds = []
    original = compiler.build_imported_request

    def build(*args, **kwargs):
        builds.append(kwargs["backend"])
        return original(*args, **kwargs)

    monkeypatch.setattr(compiler, "build_imported_request", build)
    request = cad._request()
    request.options.accuracy = "accurate"
    record = cad._record()
    if invalid:
        record["source_tags"]["source-a"] = 999
    plan = asyncio.run(core.plan_imported_submission(
        request, ReadyBeatRegistry(), symmetry_metadata={"resolved_quadrants": 1234},
        imported_record=record, imported_msh_text=cad.MESH,
    ))
    assert len(builds) == 1
    rows = [row for row in plan["engines"] if row["name"].startswith("beat-")]
    assert len(rows) == 4
    assert all(row["solves"] is not invalid for row in rows)
    if invalid:
        assert all("BEAT cannot read this return's solve inputs:" in row["reason"] for row in rows)
        assert "BEAT cannot read this return's solve inputs:" in plan["reason"]
    else:
        assert plan["engine"] == "beat-cpu"

    # A changed return, even on the same mesh, gets its own verdict.
    record["source_tags"]["source-a"] = 101 if invalid else 999
    second = asyncio.run(core.plan_imported_submission(
        request, ReadyBeatRegistry(), symmetry_metadata={"resolved_quadrants": 1234},
        imported_record=record, imported_msh_text=cad.MESH,
    ))
    assert len(builds) == 2
    assert all(row["solves"] is invalid for row in second["engines"] if row["name"].startswith("beat-"))


@pytest.mark.parametrize("case", ["valid", "missing_source", "invalid_frame", "y_only_half"])
def test_normal_velocity_compiler_has_the_same_verdict_on_each_backend(case):
    record = cad._record(planes=["y0"] if case == "y_only_half" else [])
    if case == "missing_source":
        record["source_tags"]["source-a"] = 999
    if case == "invalid_frame":
        record["anchor"]["throat_frame"]["u"] = [-1., 0., 0.]
    request = cad._request()
    reasons = [beat_imported.imported_beat_preflight(record, cad.MESH, request=request, backend=backend)
               for backend in beat.BEAT_BACKENDS]
    assert len(set(reasons)) == 1
    assert (reasons[0] is not None) is (case != "valid")


def test_submission_shares_refusal_across_all_alternative_backends(monkeypatch):
    builds = []
    original = compiler.build_imported_request

    def build(*args, **kwargs):
        builds.append(kwargs["backend"])
        return original(*args, **kwargs)

    monkeypatch.setattr(compiler, "build_imported_request", build)
    request = cad._request()
    request.options.accuracy = "accurate"
    record = cad._record()
    record["source_tags"]["source-a"] = 999
    with pytest.raises(core.ImportedSolveRefusal) as caught:
        asyncio.run(core.resolve_imported_submission(
            request, ReadyBeatRegistry(), symmetry_metadata={"resolved_quadrants": 1234},
            imported_record=record, imported_msh_text=cad.MESH, request_refusal=None,
        ))
    assert len(builds) == 1
    assert "BEAT cannot read this return's solve inputs:" in str(caught.value)
    assert caught.value.details["capable_engines"] == []


def test_live_two_channel_fixture_compiles_on_both_frequency_paths():
    from server.tests.test_imported_adaptive_beat_live import _two_channel_case

    mesh, request, record = _two_channel_case()
    for adaptive in (False, True):
        request.options.adaptive_frequency_sampling = adaptive
        compiled = beat_imported.compile_imported_beat(record, mesh, request=request)
        assert set(compiled.channel_ports) == {"left", "right"}
        assert set(compiled.channel_ports["left"]).isdisjoint(compiled.channel_ports["right"])


def test_transducer_preflight_keeps_backend_specific_verdicts(monkeypatch):
    monkeypatch.setattr(beat.BeatEngine, "imported_preflight",
                        lambda self, *args, **kwargs: None if self.backend == "cpu" else "unsupported backend")
    channel = SimpleNamespace(exterior_transducer=object())
    cache = ImportedPreflightCache()

    async def check():
        cpu = await _imported_preflight_refusal(beat.BeatEngine("cpu"), {}, "mesh", [channel],
                                                 preflight_cache=cache)
        cuda = await _imported_preflight_refusal(beat.BeatEngine("cuda"), {}, "mesh", [channel],
                                                  preflight_cache=cache)
        return cpu, cuda

    assert asyncio.run(check()) == (None, "unsupported backend")
