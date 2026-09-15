"""The installed-candidate CPU gate must fail on the things it exists to catch.

The RC builder already asks a candidate whether its backends report ready and
whether the server answers HTTP, and a Windows or Linux installer could pass
both while BEAT's CPU backend had never executed a frequency. These tests hold
the new gate to the shape of that problem: every assertion it makes is exercised
with an input that violates it, because a gate whose checks cannot fail is a
green light with no evidence behind it.

What is *not* tested here is a real installed payload — that needs a built
candidate on the platform it was built for, which is a runner's job and is
recorded as owed rather than simulated.
"""

from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from urllib.parse import urlsplit

import pytest

from scripts import qualify_installed_cpu as gate


PINS = {
    "hornlab-beat-bem": "74da18cdbb12844729a154005a26110511864aa0",
    "hornlab-bempp-bem": "57b1260777c09097c879a3fe7ecb377c32f322cb",
}


def _app_layer(tmp_path: Path, **manifest: object) -> Path:
    app = tmp_path / "payload" / "app"
    app.mkdir(parents=True)
    payload = {
        "schemaVersion": 1,
        "version": "0.3.1",
        "commit": "a" * 40,
        "runtimeId": "0123456789ab",
        "treeSha256": "b" * 64,
    }
    payload.update(manifest)
    (app / "APP-MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    return app


def _payload(tmp_path: Path, *, windows: bool = False) -> Path:
    app = _app_layer(tmp_path)
    runtime = app.parent / "runtime"
    if windows:
        runtime.mkdir()
        (runtime / "python.exe").write_text("stub", encoding="utf-8")
    else:
        (runtime / "bin").mkdir(parents=True)
        (runtime / "bin" / "python3.13").write_text("stub", encoding="utf-8")
    return app.parent


# ---------------------------------------------------------------------------
# Payload layout
# ---------------------------------------------------------------------------


def test_a_checkout_is_refused_as_a_payload(tmp_path: Path) -> None:
    """The gate qualifies a built candidate, and says so when handed anything else."""

    (tmp_path / "server").mkdir()
    with pytest.raises(gate.QualificationError, match="no app layer"):
        gate.resolve_payload(tmp_path)


def test_a_payload_with_no_runtime_layer_is_refused(tmp_path: Path) -> None:
    """A payload whose interpreter is missing cannot be the one under test."""

    _app_layer(tmp_path)
    with pytest.raises(gate.QualificationError, match="no packaged interpreter"):
        gate.resolve_payload(tmp_path / "payload")


@pytest.mark.parametrize("windows", (False, True), ids=("posix", "windows"))
def test_both_runtime_layouts_resolve(tmp_path: Path, windows: bool) -> None:
    payload = _payload(tmp_path, windows=windows)
    resources, app, interpreter = gate.resolve_payload(payload)

    assert resources == payload
    assert app == payload / "app"
    assert interpreter.name == ("python.exe" if windows else "python3.13")


def test_a_macos_bundle_resolves_through_contents_resources(tmp_path: Path) -> None:
    bundle = tmp_path / "Waveguide Generator.app"
    resources = bundle / "Contents" / "Resources"
    (resources / "app").mkdir(parents=True)
    (resources / "app" / "APP-MANIFEST.json").write_text("{}", encoding="utf-8")
    (resources / "runtime" / "bin").mkdir(parents=True)
    (resources / "runtime" / "bin" / "python3.13").write_text("stub", encoding="utf-8")

    found, app, _interpreter = gate.resolve_payload(bundle)

    assert found == resources
    assert app == resources / "app"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_the_manifest_is_held_to_what_the_build_supplied(tmp_path: Path) -> None:
    app = _app_layer(tmp_path)

    report = gate.check_manifest(
        app, {"version": "0.3.1", "commit": "a" * 40, "treeSha256": "b" * 64}
    )
    assert report["version"] == "checked"
    assert report["treeSha256"] == "checked"

    with pytest.raises(gate.QualificationError, match="APP-MANIFEST version"):
        gate.check_manifest(app, {"version": "9.9.9"})
    with pytest.raises(gate.QualificationError, match="APP-MANIFEST treeSha256"):
        gate.check_manifest(app, {"treeSha256": "c" * 64})


def test_a_field_nobody_supplied_is_reported_as_unchecked(tmp_path: Path) -> None:
    """A run that asserted nothing must not read as a run that asserted and passed."""

    app = _app_layer(tmp_path)

    report = gate.check_manifest(app, {"version": None, "commit": None, "treeSha256": None})

    assert report["version"] == "not supplied"
    assert report["commit"] == "not supplied"
    assert report["treeSha256"] == "not supplied"


def test_a_missing_manifest_names_the_mistake(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    with pytest.raises(gate.QualificationError, match="not a checkout"):
        gate.check_manifest(app, {})


def _pin_reader(monkeypatch: pytest.MonkeyPatch, answer: dict[str, object]) -> None:
    class Completed:
        returncode = 0
        stdout = json.dumps(answer)
        stderr = ""

    monkeypatch.setattr(gate.subprocess, "run", lambda *_a, **_k: Completed())


def test_every_expected_pin_is_read_from_the_packaged_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_reader(monkeypatch, {name: {"commit": sha} for name, sha in PINS.items()})

    report = gate.check_pins(Path("python"), PINS, {})

    assert report["checked"] == sorted(PINS)


@pytest.mark.parametrize(
    ("answer", "message"),
    (
        pytest.param({"commit": "0" * 40}, "expected", id="wrong-commit"),
        pytest.param({"error": "not installed: nope"}, "not installed", id="absent"),
        pytest.param(
            {"commit": PINS["hornlab-beat-bem"], "editable": True},
            "editable",
            id="editable",
        ),
    ),
)
def test_a_drifted_or_editable_module_fails_the_gate(
    monkeypatch: pytest.MonkeyPatch, answer: dict[str, object], message: str
) -> None:
    """An editable install resolves to a working tree, which is not a payload."""

    _pin_reader(monkeypatch, {"hornlab-beat-bem": answer})

    with pytest.raises(gate.QualificationError, match=message):
        gate.check_pins(Path("python"), {"hornlab-beat-bem": PINS["hornlab-beat-bem"]}, {})


def test_a_gate_with_no_expected_pins_refuses_to_run() -> None:
    """Asserting provenance is the point; running without any is not a pass."""

    with pytest.raises(gate.QualificationError, match="assert provenance"):
        gate.check_pins(Path("python"), {}, {})


# ---------------------------------------------------------------------------
# The solve contract
# ---------------------------------------------------------------------------


#: Every plane a solve that names none is asked for.
PLANES = ("horizontal", "vertical", "diagonal")


def _plane(*per_frequency: tuple[float, float, float]) -> list[list[list[float]]]:
    """A directivity plane as the result builder writes it.

    One row per frequency, each row ``[angle, value]`` pairs. The values are
    relative to on-axis -- 0 dB at 0 degrees, less off it -- and the angles
    are an axis, never evidence that anything was solved.
    """

    return [
        [[-90.0, low], [0.0, centre], [90.0, high]] for low, centre, high in per_frequency
    ]


DIRECTIVITY = {plane: _plane((-6.0, 0.0, -6.0), (-9.0, 0.0, -9.0)) for plane in PLANES}


def _result(**overrides: object) -> dict[str, object]:
    result = {
        "frequencies": [500.0, 1000.0],
        "spl_on_axis": {
            "frequencies": [500.0, 1000.0],
            "spl": [92.5, 94.1],
            "phase_degrees": [10.0, -20.0],
        },
        "directivity": json.loads(json.dumps(DIRECTIVITY)),
        "metadata": dict(gate.CPU_RESULT_CONTRACT),
        "provenance": {"dependency_drift": [], "dependency_shas": dict(PINS)},
    }
    result.update(overrides)
    return result


def test_a_real_cpu_solve_passes_every_check() -> None:
    report = gate.check_solve(_result(), PINS)

    assert report["beat_backend"] == "cpu"
    assert report["axes"]["frequencies"] == 2
    assert report["axes"]["finite_non_zero"] > 0
    assert report["pins_cross_checked"] == sorted(PINS)


def test_a_solve_that_ran_on_another_backend_fails() -> None:
    """The gate is about the CPU path, so the backend that ran has to be it.

    Selecting `beat-cpu` by name keeps AUTO's preference order out of it, but
    the server still substitutes a named engine it finds unavailable; this is
    what catches such a swap.
    """

    metal = dict(gate.CPU_RESULT_CONTRACT, beat_backend="metal")
    with pytest.raises(gate.QualificationError, match="expected"):
        gate.check_solve(_result(metadata=metal), PINS)


def test_dependency_drift_reported_by_the_application_fails() -> None:
    drifted = {"dependency_drift": ["hornlab-beat-bem"], "dependency_shas": dict(PINS)}
    with pytest.raises(gate.QualificationError, match="dependency drift"):
        gate.check_solve(_result(provenance=drifted), PINS)


def test_a_solve_against_other_module_commits_fails() -> None:
    """The pins are verified where the solve saw them, not only where pip did."""

    other = dict(PINS, **{"hornlab-beat-bem": "f" * 40})
    provenance = {"dependency_drift": [], "dependency_shas": other}
    with pytest.raises(gate.QualificationError, match="other module commits"):
        gate.check_solve(_result(provenance=provenance), PINS)


@pytest.mark.parametrize(
    ("result", "message"),
    (
        pytest.param({"frequencies": []}, "no frequency axis", id="no-axis"),
        pytest.param(
            {"spl_on_axis": {"frequencies": [500.0, 1000.0], "spl": [float("nan"), 1.0],
                             "phase_degrees": [0.0, 1.0]}},
            "non-finite",
            id="nan",
        ),
        pytest.param(
            {"spl_on_axis": {"frequencies": [500.0, 1000.0], "spl": [float("inf"), 1.0],
                             "phase_degrees": [0.0, 1.0]}},
            "non-finite",
            id="infinity",
        ),
        pytest.param(
            {"frequencies": [500.0, 1000.0, 2000.0]},
            "not aligned",
            id="misaligned-on-axis",
        ),
        pytest.param(
            {"directivity": dict(DIRECTIVITY, horizontal=DIRECTIVITY["horizontal"][:1])},
            "directivity plane",
            id="misaligned-directivity",
        ),
    ),
)
def test_a_result_that_answered_without_solving_fails(
    result: dict[str, object], message: str
) -> None:
    """Answering is not solving.

    A NaN or an infinity is a solve that produced nothing usable while still
    returning, and a row count that does not match the frequency axis is a
    polar plot that is mislabelled -- neither of which a count of finite
    numbers would notice on its own.
    """

    with pytest.raises(gate.QualificationError, match=message):
        gate.check_solve(_result(**result), PINS)


def test_an_all_zero_result_is_not_a_solve() -> None:
    """Real frequencies do not make zeroed data a solve.

    The frequency axis and the directivity angles are axes: non-zero in any
    result that has them, so neither can be what shows a solver produced
    something. This test used to zero the frequencies as well, and so passed
    against a check that counted them.
    """

    zeroed = _result(
        spl_on_axis={
            "frequencies": [500.0, 1000.0], "spl": [0.0, 0.0], "phase_degrees": [0.0, 0.0],
        },
        directivity={plane: _plane((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)) for plane in PLANES},
    )
    with pytest.raises(gate.QualificationError, match="nothing was solved"):
        gate.check_solve(zeroed, PINS)


_ZERO_PLANE = _plane((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        pytest.param(
            {"spl_on_axis": {"frequencies": [500.0, 1000.0], "spl": [0.0, 0.0],
                             "phase_degrees": [10.0, -20.0]}},
            "spl_on_axis.*nothing was solved",
            id="zero-spl",
        ),
        pytest.param(
            {"spl_on_axis": {"frequencies": [500.0, 1000.0], "spl": [None, None],
                             "phase_degrees": [None, None]}},
            "spl_on_axis.*nothing was solved",
            id="every-spl-value-absent",
        ),
        pytest.param(
            {"spl_on_axis": {"frequencies": [500.0, 1000.0]}}, "spl_on_axis", id="no-spl-values",
        ),
        pytest.param({"spl_on_axis": None}, "spl_on_axis", id="no-spl-on-axis"),
        pytest.param(
            {"directivity": dict(DIRECTIVITY, vertical=_ZERO_PLANE)},
            "'vertical'.*nothing was solved",
            id="zero-plane-with-real-angles",
        ),
        pytest.param(
            {"directivity": {"horizontal": DIRECTIVITY["horizontal"],
                             "vertical": DIRECTIVITY["vertical"]}},
            "diagonal",
            id="requested-plane-missing",
        ),
        pytest.param({"directivity": {}}, "horizontal", id="no-planes"),
        pytest.param({"directivity": None}, "directivity", id="no-directivity"),
        pytest.param(
            {"directivity": dict(DIRECTIVITY, diagonal=_plane((float("nan"), 0.0, -6.0),
                                                              (-9.0, 0.0, -9.0)))},
            "non-finite",
            id="nan-directivity-value",
        ),
        pytest.param(
            {"directivity": dict(DIRECTIVITY, diagonal=[[-6.0, 0.0, -6.0], [-9.0, 0.0, -9.0]])},
            "pair",
            id="rows-without-angles",
        ),
    ),
)
def test_real_frequencies_do_not_make_a_missing_or_zeroed_field_a_solve(
    overrides: dict[str, object], message: str
) -> None:
    """On-axis SPL and every requested plane must be there, finite, and not all zero."""

    with pytest.raises(gate.QualificationError, match=message):
        gate.check_solve(_result(**overrides), PINS)


def test_a_documented_absence_is_not_treated_as_a_bad_number() -> None:
    """The result contract keeps a missing value missing rather than interpolating."""

    with_nulls = _result(
        spl_on_axis={
            "frequencies": [500.0, 1000.0],
            "spl": [92.5, 94.1],
            "phase_degrees": [None, None],
        }
    )
    assert gate.check_solve(with_nulls, PINS)["axes"]["frequencies"] == 2


# ---------------------------------------------------------------------------
# GPU independence
# ---------------------------------------------------------------------------


def test_the_gpu_claim_matches_what_the_host_could_support() -> None:
    """The claim is only ever as strong as the host allows, and says which it is."""

    bare = {"engines": [{"name": "beat-metal", "available": False, "reason": "no GPU"}]}
    on_bare = gate.gpu_independence(bare, {"beat_backend": "cpu"})
    assert on_bare["any_accelerator_available"] is False
    assert "without one" in on_bare["claim"]

    accelerated = {"engines": [{"name": "metal", "available": True, "reason": "ready"}]}
    on_gpu = gate.gpu_independence(accelerated, {"beat_backend": "cpu"})
    assert on_gpu["any_accelerator_available"] is True
    assert "not silently upgraded" in on_gpu["claim"]
    assert on_gpu["engine_selected_by_name"] == "beat-cpu"


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def test_pins_are_supplied_as_name_equals_commit() -> None:
    assert gate.parse_pins(["a=1", "b=2"]) == {"a": "1", "b": "2"}
    with pytest.raises(SystemExit):
        gate.parse_pins(["hornlab-beat-bem"])
    with pytest.raises(SystemExit):
        gate.parse_pins(["=abc"])


def test_a_failure_writes_a_report_and_exits_non_zero(tmp_path: Path) -> None:
    """A failed gate has to leave evidence, not only a red step."""

    output = tmp_path / "out"
    code = gate.main(
        [
            "--payload",
            str(tmp_path / "nothing-here"),
            "--work",
            str(tmp_path / "work"),
            "--output",
            str(output),
            "--expected-pin",
            "hornlab-beat-bem=deadbeef",
        ]
    )

    assert code == 1
    written = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    assert written["qualified"] is False
    assert "--payload" in written["error"]


def test_the_isolated_environment_leaves_the_users_directories_alone(tmp_path: Path) -> None:
    """Nothing this gate runs may write into the machine it ran on.

    The cache redirections are the launchers' own, and on macOS they are not
    tidiness: the bundle is ad-hoc signed, so a cache written beside its sources
    breaks the seal and the next launch fails as damaged.
    """

    app = _app_layer(tmp_path)
    work = tmp_path / "work"

    environment = gate.isolated_environment(app, work)

    assert environment["WG2_BUNDLE"] == "1"
    assert environment["WG2_APP_ROOT"] == str(app)
    for name in ("PYTHONPYCACHEPREFIX", "NUMBA_CACHE_DIR", "MPLCONFIGDIR",
                 "HORNLAB_BEAT_RUNTIME_DIR", "HORNLAB_BEAT_WORKER_DIR"):
        assert environment[name].startswith(str(work)), name
    assert "PYTHONPATH" not in environment
    # The name is not a detail. `worker_registry.py` reads WORKER_DIR_ENV_VAR
    # and nothing else, so an invented one is ignored in silence: the workers
    # go to the user's default registry and the directory reported as isolated
    # is one nothing ever wrote to.
    assert "HORNLAB_BEAT_WORKER_REGISTRY" not in environment


# ---------------------------------------------------------------------------
# Expectations taken from the build's own files
# ---------------------------------------------------------------------------


def test_every_pinned_module_becomes_an_expected_commit(tmp_path: Path) -> None:
    """Reading the build's own pins.json is what makes the check unfudgeable.

    A hand-typed list can be edited to match whatever was found; the file the
    build also consumed cannot.
    """

    path = tmp_path / "pins.json"
    path.write_text(
        json.dumps({"modules": {name: {"sha": sha} for name, sha in PINS.items()}}),
        encoding="utf-8",
    )

    assert gate.pins_from_file(path) == PINS
    assert gate.pins_from_file(None) == {}


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        pytest.param({"modules": {}}, "declares no modules", id="empty"),
        pytest.param({}, "declares no modules", id="absent"),
        pytest.param({"modules": {"a": {}}}, "no sha", id="no-sha"),
        pytest.param({"modules": {"a": "abc"}}, "no sha", id="not-an-object"),
    ),
)
def test_an_unusable_pins_file_fails_rather_than_checking_nothing(
    tmp_path: Path, payload: dict[str, object], message: str
) -> None:
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(gate.QualificationError, match=message):
        gate.pins_from_file(path)


