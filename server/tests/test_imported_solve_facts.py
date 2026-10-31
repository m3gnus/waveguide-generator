"""Common imported facts preserve adapter context and trace-retention decisions."""

from dataclasses import FrozenInstanceError

import pytest

from server.solver.imported import imported_record_symmetry
from server.solver.imported_channels import prepare_imported_solve_facts
from server.tests import test_imported_beat as cad


@pytest.mark.parametrize("motions,expected", [
    (["normal", "normal"], "normal"),
    (["axial", "axial"], "axial"),
    (["axial", "normal"], "normal"),
])
@pytest.mark.parametrize("planes,quadrants,native", [
    ([], 1234, None), (["x0"], 14, "yz"), (["y0"], 12, "xz"),
    (["y0", "x0"], 1, "yz+xz"),
])
def test_domain_context_and_trace_inputs(motions, expected, planes, quadrants, native):
    request = cad._request(drive_channels=[
        {"id": "a", "source_ids": ["source-a"], "motion": motions[0]},
        {"id": "b", "source_ids": ["source-b", "source-c"], "motion": motions[1]},
    ])
    record = cad._record()
    record["symmetry"] = {"cut_planes": ["z0"], "domain_planes": planes}
    symmetry = imported_record_symmetry(record)
    assert (symmetry.quadrants, symmetry.native_plane, symmetry.cut_planes) == (
        quadrants, native, tuple(planes),
    )
    facts = prepare_imported_solve_facts(request, record, quadrants=quadrants)
    assert facts.context.source_motion == expected
    assert facts.context.quadrants == quadrants
    assert facts.context.polar_config["observation_origin"] == "throat"
    assert facts.frequencies_hz == (100., 1000., 500.)
    assert facts.motions == frozenset(motions)
    # Three frequencies x two channels x (4 vertices + 4 faces) x complex128.
    assert facts.trace_retention_plan(cad.MESH, supported=True, cap_bytes=10000) == (
        True, None, 768, 10000,
    )
    assert facts.trace_retention_plan(
        cad.MESH, supported=False, cap_bytes=10000, unsupported_reason="backend_specific",
    ) == (False, "backend_specific", None, 10000)
    with pytest.raises(FrozenInstanceError):
        facts.field_plane_enabled = False


@pytest.mark.parametrize("mesh", [None, [], {}, {"stats": []}])
def test_optional_stats_and_field_plane_option(mesh):
    request = cad._request()
    request.options.polar_config.field_plane = False
    facts = prepare_imported_solve_facts(request, {"mesh": mesh}, quadrants=1234)
    assert facts.mesh_stats is None
    # Disabled intent wins over support and never needs a readable mesh.
    assert facts.trace_retention_plan("not a mesh", supported=False, cap_bytes=200) == (
        False, "disabled_by_option", None, 200,
    )


def test_legacy_domain_fallback_and_context_revalidation():
    record = cad._record(planes=["x0"])
    assert imported_record_symmetry(record).quadrants == 14
    request = cad._request()
    request.options.frequencies_hz = None
    request.options.num_frequencies = 3
    request.options.frequency_range = [1000., 100.]
    with pytest.raises(ValueError, match="positive and increasing"):
        prepare_imported_solve_facts(request, record, quadrants=14)
