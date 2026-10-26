"""The capability payload identifies the solver host for driver guidance."""

import asyncio

import pytest

from server.diagnostics import capabilities


class EmptyRegistry:
    async def capabilities(self):
        return ()


@pytest.mark.parametrize("system,expected", [
    ("Windows", "windows"), ("Linux", "linux"), ("Darwin", "darwin"),
    ("FreeBSD", "freebsd"),
])
def test_capabilities_identifies_the_server_host(monkeypatch, system, expected):
    monkeypatch.setattr(capabilities.platform, "system", lambda: system)
    payload = asyncio.run(capabilities.capabilities_payload(EmptyRegistry()))
    assert payload["hostPlatform"] == expected


@pytest.mark.parametrize("qualification", ["pending", "done"])
def test_capability_entries_have_uniform_keys_and_labels(qualification):
    from server.engines.registry import EngineInfo

    class Snapshot:
        async def capabilities(self):
            return (
                EngineInfo("bempp", False, "Checking OpenCL…", None,
                           qualification=qualification),
                EngineInfo("metal", True, "Ready", "test", label="Metal"),
            )

    engines = asyncio.run(capabilities.capabilities_payload(Snapshot()))["engines"]
    assert engines[0]["qualification"] == qualification
    assert engines[0]["label"] == "bempp"
    assert engines[1]["label"] == "Metal"
    assert set(engines[0]) == set(engines[1])
    assert all(isinstance(engine["opencl_retry_pending"], bool) for engine in engines)
