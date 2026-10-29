"""Ordinary design solves must never omit compiled assembly radiators."""

import pytest

from test_assembly_submission import compile_body


@pytest.mark.parametrize("engine", ["beat-cpu", "beat-metal", "metal", "bempp", "auto"])
def test_ordinary_solve_refuses_to_silently_omit_the_assembly(engine):
    from server.jobs.models import SolveRequest

    with pytest.raises(ValueError, match="compiled model so every radiator is included"):
        SolveRequest.model_validate({"design": compile_body()["design"], "options": {"engine": engine}})
