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
