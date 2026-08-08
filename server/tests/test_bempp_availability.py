"""bempp availability must mean "a solve can run", not "the wrapper imported".

v1 shipped this bug and then wrote the diagnosis down in
``server/scripts/check_solver_engine.py``: ``hornlab_bempp_bem`` is a thin
pure-Python wrapper that imports fine on a clean Windows box where bempp-cl
cannot assemble at all, because numba's compiled extensions need a Visual C++
redistributable Windows does not install by default. The installer reported
"Bempp ready", the preflight reported READY, and every solve died on
``ImportError: Numba could not be imported``.

v2 is about to be validated on Windows for the first time, so these tests pin
the behaviour that keeps it from repeating.
"""

from __future__ import annotations

import importlib

import pytest

from server.solver import bempp


def _refusing_numba():
    """Import everything normally except numba, which fails as it does on Windows."""

    real_import = importlib.import_module

    def refuse(name, *args, **kwargs):
        if name == "numba":
            raise ImportError("Numba could not be imported")
        return real_import(name, *args, **kwargs)

    return refuse


def test_available_requires_a_working_assembly_backend(monkeypatch):
    """A wrapper that imports is not enough to call the engine available."""

    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    monkeypatch.setattr(bempp.importlib, "import_module", _refusing_numba())

    status = bempp.bempp_status()

    assert status["available"] is False
    assert status["assembly_backend"] is None
    assert "numba" in status["reason"]


def test_windows_missing_runtime_names_the_dlls_and_the_fix(monkeypatch):
    """The remedy has to be in the message; the failure is otherwise a mystery."""

    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    monkeypatch.setattr(bempp.importlib, "import_module", _refusing_numba())
    monkeypatch.setattr(
        bempp, "_missing_windows_runtime_dlls", lambda: ["vcruntime140.dll", "msvcp140.dll"]
    )

    reason = bempp.bempp_status()["reason"]

    assert "vcruntime140.dll" in reason and "msvcp140.dll" in reason
    assert "VCRedist" in reason or "vc_redist" in reason


def test_dll_probe_is_windows_only(monkeypatch):
    """ctypes.CDLL on those names would fail everywhere else and mislead."""

    monkeypatch.setattr(bempp.platform, "system", lambda: "Darwin")
    assert bempp._missing_windows_runtime_dlls() == []
    monkeypatch.setattr(bempp.platform, "system", lambda: "Linux")
    assert bempp._missing_windows_runtime_dlls() == []


def test_absent_package_is_still_a_plain_unavailable(monkeypatch):
    monkeypatch.setattr(bempp, "_load_api", lambda: False)

    status = bempp.bempp_status()

    assert status["available"] is False
    assert "not importable" in status["reason"]


def test_solving_refuses_when_the_backend_cannot_run(monkeypatch):
    """The guard must sit in front of the solve, not only in the report."""

    monkeypatch.setattr(bempp, "_load_api", lambda: True)
    monkeypatch.setattr(bempp, "SolveConfig", object())
    monkeypatch.setattr(bempp, "bempp_solve", lambda *a, **k: None)
    monkeypatch.setattr(
        bempp,
        "bempp_status",
        lambda: {"available": False, "reason": "numba cannot load", "version": None,
                 "assembly_backend": None},
    )

    class _Context:
        solver_mode = "full_3d"
        frequencies_hz = None

        def validate(self):
            return None

    monkeypatch.setattr(bempp, "reject_bempp_infinite_baffle", lambda _context: None)

    with pytest.raises(bempp.BemppUnavailable, match="numba cannot load"):
        bempp.solve_bempp_from_msh_text("mesh", _Context())
