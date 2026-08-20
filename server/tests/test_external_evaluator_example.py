from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def _module():
    spec = importlib.util.spec_from_file_location(
        "wg_external_evaluator",
        ROOT / "examples" / "external_evaluator.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_reference_client_polls_to_a_versioned_result() -> None:
    module = _module()

    class FixtureClient(module.WaveguideGeneratorClient):
        def __init__(self) -> None:
            self.seen = 0

        def submit(self, solve_request: dict[str, Any]) -> str:
            assert solve_request["client_request_id"] == "example-1"
            return "job-1"

        def status(self, job_id: str) -> dict[str, Any]:
            assert job_id == "job-1"
            self.seen += 1
            return {"status": "complete" if self.seen > 1 else "running"}

        def results(self, job_id: str) -> tuple[dict[str, Any], str]:
            assert job_id == "job-1"
            return {"result_kind": "parametric", "result_contract_version": 1}, "a" * 64

    evaluation = FixtureClient().evaluate(
        {"client_request_id": "example-1"},
        poll_seconds=0.01,
        timeout_seconds=1,
    )
    assert evaluation.job_id == "job-1"
    assert evaluation.results["result_contract_version"] == 1
    assert evaluation.result_sha256 == "a" * 64
