"""Public request identity and provenance contract tests."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from server.integration.provenance import enrich_result_contract
from server.jobs.models import SolveRequest


def _request(**changes: object) -> SolveRequest:
    payload: dict[str, object] = {
        "design": {"formula": "OSSE", "L": 120, "a": 40},
        "options": {"engine": "metal", "frequencies_hz": [500.0]},
        "client_request_id": "hes-so-evaluation-17",
        "client_metadata": {"study": "osse-target-a", "iteration": 2},
    }
    payload.update(changes)
    return SolveRequest.model_validate(payload)


def test_parametric_result_has_stable_identity_and_provenance() -> None:
    request = _request()
    first = enrich_result_contract({"metadata": {}}, request)
    second = enrich_result_contract({"metadata": {}}, request)

    assert first == second
    assert first["result_kind"] == "parametric"
    assert first["result_contract_version"] == 1
    assert first["metadata"]["result_contract_version"] == 1
    assert first["client_request_id"] == "hes-so-evaluation-17"
    assert first["client_metadata"] == {
        "study": "osse-target-a",
        "iteration": 2,
    }
    provenance = first["provenance"]
    assert provenance["schema_version"] == 1
    assert provenance["wg_version"]
    assert provenance["request_identity"] == "execution"
    assert provenance["resolved_engine"] == "metal"
    assert set(provenance["dependency_shas"]) >= {
        "hornlab-waveguide-mesher",
        "hornlab-metal-bem",
    }
    assert all(
        len(provenance[name]) == 64
        for name in (
            "request_sha256",
            "geometry_sha256",
            "solve_options_sha256",
            "execution_request_sha256",
            "execution_geometry_sha256",
            "execution_solve_options_sha256",
            "effective_request_sha256",
            "effective_geometry_sha256",
            "effective_solve_options_sha256",
        )
    )
    assert provenance["request_sha256"] == provenance["execution_request_sha256"]
    assert provenance["geometry_sha256"] == provenance["execution_geometry_sha256"]
    assert (
        provenance["solve_options_sha256"]
        == provenance["execution_solve_options_sha256"]
    )
    assert provenance["request_sha256"] == provenance["effective_request_sha256"]


@pytest.mark.parametrize(
    "metadata",
    [
        {"bad": math.nan},
        {"too_large": "x" * (16 * 1024)},
    ],
)
def test_client_metadata_must_be_bounded_finite_json(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="client_metadata"):
        _request(client_metadata=metadata)
