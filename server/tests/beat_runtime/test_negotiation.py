"""Single WG validation with the installed engine's real handshake checks."""

import copy
import importlib

import pytest

from server.solver.beat_runtime import negotiation, probe
from server.solver.beat_runtime.manager import ManagedWorker
from server.solver.beat_runtime.session import SolveSession
from server.tests.beat_runtime.test_probe import WORKER_INFO
from server.tests.beat_runtime.test_session import Worker


def test_validated_session_checks_capabilities_without_validating_twice(monkeypatch, tmp_path):
    contract = importlib.import_module("beat_engine.beat_contract.worker")
    original = contract.validate_solve_request
    calls = []

    def validate(request):
        calls.append(request)
        original(request)

    monkeypatch.setattr(contract, "validate_solve_request", validate)
    worker = Worker()
    worker.worker_info = copy.deepcopy(WORKER_INFO)
    worker.worker_info["contracts"]["field_array"] = [1]
    worker.worker_info["cancellation"] = "marker_file"
    with SolveSession() as session:
        request = dict(probe.build_request(tmp_path / "mesh.msh"), cancel_path=str(session.cancel_path.resolve()))
        negotiate = negotiation.validated_negotiator(contract, request)
        session.submit(ManagedWorker(worker, "child"), request, negotiate=negotiate)
        assert len(calls) == 1
        assert list(session.events())[-1]["type"] == "completed"
    incompatible = copy.deepcopy(worker.worker_info)
    incompatible["contracts"]["system_result"] = [99]
    with pytest.raises(contract.WorkerCompatibilityError, match="system_result"):
        negotiate(incompatible, request, "solve")
    assert len(calls) == 1
    # A different request, even byte-identical, is validated normally.
    negotiate(worker.worker_info, copy.deepcopy(request), "solve")
    assert len(calls) == 2
    assert contract.negotiate_submission.__globals__["validate_solve_request"] is validate


def test_unvalidated_session_uses_regular_negotiation(monkeypatch, tmp_path):
    contract = importlib.import_module("beat_engine.beat_contract.worker")
    request = probe.build_request(tmp_path / "mesh.msh")
    request["frequencies_hz"] = [float("inf")]
    with SolveSession() as session, pytest.raises(ValueError):
        session.submit(ManagedWorker(Worker(), "child"), request, negotiate=contract.negotiate_submission)


def test_completed_session_needs_no_monitor_or_redundant_retirement(monkeypatch):
    from server.solver.beat_runtime import manager

    with SolveSession() as session:
        session.submit(ManagedWorker(Worker(), "child"), {})
        assert session._monitor is None
        # Ownership itself closes the terminal stream synchronously. This
        # bounded helper must never create another retirement thread afterward.
        monkeypatch.setattr(manager, "bounded_call", lambda action: pytest.fail("redundant retirement"))
        assert list(session.events())[-1]["type"] == "completed"


# A stand-in contract whose defaults exercise FunctionType reconstruction.
def validate_solve_request(request):
    pass


def defaulted_negotiate(info, request, operation="solve", *, strict=True):
    validate_solve_request(request)
    return operation, strict


def test_negotiator_preserves_positional_and_keyword_defaults():
    from types import SimpleNamespace

    calls = []
    contract = SimpleNamespace(validate_solve_request=lambda request: calls.append(request),
                               negotiate_submission=defaulted_negotiate)
    request = {}
    rebuilt = negotiation.validated_negotiator(contract, request)
    assert rebuilt.__defaults__ == defaulted_negotiate.__defaults__
    assert rebuilt.__kwdefaults__ == defaulted_negotiate.__kwdefaults__
    assert rebuilt(None, request) == ("solve", True)
    assert rebuilt(None, request, strict=False) == ("solve", False)
    assert calls == [request]
    rebuilt.__kwdefaults__["strict"] = False
    assert defaulted_negotiate.__kwdefaults__["strict"] is True
