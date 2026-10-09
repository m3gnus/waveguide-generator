"""False-green controls for raw transport and independent analytic scoring."""

import base64
from copy import deepcopy
from dataclasses import replace
import json
import os
from types import SimpleNamespace

import numpy as np
import pytest

from server.solver.beat_adapter.results import ResultContractError
from scripts.beat_conformance import reference_support as support
from scripts.beat_conformance import reference_sphere as sphere, reference_lem_sphere as lem
from server.tests.beat_adapter.test_reference_sphere import synthetic_outcome as sphere_outcome
from server.tests.beat_adapter.test_reference_lem_sphere import synthetic_outcome as lem_outcome


def wire(values):
    array = np.asarray(values, dtype="<c16")
    return dict(
        encoding="base64",
        dtype="complex128",
        shape=list(array.shape),
        order="C",
        byte_order="little",
        content_base64=base64.b64encode(array.tobytes()).decode(),
    )


def reference_pair():
    outputs = [
        dict(
            id="pressure",
            quantity="exterior_pressure",
            target_ids=[],
            options={"points_m": [[1, 0, 0], [2, 0, 0]]},
        ),
        dict(id="load", quantity="radiation_impedance", target_ids=[], options={}),
    ]
    request = dict(
        frequencies_hz=[100.0],
        excitation_port_ids=["port"],
        outputs=outputs,
        compiled_system={"components": [{"id": "sphere", "kind": "ideal_source"}]},
        solver_options=dict(
            precision="float64",
            bem_backend="cpu",
            symmetry="off",
            phasor_convention=support.NEGATIVE_TIME,
        ),
    )
    raw = dict(
        schema_version=2,
        freq_hz=100.0,
        excitation_port_ids=["port"],
        diagnostics={
            **request["solver_options"],
            "compiled_worker": {"driver_mode": "source"},
            "engine_provenance": {
                "engine": {
                    "repository_revision": support.expected_engine_revision(),
                    "repository_dirty": False,
                }
            },
        },
        quantities=[
            dict(
                id="pressure",
                quantity="exterior_pressure",
                unit="Pa",
                axes=["excitation", "observation"],
                metadata={},
                values=wire([[1 + 2j, 3 + 4j]]),
            ),
            dict(
                id="load",
                quantity="radiation_impedance",
                unit="N*s/m",
                axes=["radiator"],
                metadata={},
                values=wire([5 - 6j]),
            ),
        ],
    )
    return request, raw


def test_raw_reference_preserves_velocity_force_complex_values():
    request, raw = reference_pair()
    for quantity in raw["quantities"]:
        quantity["target_id"] = None
    result = support.decode_reference_result(raw, request, 0)
    np.testing.assert_array_equal(result.quantities[0].values, [[1 + 2j, 3 + 4j]])
    np.testing.assert_array_equal(result.quantities[1].values, [5 - 6j])


@pytest.mark.parametrize(
    "defect",
    [
        "schema",
        "phasor",
        "precision",
        "backend",
        "symmetry",
        "frequency",
        "near_frequency",
        "ports",
        "missing",
        "duplicate",
        "quantity",
        "unit",
        "axes",
        "shape",
        "nonfinite",
        "encoding",
        "dirty",
        "revision",
    ],
)
def test_raw_reference_refuses_contract_corruption(defect):
    request, raw = reference_pair()
    q = raw["quantities"][0]
    if defect == "schema":
        raw["schema_version"] = True
    elif defect in {"phasor", "precision", "backend", "symmetry"}:
        key = {"phasor": "phasor_convention", "backend": "bem_backend"}.get(defect, defect)
        raw["diagnostics"][key] = "wrong"
    elif defect in {"frequency", "near_frequency"}:
        raw["freq_hz"] = 101 if defect == "frequency" else 100.000001
    elif defect == "ports":
        raw["excitation_port_ids"] = ["other"]
    elif defect == "missing":
        raw["quantities"].pop()
    elif defect == "duplicate":
        raw["quantities"].append(deepcopy(q))
    elif defect in {"quantity", "unit", "axes"}:
        q[defect] = ["observation", "excitation"] if defect == "axes" else "wrong"
    elif defect == "shape":
        q["values"] = wire([1 + 2j, 3 + 4j])
    elif defect == "nonfinite":
        q["values"] = wire([[complex(np.nan, 0), 3 + 4j]])
    elif defect == "encoding":
        q["values"]["content_base64"] = "!"
    elif defect == "dirty":
        raw["diagnostics"]["engine_provenance"]["engine"]["repository_dirty"] = True
    else:
        raw["diagnostics"]["engine_provenance"]["engine"]["repository_revision"] = "0" * 40
    with pytest.raises((ValueError, ResultContractError)):
        support.decode_reference_result(raw, request, 0)


