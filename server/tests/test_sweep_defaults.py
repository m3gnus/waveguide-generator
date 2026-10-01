"""Default sweeps and caller-supplied frequencies across editor/headless boundaries."""

from __future__ import annotations

import json

import numpy as np
import pytest

from server.cli.request import build_request, load_request_document
from server.design.defaults import default_sweep_start_hz
from server.design.textcfg import parse
from server.jobs.models import SolveRequest
from server.jobs.runtime import JobRuntime
from server.solver.context import SolverContext
from server.solver.frequency_sweep import canonical_frequencies


TEXT = "Length = 120\nCoverage.Angle = 45\nThroat.Diameter = 25.4\n"


def assert_sweep(request: SolveRequest, expected: tuple[float, float, int]) -> None:
    context = SolverContext.from_request(request, solver_mode="full_3d")
    assert (*context.frequency_range, context.num_frequencies) == expected
    assert JobRuntime._frequency_options(request) == expected
    points = canonical_frequencies(context)
    assert len(points) == expected[2]
    assert points[0] == pytest.approx(expected[0])
    assert points[-1] == pytest.approx(expected[1])


def test_sparse_request_uses_shared_start_and_retains_headless_end_count() -> None:
    assert default_sweep_start_hz() == 50.0
    request = SolveRequest.model_validate({"design": {"formula": "OSSE"}})
    assert_sweep(request, (50.0, 20_000.0, 24))


def test_request_validation_uses_new_start_for_omitted_f1() -> None:
    # This end is valid above 50 Hz but was invalid above the old 200 Hz default.
    request = SolveRequest.model_validate(
        {"design": {"formula": "OSSE", "simulation": {"f2": 100}}}
    )
    assert_sweep(request, (50.0, 100.0, 24))


@pytest.mark.parametrize("header", ["; Parameter config\n", "; ATH design\n"])
@pytest.mark.parametrize("prefix", ["Simulation", "ABEC"])
@pytest.mark.parametrize("explicit", [False, True])
def test_cli_text_omitted_start_uses_new_default_and_explicit_values_win(
    header: str, prefix: str, explicit: bool
) -> None:
    suffix = (
        f"{prefix}.{'F1' if prefix == 'Simulation' else 'f1'} = 315\n"
        f"{prefix}.{'F2' if prefix == 'Simulation' else 'f2'} = 8000\n"
        f"{prefix}.NumFrequencies = 7\n"
        if explicit else ""
    )
    parsed = parse(header + TEXT + suffix)
    request = build_request(parsed).request
    assert_sweep(request, (315.0, 8000.0, 7) if explicit else (50.0, 20_000.0, 24))
    if not explicit:
        assert parsed.design.root.simulation.f1 is None
        assert not any("sweep" in item.name or "f1" in item.name for item in parsed.migrations)


def test_cli_json_request_uses_new_start(tmp_path) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps({"design": {"formula": "OSSE"}}), encoding="utf-8")
    assert_sweep(load_request_document(path).request, (50.0, 20_000.0, 24))


@pytest.mark.parametrize("family", ["OSSE", "R-OSSE", "ICW", "FREEFORM"])
def test_new_editor_sweep_reaches_solver_and_job_summary(family: str) -> None:
    design = {"formula": family, "simulation": {"f1": 50, "f2": 16_000, "num_frequencies": 32}}
    if family == "FREEFORM":
        design.update({
            "length": 120,
            "cross_sections": [{"t": 0, "shape": "circle"}, {"t": 1, "shape": "circle"}],
            "profile_h": {"points": [{"t": 0, "r": 12.7}, {"t": 1, "r": 140}]},
            "profile_v": {"points": [{"t": 0, "r": 12.7}, {"t": 1, "r": 140}]},
        })
    request = SolveRequest.model_validate({"design": design})
    assert_sweep(request, (50.0, 16_000.0, 32))
    assert (32 - 1) / np.log2(16_000 / 50) == pytest.approx(3.725098, abs=1e-6)


@pytest.mark.parametrize("options,expected", [
    ({"frequency_range": [400, 8000], "num_frequencies": 20}, (400.0, 8000.0, 20)),
    ({"frequencies_hz": [63, 500, 1600]}, (63.0, 1600.0, 3)),
])
def test_explicit_options_override_design_defaults(options, expected) -> None:
    request = SolveRequest.model_validate({
        "design": {"formula": "OSSE", "simulation": {"f1": 50, "f2": 16_000, "num_frequencies": 32}},
        "options": options,
    })
    assert_sweep(request, expected)
    if "frequencies_hz" in options:
        context = SolverContext.from_request(request, solver_mode="full_3d")
        assert canonical_frequencies(context).tolist() == options["frequencies_hz"]
