"""The host report must show declared-vs-installed, and shout when they differ."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from scripts import check_backends
from server.integration import installed as installed_module
from server.integration import provenance as provenance_module


_PINNED = {"hornlab-metal-bem": "a" * 40, "hornlab-waveguide-mesher": "b" * 40}


def _environment(
    monkeypatch: pytest.MonkeyPatch,
    pinned: dict[str, str],
    installed: dict[str, str | None],
) -> None:
    installed_module.clear_measurement_cache()
    monkeypatch.setattr(
        provenance_module, "_release_identity", lambda: ("9.9.9", dict(pinned))
    )
    monkeypatch.setattr(
        installed_module,
        "measure_installed_commit",
        lambda name: installed.get(name),
    )


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    installed_module.clear_measurement_cache()
    yield
    installed_module.clear_measurement_cache()


def test_a_matching_environment_lists_every_module_without_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _environment(monkeypatch, _PINNED, dict(_PINNED))

    assert check_backends.report_dependency_drift() == []

    printed = capsys.readouterr().out
    assert "Pinned dependencies:" in printed
    for name in _PINNED:
        assert name in printed
    assert "matches" in printed
    assert "WARNING" not in printed
    assert "DRIFTED" not in printed


def test_a_drifted_module_is_named_and_visibly_abnormal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _environment(
        monkeypatch,
        _PINNED,
        {"hornlab-metal-bem": "a" * 40, "hornlab-waveguide-mesher": "9" * 40},
    )

    assert check_backends.report_dependency_drift() == ["hornlab-waveguide-mesher"]

    printed = capsys.readouterr().out
    assert "DRIFTED" in printed
    assert "WARNING" in printed
    assert "hornlab-waveguide-mesher" in printed
    assert "requirements-pins.txt" in printed


def test_an_unmeasurable_module_is_reported_rather_than_assumed_correct(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _environment(monkeypatch, _PINNED, {"hornlab-metal-bem": "a" * 40})

    assert check_backends.report_dependency_drift() == ["hornlab-waveguide-mesher"]

    printed = capsys.readouterr().out
    assert "NOT MEASURED" in printed
    assert "WARNING" in printed


def test_the_report_stays_ascii_so_any_windows_code_page_renders_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _environment(
        monkeypatch,
        _PINNED,
        {"hornlab-metal-bem": "a" * 40, "hornlab-waveguide-mesher": None},
    )

    check_backends.report_dependency_drift()

    capsys.readouterr().out.encode("ascii")


def test_drift_does_not_change_the_can_this_host_solve_exit_status() -> None:
    # ``main`` exits non-zero for exactly one claim: no backend can run here.
    # Drift is a different, weaker statement and must not be smuggled into it.
    source = (check_backends.REPO_ROOT / "scripts" / "check_backends.py").read_text(
        encoding="utf-8"
    )
    body = source.split("def main(")[1]
    assert "report_dependency_drift()" in body
    assert "return report_dependency_drift" not in body