@pytest.mark.parametrize("family", ["sphere", "lem"])
@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "extra",
        "duplicate_frequency",
        "reordered",
        "nonfinite_frequency",
        "near_frequency",
        "missing_quantity",
        "duplicate_quantity",
        "shape",
        "nonfinite_value",
        "phasor",
        "precision",
        "ports",
    ],
)
def test_direct_scorers_refuse_malformed_synthetic_outcomes(family, defect):
    outcome = (sphere_outcome if family == "sphere" else lem_outcome)(support.POSITIVE_TIME)
    first = outcome.results[0]
    if defect == "missing":
        outcome.results.pop()
    elif defect == "extra":
        outcome.results.append(first)
    elif defect == "duplicate_frequency":
        outcome.results[1] = first
    elif defect == "reordered":
        outcome.results.reverse()
    elif defect in {"nonfinite_frequency", "near_frequency"}:
        outcome.results[0] = replace(
            first, freq_hz=np.nan if defect == "nonfinite_frequency" else first.freq_hz + 1e-7
        )
    elif defect == "missing_quantity":
        outcome.results[0] = replace(first, quantities=first.quantities[:-1])
    elif defect == "duplicate_quantity":
        outcome.results[0] = replace(first, quantities=first.quantities + (first.quantities[0],))
    elif defect in {"shape", "nonfinite_value"}:
        q = first.quantities[0]
        values = q.values.reshape(-1).copy() if defect == "shape" else q.values.copy()
        if defect == "nonfinite_value":
            values.flat[0] = complex(np.inf, 0)
        outcome.results[0] = replace(
            first, quantities=(replace(q, values=values),) + first.quantities[1:]
        )
    elif defect in {"phasor", "precision"}:
        first.diagnostics["phasor_convention" if defect == "phasor" else "precision"] = "wrong"
    else:
        outcome.results[0] = replace(first, excitation_port_ids=("wrong",))
    with pytest.raises(ValueError):
        if family == "sphere":
            sphere.score_outcome(outcome, support.POSITIVE_TIME, sphere.observation_sets())
        else:
            lem.score_outcome(outcome, support.POSITIVE_TIME)


def test_halved_exterior_real_self_load_fails_separate_resistance_budget():
    outcome = sphere_outcome(support.POSITIVE_TIME)
    for row in outcome.results:
        row.quantities[-1].values.real[:] *= 0.5
    score = sphere.score_outcome(outcome, support.POSITIVE_TIME, sphere.observation_sets())
    assert not score["passed"]
    assert all(not row["radiation_impedance"]["passed"] for row in score["frequencies"])


@pytest.mark.parametrize("capture_failure", [False, True])
def test_decode_failure_cancels_and_drains_then_records_failed_attempt(
    tmp_path, monkeypatch, capture_failure
):
    request, raw = reference_pair()
    raw["quantities"][0]["values"]["content_base64"] = "!"
    seen = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def submit(self, *args, **kwargs):
            pass

        def request_cancel(self):
            seen.append("cancel")

        def events(self):
            yield {"type": "result", "result": raw}
            seen.append("drained")
            yield {"type": "completed", "solved_count": 1}

    monkeypatch.setattr(support, "SolveSession", Session)
    from beat_engine.beat_contract import worker as contract

    monkeypatch.setattr(contract, "validate_solve_request", lambda payload: None)
    if capture_failure:
        monkeypatch.setattr(
            support.tempfile,
            "mkstemp",
            lambda **kw: (_ for _ in ()).throw(OSError("disk unavailable")),
        )
    selected = support.ReferenceSession(julia="unused", record_dir=tmp_path)
    selected.facts = {"julia_executable": "unused", "project": "unused", "solver_script": "unused"}
    selected.manager = SimpleNamespace(get_worker=lambda *a, **kw: SimpleNamespace(worker_info={}))
    with pytest.raises(support.ReferenceSolveError):
        selected.solve(request)
    assert seen == ["cancel", "drained"]
    record = json.loads((tmp_path / "attempt-01.json").read_text())
    assert not record["passed"] and record["failure_type"] == "ResultContractError"
    assert record["terminal_events"] == [{"type": "completed", "solved_count": 1}]
    if capture_failure:
        assert record["private_capture_failure_type"] == "OSError"
    else:
        private = json.loads((tmp_path / record["private_failure_wire"]).read_text())
        assert private["raw_result"] == raw
        assert private["failure_type"] == "ResultContractError"
        assert private["failure_message"]
        if os.name != "nt":
            assert (tmp_path / record["private_failure_wire"]).stat().st_mode & 0o777 == 0o600
    assert record["record_sha256"]


