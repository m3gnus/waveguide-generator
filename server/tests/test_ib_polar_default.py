"""An infinite-baffle solve observes 0-90 degrees unless the user chose an arc."""

from __future__ import annotations

from typing import Any

import pytest

from server.jobs.models import SolveRequest
from server.solver.context import SolverContext


def _request(sim_type: str, polar: dict[str, Any] | None) -> SolveRequest:
    options: dict[str, Any] = {}
    if polar is not None:
        options["polar_config"] = polar
    return SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "simulation": {
                    "f1": 100,
                    "f2": 1000,
                    "num_frequencies": 5,
                    "sim_type": sim_type,
                },
            },
            "options": options,
        }
    )


def _arc(sim_type: str, polar: dict[str, Any] | None) -> tuple[Any, ...]:
    context = SolverContext.from_request(_request(sim_type, polar), solver_mode="full_3d")
    return tuple(context.polar_config["angle_range"])


def test_infinite_baffle_defaults_to_the_front_half_space() -> None:
    assert _arc("infinite-baffle", None) == (0.0, 90.0, 19)


def test_browser_default_arc_counts_as_unset() -> None:
    # The browser always sends its 0-180 default; that is "not set".
    polar = {"angle_range": [0.0, 180.0, 37], "angle_step": 5.0}
    assert _arc("infinite-baffle", polar) == (0.0, 90.0, 19)


@pytest.mark.parametrize(
    "polar",
    [
        {"angle_range": [0.0, 120.0, 25], "angle_step": 5.0},
        {"angle_range": [0.0, 180.0, 19], "angle_step": 10.0},
        {"angle_range": [0.0, 60.0, 13], "angle_step": 5.0},
    ],
)
def test_a_user_set_arc_is_kept(polar: dict[str, Any]) -> None:
    assert _arc("infinite-baffle", polar) == tuple(polar["angle_range"])


def test_free_standing_solves_keep_the_full_arc() -> None:
    assert _arc("freestanding", None) == (0.0, 180.0, 37)
    polar = {"angle_range": [0.0, 180.0, 37], "angle_step": 5.0}
    assert _arc("freestanding", polar) == (0.0, 180.0, 37)


def test_the_replaced_arc_stays_consistent_with_the_rest_of_the_config() -> None:
    context = SolverContext.from_request(
        _request("infinite-baffle", None), solver_mode="full_3d"
    )
    assert context.polar_config["angle_step"] == 5.0
    assert context.polar_config["norm_angle"] == 5.0
    # 0-90 in 5 degree steps is 19 samples.
    assert (90.0 - 0.0) / 5.0 + 1 == context.polar_config["angle_range"][2]
