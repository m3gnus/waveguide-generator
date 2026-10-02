"""Real-pipeline qualification observes the production submission verdict."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from server.tests.test_real_pipeline import _settled_capabilities


def test_cold_bempp_pending_snapshot_waits_for_the_initial_verdict():
    async def scenario():
        pending = SimpleNamespace(name="bempp", available=False, qualification="pending",
                                  opencl_unavailable_reason=None)
        ready = SimpleNamespace(name="bempp", available=True, qualification="qualified",
                                opencl_unavailable_reason=None)
        waits = []

        class Registry:
            async def capabilities(self):
                return (pending,)

            async def wait_for_bempp(self):
                waits.append("initial")
                await asyncio.sleep(0)
                return (ready,)

        result = await _settled_capabilities(Registry())
        assert result == [ready] and waits == ["initial"]

    asyncio.run(scenario())


def test_settled_bempp_refusal_remains_unavailable():
    async def scenario():
        refused = SimpleNamespace(name="bempp", available=False, qualification="unavailable",
                                  opencl_unavailable_reason="no_cpu_device")

        class Registry:
            async def capabilities(self):
                return (refused,)

            async def wait_for_bempp(self):
                return (refused,)

        result = await _settled_capabilities(Registry())
        assert result == [refused] and result[0].available is False

    asyncio.run(scenario())