@pytest.mark.parametrize(
    "selection,failed,passed",
    [("all", False, True), ("sphere", False, False), ("all", True, False)],
)
def test_qualification_requires_both_successful_reference_reports(
    tmp_path, monkeypatch, selection, failed, passed
):
    from scripts.beat_conformance.reference_qualification import run_references

    monkeypatch.setattr(sphere, "run_reference", lambda **kw: {"passed": True})

    def coupled(**kw):
        if failed:
            raise ValueError("private error prose")
        return {"passed": True}

    monkeypatch.setattr(lem, "run_reference", coupled)
    summary = run_references(output_dir=tmp_path, selection=selection, julia="unused")
    assert summary["passed"] is passed
    assert summary["coupled_feedback_resistance_qualified"] is False
    assert "private error prose" not in (tmp_path / "analytic-references.json").read_text()


@pytest.mark.parametrize(
    "defect",
    ["missing_source", "wrong_pin", "attested", "dirty", "entry_mismatch", "editable", None],
)
def test_source_reference_selection_requires_exact_independent_proof(tmp_path, monkeypatch, defect):
    source = tmp_path / "source"
    source_script = source / "src/beat_engine/julia_local/coupled_solver.jl"
    source_script.parent.mkdir(parents=True)
    source_script.write_bytes(b"exact source")
    installed = tmp_path / "installed.jl"
    installed.write_bytes(b"exact source")
    if defect == "entry_mismatch":
        installed.write_bytes(b"other source")
    monkeypatch.setenv("WG_BEAT_ENGINE_SRC", "" if defect == "missing_source" else str(source))
    calls = []
    facts = {
        "engine_revision": "0" * 40
        if defect == "wrong_pin"
        else support.expected_engine_revision(),
        "engine_revision_status": "attested" if defect == "attested" else "observed",
        "solver_script": str(installed),
        "artifact_kind": "source" if defect == "editable" else "installed",
        "engine_revision_source": "clean_source_git"
        if defect == "editable"
        else "installed_source_byte_match",
    }

    def verify(julia, backend, *, engine_source):
        calls.append((julia, backend, engine_source))
        if defect == "dirty":
            raise ValueError("Engine source tree has uncommitted changes")
        return dict(facts)

    monkeypatch.setattr(support, "verify_runtime", verify)
    monkeypatch.setattr(
        support, "WorkerManager", lambda **kw: SimpleNamespace(shutdown=lambda: None)
    )
    reference = support.ReferenceSession(julia="selected-julia", record_dir=tmp_path / "records")
    if defect is None:
        with reference:
            assert reference.facts["solver_script"] == str(source_script)
            assert reference.facts["installed_solver_script"] == str(installed)
            assert reference.facts["selected_source_revision"] == support.expected_engine_revision()
            assert reference.facts["selected_source_solver_sha256"]
    else:
        with pytest.raises(ValueError):
            reference.__enter__()
        assert reference.manager is None
    assert (
        not calls
        if defect == "missing_source"
        else calls == [("selected-julia", "cpu", source.resolve())]
    )


def test_source_reference_rejects_compiled_bundle_even_with_clean_pin():
    request, raw = reference_pair()
    raw["diagnostics"]["compiled_worker"]["driver_mode"] = "bundle"
    with pytest.raises(ValueError, match="selected source driver"):
        support.decode_reference_result(raw, request, 0)


@pytest.mark.parametrize("family", ["raw", "sphere", "lem"])
@pytest.mark.parametrize("target_id", ["unrequested-component", False, []])
def test_untargeted_references_reject_returned_target_identity(family, target_id):
    if family == "raw":
        request, raw = reference_pair()
        raw["quantities"][0]["target_id"] = target_id
        with pytest.raises(ValueError, match="target"):
            support.decode_reference_result(raw, request, 0)
    else:
        outcome = (sphere_outcome if family == "sphere" else lem_outcome)(support.POSITIVE_TIME)
        row = outcome.results[0]
        outcome.results[0] = replace(
            row, quantities=(replace(row.quantities[0], target_id=target_id),) + row.quantities[1:]
        )
        with pytest.raises(ValueError, match="target"):
            if family == "sphere":
                sphere.score_outcome(outcome, support.POSITIVE_TIME, sphere.observation_sets())
            else:
                lem.score_outcome(outcome, support.POSITIVE_TIME)
