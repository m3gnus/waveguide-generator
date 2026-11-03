"""STL serialization uses array IPC and the existing writer off the event loop."""

import asyncio
import threading

import numpy as np

from server.design.schema import DesignConfig
from server.exports import core


def test_stl_serialization_leaves_event_loop_and_preserves_bytes(monkeypatch):
    arrays = {
        "vertices": np.asarray([0, 0, 0, .001, 0, 0, 0, .001, 0]),
        "indices": np.asarray([0, 1, 2]), "surfaceTags": np.asarray([1]),
        "warnings": ["sizing warning"],
    }
    owner = threading.get_ident()
    writer_threads = []
    writer = core.binary_stl
    expected = writer(arrays["vertices"].tolist(), arrays["indices"].tolist(), arrays["surfaceTags"].tolist(), "custom model")

    async def child(*_):
        return arrays

    def serialize(*args, **kwargs):
        writer_threads.append(threading.get_ident())
        return writer(*args, **kwargs)

    monkeypatch.setattr(core, "_export_child", child)
    monkeypatch.setattr(core, "binary_stl", serialize)
    result = asyncio.run(core.build_stl(DesignConfig.model_validate({"formula": "OSSE"}), "custom model"))
    assert result.data == expected
    assert result.warning == "sizing warning"
    assert writer_threads and owner not in writer_threads
