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


def test_a_normalisation_angle_behind_the_front_half_space_keeps_the_full_arc() -> None:
    from server.jobs.models import PolarConfig

    polar = {"angle_range": [0.0, 180.0, 37], "angle_step": 5.0, "norm_angle": 120.0}
    context = SolverContext.from_request(
        _request("infinite-baffle", polar), solver_mode="full_3d"
    )
    assert tuple(context.polar_config["angle_range"]) == (0.0, 180.0, 37)
    assert context.polar_config["norm_angle"] == 120.0
    # The config the solver receives still satisfies PolarConfig's own rule.
    PolarConfig.model_validate(context.polar_config)


def test_the_replaced_config_revalidates() -> None:
    from server.jobs.models import PolarConfig

    context = SolverContext.from_request(
        _request("infinite-baffle", None), solver_mode="full_3d"
    )
    PolarConfig.model_validate(context.polar_config)


def test_recorded_polar_grid_is_the_observed_arc() -> None:
    from server.jobs.runtime import JobRuntime
    from server.solver.polar_arc import effective_polar_grid

    request = _request("infinite-baffle", {"angle_range": [0.0, 180.0, 37], "angle_step": 5.0})
    grid = effective_polar_grid(request)
    assert (grid["start"], grid["end"], grid["count"], grid["resolved_step"]) == (0.0, 90.0, 19, 5.0)
    enriched = JobRuntime._with_request_metadata({"metadata": {}}, request)
    assert enriched["metadata"]["polar_grid"] == grid
    free = _request("freestanding", {"angle_range": [0.0, 180.0, 37], "angle_step": 5.0})
    assert effective_polar_grid(free)["end"] == 180.0


def test_a_text_config_arc_of_exactly_the_default_is_narrowed_deliberately() -> None:
    """``MapAngleRange = 0,180,37`` is the default tuple, so an IB solve narrows it.

    Pinned on purpose: the rear is zero pressure, so nothing is lost, and any other
    arc (0-180 at 10 degrees, say) is the way to keep the full range.
    """
    from server.design.solve_block import solve_options_from_blocks

    blocks = {"ABEC.Polars:SPL": {"items": {"MapAngleRange": "0,180,37"}}}
    options = solve_options_from_blocks(blocks)
    assert tuple(options.polar_config.angle_range) == (0.0, 180.0, 37)
    request = _request("infinite-baffle", options.polar_config.model_dump(mode="json"))
    context = SolverContext.from_request(request, solver_mode="full_3d")
    assert tuple(context.polar_config["angle_range"]) == (0.0, 90.0, 19)
    blocks["ABEC.Polars:SPL"]["items"]["MapAngleRange"] = "0,180,19"
    kept = solve_options_from_blocks(blocks)
    request = _request("infinite-baffle", kept.polar_config.model_dump(mode="json"))
    context = SolverContext.from_request(request, solver_mode="full_3d")
    assert tuple(context.polar_config["angle_range"]) == (0.0, 180.0, 19)


def test_spherical_sampling_keeps_the_full_arc() -> None:
    polar = {"angle_range": [0.0, 180.0, 37], "angle_step": 5.0, "spherical_sampling": True}
    assert _arc("infinite-baffle", polar) == (0.0, 180.0, 37)


def test_infinite_baffle_run_records_one_grid_everywhere() -> None:
    """metadata.polar_grid, the job summary, directivity metadata and the dry-run agree."""
    from server.engines.dryrun import DryRunEngine
    from server.jobs.runtime import JobRuntime
    from server.solver.polar_arc import effective_polar_config, effective_polar_grid
    from server.solver.contract import build_directivity_metadata

    request = _request("infinite-baffle", {"angle_range": [0.0, 180.0, 37], "angle_step": 5.0})
    grid = effective_polar_grid(request)
    context = SolverContext.from_request(request, solver_mode="full_3d")
    directivity = build_directivity_metadata(context.polar_config, {})["resolved_grid"]
    observed = (0.0, 90.0, 19)
    assert (grid["start"], grid["end"], grid["count"]) == observed
    assert (directivity["start"], directivity["end"], directivity["count"]) == observed
    assert JobRuntime._with_request_metadata({"metadata": {}}, request)["metadata"]["polar_grid"] == grid
    assert JobRuntime._config_summary(request)["polar_grid"] == grid

    result = DryRunEngine().solve(
        request.design.model_dump(mode="json"),
        frequency_start_hz=100.0,
        frequency_end_hz=200.0,
        num_frequencies=2,
        frequency_spacing="log",
        polar_config=effective_polar_config(request),
    )
    angles = [sample[0] for sample in result["directivity"]["horizontal"][0]]
    assert (angles[0], angles[-1], len(angles)) == (0.0, 90.0, 19)