def test_the_build_manifest_supplies_the_identity_to_hold_the_payload_to(
    tmp_path: Path,
) -> None:
    """Installing a stale artifact, or another platform's, is caught before startup."""

    built = tmp_path / "update-app-0.3.1.manifest.json"
    built.write_text(
        json.dumps({"version": "0.3.1", "commit": "a" * 40, "treeSha256": "b" * 64}),
        encoding="utf-8",
    )

    assert gate.expectations_from_build_manifest(built) == {
        "version": "0.3.1",
        "commit": "a" * 40,
        "treeSha256": "b" * 64,
    }
    assert gate.expectations_from_build_manifest(None) == {
        "version": None,
        "commit": None,
        "treeSha256": None,
    }


def test_a_build_manifest_missing_a_field_fails_rather_than_skipping_it(
    tmp_path: Path,
) -> None:
    built = tmp_path / "update-app.manifest.json"
    built.write_text(json.dumps({"version": "0.3.1", "commit": "a" * 40}), encoding="utf-8")

    with pytest.raises(gate.QualificationError, match="records no treeSha256"):
        gate.expectations_from_build_manifest(built)


def test_a_payload_from_a_different_build_is_refused(tmp_path: Path) -> None:
    """The end-to-end shape of the identity check, through the real entry point."""

    app = _app_layer(tmp_path, treeSha256="c" * 64)
    built = tmp_path / "built.manifest.json"
    built.write_text(
        json.dumps({"version": "0.3.1", "commit": "a" * 40, "treeSha256": "b" * 64}),
        encoding="utf-8",
    )

    with pytest.raises(gate.QualificationError, match="APP-MANIFEST treeSha256"):
        gate.check_manifest(app, gate.expectations_from_build_manifest(built))


# ---------------------------------------------------------------------------
# The RC workflow step
# ---------------------------------------------------------------------------


RC_WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "rc-build.yml"
RC_WORKFLOW = RC_WORKFLOW_PATH.read_text(encoding="utf-8")
PLATFORM_JOBS = ("macos-bundle", "windows-bundle", "linux-bundle")


def _workflow() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(RC_WORKFLOW)


def _steps(job: str) -> list[dict]:
    return _workflow()["jobs"][job]["steps"]


@pytest.mark.parametrize("job", PLATFORM_JOBS)
def test_every_platform_candidate_is_qualified_for_cpu(job: str) -> None:
    """The gate the user asked for: a real CPU solve in each platform candidate."""

    runs = [step for step in _steps(job) if "qualify_installed_cpu.py" in (step.get("run") or "")]

    assert len(runs) == 1, f"{job} does not run the CPU qualification exactly once"
    command = runs[0]["run"]
    assert "--pins-json pins.json" in command, job
    assert "--build-manifest" in command, job
    assert "--payload-kind" in command, job


@pytest.mark.parametrize("job", PLATFORM_JOBS)
def test_the_candidate_is_installed_or_extracted_before_it_is_qualified(job: str) -> None:
    """What a user receives, not the build directory it came from.

    Each platform takes the route its own users take: the Inno installer
    unattended and per-user, the tarball's own `install.sh` as a non-root user,
    and `ditto` out of the mounted disk image.
    """

    command = next(
        step["run"] for step in _steps(job) if "qualify_installed_cpu.py" in (step.get("run") or "")
    )
    expected = {
        "macos-bundle": ("hdiutil attach", "ditto", "--payload-kind dmg-ditto"),
        "windows-bundle": ("/VERYSILENT", "/DIR=$install", "--payload-kind windows-installer"),
        "linux-bundle": ("install.sh", "--prefix", "--payload-kind linux-install-sh"),
    }[job]
    names = [step.get("name") or "" for step in _steps(job)]
    assert any(name.startswith("Qualify BEAT CPU on the candidate") for name in names), (
        f"{job} must say which kind of payload it qualified"
    )
    for fragment in expected:
        assert fragment in command, f"{job} is missing {fragment!r}"


@pytest.mark.parametrize("job", PLATFORM_JOBS)
def test_a_failed_gate_still_leaves_the_candidate_and_its_logs(job: str) -> None:
    """A candidate that fails is the one somebody has to look at.

    So the gate runs after the RC assets are uploaded -- it still fails the job
    -- and the log step runs whatever happened.
    """

    names = [step.get("name") or step.get("uses") or "" for step in _steps(job)]
    upload = max(index for index, name in enumerate(names) if "RC assets" in name)
    gate_step = next(index for index, name in enumerate(names) if name.startswith("Qualify BEAT CPU"))
    logs = next(index for index, name in enumerate(names) if name == "Preserve the CPU qualification logs")

    assert upload < gate_step, f"{job} would withhold the candidate when the gate fails"
    assert logs > gate_step, f"{job} preserves logs before the step that writes them"
    assert _steps(job)[logs]["if"] == "always()", job


