"""The durable result identifies the sources and helper that actually ran."""

from types import SimpleNamespace

from server.integration.provenance import enrich_result_contract
from server.jobs.models import SolveRequest
from server.jobs.result_contracts import RESULT_ENVELOPE_ADAPTER
from server.solver.metal import _metal_fingerprints


def test_native_results_keep_distinct_executed_helpers_and_unknown_sources():
    source = {"version": "0.3.0.dev0", "repository_revision": "a" * 40, "source_sha256": "b" * 64}
    first = SimpleNamespace(native_runtime={"source": source, "helper": {"sha256": "c" * 64}})
    other = SimpleNamespace(native_runtime={"source": source, "helper": {"sha256": "d" * 64}})
    missing = SimpleNamespace(native_runtime={})
    fingerprints = _metal_fingerprints([first, first, other, missing])
    assert len(fingerprints) == 3
    assert fingerprints[0]["commit"] == "a" * 40
    assert fingerprints[0]["source_sha256"] == "b" * 64
    assert fingerprints[0]["build_sha256"] == "c" * 64
    assert fingerprints[1]["build_sha256"] == "d" * 64
    assert fingerprints[2]["commit"] is None
    assert fingerprints[2]["version"] is None


def test_script_fallback_keeps_script_and_interpreter_hashes_separate():
    result = SimpleNamespace(native_runtime={
        "source": {"version": "0+unknown"},
        "helper": {"source": "swift-script", "sha256": "a" * 64},
        "swift": {"sha256": "b" * 64},
    })
    fingerprint = _metal_fingerprints([result])[0]
    assert fingerprint["version"] is None
    assert fingerprint["build_sha256"] is None
    assert fingerprint["script_sha256"] == "a" * 64
    assert fingerprint["interpreter_sha256"] == "b" * 64


def test_result_provenance_keeps_observed_fingerprint_not_the_configured_pin():
    request = SolveRequest.model_validate({
        "design": {"formula": "OSSE", "L": 120, "a": 40},
        "options": {"engine": "metal", "frequencies_hz": [500]},
    })
    fingerprint = _metal_fingerprints([SimpleNamespace(native_runtime={
        "source": {"version": "0.3.0.dev0", "repository_revision": "a" * 40, "source_sha256": "b" * 64},
        "helper": {"sha256": "c" * 64},
    })])[0]
    result = enrich_result_contract({"metadata": {"_solver_fingerprints": [fingerprint]}}, request)
    assert result["provenance"]["solver_fingerprints"] == [fingerprint]
    assert "_solver_fingerprints" not in result["metadata"]
    RESULT_ENVELOPE_ADAPTER.validate_python(result)
    fingerprint["commit"] = "changed"
    assert result["provenance"]["solver_fingerprints"][0]["commit"] == "a" * 40


def test_unknown_solver_source_is_not_substituted_with_a_pin():
    request = SolveRequest.model_validate({
        "design": {"formula": "OSSE", "L": 120, "a": 40},
        "options": {"engine": "metal", "frequencies_hz": [500]},
    })
    result = enrich_result_contract({"metadata": {"engine": "hornlab-metal-bem"}}, request)
    unknown = result["provenance"]["solver_fingerprints"][0]
    assert unknown["package"] == "hornlab-metal-bem"
    assert unknown["commit"] is None
    assert unknown["source_sha256"] is None
    assert unknown["build_sha256"] is None


def test_beat_fingerprint_comes_from_executed_result_diagnostics():
    request = SolveRequest.model_validate({
        "design": {"formula": "OSSE", "L": 120, "a": 40},
        "options": {"engine": "beat-cpu", "frequencies_hz": [500]},
    })
    run = {
        "engine": {"version": "0.3", "repository_revision": "a" * 40, "source_sha256": "b" * 64},
        "runtime": {"sysimage_sha256": "c" * 64, "sysimage_file": "private-location"},
        "execution": {"backend": "cpu"},
    }
    beat = {"solver_log": [{"native_diagnostics": {"engine_provenance": run}}]}
    result = enrich_result_contract({"metadata": {"beat": beat}}, request)
    assert result["provenance"]["solver_fingerprints"] == [{
        "package": "beat-engine", "version": "0.3", "commit": "a" * 40,
        "source_sha256": "b" * 64, "build_sha256": "c" * 64, "backend": "cpu",
    }]
