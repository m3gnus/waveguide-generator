"""Cached frames own validated wire bytes, independent of mutable native arrays."""

import time
from unittest.mock import patch

import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.preview import core
from server.protocol.frame import FrameError, decode
from test_preview_ws_protocol import _sized_geometry, _small_geometry


def test_repeat_requests_prepare_once_and_own_payload(monkeypatch):
    geometry = _small_geometry()
    protocol = core.PreviewProtocol(epoch=9, preview_builder=lambda *_: geometry)
    request = core._Request(kind="preview", seq=1, design_revision=1, lod="fine",
                            design=DesignConfig.model_validate({"formula": "OSSE", "L": 120}))
    with patch.object(core, "encode_preview_geometry", wraps=core.encode_preview_geometry) as encoder:
        first_header, first_arrays = decode(protocol._compute_frame(request))
        geometry.surfaces[0].positions.setflags(write=True)
        geometry.surfaces[0].positions[:] = 999
        geometry.metadata["mutated"] = True
        request.seq = 2
        request.design_revision = 2
        second_header, second_arrays = decode(protocol._compute_frame(request))
        assert encoder.call_count == 1
    assert second_header["seq"] == 2 and second_header["designRevision"] == 2
    assert first_header["previewMetadata"] == second_header["previewMetadata"]
    assert {k: v.tobytes() for k, v in first_arrays.items()} == {k: v.tobytes() for k, v in second_arrays.items()}
    assert protocol._preview_service._cache_bytes >= sum(a.nbytes for a in first_arrays.values())


def test_prepared_frame_matches_public_encoder_and_budget():
    geometry = _sized_geometry(2_000)
    fields = dict(epoch=9, seq=2, design_revision=3, lod="fine", eval_ms=4.5)
    expected = core.encode_preview_geometry(geometry, **fields)
    prepared = core.prepare_preview_geometry(geometry)
    assert prepared.encode(**fields) == expected
    with pytest.raises(FrameError, match="frame-too-large"):
        prepared.encode(**fields, max_frame_bytes=len(expected) - 1)
    geometry.surfaces[0].positions.setflags(write=True)
    for value in (np.nan, np.inf):
        geometry.surfaces[0].positions[0, 0] = value
        with pytest.raises(ValueError, match="non-finite"):
            core.encode_preview_geometry(geometry, **fields)


def test_payload_microbenchmark():
    geometry = _sized_geometry(8_000)
    fields = dict(epoch=9, seq=2, design_revision=3, lod="fine", eval_ms=4.5)
    with patch.object(core.np, "ascontiguousarray", wraps=core.np.ascontiguousarray) as conversions, patch("server.protocol.frame.decode", wraps=decode) as validations:
        start = time.perf_counter()
        before = [core.encode_preview_geometry(geometry, **{**fields, "seq": seq}) for seq in range(20)]
        before_ms = (time.perf_counter() - start) * 1000
        before_counts = (conversions.call_count, validations.call_count)
    prepared = core.prepare_preview_geometry(geometry)
    with patch.object(core.np, "ascontiguousarray", wraps=core.np.ascontiguousarray) as conversions, patch("server.protocol.frame.decode", wraps=decode) as validations:
        start = time.perf_counter()
        after = [prepared.encode(**{**fields, "seq": seq}) for seq in range(20)]
        after_ms = (time.perf_counter() - start) * 1000
        after_counts = (conversions.call_count, validations.call_count)
    assert before_counts == (60, 20)
    assert after_counts == (0, 0)
    assert before == after
    print({"public_encode_20_ms": before_ms, "prepared_20_ms": after_ms,
           "retained_bytes": prepared.nbytes, "frames_bytes_equal": True,
           "conversion_validation_calls_before": before_counts,
           "conversion_validation_calls_after": after_counts})