def test_the_windows_step_fails_when_the_qualifier_fails() -> None:
    """PowerShell does not fail a step on a native command's exit code.

    `$ErrorActionPreference` governs PowerShell's own errors, not the exit
    status of `python.exe`, so without an explicit `$LASTEXITCODE` check the
    qualifier could exit 1 and the step would still be green -- the one thing a
    gate must never do. The POSIX jobs get this from `set -e`.
    """

    windows = next(
        step["run"]
        for step in _steps("windows-bundle")
        if "qualify_installed_cpu.py" in (step.get("run") or "")
    )
    check = windows.index("$LASTEXITCODE")
    invocation = windows.index("qualify_installed_cpu.py")
    assert check > invocation, "the exit code must be checked after the qualifier runs"
    assert "throw" in windows[check:], "a non-zero exit has to fail the step"


@pytest.mark.parametrize("job", ("macos-bundle", "linux-bundle"))
def test_the_posix_steps_stop_on_a_failing_command(job: str) -> None:
    command = next(
        step["run"]
        for step in _steps(job)
        if "qualify_installed_cpu.py" in (step.get("run") or "")
    )
    assert command.lstrip().startswith("set -euo pipefail"), job


def test_no_step_turns_a_failed_qualification_into_a_pass() -> None:
    """No `|| true`, no `continue-on-error`, and no second attempt anywhere."""

    spec = _workflow()
    for name, body in spec["jobs"].items():
        # The qualify job calls ci.yml and has no steps of its own.
        for step in body.get("steps") or []:
            assert "continue-on-error" not in step, f"{name}: {step.get('name')}"
            run = step.get("run") or ""
            if "qualify_installed_cpu.py" in run:
                assert "|| true" not in run, name
                assert run.count("qualify_installed_cpu.py") == 1, (
                    f"{name} invokes the qualifier more than once, which is how a "
                    "retry would hide a product failure"
                )


def test_the_workflow_action_inventory_is_explicit() -> None:
    """Every workflow action is reviewed and pinned in its owning test.

    The provenance step adds one deliberate action to the bundle workflow. It
    does not belong to the installed-candidate gate itself, which remains pure
    shell plus the packaged interpreter.
    """

    spec = _workflow()
    # The repositories, not the refs: a sibling branch is replacing every ref
    # with an immutable commit, and this assertion has to survive that merge
    # while still catching a new dependency.
    repositories = {
        str(step["uses"]).split("@", 1)[0]
        for job in spec["jobs"].values()
        for step in job.get("steps") or []
        if "uses" in step
    }
    # A job that calls a reusable workflow is a dependency too. The only one is
    # this repository's own ci.yml, as source qualification.
    workflows = {str(job["uses"]) for job in spec["jobs"].values() if "uses" in job}
    assert workflows == {"./.github/workflows/ci.yml"}
    assert repositories == {
        "actions/checkout",
        "actions/setup-node",
        "actions/upload-artifact",
        "actions/download-artifact",
        "actions/attest",
        "astral-sh/setup-uv",
    }


def test_the_gate_changes_no_permission_and_no_trigger() -> None:
    """Artifact-only job verification under the permissions that already exist."""

    spec = _workflow()
    # YAML 1.1 reads a bare `on` key as the boolean True.
    triggers = spec[True] if True in spec else spec["on"]
    assert spec["permissions"] == {"contents": "read"}
    assert set(triggers) == {"workflow_dispatch"}
    assert "secrets" not in RC_WORKFLOW


# ---------------------------------------------------------------------------
# The harness plumbing, against a stub payload
# ---------------------------------------------------------------------------
#
# The pure checks above are the gate's judgement; this is its machinery, which
# is the part most likely to be wrong in a way no unit test notices: argument
# wiring, the startup wait, the capability poll, and the shutdown handshake.
# A stub payload exercises all four without a built candidate.
#
# It does not qualify a real application, and nothing here should be read as
# platform coverage: an actual installed candidate on each platform is owed
# from the runners.

_FAKE_INTERPRETER = '''#!{python}
"""Answer the three things the qualifier asks a packaged interpreter to do."""
import json
import sys

argv = sys.argv[1:]
if argv and argv[0] == "-c":
    program = argv[1]
    if "importlib.metadata" in program:
        print(json.dumps({{name: {{"commit": "{sha}"}} for name in argv[2:]}}))
    else:
        mode = "{cleanup}"
        if mode == "crash":
            print("the probe fell over", file=sys.stderr)
            raise SystemExit(3)
        if mode == "garbage":
            print("this is not json")
            raise SystemExit(0)
        wanted = argv[2]
        effective = "{effective}" or wanted
        alive = []
        if mode == "still-alive":
            alive = [{{"host_pid": 4242, "engine_pid": -1, "still_alive": True}}]
        print(json.dumps({{"verified": alive, "refused": [],
                           "effective_worker_dir": effective,
                           "contained": effective == wanted}}))
    raise SystemExit(0)
if argv and argv[0] == "-m":
    raise SystemExit(0)
sys.argv = [argv[0], *argv[1:]]
exec(compile(open(argv[0]).read(), argv[0], "exec"),
     {{"__name__": "__main__", "__file__": argv[0]}})
'''

# ---------------------------------------------------------------------------
# The stub application
# ---------------------------------------------------------------------------
#
# One definition of what the stub answers, used two ways: copied into a
# payload's launch/serve.py, where the real ``gate.Server`` starts it as a
# process, and served from a thread in this process for the imported-phase
# tests that must launch nothing. Its source is copied, so it may use only the
# standard-library names the serve.py header below imports.


