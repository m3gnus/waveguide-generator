"""Exact stored/streamed JSON with bounded cooperative encoding."""

import json
import math

import pytest

from server.jobs.json_encoding import json_chunks, result_json_text


@pytest.mark.parametrize("value", [
    {}, [], (), {"nested": [1.25, -0.0, None, True, False, 10**200]},
    {"unicode": "λ\n\"\\\ud800𝄞" * 70000},
    {"𝄞" * 200000: "long key"},
    {1: "integer", None: "null", False: "false", 1.25: "float"},
    {"matrix": [[i / 7 for i in range(517)], [None] * 517]},
])
def test_exact_standard_json_bytes(value):
    assert result_json_text(value) == json.dumps(value, allow_nan=False)
    assert b"".join(json_chunks(value)) == json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_refused_in_nested_leaves(value):
    with pytest.raises(ValueError, match="JSON compliant"):
        result_json_text({"nested": [[1.0, value]]})


def test_large_result_yields_between_bounded_chunks(monkeypatch):
    from server.jobs import json_encoding

    yields = []
    monkeypatch.setattr(json_encoding.time, "sleep", lambda duration: yields.append(duration))
    result = {"binary": "𝄞" * 300000, "numbers": list(range(100000))}
    assert result_json_text(result) == json.dumps(result, allow_nan=False)
    assert len(yields) > 1
    assert max(map(len, json_chunks(result))) < 1024 * 1024
    assert set(yields) == {0}
