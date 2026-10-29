"""The front view's live placement preview is pinned to the placement owner.

The front workspace draws a woofer whose placement changed since the last
server context by shifting the resolved centre (``frontWoofers`` in
``frontend/src/sketch/adapters/frontView.ts``). This test resolves both the
before and the after assembly with the server's own front context, which
uses the design mesher's placement, and keeps the result in a fixture that
the frontend test reads: both sides must agree on every edited woofer.

Set ``UPDATE_FRONT_VIEW_FIXTURE=1`` to rewrite the fixture after a deliberate
change of the placement contract.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from server.mesh.builder import _assembly_front_context_sync

FIXTURE = Path(__file__).resolve().parents[2] / "frontend/src/sketch/adapters/frontView.fixture.json"
HORN = {
    "formula": "R-OSSE", "R": 100, "r0": 12.7, "a0": 15.5, "a": 25, "k": 2, "m": 0.85,
    "b": 0.2, "r": 0.4, "q": 3.4, "tmax": 1,
    "mesh": {"quadrants": 1234, "throat_resolution": 8, "mouth_resolution": 20,
             "rear_resolution": 40, "angular_segments": 32, "length_segments": 12},
    "enclosure": {"depth": 200, "space_l": 200, "space_t": 250, "space_r": 200, "space_b": 450,
                  "front_resolution": 30, "back_resolution": 50, "edge_radius": 10},
}
W1, W2, W3, W4 = (f"00000000-0000-4000-8000-0000000000{suffix}" for suffix in ("a1", "a2", "a3", "a4"))
GROUP = "00000000-0000-4000-8000-0000000000b1"


def woofer(identity, placement, radius=80):
    return {"id": identity, "label": f"Woofer {identity[-1]}", "radius_mm": radius,
            "shape": {"kind": "piston"}, "placement": placement}


def assembly(*woofers):
    return {"woofers": list(woofers), "signal_groups": [{"id": GROUP, "label": "All", "members": [
        {"radiator_id": "horn-throat"}, *({"radiator_id": item["id"]} for item in woofers)]}]}


BEFORE = assembly(
    woofer(W1, {"anchor": "below", "gap_mm": 10}),
    woofer(W2, {"anchor": "above", "gap_mm": 10}, radius=60),
    woofer(W3, {"anchor": "free", "x_mm": 120, "y_mm": -320, "spacing_to": {"radiator_id": W1, "distance_mm": 190}}, radius=50),
    woofer(W4, {"anchor": "free", "x_mm": -220, "y_mm": 260}, radius=40),
)


def edited(changes):
    result = copy.deepcopy(BEFORE)
    for item in result["woofers"]:
        item.update(copy.deepcopy(changes.get(item["id"], {})))
    return result


CASES = {
    "gap-and-radius": edited({W1: {"placement": {"anchor": "below", "gap_mm": 25}, "radius_mm": 70},
                              W2: {"placement": {"anchor": "above", "gap_mm": 4}}}),
    "spacing-distance": edited({W3: {"placement": {"anchor": "free", "x_mm": 120, "y_mm": -320,
                                                    "spacing_to": {"radiator_id": W1, "distance_mm": 230}}}}),
    "free-centre": edited({W4: {"placement": {"anchor": "free", "x_mm": -205.5, "y_mm": 271}}}),
}


def resolve(dump):
    context = _assembly_front_context_sync({**copy.deepcopy(HORN), "assembly": dump})
    assert "refusal" not in context, context
    return {key: context[key] for key in ("mouth_bottom_mm", "mouth_top_mm", "woofers")}


def fixture():
    return {
        "before": {"assembly": BEFORE, "context": resolve(BEFORE)},
        "cases": {name: {"assembly": dump, "context": resolve(dump)} for name, dump in CASES.items()},
    }


def test_the_front_view_fixture_is_the_placement_owners_answer():
    actual = fixture()
    if os.environ.get("UPDATE_FRONT_VIEW_FIXTURE") == "1":
        FIXTURE.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n")
    expected = json.loads(FIXTURE.read_text())
    assert expected["before"]["assembly"] == actual["before"]["assembly"]
    for name, case in actual["cases"].items():
        assert expected["cases"][name]["assembly"] == case["assembly"]
        for got, want in zip(case["context"]["woofers"], expected["cases"][name]["context"]["woofers"], strict=True):
            assert got["radiator_id"] == want["radiator_id"]
            assert (got["x_mm"], got["y_mm"]) == pytest.approx((want["x_mm"], want["y_mm"]), abs=1e-9)
    assert actual["before"]["context"]["mouth_top_mm"] == pytest.approx(expected["before"]["context"]["mouth_top_mm"], abs=1e-9)