class StubApplication:
    """The endpoints the qualifier drives, and what the application keeps.

    State lives in the data directory, so a second start on the same directory
    sees what the first one stored -- and can be told to forget it.
    """

    def __init__(self, data_dir: Path, settings: dict) -> None:
        self.settings = settings
        self.started = time.monotonic()
        self.capability_polls = 0
        self.data = Path(data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self.workspace = {"path": str(self.data / "workspace-default")}
        self.state_path = self.data / "stub-state.json"
        self.state = (
            json.loads(self.state_path.read_text(encoding="utf-8"))
            if self.state_path.exists()
            else {"starts": 0, "cad_workspace": None, "ingests": {}, "jobs": {}}
        )
        self.state["starts"] += 1
        if self.state["starts"] == 1 and settings.get("preexisting_jobs"):
            self.state["jobs"]["job-old"] = {
                "engine": "beat-cpu", "frequencies": [1000.0], "ingest_id": "wgi_old",
            }
        self.restarted = self.state["starts"] > 1
        self.imported_requests = self.data / "imported-solve-requests.json"
        self.save()

    def save(self) -> None:
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def exit_code(self) -> int:
        """A server that stops, but reports failure doing so."""

        return int(self.settings.get("exit_code_on_stop", 0))

    def capabilities(self) -> dict:
        self.capability_polls += 1
        settled = (
            self.capability_polls > self.settings.get("ready_after_capability_polls", 0)
            and (time.monotonic() - self.started) >= self.settings["ready_after_s"]
        )
        available = bool(settled) and self.settings["ever_ready"]
        metal = bool(self.settings.get("metal_available", False))
        return {
            "engines": [
                {"name": "beat-cpu", "available": available,
                 "reason": "ready" if available else "preparing"},
                {"name": "beat-metal", "available": False, "reason": "no GPU here"},
                {"name": "metal", "available": metal,
                 "reason": "ready" if metal else "no Metal device on this runner"},
            ],
            "cpuPreparationInFlight": not settled,
        }

    def imported_record(self) -> dict:
        findings = []
        if not self.settings.get("design_known"):
            findings.append({"id": "f-fresh", "kind": "freshness", "blocking": True,
                             "instance_id": "anchor", "verdict": "missing_design"})
        findings.append({"id": "f-paint", "kind": "source-paint-missing", "blocking": True,
                         "source_id": "source-hf"})
        findings.append({"id": "f-note", "kind": "freshness-degraded", "blocking": False})
        if self.settings.get("extra_blocking"):
            findings.append({"id": "f-heal", "kind": "healing-performed", "blocking": True})
        return {
            "ingest_id": "wgi_01J5A8QK3M9T2XVBH0RD7NWEF0",
            "report_sha256": "sha256:" + "c" * 64,
            "manifest_sha256": "sha256:" + "d" * 64,
            "artifact_sha256": "sha256:" + "e" * 64,
            "sources": [{"id": "source-hf", "default_drive_channel_id": "drive-hf"}],
            "polar_grid_derivation": {"angle_range": [-180.0, 180.0, 73]},
            "findings": findings,
            "finding_ids": [item["id"] for item in findings],
        }

    def imported_result(self, job: dict) -> dict:
        # A result may answer fewer frequencies, or another channel, than asked.
        frequencies = job["frequencies"][: self.settings.get("result_frequency_count", len(job["frequencies"]))]
        channel_id = self.settings.get("result_channel_id", "drive-hf")
        spl = [90.0 + index for index in range(len(frequencies))]
        if self.settings.get("imported_nan"):
            spl[0] = float("nan")
        channel = {
            "frequencies": frequencies,
            "spl_on_axis": {"frequencies": frequencies, "spl": spl,
                            "phase_degrees": [0.0] * len(frequencies)},
            "directivity": {
                plane: [[[-90.0, -6.0], [0.0, 0.0], [90.0, -6.0]] for _ in frequencies]
                for plane in ("horizontal", "vertical", "diagonal")
            },
        }
        reported = self.settings.get("substitute", {}).get(job["engine"], job["engine"])
        result = {
            "result_kind": "multi_channel",
            "result_contract_version": 2,
            "channels": {channel_id: channel},
            "channel_order": [channel_id],
            "frequencies": frequencies,
            "metadata": {"geometry_type": "imported", "ingest_id": job["ingest_id"],
                         "solver_engine": {"engine": reported}},
        }
        if self.settings.get("results_change_on_restart") and self.restarted:
            result["metadata"]["reopened"] = True
        return result

    def get(self, path: str) -> tuple:
        """Answer a GET as ``(status, payload, headers)``."""

        settings, state = self.settings, self.state
        if path == "/health":
            return 200, {"status": "ok"}, {}
        if path == "/api/capabilities":
            return 200, self.capabilities(), {}
        if path == "/api/workspace/path":
            if settings.get("runs_workspace_elsewhere"):
                return 200, {"path": str(self.data / "elsewhere"), "selected": True}, {}
            return 200, self.workspace, {}
        if path == "/api/cad-workspace/path":
            selected = state["cad_workspace"]
            if settings.get("cad_workspace_elsewhere") and not self.restarted and selected:
                selected = str(self.data / "elsewhere")
            if settings.get("forget_cad_workspace_on_restart") and self.restarted:
                selected = None
            return 200, {"path": selected, "selected": selected is not None}, {}
        if path == "/api/cadlink/designs":
            listed = [{"designId": "wgd_known"}] if settings.get("designs_listed") else []
            return 200, {"items": listed}, {}
        if path.startswith("/api/cadlink/ingest/"):
            record = state["ingests"].get(path.rsplit("/", 1)[-1])
            if self.restarted and settings.get("forget_ingest_on_restart"):
                record = None
            if record and self.restarted and settings.get("ingest_report_changes_on_restart"):
                record = dict(record, report_sha256="sha256:" + "f" * 64)
            return (200, record, {}) if record else (404, {"detail": "unknown"}, {})
        if path == "/api/jobs":
            forget = settings.get("forget_jobs_on_restart") and self.restarted
            hide = settings.get("hide_jobs_before_restart") and not self.restarted
            items = [] if forget or hide else [
                {"id": job, "status": "complete", "has_results": True} for job in state["jobs"]
            ]
            return 200, {"items": items, "total": len(items), "limit": 200, "offset": 0}, {}
        if path.startswith("/api/status/"):
            return 200, {"status": "complete"}, {}
        if path.startswith("/api/results/"):
            job = state["jobs"].get(path.rsplit("/", 1)[-1])
            if job is None:
                return 200, settings["result"], {}
            if self.restarted and settings.get("forget_job_results_on_restart"):
                return 404, {"detail": "unknown job"}, {}
            body = json.dumps(self.imported_result(job), sort_keys=True).encode()
            digest = hashlib.sha256(body).hexdigest()
            if settings.get("bad_results_digest"):
                digest = "0" * 64
            return 200, body, {"X-WG-Results-SHA256": digest}
        return 200, {}, {}

    def post(self, path: str, body: dict) -> tuple:
        """Answer a POST as ``(status, payload, headers)``."""

        state = self.state
        if path == "/api/workspace/select":
            self.workspace["path"] = body["path"]
            return 200, self.workspace, {}
        if path == "/api/cad-workspace/select":
            state["cad_workspace"] = body["path"]
            self.save()
            return 200, {"selected": True, "path": body["path"]}, {}
        if path == "/api/cadlink/ingest":
            (self.data / "ingest-request.json").write_text(json.dumps(body), encoding="utf-8")
            if state["cad_workspace"] is None:
                return 409, {"detail": "No WGLink folder has been selected."}, {}
            segments = body["bundlePath"].split("/")
            bundle = Path(state["cad_workspace"]).joinpath(*segments)
            if segments[0] != "wgreturn" or not (bundle / "wgreturn.json").is_file():
                return 422, {"detail": f"no return bundle at {body['bundlePath']}"}, {}
            record = self.imported_record()
            state["ingests"][record["ingest_id"]] = record
            self.save()
            if self.settings.get("ingest_without_id"):
                return 200, {key: value for key, value in record.items() if key != "ingest_id"}, {}
            return 200, record, {}
        if path == "/api/solve":
            geometry = body.get("geometry") or {}
            if geometry.get("type") != "imported":
                (self.data / "solve-request.json").write_text(json.dumps(body))
                return 200, {"job_id": "job-1"}, {}
            sent = (
                json.loads(self.imported_requests.read_text(encoding="utf-8"))
                if self.imported_requests.exists()
                else []
            )
            sent.append(body)
            self.imported_requests.write_text(json.dumps(sent), encoding="utf-8")
            record = state["ingests"][geometry["ingest_id"]]
            wanted = {f"{record['report_sha256']}:{item['id']}"
                      for item in record["findings"] if item["blocking"]}
            if set(geometry.get("acknowledged_findings") or []) != wanted:
                return 422, {"error": {"message": "unacknowledged blocking findings"}}, {}
            if self.settings.get("solve_without_job_id"):
                return 200, {"status": "queued"}, {}
            job = f"job-imported-{len(state['jobs']) + 1}"
            state["jobs"][job] = {"engine": body["options"]["engine"],
                                  "frequencies": body["options"]["frequencies_hz"],
                                  "ingest_id": geometry["ingest_id"]}
            self.save()
            return 200, {"job_id": job}, {}
        return 200, {}, {}


def _stub_handler(application: StubApplication) -> type:
    """A request handler class that serves *application*."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def _send(self, status: int, payload: object, headers: dict) -> None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._send(*application.get(urlsplit(self.path).path))

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._send(*application.post(self.path, body))

    return Handler


_STUB_MAIN = '''

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int)
parser.add_argument("--no-browser", action="store_true")
parser.add_argument("--data-dir", type=Path)
parser.add_argument("--status-control", type=Path)
args = parser.parse_args()

application = StubApplication(
    args.data_dir, json.loads(Path(__file__).with_name("stub-settings.json").read_text())
)
server = ThreadingHTTPServer(("127.0.0.1", args.port), _stub_handler(application))
# serve_forever's default 0.5 s poll made every stop wait that long for shutdown().
threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
deadline = time.monotonic() + 120
while time.monotonic() < deadline:
    if args.status_control and args.status_control.exists():
        break
    time.sleep(0.05)
server.shutdown()
raise SystemExit(application.exit_code())
'''

#: A stand-in for launch/serve.py, built from the definitions above so the
#: process and the in-process tests answer exactly alike.
_STUB_SERVER = (
    '"""A stand-in for launch/serve.py: the endpoints the qualifier drives."""\n'
    "import argparse, hashlib, json, threading, time\n"
    "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
    "from pathlib import Path\n"
    "from urllib.parse import urlsplit\n\n\n"
    + inspect.getsource(StubApplication)
    + "\n\n"
    + inspect.getsource(_stub_handler)
    + _STUB_MAIN
)


@pytest.fixture
def _quick_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail fast against a stub.

    The real waits are minutes long because a packaged application starting on
    a cold runner legitimately takes minutes. A stub that does not answer is a
    broken test, not a slow application, and waiting the production timeout for
    it would run into the suite's own faulthandler.
    """

    monkeypatch.setattr(gate, "STARTUP_TIMEOUT_S", 30.0)
    monkeypatch.setattr(gate, "CAPABILITY_TIMEOUT_S", 30.0)
    monkeypatch.setattr(gate, "SOLVE_TIMEOUT_S", 30.0)
    monkeypatch.setattr(gate, "PROVISION_TIMEOUT_S", 30.0)
    monkeypatch.setattr(gate, "SHUTDOWN_TIMEOUT_S", 20.0)
    # ``raising=False`` so the stub tests that predate the imported phase keep
    # running against a gate that has no such constant.
    monkeypatch.setattr(gate, "INGEST_TIMEOUT_S", 30.0, raising=False)
    # The stub answers at once, so the production intervals were pure sleep:
    # about 1 s per server start and more per capability poll, which the macOS
    # runner multiplies 12-17x until the harness job runs out of its budget.
    monkeypatch.setattr(gate, "POLL_INTERVAL_S", 0.02, raising=False)
    monkeypatch.setattr(gate, "CAPABILITY_POLL_S", 0.02)


def _stub_payload(tmp_path: Path, **settings: object) -> Path:
    payload = tmp_path / "payload"
    app = payload / "app"
    (app / "launch").mkdir(parents=True)
    (payload / "runtime" / "bin").mkdir(parents=True)

    (app / "APP-MANIFEST.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "version": "0.3.1",
                "commit": "a" * 40,
                "runtimeId": "0123456789ab",
                "treeSha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    (app / "launch" / "serve.py").write_text(_STUB_SERVER, encoding="utf-8")
    interpreter = payload / "runtime" / "bin" / "python3.13"
    interpreter.write_text(
        _FAKE_INTERPRETER.format(
            python=sys.executable,
            sha=PINS["hornlab-beat-bem"],
            effective=str(settings.pop("effective_worker_dir", "")),
            cleanup=str(settings.pop("cleanup", "")),
        ),
        encoding="utf-8",
    )
    interpreter.chmod(0o755)

    resolved = {"ready_after_s": 0.0, "ever_ready": True, "result": _result()}
    resolved.update(settings)
    (app / "launch" / "stub-settings.json").write_text(json.dumps(resolved), encoding="utf-8")
    return payload


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the stub interpreter is a shebang script; Windows harness coverage is owed "
    "from the runner, where the real packaged python.exe is the interpreter",
)
def test_the_harness_starts_waits_solves_and_stops_against_a_stub(tmp_path: Path, _quick_timeouts: None) -> None:
    """Argument wiring, the startup wait, the capability poll and the shutdown."""

    # A wall-clock delay can elapse before the first request on a slower
    # runner, so use the protocol's poll count for deterministic wait coverage.
    payload = _stub_payload(tmp_path, ready_after_capability_polls=1)
    output = tmp_path / "out"

    code = gate.main(
        [
            "--payload", str(payload),
            "--payload-kind", "stub",
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", f"hornlab-beat-bem={PINS['hornlab-beat-bem']}",
            "--expected-version", "0.3.1",
            "--expected-tree-sha256", "b" * 64,
        ]
    )

    assert code == 0, (output / "cpu-qualification.json").read_text(encoding="utf-8")
    report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    assert report["qualified"] is True
    assert report["cpu_offered"]["available"] is True
    assert report["solve"]["beat_backend"] == "cpu"
    assert report["gpu_independence"]["any_accelerator_available"] is False
    # The engine really was requested by name, over the wire.
    request = json.loads(
        (tmp_path / "work" / "data" / "solve-request.json").read_text(encoding="utf-8")
    )
    assert request["options"]["engine"] == "beat-cpu"
    assert request["options"]["solver_mode"] == "full_3d"
    # It waited rather than reading one sample and giving up.
    samples = json.loads((output / "cpu-capability-samples.json").read_text(encoding="utf-8"))
    assert len(samples) >= 2
    assert samples[0]["available"] is False
    # Without --imported-engine the imported phase does not run at all.
    assert "imported_return" not in report
    assert not (output / "imported-server-1.log").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="see the test above")
def test_an_application_that_does_not_offer_cpu_itself_fails_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _quick_timeouts: None
) -> None:
    """The requirement is that the *application* offers it, on every platform.

    Since 69b1ed0 the application prepares the CPU runtime on macOS too, so a
    row that never becomes available is a product defect on any supported
    computer. An earlier version of this gate answered that by running the
    provisioning command itself and restarting, which passed -- and so left
    exactly the user requirement it exists for unqualified. It must fail, with
    the application's own reason.
    """

    payload = _stub_payload(tmp_path, ever_ready=False)
    output = tmp_path / "out"
    monkeypatch.setattr(gate, "CAPABILITY_POLL_S", 0.1)

    code = gate.main(
        [
            "--payload", str(payload),
            "--payload-kind", "stub",
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", f"hornlab-beat-bem={PINS['hornlab-beat-bem']}",
        ]
    )

    assert code == 1
    report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    assert report["qualified"] is False
    assert "did not offer beat-cpu by itself" in report["error"]
    assert report["cpu_offered"]["available"] is False
    assert report["cpu_offered"]["reason"] == "preparing"
    # No provisioning was run, and no second server was started to hide it.
    assert "preparation_diagnosis" not in report
    assert not (output / "cpu-provision-diagnosis.log").exists()
    assert not (output / "server-2.log").exists()
    # Everything established before the failure survived it.
    assert report["pins"]["checked"] == ["hornlab-beat-bem"]
    assert report["app_manifest"]["manifest"]["version"] == "0.3.1"
    assert report["cpu_preparation"]["settled"] == "not-preparing"


@pytest.mark.skipif(sys.platform == "win32", reason="see the test above")
def test_the_diagnosis_flag_adds_evidence_and_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _quick_timeouts: None
) -> None:
    """An explicitly labelled diagnosis cannot qualify a candidate.

    It runs from the failure path, after the verdict, so the most it can do is
    say whether the runtime could have been built on this host at all.
    """

    payload = _stub_payload(tmp_path, ever_ready=False)
    output = tmp_path / "out"
    monkeypatch.setattr(gate, "CAPABILITY_POLL_S", 0.1)

    code = gate.main(
        [
            "--payload", str(payload),
            "--payload-kind", "stub",
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", f"hornlab-beat-bem={PINS['hornlab-beat-bem']}",
            "--diagnose-preparation-failure",
        ]
    )

    assert code == 1, "a diagnosis must never turn a failed gate green"
    report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    assert report["qualified"] is False
    assert report["preparation_diagnosis"]["note"].startswith("diagnosis only")
    assert (output / "cpu-provision-diagnosis.log").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="see the test above")
