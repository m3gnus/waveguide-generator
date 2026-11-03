"""STL sizing skips unused fits while retaining saved-design grids exactly."""

from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pytest

from server.design.schema import DesignConfig
from server.exports import core, sizing

CASES = json.loads((Path(__file__).parent / "fixtures/stl-sizing-parity.json").read_text())


@pytest.mark.parametrize("case", CASES)
def test_saved_design_plan_and_grid_bytes_match(case):
    plan, params = core._stl_grid_plan(DesignConfig.model_validate(case["design"]))
    assert asdict(plan) == case["plan"]
    grid, _, _ = sizing._point_grid(params, plan.angular, plan.length)
    assert hashlib.sha256(grid.tobytes()).hexdigest() == case["grid_sha256"]


def test_linear_planner_skips_cubic_work(monkeypatch):
    def forbidden(*_):
        pytest.fail("linear STL planning must not evaluate a cubic surface")

    monkeypatch.setattr(sizing, "_refined", forbidden)
    monkeypatch.setattr(sizing, "_cubic_resample", forbidden)
    core._stl_grid_plan(DesignConfig.model_validate(CASES[1]["design"]))


def test_linear_planner_carries_candidate_grid_to_measurement(monkeypatch):
    real = sizing._point_grid
    requests = []

    def sample(params, angular, length):
        requests.append((angular, length))
        return real(params, angular, length)

    monkeypatch.setattr(sizing, "_point_grid", sample)
    core._stl_grid_plan(DesignConfig.model_validate(CASES[0]["design"]))
    assert max(Counter(requests).values()) == 1
