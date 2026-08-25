"""Public request identity and provenance contract tests."""

from __future__ import annotations

from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError
import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.integration import installed as installed_module
from server.integration import provenance as provenance_module
from server.integration.installed import (
    clear_measurement_cache,
    dependency_drift,
    installed_dependency_shas,
    measure_installed_commit,
    measure_installed_stack,
)
from server.integration.provenance import enrich_result_contract, pinned_dependency_shas
from server.jobs.models import SolveRequest


@pytest.fixture(autouse=True)
def _isolated_measurement_cache() -> Iterator[None]:
    """The installed-stack answer is cached per process; never leak it between tests."""

    clear_measurement_cache()
    yield
    clear_measurement_cache()


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


def test_bundle_provenance_uses_the_shipped_generated_pin_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "shared").mkdir()
    (tmp_path / "server").mkdir()
    (tmp_path / "shared" / "version.json").write_text(
        '{"version":"1.2.3"}\n', encoding="utf-8"
    )
    (tmp_path / "server" / "requirements-pins.txt").write_text(
        "git+https://github.com/m3gnus/hornlab-metal-bem.git@"
        + "a" * 40
        + "#egg=hornlab-metal-bem\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(provenance_module, "_REPOSITORY_ROOT", tmp_path)
    provenance_module._release_identity.cache_clear()
    try:
        version, pins = provenance_module._release_identity()
    finally:
        provenance_module._release_identity.cache_clear()

    assert version == "1.2.3"
    assert pins == {"hornlab-metal-bem": "a" * 40}


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


# ---------------------------------------------------------------------------
# Declared pins vs. the stack that actually ran.
#
# ``pins.json`` is a declaration. Stamping it on a result and calling it
# provenance is how a Windows box produced weeks of measured evidence against
# four stale modules, none of which moved its version string off ``0.1.0``
# (docs/validation/2026-08/PINNED-VS-INSTALLED.md).
# ---------------------------------------------------------------------------


_PINS = {"hornlab-metal-bem": "a" * 40, "hornlab-waveguide-mesher": "b" * 40}


class _FakeDistribution:
    def __init__(self, text: str | None) -> None:
        self._text = text

    def read_text(self, filename: str) -> str | None:
        assert filename == "direct_url.json"
        return self._text


def _fake_environment(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[str, str | None]
) -> None:
    """Install a ``direct_url.json`` reader over the named distributions.

    A name absent from ``recorded`` behaves like a distribution that is not
    installed at all.
    """

    def distribution(name: str) -> _FakeDistribution:
        if name not in recorded:
            raise PackageNotFoundError(name)
        return _FakeDistribution(recorded[name])

    monkeypatch.setattr(installed_module, "distribution", distribution)


def _git_direct_url(commit: str) -> str:
    return json.dumps(
        {
            "url": "https://github.com/m3gnus/hornlab-metal-bem.git",
            "vcs_info": {
                "vcs": "git",
                "commit_id": commit,
                "requested_revision": commit,
            },
        }
    )


def _pin_the_release(
    monkeypatch: pytest.MonkeyPatch, pins: dict[str, str] = _PINS
) -> None:
    monkeypatch.setattr(
        provenance_module, "_release_identity", lambda: ("9.9.9", dict(pins))
    )


def test_installed_commit_comes_from_the_pip_recorded_direct_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_environment(monkeypatch, {"hornlab-metal-bem": _git_direct_url("c" * 40)})

    assert measure_installed_commit("hornlab-metal-bem") == "c" * 40


@pytest.mark.parametrize(
    "recorded",
    [
        pytest.param({}, id="not installed at all"),
        pytest.param({"hornlab-metal-bem": None}, id="no direct_url.json"),
        pytest.param({"hornlab-metal-bem": "{not json"}, id="unreadable json"),
        pytest.param({"hornlab-metal-bem": "[]"}, id="not a json object"),
        pytest.param(
            {"hornlab-metal-bem": json.dumps({"dir_info": {"editable": True}})},
            id="editable path install, not a vcs install",
        ),
        pytest.param(
            {"hornlab-metal-bem": json.dumps({"vcs_info": {"vcs": "hg"}})},
            id="a vcs that is not git",
        ),
        pytest.param(
            {
                "hornlab-metal-bem": json.dumps(
                    {"vcs_info": {"vcs": "git", "commit_id": "v1.2.3"}}
                )
            },
            id="a tag rather than a resolved commit",
        ),
    ],
)
def test_an_unmeasurable_distribution_is_unknown_rather_than_an_error(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[str, str | None]
) -> None:
    _fake_environment(monkeypatch, recorded)

    assert measure_installed_commit("hornlab-metal-bem") is None


def test_a_matching_environment_reports_an_empty_drift_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_environment(
        monkeypatch,
        {name: _git_direct_url(sha) for name, sha in _PINS.items()},
    )

    installed, drift = measure_installed_stack(_PINS)

    assert installed == _PINS
    assert drift == []


def test_a_module_at_another_commit_is_named_in_the_drift_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_environment(
        monkeypatch,
        {
            "hornlab-metal-bem": _git_direct_url("a" * 40),
            "hornlab-waveguide-mesher": _git_direct_url("f" * 40),
        },
    )

    installed, drift = measure_installed_stack(_PINS)

    assert installed["hornlab-waveguide-mesher"] == "f" * 40
    assert drift == ["hornlab-waveguide-mesher"]


def test_an_unmeasurable_module_is_null_and_counts_as_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_environment(monkeypatch, {"hornlab-metal-bem": _git_direct_url("a" * 40)})

    installed, drift = measure_installed_stack(_PINS)

    assert installed == {
        "hornlab-metal-bem": "a" * 40,
        "hornlab-waveguide-mesher": None,
    }
    assert drift == ["hornlab-waveguide-mesher"]


def test_unknown_is_never_reported_as_agreement() -> None:
    # The whole point: a measurement that came back empty must not read as
    # "matches the pin", whatever the pin happens to be.
    assert dependency_drift({"m": "a" * 40}, {"m": None}) == ["m"]
    assert dependency_drift({"m": "a" * 40}, {}) == ["m"]


def test_the_measurement_is_cached_per_process_and_clearable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def counting(name: str) -> str | None:
        calls.append(name)
        return "a" * 40

    monkeypatch.setattr(installed_module, "measure_installed_commit", counting)

    assert installed_dependency_shas(["hornlab-metal-bem"]) == {
        "hornlab-metal-bem": "a" * 40
    }
    assert installed_dependency_shas(["hornlab-metal-bem"]) == {
        "hornlab-metal-bem": "a" * 40
    }
    assert calls == ["hornlab-metal-bem"]

    clear_measurement_cache()
    assert installed_dependency_shas(["hornlab-metal-bem"])
    assert calls == ["hornlab-metal-bem", "hornlab-metal-bem"]


def test_a_clean_solve_records_both_stacks_and_verifies_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_the_release(monkeypatch)
    _fake_environment(
        monkeypatch,
        {name: _git_direct_url(sha) for name, sha in _PINS.items()},
    )

    provenance = enrich_result_contract({"metadata": {}}, _request())["provenance"]

    assert provenance["dependency_shas"] == _PINS
    assert provenance["installed_dependency_shas"] == _PINS
    assert provenance["dependency_drift"] == []


def test_a_drifted_solve_says_so_instead_of_echoing_the_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_the_release(monkeypatch)
    _fake_environment(
        monkeypatch,
        {
            "hornlab-metal-bem": _git_direct_url("a" * 40),
            "hornlab-waveguide-mesher": _git_direct_url("9" * 40),
        },
    )

    provenance = enrich_result_contract({"metadata": {}}, _request())["provenance"]

    assert provenance["dependency_shas"] == _PINS
    assert provenance["installed_dependency_shas"]["hornlab-waveguide-mesher"] == "9" * 40
    assert provenance["dependency_drift"] == ["hornlab-waveguide-mesher"]


def test_a_measurement_failure_cannot_break_a_solve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_the_release(monkeypatch)

    def explode(name: str) -> str | None:
        raise RuntimeError("the metadata directory is on fire")

    monkeypatch.setattr(installed_module, "distribution", explode)

    provenance = enrich_result_contract({"metadata": {}}, _request())["provenance"]

    assert provenance["installed_dependency_shas"] == dict.fromkeys(_PINS)
    assert provenance["dependency_drift"] == sorted(_PINS)


def test_even_a_broken_measurement_entry_point_degrades_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(names: object) -> dict[str, str | None]:
        raise MemoryError

    monkeypatch.setattr(installed_module, "installed_dependency_shas", explode)

    assert measure_installed_stack(_PINS) == (dict.fromkeys(_PINS), sorted(_PINS))


def test_this_host_is_measurable_against_its_own_pins() -> None:
    """Run the measurement against the real environment, not a fake one.

    Host-independent on purpose: which modules drift here is a property of the
    machine, but every pinned name must be answered with either a resolved
    40-character commit or an explicit ``None``.
    """

    pinned = pinned_dependency_shas()
    assert pinned, "pins.json must name the modules this release declares"

    installed, drift = measure_installed_stack(pinned)

    assert set(installed) == set(pinned)
    assert all(
        sha is None or (len(sha) == 40 and set(sha) <= set("0123456789abcdef"))
        for sha in installed.values()
    )
    assert drift == sorted(
        name for name in pinned if installed[name] != pinned[name]
    )