def test_a_stub_that_answers_with_rubbish_fails_the_gate(tmp_path: Path, _quick_timeouts: None) -> None:
    """A candidate that returns a result full of infinities is not a solve."""

    broken = _result(
        spl_on_axis={
            "frequencies": [500.0, 1000.0],
            "spl": [float("inf"), 1.0],
            "phase_degrees": [0.0, 1.0],
        }
    )
    payload = _stub_payload(tmp_path, result=broken)
    output = tmp_path / "out"

    code = gate.main(
        [
            "--payload", str(payload),
            "--payload-kind", "stub",
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", f"hornlab-beat-bem={PINS['hornlab-beat-bem']}",
        ]
    )

    assert code == 1
    report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    assert "non-finite" in report["error"]


@pytest.mark.skipif(sys.platform == "win32", reason="see the harness test above")
def test_the_gate_starts_exactly_one_server(tmp_path: Path, _quick_timeouts: None) -> None:
    """No second launch, because there is nothing left that would restart.

    The fallback that ran the provisioning command and started the application
    again is gone -- it made a product defect pass -- and with it the shared
    status-control file that told the second launch to stop before it had
    started. One launch is now the whole of the gate, so both are closed.
    """

    payload = _stub_payload(tmp_path, ever_ready=False)
    output = tmp_path / "out"

    assert gate.main(
        [
            "--payload", str(payload),
            "--payload-kind", "stub",
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", f"hornlab-beat-bem={PINS['hornlab-beat-bem']}",
        ]
    ) == 1

    assert (output / "server.log").is_file()
    assert not (output / "server-2.log").exists()
    assert [p.name for p in (tmp_path / "work" / "status").iterdir()] == ["stop-1"]


@pytest.mark.skipif(sys.platform == "win32", reason="see the harness test above")
def test_a_registry_override_that_did_not_take_effect_signals_nothing(
    tmp_path: Path, _quick_timeouts: None
) -> None:
    """The failure mode the wrong variable name produced, caught rather than hidden.

    `worker_registry.py` reads `HORNLAB_BEAT_WORKER_DIR` and nothing else, so an
    override under any other name is ignored in silence: the workers land in the
    user's own registry and the gate happily reports an isolated directory that
    nothing ever wrote to. Cleanup would then be reading -- and signalling --
    another person's workers. Authenticating as itself does not make a
    stranger's worker ours, so the answer is to touch nothing and say so.
    """

    payload = _stub_payload(tmp_path, effective_worker_dir="/somewhere/else/entirely")
    output = tmp_path / "out"

    code = gate.main(
        [
            "--payload", str(payload),
            "--payload-kind", "stub",
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", f"hornlab-beat-bem={PINS['hornlab-beat-bem']}",
        ]
    )

    assert code == 1
    report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    assert "did not take effect" in report["worker_cleanup"]["refused"]
    assert "Nothing was signalled" in report["worker_cleanup"]["refused"]


@pytest.mark.skipif(sys.platform == "win32", reason="see the harness test above")
def test_a_failure_still_cleans_up_and_keeps_what_it_had_established(
    tmp_path: Path, _quick_timeouts: None
) -> None:
    """Cleanup and the partial report are in the finally, not after success.

    A solve that returns rubbish used to leave both behind: no worker cleanup,
    and a report containing only the error -- not the pins already checked, not
    the manifest, not the capability samples that say what the application was
    doing when it stopped.
    """

    broken = _result(
        spl_on_axis={
            "frequencies": [500.0, 1000.0],
            "spl": [float("nan"), 1.0],
            "phase_degrees": [0.0, 1.0],
        }
    )
    payload = _stub_payload(tmp_path, result=broken)
    output = tmp_path / "out"

    assert gate.main(
        [
            "--payload", str(payload),
            "--payload-kind", "stub",
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", f"hornlab-beat-bem={PINS['hornlab-beat-bem']}",
        ]
    ) == 1

    report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    assert report["qualified"] is False
    assert "non-finite" in report["error"]
    assert report["worker_cleanup"]["contained"] is True
    assert report["pins"]["checked"] == ["hornlab-beat-bem"]
    assert report["cpu_offered"]["available"] is True
    assert report["workspace"]["workspace_path"].startswith(str(tmp_path / "work"))


@pytest.mark.skipif(sys.platform == "win32", reason="see the harness test above")
def test_the_workspace_is_moved_into_the_run_before_anything_solves(
    tmp_path: Path, _quick_timeouts: None
) -> None:
    """`--data-dir` is not workspace isolation, and on Windows nothing else is.

    `launch/serve.py` resolves the workspace through `documents_root()`, which
    honours `XDG_DOCUMENTS_DIR` on POSIX and has no supported override on
    Windows. So the workspace is selected through the same API the application's
    own settings use, before the first solve -- a run that wrote a result into
    somebody's Documents and only then checked would already have done the thing
    the check exists to prevent.
    """

    payload = _stub_payload(tmp_path)
    output = tmp_path / "out"

    assert gate.main(
        [
            "--payload", str(payload),
            "--payload-kind", "stub",
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", f"hornlab-beat-bem={PINS['hornlab-beat-bem']}",
        ]
    ) == 0

    report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    workspace = report["workspace"]
    assert workspace["established_through_the_api"] is True
    assert workspace["workspace_path"] == str(tmp_path / "work" / "workspace")
    # And it happened before the solve: the request the stub recorded is there.
    assert (tmp_path / "work" / "data" / "solve-request.json").is_file()


# ---------------------------------------------------------------------------
# The final verdict, when only the cleanup fails
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="see the harness test above")
@pytest.mark.parametrize(
    ("cleanup", "expected"),
    (
        pytest.param("crash", "exited 3", id="probe-exited-non-zero"),
        pytest.param("garbage", "unreadable worker cleanup output", id="unreadable-output"),
        pytest.param("still-alive", "still running after cleanup", id="worker-survived"),
    ),
)
def test_a_solve_that_worked_does_not_excuse_a_cleanup_that_did_not(
    tmp_path: Path, _quick_timeouts: None, cleanup: str, expected: str
) -> None:
    """A cleanup whose outcome is unknown is not a pass.

    The solve succeeds in every case here, and the run still fails. Returning
    `qualified=true` with a probe that exited non-zero, output nobody could
    read, or a worker still running would be a green step that says nothing
    about what is left behind on the machine -- and on a shared runner that is
    the next job's problem.
    """

    payload = _stub_payload(tmp_path, cleanup=cleanup)
    output = tmp_path / "out"

    code = gate.main(
        [
            "--payload", str(payload),
            "--payload-kind", "stub",
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", f"hornlab-beat-bem={PINS['hornlab-beat-bem']}",
        ]
    )

    assert code == 1
    report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    assert report["qualified"] is False
    assert expected in report["error"]
    # And the evidence from the part that did work is still there.
    assert report["solve"]["beat_backend"] == "cpu"
    assert report["cpu_offered"]["available"] is True


@pytest.mark.skipif(sys.platform == "win32", reason="see the harness test above")
def test_an_unexpected_cleanup_exception_also_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _quick_timeouts: None
) -> None:
    """The exact edge the review found: a generic exception, after a good solve.

    `stop_our_workers` raising `TimeoutExpired` or `OSError` was recorded in the
    report and then ignored, so the run came back `qualified=true` and exit 0.
    Anything that stops the cleanup completing now fails the qualification.
    """

    payload = _stub_payload(tmp_path)
    output = tmp_path / "out"

    def explode(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="probe", timeout=1.0)

    monkeypatch.setattr(gate, "stop_our_workers", explode)

    code = gate.main(
        [
            "--payload", str(payload),
            "--payload-kind", "stub",
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", f"hornlab-beat-bem={PINS['hornlab-beat-bem']}",
        ]
    )

    assert code == 1, "an unknown cleanup outcome must not be reported as qualified"
    report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    assert report["qualified"] is False
    assert "the worker cleanup did not complete" in report["error"]
    assert "TimeoutExpired" in report["worker_cleanup"]["error"]
    assert "traceback" in report["worker_cleanup"]
    # The solve that did work is preserved, so the failure is diagnosable.
    assert report["solve"]["beat_backend"] == "cpu"


@pytest.mark.skipif(sys.platform == "win32", reason="see the harness test above")
def test_an_earlier_failure_is_not_replaced_by_the_cleanup_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _quick_timeouts: None
) -> None:
    """When both fail, the reader needs the original first."""

    broken = _result(metadata=dict(gate.CPU_RESULT_CONTRACT, beat_backend="metal"))
    payload = _stub_payload(tmp_path, result=broken)
    output = tmp_path / "out"

    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("and the cleanup fell over too")

    monkeypatch.setattr(gate, "stop_our_workers", explode)

    assert gate.main(
        [
            "--payload", str(payload),
            "--payload-kind", "stub",
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", f"hornlab-beat-bem={PINS['hornlab-beat-bem']}",
        ]
    ) == 1

    report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    assert "expected" in report["error"], "the solve failure is the one to report"
    assert "cleanup" not in report["error"]
    # The cleanup failure is not lost, only demoted.
    assert "fell over too" in report["worker_cleanup"]["error"]


def test_the_julia_depot_is_inside_the_run(tmp_path: Path) -> None:
    """The isolation claim has to include Julia, because nothing else sets it.

    `HORNLAB_BEAT_RUNTIME_DIR` isolates the provisioning *record*. The pinned
    package launches Julia with `{**os.environ, ...}` and never sets
    `JULIA_DEPOT_PATH`, so without this the application's own preparation reads
    and writes the caller's `~/.julia` while the report calls every cache
    isolated.
    """

    app = _app_layer(tmp_path)
    work = tmp_path / "work"

    environment = gate.isolated_environment(app, work)

    assert environment["JULIA_DEPOT_PATH"] == str(work / "julia-depot")
    assert (work / "julia-depot").is_dir()


# ---------------------------------------------------------------------------
# The fresh-install imported return
# ---------------------------------------------------------------------------
#
# A candidate can pass the parametric gate while the path a CAD Link user
# takes on a new machine -- a return arriving in a folder with a name nobody
# chose for a parser, ingested with no design registry, solved, and found again
# after the application is closed -- has never run. These hold the imported
# phase to that sequence: every step it claims, and every way it can fail.

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "imported-return" / "round.wgreturn"
)


def _fixture_manifest() -> dict:
    return json.loads((FIXTURE / "wgreturn.json").read_text(encoding="utf-8"))


def test_the_committed_return_fixture_matches_its_own_manifest() -> None:
    """The checksums ingest verifies first, verified here before any runner does."""

    files = _fixture_manifest()["files"]

    assert files, "the fixture manifest lists no files"
    for name, entry in files.items():
        data = (FIXTURE / name).read_bytes()
        assert entry["sha256"] == "sha256:" + hashlib.sha256(data).hexdigest(), name
        assert entry["size_bytes"] == len(data), name
    assert sorted(path.name for path in FIXTURE.iterdir()) == sorted([*files, "wgreturn.json"])
    # Copied on every candidate, so it stays small.
    assert sum(entry["size_bytes"] for entry in files.values()) < 300_000


def test_the_fixture_binds_the_throat_to_the_bore_facing_disc() -> None:
    """The builder's flat membrane: the contract names the disc in the throat plane.

    With a rounded membrane the only planar face left would be the plug's rear,
    facing away from the bore, and the contract would bind there instead.
    """

    contract = _fixture_manifest()["instances"][0]["source_contract"]

    assert abs(contract["throat_z_mm"]) < 1e-6
    assert contract["throat_plane_link"]["normal"] == [0, 0, 1]
    assert 290.0 < contract["expected_disc_area_mm2"] < 330.0


