"""Crossing the trace retention cap must not be silent.

Retention is capped in memory, and the cap is a cliff: one step past it -- a few
more frequencies, a slightly denser mesh -- and the field plane simply stops
being offered, with no other visible difference in the solve. The machine token
``size_cap_exceeded`` alone tells a user neither how far over they are nor what
to change.
"""

from __future__ import annotations

from server.solver.field_traces_store import (
    DEFAULT_FIELD_TRACES_MAX_BYTES,
    describe_retention_refusal,
    estimate_field_trace_retention_bytes,
)


def test_nothing_refused_says_nothing():
    assert describe_retention_refusal(None, 123, 456) is None


def test_the_cap_refusal_quotes_both_numbers_and_a_remedy():
    detail = describe_retention_refusal(
        "size_cap_exceeded", 310 * 1048576, 256 * 1048576
    )

    assert detail is not None
    assert "310 MB" in detail
    assert "256 MB" in detail
    assert "WG2_FIELD_TRACES_MAX_BYTES" in detail
    # The distinction that matters most: the numbers moved, the solve did not.
    assert "solve itself is unaffected" in detail


def test_other_refusals_are_still_named():
    detail = describe_retention_refusal("disabled_by_option", None, 1)

    assert detail is not None
    assert "disabled_by_option" in detail


def test_the_cliff_sits_where_an_ordinary_sweep_can_reach_it():
    """Guards the headroom, not the constant. A 200-frequency sweep on a
    closed surface mesh crosses the default cap at roughly 56k triangles, which
    is inside what Mesh.MaxTriangles allows -- so this is a limit users meet by
    doing something ordinary, and the message above is load-bearing.
    """

    triangles = 56_000
    vertices = triangles // 2  # closed surface: T ~ 2V
    estimated = estimate_field_trace_retention_bytes(200, vertices, triangles, 1)

    assert estimated > DEFAULT_FIELD_TRACES_MAX_BYTES
    # ...and a materially smaller mesh stays under it, so the cliff is genuinely
    # near this size rather than far below it.
    smaller = estimate_field_trace_retention_bytes(200, 12_500, 25_000, 1)
    assert smaller < DEFAULT_FIELD_TRACES_MAX_BYTES
