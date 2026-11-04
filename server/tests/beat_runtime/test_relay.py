"""Host v2 provenance round trips and strict finite-number decoding."""

import copy
import json

import pytest

from server.solver.beat_runtime import ipc, paths, relay
from server.tests.beat_runtime.test_ipc import FragmentedPeer, frame


def roundtrip(payload):
    class Peer:
        wire = b""
        def sendall(self, wire):
            self.wire = wire
    peer = Peer()
    ipc.send_frame(peer, payload)
    return ipc.receive_frame(FragmentedPeer(peer.wire))


def test_provenance_transmitted_once_without_mutation_or_deepcopy(monkeypatch):
    encoder, decoder = relay.EventRelay(), relay.EventRelay()
    provenance = {"schema_version": 1, "engine": {"commit": "fixture"}}
    event = {"type": "result", "result": {"freq_hz": 1000., "diagnostics": {"engine_provenance": provenance}}}
    original = copy.deepcopy(event)
    monkeypatch.setattr(copy, "deepcopy", lambda *a: pytest.fail("deep copy"))
    first = encoder.envelope(event)
    assert first["engine_provenance"] is provenance
    assert event == original
    decoded_first = decoder.accept(roundtrip(first))
    second = encoder.envelope(event)
    assert "engine_provenance" not in second
    decoded_second = decoder.accept(roundtrip(second))
    assert decoded_first == decoded_second == original
    assert decoded_first["result"]["diagnostics"]["engine_provenance"] is decoded_second["result"]["diagnostics"]["engine_provenance"]
    assert paths.HOST_PROTOCOL_VERSION == 2
    changed = {"type": "result", "result": {"diagnostics": {"engine_provenance": {"engine": "changed"}}}}
    frame = encoder.envelope(changed)
    assert "engine_provenance" in frame
    assert decoder.accept(roundtrip(frame)) == changed
    # A new sweep must send its own provenance even on a reused host.
    assert "engine_provenance" in relay.EventRelay().envelope(event)


def test_missing_reference_is_refused():
    with pytest.raises(ValueError, match="reference"):
        relay.EventRelay().accept({"event": {"result": {"diagnostics": {}}}, "provenance_ref": True})


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
@pytest.mark.parametrize("wrapper", ['{"x":%s}', '{"x":[[%s]]}', '{"x":["text",%s]}', '{"x":[{},[%s,1]]}'])
def test_decode_rejects_nonfinite_everywhere(token, wrapper):
    body = (wrapper % token).encode()
    with pytest.raises(ipc.FrameError):
        ipc.receive_frame(FragmentedPeer(frame(body)))


def test_finite_arrays_ragged_lists_big_integers_and_strings_survive():
    payload = {"array": [[1.2, 3.4], [5., 6.]], "ragged": [[1.], [2., 3.]],
               "big": 10**100, "text": ["NaN", "Infinity"], "zero": -0.0}
    decoded = roundtrip(payload)
    assert decoded == payload
    assert json.dumps(decoded, allow_nan=False).encode() == json.dumps(payload, allow_nan=False).encode()


@pytest.mark.parametrize("result", [None, [], "invalid", 1])
def test_nondict_provenance_result_becomes_host_error(result):
    from server.solver.beat_runtime.client import HostError, _RemoteStream
    from types import SimpleNamespace
    from unittest.mock import patch

    peer = SimpleNamespace(shutdown=lambda *a: None, close=lambda: None, sendall=lambda *a: None)
    client = SimpleNamespace(_closed=SimpleNamespace(is_set=lambda: False))
    stream = _RemoteStream(client, peer, None)
    with patch("server.solver.beat_runtime.client._receive_frame", return_value={
            "type": "event", "event": {"result": result}, "provenance_ref": True}):
        with pytest.raises(HostError, match="reference"):
            next(stream)


def test_long_mixed_lists_preserve_json_values():
    mixed = {"text": ["x"] * 100, "shape": [2, 3], "dicts": [{"x": 1.}] * 100,
             "nested": [[1., 2.]] * 100, "numbers": list(range(100)),
             "literals": [True, False, None] * 100}
    assert roundtrip(mixed) == mixed


@pytest.mark.parametrize("overflow", [False, True])
def test_long_homogeneous_arrays_keep_nonfinite_strictness(overflow):
    body = ('{"x":[' + ','.join(["1"] * 65 + (["1e999"] if overflow else ["2"])) + ']}').encode()
    if overflow:
        with pytest.raises(ipc.FrameError):
            ipc.receive_frame(FragmentedPeer(frame(body)))
    else:
        assert ipc.receive_frame(FragmentedPeer(frame(body)))["x"][-1] == 2


@pytest.mark.parametrize("token", ["1.0", "NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_long_numeric_lists_preserve_large_integer_and_overflow_parity(token):
    body = ('{"x":[' + ','.join([str(10**1000), token] * 40) + ']}').encode()
    peer = FragmentedPeer(frame(body))
    if token == "1.0":
        decoded = ipc.receive_frame(peer)
        assert decoded == {"x": [10**1000, 1.] * 40}
        assert json.dumps(decoded, separators=(",", ":"), allow_nan=False).encode() == body
    else:
        with pytest.raises(ipc.FrameError):
            ipc.receive_frame(peer)