def test_the_fixture_carries_no_local_path() -> None:
    manifest = (FIXTURE / "wgreturn.json").read_text(encoding="utf-8")
    header = (FIXTURE / "assembly.step").read_bytes()[:2000].decode("ascii")

    for body in (manifest, header):
        for marker in ("/Users/", "/home/", "/private/", "/tmp/", ":\\\\", "C:/"):
            assert marker not in body, marker


def test_line_endings_cannot_change_the_fixture_bytes() -> None:
    """The manifest pins the STEP's SHA-256, so a checkout must not rewrite it.

    The repository default normalises text on commit and fixes LF on checkout.
    A STEP is ASCII, so it would be treated as text; ``-text`` makes the bytes
    the commit holds the bytes every platform gets, whatever the generator
    wrote. The STEP writer leaves trailing spaces, and ``-whitespace`` keeps a
    whitespace fix from stripping those pinned bytes.
    """

    root = Path(__file__).resolve().parents[2]
    rules = [
        line.split()
        for line in (root / ".gitattributes").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    fixture = [rule[1:] for rule in rules if rule[0] == "scripts/fixtures/imported-return/**"]

    assert fixture, "no .gitattributes rule covers the fixture"
    assert "-text" in fixture[-1]
    assert "-whitespace" in fixture[-1]


@pytest.mark.parametrize(
    "name", ("IMPORTED_DATA_DIR_NAME", "IMPORTED_WORKSPACE_NAME", "IMPORTED_BUNDLE_NAME")
)
def test_every_name_the_imported_phase_creates_has_spaces_and_non_ascii(name: str) -> None:
    """Created by the script, not the shell, so no platform's quoting can drop them."""

    value = getattr(gate, name)

    assert " " in value
    assert any(ord(character) > 127 for character in value)
    assert unicodedata.is_normalized("NFC", value)


def test_the_bundle_name_is_one_ingest_accepts() -> None:
    assert gate.IMPORTED_BUNDLE_NAME.endswith(".wgreturn")
    assert "/" not in gate.IMPORTED_BUNDLE_NAME and "\\" not in gate.IMPORTED_BUNDLE_NAME


def test_the_gate_uses_the_committed_fixture_by_default() -> None:
    assert gate.DEFAULT_IMPORTED_FIXTURE.resolve() == FIXTURE


def test_a_tampered_fixture_is_refused_before_it_is_copied(tmp_path: Path) -> None:
    copy = tmp_path / "round.wgreturn"
    shutil.copytree(FIXTURE, copy)

    verified = gate.verify_return_bundle(copy)
    assert verified["files"] == ["assembly.step"]
    assert verified["design_id"] == _fixture_manifest()["instances"][0]["design_id"]
    assert verified["source_ids"] == ["source-hf"]

    step = copy / "assembly.step"
    step.write_bytes(step.read_bytes().replace(b"ISO-10303-21", b"ISO-10303-22", 1))
    with pytest.raises(gate.QualificationError, match="sha256"):
        gate.verify_return_bundle(copy)

    step.write_bytes(step.read_bytes() + b"\n")
    with pytest.raises(gate.QualificationError, match="bytes"):
        gate.verify_return_bundle(copy)


REPORT_SHA = "sha256:" + "c" * 64
FRESH = {"id": "f1", "kind": "freshness", "blocking": True, "verdict": "missing_design"}
PAINT = {"id": "f2", "kind": "source-paint-missing", "blocking": True}
NOTE = {"id": "f3", "kind": "freshness-degraded", "blocking": False}


def test_only_the_blocking_findings_of_a_fresh_install_are_acknowledged() -> None:
    answer = gate.blocking_acknowledgements(
        {"report_sha256": REPORT_SHA, "findings": [FRESH, NOTE, PAINT]}
    )

    assert answer["acknowledged"] == [f"{REPORT_SHA}:f1", f"{REPORT_SHA}:f2"]
    assert [item["kind"] for item in answer["informational"]] == ["freshness-degraded"]


@pytest.mark.parametrize(
    ("record", "message"),
    (
        pytest.param(
            {"report_sha256": REPORT_SHA,
             "findings": [FRESH, PAINT, {"id": "f4", "kind": "healing-performed", "blocking": True}]},
            "healing-performed",
            id="unexpected-blocking",
        ),
        pytest.param(
            {"report_sha256": REPORT_SHA, "findings": [PAINT]},
            "missing_design",
            id="design-already-known",
        ),
        pytest.param(
            {"report_sha256": REPORT_SHA,
             "findings": [dict(FRESH, verdict="design_changed"), PAINT]},
            "design_changed",
            id="other-freshness-verdict",
        ),
        pytest.param({"findings": [FRESH, PAINT]}, "report_sha256", id="no-report-digest"),
    ),
)
def test_a_finding_a_fresh_install_should_not_have_is_never_acknowledged_away(
    record: dict[str, object], message: str
) -> None:
    """Acknowledging whatever blocks would hide the regression that caused it."""

    with pytest.raises(gate.QualificationError, match=message):
        gate.blocking_acknowledgements(record)


def _imported_result(engine: str = "beat-cpu", **overrides: object) -> dict[str, object]:
    channel = {key: value for key, value in _result().items()
               if key in ("frequencies", "spl_on_axis", "directivity")}
    result: dict[str, object] = {
        "result_kind": "multi_channel",
        "channels": {"drive-hf": channel},
        "channel_order": ["drive-hf"],
        "frequencies": [500.0, 1000.0],
        "metadata": {"geometry_type": "imported", "ingest_id": "wgi_x",
                     "solver_engine": {"engine": engine}},
    }
    result.update(overrides)
    return result


def test_an_imported_result_from_the_requested_engine_passes() -> None:
    report = gate.check_imported_result(
        _imported_result("metal"), "metal", channels=["drive-hf"], frequencies=[500.0, 1000.0]
    )

    assert report["solver_engine"]["engine"] == "metal"
    assert report["channels"]["drive-hf"]["frequencies"] == 2
    assert report["channels"]["drive-hf"]["finite_non_zero"] > 0


#: Real frequencies and real angles, and nothing solved: what the check used
#: to pass, because it counted the axes as data.
_ZEROED_CHANNEL = {
    "frequencies": [500.0, 1000.0],
    "spl_on_axis": {
        "frequencies": [500.0, 1000.0], "spl": [0.0, 0.0], "phase_degrees": [0.0, 0.0],
    },
    "directivity": {plane: _ZERO_PLANE for plane in PLANES},
}
_GOOD_CHANNEL = {key: value for key, value in _result().items()
                 if key in ("frequencies", "spl_on_axis", "directivity")}


@pytest.mark.parametrize(
    ("result", "requested", "message"),
    (
        pytest.param(_imported_result("beat-cpu"), "metal", "reported", id="substituted"),
        pytest.param(_imported_result(metadata={"geometry_type": "imported"}), "beat-cpu",
                     "reported", id="no-engine"),
        pytest.param(_imported_result(metadata={"geometry_type": "parametric",
                                                "solver_engine": {"engine": "beat-cpu"}}),
                     "beat-cpu", "imported", id="not-imported"),
        pytest.param(_imported_result(channels={}), "beat-cpu", "no channels", id="no-channels"),
        pytest.param(
            _imported_result(channels={"drive-hf": dict(
                _ZEROED_CHANNEL,
                spl_on_axis={"frequencies": [500.0, 1000.0], "spl": [float("nan"), 1.0],
                             "phase_degrees": [0.0, 0.0]},
                frequencies=[500.0, 1000.0])}),
            "beat-cpu", "non-finite", id="nan"),
        pytest.param(_imported_result(channels={"drive-hf": _ZEROED_CHANNEL}), "beat-cpu",
                     "nothing was solved", id="all-zero"),
        pytest.param(
            _imported_result(channels={"drive-hf": dict(_GOOD_CHANNEL, directivity={
                "horizontal": DIRECTIVITY["horizontal"], "vertical": DIRECTIVITY["vertical"],
            })}),
            "beat-cpu", "diagonal", id="requested-plane-missing",
        ),
        pytest.param(
            _imported_result(channels={"drive-hf": {
                key: value for key, value in _GOOD_CHANNEL.items() if key != "directivity"
            }}),
            "beat-cpu", "directivity", id="no-directivity",
        ),
        pytest.param(
            _imported_result(channels={"drive-hf": dict(
                _GOOD_CHANNEL, spl_on_axis=_ZEROED_CHANNEL["spl_on_axis"])}),
            "beat-cpu", "spl_on_axis.*nothing was solved", id="zero-spl",
        ),
        pytest.param(
            _imported_result(channels={"drive-hf": _GOOD_CHANNEL, "drive-lf": _ZEROED_CHANNEL}),
            "beat-cpu", "drive-lf", id="one-channel-zeroed",
        ),
    ),
)
def test_an_imported_result_the_contract_refuses_fails(
    result: dict[str, object], requested: str, message: str
) -> None:
    # Asked for exactly the channels it carries, so each case fails on its own
    # check and not on the channel match.
    with pytest.raises(gate.QualificationError, match=message):
        gate.check_imported_result(
            result, requested, channels=list(result.get("channels") or {}), frequencies=[500.0, 1000.0]
        )


def test_an_optional_imported_engine_needs_a_required_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run whose only imported engines were optional could qualify nothing."""

    with pytest.raises(SystemExit) as raised:
        gate.main([
            "--payload", str(tmp_path), "--work", str(tmp_path / "w"),
            "--output", str(tmp_path / "o"),
            "--imported-engine-when-offered", "metal",
        ])

    assert raised.value.code == 2
    assert "at least one --imported-engine" in capsys.readouterr().err


def test_an_imported_engine_is_either_required_or_optional(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        gate.main([
            "--payload", str(tmp_path), "--work", str(tmp_path / "w"),
            "--output", str(tmp_path / "o"),
            "--imported-engine", "metal", "--imported-engine-when-offered", "metal",
        ])

    assert raised.value.code == 2
    assert "both --imported-engine and --imported-engine-when-offered" in capsys.readouterr().err


@pytest.mark.parametrize("job", PLATFORM_JOBS)
def test_every_platform_qualifies_a_fresh_install_imported_return(job: str) -> None:
    """BEAT CPU everywhere; Metal on macOS, and only when the candidate offers it."""

    command = next(
        step["run"] for step in _steps(job) if "qualify_installed_cpu.py" in (step.get("run") or "")
    )

    assert "--imported-engine beat-cpu" in command, job
    if job == "macos-bundle":
        assert "--imported-engine-when-offered metal" in command
    else:
        assert "metal" not in command, job


def _run_imported(
    tmp_path: Path, *extra: str, **settings: object
) -> tuple[int, dict[str, object]]:
    payload = _stub_payload(tmp_path, **settings)
    output = tmp_path / "out"
    code = gate.main(
        [
            "--payload", str(payload),
            "--payload-kind", "stub",
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", f"hornlab-beat-bem={PINS['hornlab-beat-bem']}",
            *extra,
        ]
    )
    return code, json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))


def _imported_requests(tmp_path: Path) -> list[dict[str, object]]:
    sent = tmp_path / "work" / gate.IMPORTED_DATA_DIR_NAME / "imported-solve-requests.json"
    return json.loads(sent.read_text(encoding="utf-8")) if sent.exists() else []


@pytest.mark.skipif(sys.platform == "win32", reason="see the harness test above")
def test_a_fresh_install_ingests_solves_and_reopens_an_imported_return(
    tmp_path: Path, _quick_timeouts: None
) -> None:
    """The whole sequence, through the real entry point and real processes.

    Import, prepare, solve on BEAT CPU and on Metal when offered, restart,
    reopen. This is the one imported-phase test that launches anything -- the
    pin read, the parametric server, the imported server and its restart, and
    the worker probe -- because every launch costs seconds on a hosted macOS
    runner. Every failure mode below drives the same phase in this process.
    """

    code, report = _run_imported(
        tmp_path, "--imported-engine", "beat-cpu", "--imported-engine-when-offered", "metal",
        metal_available=True,
    )

    assert code == 0, report.get("error")
    section = report["imported_return"]
    work = tmp_path / "work"
    data_dir = work / gate.IMPORTED_DATA_DIR_NAME
    workspace = work / gate.IMPORTED_WORKSPACE_NAME

    # A data directory nothing had used, under names a parser has to survive.
    assert section["fresh"] == {
        "data_dir_existed_before": False, "jobs_before": 0, "designs_before": 0,
    }
    assert section["paths"]["data_dir"] == str(data_dir)
    assert section["paths"]["workspace"] == str(workspace)
    # The committed return, copied into the selected workspace's wgreturn/.
    copied = workspace / "wgreturn" / gate.IMPORTED_BUNDLE_NAME
    assert (copied / "wgreturn.json").read_bytes() == (FIXTURE / "wgreturn.json").read_bytes()
    # Ingested through the application's own API, relative to that workspace.
    ingest = json.loads((data_dir / "ingest-request.json").read_text(encoding="utf-8"))
    assert ingest["bundlePath"] == f"wgreturn/{gate.IMPORTED_BUNDLE_NAME}"
    assert ingest["expectedDesignId"] == _fixture_manifest()["instances"][0]["design_id"]
    assert set(ingest["mesh"]) == {"rigidSizeMm", "transitionMm", "sourceSizeMm"}
    # The blocking findings, acknowledged as report:finding; the other is not.
    assert section["ingest"]["acknowledged"] == [
        f"{REPORT_SHA}:f-fresh", f"{REPORT_SHA}:f-paint",
    ]
    sent = _imported_requests(tmp_path)
    assert [body["options"]["engine"] for body in sent] == ["beat-cpu", "metal"]
    assert all(body["geometry"]["type"] == "imported" for body in sent)
    assert all(body["geometry"]["ingest_id"] == section["ingest"]["ingest_id"] for body in sent)
    # Each engine solved, and each result says it came from that engine.
    engines = {row["engine"]: row for row in section["engines"]}
    assert engines["beat-cpu"]["decision"] == "solved"
    assert engines["beat-cpu"]["solver_engine"]["engine"] == "beat-cpu"
    assert engines["metal"]["requirement"] == "when-offered"
    assert engines["metal"]["offered"] is True
    assert engines["metal"]["decision"] == "solved"
    assert engines["metal"]["solver_engine"]["engine"] == "metal"
    # Listed before the restart; then stopped, started again on the same data
    # directory, and found unchanged.
    jobs = [engines[name]["job_id"] for name in ("beat-cpu", "metal")]
    assert section["listed_before_restart"] == jobs
    reopen = section["reopen"]
    for name in ("beat-cpu", "metal"):
        assert reopen["jobs"][engines[name]["job_id"]] == {
            "listed": True, "equal": True,
            "sha256_before": engines[name]["results_sha256"],
            "sha256_after": engines[name]["results_sha256"],
        }
    assert reopen["ingest_record_reopened"] is True
    assert reopen["cad_workspace_persisted"] is True
    assert (tmp_path / "out" / "imported-server-1.log").is_file()
    assert (tmp_path / "out" / "imported-server-2.log").is_file()


# ---------------------------------------------------------------------------
# The imported phase in this process
# ---------------------------------------------------------------------------
#
# A launch costs seconds on a hosted macOS runner, and the stub tests' cost is
# almost all launches: the harness job ran out of its budget at three to five
# per imported test. So every failure mode below runs the phase function itself
# -- not the parametric phase, the pin read or the worker probe, which the
# tests above cover -- against the same stub application, served from a thread
# in this process. Launching anything is refused, so a test that starts to
# fails instead of quietly costing minutes again.


class _InProcessServer(gate.Server):
    """``gate.Server`` with the stub application on a thread instead of a child.

    Only starting and stopping differ. The startup wait, the job wait, the
    stored-results digest and ``__exit__`` keeping the failure that ended a
    block are the real class's code.
    """

    def __init__(
        self,
        _interpreter: Path,
        app: Path,
        _environment: dict[str, str],
        data_dir: Path,
        _control: Path,
        log_path: Path,
    ) -> None:
        settings = json.loads((app / "launch" / "stub-settings.json").read_text(encoding="utf-8"))
        self.application = StubApplication(data_dir, settings)
        self._http = ThreadingHTTPServer(("127.0.0.1", 0), _stub_handler(self.application))
        self.port = int(self._http.server_address[1])
        self.base = f"http://127.0.0.1:{self.port}"
        self.log_path = log_path
        log_path.write_text("the stub application, served in process\n", encoding="utf-8")
        threading.Thread(
            target=self._http.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        ).start()

    def stop(self, *, force: bool = False) -> None:
        self._http.shutdown()
        self._http.server_close()


def _refuse_to_launch(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("an in-process imported-phase test launched a process")


@pytest.fixture
def _in_process(monkeypatch: pytest.MonkeyPatch, _quick_timeouts: None) -> None:
    monkeypatch.setattr(gate, "Server", _InProcessServer)
    monkeypatch.setattr(subprocess, "Popen", _refuse_to_launch)


def _imported_phase(
    tmp_path: Path,
    *,
    required: tuple[str, ...] = ("beat-cpu",),
    when_offered: tuple[str, ...] = (),
    **settings: object,
) -> tuple[str | None, dict[str, object]]:
    """Run the imported phase in this process: its failure, if any, and its section."""

    payload = _stub_payload(tmp_path, **settings)
    work = tmp_path / "work"
    output = tmp_path / "out"
    work.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    section: dict[str, object] = {}
    try:
        gate.qualify_imported_return(
            payload / "runtime" / "bin" / "python3.13",
            payload / "app",
            {},
            work,
            output,
            required=list(required),
            when_offered=list(when_offered),
            fixture=gate.DEFAULT_IMPORTED_FIXTURE,
            section=section,
        )
    except gate.QualificationError as exc:
        return str(exc), section
    return None, section


def test_an_optional_engine_the_candidate_does_not_offer_is_recorded_not_failed(
    tmp_path: Path, _in_process: None
) -> None:
    """Metal not offered: the application's reason is recorded, and that is not a failure."""

    failure, section = _imported_phase(tmp_path, when_offered=("metal",))

    assert failure is None
    engines = {row["engine"]: row for row in section["engines"]}
    assert engines["beat-cpu"]["decision"] == "solved"
    assert engines["metal"] == {
        "engine": "metal", "requirement": "when-offered", "offered": False,
        "reason": "no Metal device on this runner", "decision": "not offered",
    }
    assert [body["options"]["engine"] for body in _imported_requests(tmp_path)] == ["beat-cpu"]
    assert all(row["equal"] for row in section["reopen"]["jobs"].values())


def test_a_required_imported_engine_the_candidate_does_not_offer_fails(
    tmp_path: Path, _in_process: None
) -> None:
    failure, _section = _imported_phase(tmp_path, required=("metal",))

    assert failure is not None
    assert "metal" in failure
    assert "no Metal device on this runner" in failure
    assert _imported_requests(tmp_path) == []


def test_an_imported_solve_reported_by_another_engine_fails(
    tmp_path: Path, _in_process: None
) -> None:
    failure, section = _imported_phase(tmp_path, substitute={"beat-cpu": "metal"})

    assert failure is not None
    assert "reported" in failure
    # What was established before the failure survives it.
    assert section["ingest"]["ingest_id"].startswith("wgi_")


def test_the_written_report_keeps_what_the_imported_phase_established_before_it_failed(
    tmp_path: Path, _in_process: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report main writes, not the phase's own section, after an imported failure.

    The tests above read the section the phase fills; this one reads the report,
    so a gate that attached the section only after a successful phase -- losing
    it exactly when a failure needs it -- fails here. The pin read and the worker
    probe launch processes and are covered by the end-to-end test; here they are
    stubbed so the whole entry point runs in this process.
    """

    monkeypatch.setattr(gate, "check_pins", lambda *_args: {"stubbed": True})
    monkeypatch.setattr(gate, "stop_our_workers", lambda *_args: {"stubbed": True})

    code, report = _run_imported(
        tmp_path, "--imported-engine", "beat-cpu", results_change_on_restart=True
    )

    assert code == 1
    assert "restart" in str(report["error"])
    section = report["imported_return"]
    assert section["ingest"]["ingest_id"].startswith("wgi_")
    assert [row["equal"] for row in section["reopen"]["jobs"].values()] == [False]


def test_results_that_change_across_the_restart_fail(tmp_path: Path, _in_process: None) -> None:
    failure, section = _imported_phase(tmp_path, results_change_on_restart=True)

    assert failure is not None
    assert "restart" in failure
    assert [row["equal"] for row in section["reopen"]["jobs"].values()] == [False]


@pytest.mark.parametrize(
    ("settings", "message"),
    (
        pytest.param({"extra_blocking": True}, "healing-performed", id="unexpected-blocking"),
        pytest.param({"designs_listed": True}, "design registry", id="design-registry-not-empty"),
        pytest.param({"preexisting_jobs": True}, "already lists 1 jobs", id="jobs-at-start"),
    ),
)
def test_a_state_a_fresh_install_should_not_have_stops_before_any_solve(
    tmp_path: Path, _in_process: None, settings: dict[str, object], message: str
) -> None:
    """Acknowledging it, or solving anyway, would hide what caused it."""

    failure, _section = _imported_phase(tmp_path, **settings)

    assert failure is not None
    assert message in failure
    assert _imported_requests(tmp_path) == []


def test_a_reused_data_directory_is_refused_as_a_fresh_install(
    tmp_path: Path, _in_process: None
) -> None:
    (tmp_path / "work" / gate.IMPORTED_DATA_DIR_NAME).mkdir(parents=True)

    failure, _section = _imported_phase(tmp_path)

    assert failure is not None
    assert "fresh" in failure
    assert not (tmp_path / "out" / "imported-server-1.log").exists()


@pytest.mark.parametrize(
    ("settings", "message"),
    (
        pytest.param({"design_known": True}, "missing_design", id="design-already-known"),
        pytest.param({"imported_nan": True}, "non-finite", id="non-finite-numbers"),
        pytest.param({"runs_workspace_elsewhere": True}, "its run workspace as",
                     id="run-workspace-elsewhere"),
        pytest.param({"cad_workspace_elsewhere": True}, "its CAD Link folder as",
                     id="cad-folder-elsewhere"),
        pytest.param({"bad_results_digest": True}, "the server declared",
                     id="results-digest-mismatch"),
        pytest.param({"hide_jobs_before_restart": True}, "after solving",
                     id="unlisted-before-restart"),
        pytest.param({"forget_jobs_on_restart": True}, "not listed",
                     id="unlisted-after-restart"),
    ),
)
def test_the_imported_phase_fails_on_every_check_it_makes(
    tmp_path: Path, _in_process: None, settings: dict[str, object], message: str
) -> None:
    """A check with no failing test is a check nobody has seen fail."""

    failure, _section = _imported_phase(tmp_path, **settings)

    assert failure is not None
    assert message in failure


@pytest.mark.parametrize(
    ("settings", "message", "field"),
    (
        pytest.param({"forget_ingest_on_restart": True},
                     "the ingestion record did not survive the restart",
                     "ingest_record_reopened", id="ingest-record-gone"),
        pytest.param({"ingest_report_changes_on_restart": True},
                     "the ingestion record did not survive the restart",
                     "ingest_record_reopened", id="ingest-record-changed"),
        pytest.param({"forget_cad_workspace_on_restart": True},
                     "the CAD Link folder selection did not survive the restart",
                     "cad_workspace_persisted", id="cad-folder-forgotten"),
    ),
)
def test_what_the_restart_must_find_again_is_checked(
    tmp_path: Path, _in_process: None, settings: dict[str, object], message: str, field: str
) -> None:
    """The results survived in every case here; the failure is the one named."""

    failure, section = _imported_phase(tmp_path, **settings)

    assert failure is not None
    assert message in failure
    reopen = section["reopen"]
    assert reopen[field] is False
    assert all(row["equal"] and row["listed"] for row in reopen["jobs"].values())


# ---------------------------------------------------------------------------
# A failure that stops a server is not replaced by how the server stopped
# ---------------------------------------------------------------------------


def _one_real_server(tmp_path: Path, **settings: object) -> gate.Server:
    """The real ``gate.Server`` on the stub: one launch, for what only a process shows."""

    payload = _stub_payload(tmp_path, **settings)
    _resources, app, interpreter = gate.resolve_payload(payload)
    work = tmp_path / "work"
    return gate.Server(
        interpreter, app, gate.isolated_environment(app, work), work / "data",
        work / "status" / "stop-1", tmp_path / "server.log",
    )


@pytest.mark.skipif(sys.platform == "win32", reason="see the harness test above")
def test_a_server_that_exits_badly_does_not_replace_the_failure_that_stopped_it(
    tmp_path: Path, _quick_timeouts: None
) -> None:
    """The cause comes first; how the server then stopped is kept beside it.

    Leaving the ``with`` block on a failure stops the server, and a server that
    exits non-zero used to raise from ``__exit__`` -- replacing the failure
    that caused the stop with one that only named an exit code.
    """

    with pytest.raises(gate.QualificationError) as raised:
        with _one_real_server(tmp_path, exit_code_on_stop=3):
            raise gate.QualificationError("the failure that ended the block")

    assert str(raised.value) == "the failure that ended the block"
    assert any("exited 3" in note for note in raised.value.__notes__)


def test_a_failure_is_reported_with_what_went_wrong_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report's error is the cause; a note on it is reported beside, not instead."""

    def fail(_arguments: object, _report: object) -> None:
        error = gate.QualificationError("the cause")
        error.add_note("then stopping the server also failed: the packaged server exited 3")
        raise error

    monkeypatch.setattr(gate, "qualify", fail)
    output = tmp_path / "out"

    code = gate.main(
        ["--payload", str(tmp_path), "--work", str(tmp_path / "work"), "--output", str(output)]
    )

    assert code == 1
    report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    assert report["error"] == "the cause"
    assert report["additional_failures"] == [
        "then stopping the server also failed: the packaged server exited 3"
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="see the harness test above")
def test_a_server_that_exits_badly_after_a_good_run_still_fails(
    tmp_path: Path, _quick_timeouts: None
) -> None:
    """Keeping the cause first must not make a bad exit on its own a pass."""

    with pytest.raises(gate.QualificationError, match="exited 3"):
        with _one_real_server(tmp_path, exit_code_on_stop=3):
            pass


# ---------------------------------------------------------------------------
# A name the Windows legacy code page cannot represent
# ---------------------------------------------------------------------------


def test_the_bundle_name_holds_characters_the_windows_legacy_code_page_cannot() -> None:
    """Every earlier character fits in Windows-1252, so a real Windows run could
    pass without ever handling a path that code page cannot spell."""

    with pytest.raises(UnicodeEncodeError):
        gate.IMPORTED_BUNDLE_NAME.encode("cp1252")


def test_a_failure_naming_such_a_path_survives_a_legacy_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report keeps the name exactly; the console line only has to survive.

    A Windows runner's piped stderr uses the ANSI code page, which cannot encode
    an omega, and a print that raised there would lose the verdict line.
    """

    omega = chr(0x3A9)
    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stderr", console)
    output = tmp_path / "out"

    code = gate.main(
        [
            "--payload", str(tmp_path / f"missing {omega}"),
            "--work", str(tmp_path / "work"),
            "--output", str(output),
            "--expected-pin", "hornlab-beat-bem=deadbeef",
        ]
    )
    console.flush()

    assert code == 1
    report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
    assert omega in report["error"]
    printed = console.buffer.getvalue().decode("cp1252")
    assert chr(92) + "u03a9" in printed


# ---------------------------------------------------------------------------
# Every check the imported phase makes has a test that sees it fail
# ---------------------------------------------------------------------------
#
# Eleven of its checks had none: removing any one of them left every test
# green. The result check never asked whether the solve answered the channels
# and frequencies it was asked, and a job the restarted application had
# forgotten failed on fetching its results, not on the listing that says so.
# Everything here runs in this process, like the failure modes above.


def _fixture_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "round.wgreturn"
    shutil.copytree(FIXTURE, copy)
    return copy


def _rewrite_manifest(bundle: Path, change) -> None:
    path = bundle / "wgreturn.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    change(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize(
    ("damage", "message"),
    (
        pytest.param(lambda bundle: (bundle / "wgreturn.json").unlink(), "holds no wgreturn.json", id="no-manifest"),
        pytest.param(lambda bundle: _rewrite_manifest(bundle, lambda manifest: manifest.update(files={})),
                     "lists no files", id="no-files"),
        pytest.param(lambda bundle: (bundle / "assembly.step").unlink(), "which is missing", id="listed-file-missing"),
        pytest.param(lambda bundle: _rewrite_manifest(bundle, lambda manifest: manifest["instances"][0].pop("design_id")),
                     "names no design", id="no-design"),
        pytest.param(lambda bundle: _rewrite_manifest(bundle, lambda manifest: manifest.update(sources=[])),
                     "declares no sources", id="no-sources"),
    ),
)
def test_a_return_bundle_its_own_manifest_does_not_describe_is_refused(
    tmp_path: Path, damage, message: str
) -> None:
    bundle = _fixture_copy(tmp_path)
    damage(bundle)

    with pytest.raises(gate.QualificationError, match=message):
        gate.verify_return_bundle(bundle)


@pytest.mark.parametrize(
    "record",
    (
        pytest.param({"sources": []}, id="no-sources"),
        pytest.param({"sources": [{"id": "source-hf"}], "skipped_source_ids": ["source-hf"]}, id="every-source-skipped"),
        pytest.param({"sources": [{"role": "HF"}]}, id="sources-without-ids"),
    ),
)
def test_a_record_with_no_source_to_drive_is_refused(record: dict[str, object]) -> None:
    with pytest.raises(gate.QualificationError, match="names no source to drive"):
        gate._drive_channels(record)


def test_each_default_channel_drives_the_sources_that_name_it() -> None:
    record = {
        "sources": [
            {"id": "a", "default_drive_channel_id": "drive-main"},
            {"id": "b", "default_drive_channel_id": "drive-main"},
            {"id": "c"},
            {"id": "d", "default_drive_channel_id": "drive-main"},
        ],
        "skipped_source_ids": ["d"],
    }

    assert gate._drive_channels(record) == [
        {"id": "drive-main", "source_ids": ["a", "b"], "motion": "normal"},
        {"id": "drive-c", "source_ids": ["c"], "motion": "normal"},
    ]


def test_a_blocking_finding_without_an_id_cannot_be_acknowledged() -> None:
    with pytest.raises(gate.QualificationError, match="carries no id"):
        gate.blocking_acknowledgements({"report_sha256": REPORT_SHA, "findings": [FRESH, dict(PAINT, id="")]})


def test_a_channel_that_is_not_an_object_fails() -> None:
    result = _imported_result(channels={"drive-hf": [1.0, 2.0]})

    with pytest.raises(gate.QualificationError, match="is not an object"):
        gate.check_imported_result(result, "beat-cpu", channels=["drive-hf"], frequencies=[500.0, 1000.0])


#: A channel solved at one frequency, aligned to it: the axes check alone passes it.
_ONE_FREQUENCY = {
    "frequencies": [500.0],
    "spl_on_axis": {"frequencies": [500.0], "spl": [92.5], "phase_degrees": [10.0]},
    "directivity": {plane: _plane((-6.0, 0.0, -6.0)) for plane in PLANES},
}
_OTHER_FREQUENCIES = dict(
    _GOOD_CHANNEL,
    frequencies=[500.0, 1500.0],
    spl_on_axis=dict(_GOOD_CHANNEL["spl_on_axis"], frequencies=[500.0, 1500.0]),
)


@pytest.mark.parametrize(
    ("channels", "message"),
    (
        pytest.param({"not-the-requested-channel": _GOOD_CHANNEL}, "not-the-requested-channel", id="another-channel"),
        pytest.param({"drive-hf": _GOOD_CHANNEL, "drive-extra": _GOOD_CHANNEL}, "drive-extra", id="an-extra-channel"),
        pytest.param({"drive-hf": _ONE_FREQUENCY}, "frequencies", id="one-frequency"),
        pytest.param({"drive-hf": _OTHER_FREQUENCIES}, "frequencies", id="other-frequencies"),
    ),
)
def test_an_imported_result_must_answer_the_channels_and_frequencies_asked(
    channels: dict[str, object], message: str
) -> None:
    with pytest.raises(gate.QualificationError, match=message):
        gate.check_imported_result(
            _imported_result(channels=channels), "beat-cpu", channels=["drive-hf"], frequencies=[500.0, 1000.0]
        )


@pytest.mark.parametrize(
    ("settings", "message"),
    (
        pytest.param({"result_channel_id": "not-the-requested-channel"}, "not-the-requested-channel",
                     id="result-on-another-channel"),
        pytest.param({"result_frequency_count": 1}, "frequencies", id="result-at-one-frequency"),
        pytest.param({"ingest_without_id": True}, "without an ingest_id", id="ingest-without-id"),
        pytest.param({"solve_without_job_id": True}, "returned no job id", id="solve-without-job-id"),
    ),
)
def test_the_imported_phase_fails_on_an_answer_that_is_not_to_its_request(
    tmp_path: Path, _in_process: None, settings: dict[str, object], message: str
) -> None:
    failure, _section = _imported_phase(tmp_path, **settings)

    assert failure is not None
    assert message in failure


def test_a_job_the_restarted_application_forgot_fails_as_not_listed(
    tmp_path: Path, _in_process: None
) -> None:
    """Its results are gone as well; the failure names what the user would see."""

    failure, section = _imported_phase(
        tmp_path, forget_jobs_on_restart=True, forget_job_results_on_restart=True
    )

    assert failure is not None
    assert "not listed" in failure
    assert "could not be fetched" not in failure
    assert [row["sha256_after"] for row in section["reopen"]["jobs"].values()] == [None]


def test_a_copy_that_differs_from_the_fixture_is_refused_before_it_is_ingested(
    tmp_path: Path, _in_process: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_copytree = shutil.copytree

    def copy_then_damage(source: Path, destination: Path, *args: object, **kwargs: object) -> object:
        copied = real_copytree(source, destination, *args, **kwargs)
        step = Path(destination) / "assembly.step"
        step.write_bytes(step.read_bytes() + b"\n")
        return copied

    monkeypatch.setattr(gate.shutil, "copytree", copy_then_damage)

    failure, section = _imported_phase(tmp_path)

    assert failure is not None
    assert gate.IMPORTED_BUNDLE_NAME in failure
    assert "bytes" in failure
    assert "ingest" not in section
    assert not (tmp_path / "work" / gate.IMPORTED_DATA_DIR_NAME / "ingest-request.json").exists()
