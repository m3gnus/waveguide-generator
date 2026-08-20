from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

import pytest


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


def _result_client(module, results: object, declared_digest: str | None):
    raw = json.dumps(results, separators=(",", ":")).encode("utf-8")

    class FixtureClient(module.WaveguideGeneratorClient):
        def _request(self, method, path, payload=None):
            assert (method, path, payload) == ("GET", "/api/results/job-1", None)
            headers = {}
            if declared_digest is not None:
                headers["x-wg-results-sha256"] = declared_digest
            return raw, headers

    return FixtureClient(), raw


def test_reference_client_accepts_a_supported_digest_verified_result() -> None:
    module = _module()
    results = {"result_kind": "parametric", "result_contract_version": 1}
    raw = json.dumps(results, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    client, _raw = _result_client(module, results, digest)

    document, received_digest = client.results("job-1")

    assert document == results
    assert received_digest == digest


def test_reference_client_requires_the_result_digest_header() -> None:
    module = _module()
    client, _raw = _result_client(
        module,
        {"result_kind": "parametric", "result_contract_version": 1},
        None,
    )

    with pytest.raises(RuntimeError, match="omitted X-WG-Results-SHA256"):
        client.results("job-1")


def test_reference_client_rejects_a_result_digest_mismatch() -> None:
    module = _module()
    client, _raw = _result_client(
        module,
        {"result_kind": "parametric", "result_contract_version": 1},
        "0" * 64,
    )

    with pytest.raises(RuntimeError, match="result digest mismatch"):
        client.results("job-1")


def test_reference_client_rejects_an_unknown_result_contract() -> None:
    module = _module()
    results = {"result_kind": "parametric", "result_contract_version": 99}
    raw = json.dumps(results, separators=(",", ":")).encode("utf-8")
    client, _raw = _result_client(module, results, hashlib.sha256(raw).hexdigest())

    with pytest.raises(RuntimeError, match="Unsupported WG result contract"):
        client.results("job-1")


def test_reference_client_reports_the_written_artifact_digest(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    module = _module()
    results = {"result_kind": "parametric", "result_contract_version": 1}

    class FixtureClient:
        def __init__(self, _base_url: str) -> None:
            pass

        def evaluate(self, solve_request):
            assert solve_request == {"geometry": "fixture"}
            return module.Evaluation(
                "job-1",
                {"status": "complete"},
                results,
                "a" * 64,
            )

    monkeypatch.setattr(module, "WaveguideGeneratorClient", FixtureClient)
    request = tmp_path / "request.json"
    request.write_text('{"geometry":"fixture"}', encoding="utf-8")
    output = tmp_path / "results.json"

    assert module.main([str(request), "--output", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)

    assert summary["result_sha256"] == "a" * 64
    assert summary["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert summary["output_sha256"] != summary["result_sha256"]
