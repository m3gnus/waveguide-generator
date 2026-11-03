"""Whole-preview identity includes the inputs used by canonical dimensions."""

import time

from hornlab_mesher.preview import api

from server.design.schema import DesignConfig
from server.preview import core
from server.protocol.frame import decode


def test_sampling_replay_matches_cold_dimensions_and_geometry(monkeypatch):
    original = api.build_preview_geometry
    builds = []

    def build(config, options):
        builds.append(config["mesh"]["angularSegments"])
        return original(config, options)

    monkeypatch.setattr(api, "build_preview_geometry", build)
    protocol = core.PreviewProtocol(epoch=12)
    responses = []
    elapsed = []
    for seq, angular in enumerate((40, 400, 40), 1):
        design = DesignConfig.model_validate({
            "formula": "OSSE", "L": 120, "r0": 12.7, "a0": 15.5,
            "a": "45+10*sin(2*p)", "k": 1, "s": 0,
            "mesh": {"angular_segments": angular, "length_segments": 16,
                     "wall_thickness": 0},
        })
        request = core._Request(kind="preview", seq=seq, design_revision=seq,
                                lod="fine", design=design)
        start = time.perf_counter()
        header, arrays = decode(protocol._compute_frame(request))
        elapsed.append((time.perf_counter() - start) * 1000)
        cold = original(core._recentred_preview_config(design), core.preview_options("fine", curvature=False))
        cold_header, cold_arrays = decode(core.encode_preview_geometry(
            cold, epoch=12, seq=seq, design_revision=seq, lod="fine", eval_ms=0,
        ))
        # Native timing fields are diagnostic, not geometry or dimensions.
        assert header["surfaces"] == cold_header["surfaces"]
        assert header["fidelity"] == cold_header["fidelity"]
        assert header["previewMetadata"]["dimensions_mm"] == cold_header["previewMetadata"]["dimensions_mm"]
        assert {k: v.tobytes() for k, v in arrays.items()} == {k: v.tobytes() for k, v in cold_arrays.items()}
        responses.append(header["previewMetadata"]["dimensions_mm"])
    assert builds == [40, 400]
    assert responses[0] != responses[1]
    assert responses[0] == responses[2]
    print({"replay_ms": elapsed, "builds": builds, "dimensions": responses, "cold_arrays_bytes_equal": True})


def test_fine_request_normalizes_once_on_miss_hit_and_refusal(monkeypatch):
    from pathlib import Path
    import pytest
    from server.design.textcfg import parse
    from server.preview import wall_check
    from test_preview_ws_protocol import _small_geometry

    design = parse((Path(__file__).parent / "fixtures/ath_consumer/r-m-240-360-a0-n32-l20.cfg").read_text()).design
    real = core.retain_mesher_construction
    calls = []

    def retain(config):
        calls.append(config)
        return real(config)

    monkeypatch.setattr(core, "retain_mesher_construction", retain)
    geometry = _small_geometry()
    monkeypatch.setattr(api, "build_preview_geometry", lambda *_: geometry)
    remembered = []
    monkeypatch.setattr(wall_check, "remember", lambda config, detail: remembered.append((config, detail)))
    protocol = core.PreviewProtocol(epoch=12)
    request = core._Request(kind="preview", seq=1, design_revision=1, lod="fine", design=design)
    frames = []
    for _ in range(2):
        calls.clear()
        frames.append(decode(protocol._compute_frame(request)))
        assert len(calls) == 1
    assert remembered[0] == remembered[1]
    assert {k: v.tobytes() for k, v in frames[0][1].items()} == {k: v.tobytes() for k, v in frames[1][1].items()}

    def refuse(*_):
        raise ValueError("test wall refusal")

    monkeypatch.setattr(api, "build_preview_geometry", refuse)
    protocol = core.PreviewProtocol(epoch=12)
    calls.clear()
    with pytest.raises(ValueError, match="test wall refusal"):
        protocol._compute_frame(request)
    assert len(calls) == 1
    assert remembered[-1][1] == "test wall refusal"
