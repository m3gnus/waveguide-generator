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

import json
from pathlib import Path
import subprocess
import sys

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


def _result(**overrides: object) -> dict[str, object]:
    result = {
        "frequencies": [500.0, 1000.0],
        "spl_on_axis": {
            "frequencies": [500.0, 1000.0],
            "spl": [92.5, 94.1],
            "phase_degrees": [10.0, -20.0],
        },
        "directivity": {"horizontal": [[90.0, 88.0], [91.0, 89.0]]},
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

    Selecting `beat-cpu` by name is what stops AUTO substituting; this is what
    catches a substitution that happened anyway.
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
            {"directivity": {"horizontal": [[90.0, 88.0]]}},
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
    zeroed = _result(
        spl_on_axis={"frequencies": [0.0, 0.0], "spl": [0.0, 0.0], "phase_degrees": [0.0, 0.0]},
        directivity={"horizontal": [[0.0, 0.0], [0.0, 0.0]]},
        frequencies=[0.0, 0.0],
    )
    with pytest.raises(gate.QualificationError, match="nothing was solved"):
        gate.check_solve(zeroed, PINS)


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
        for step in body["steps"]:
            assert "continue-on-error" not in step, f"{name}: {step.get('name')}"
            run = step.get("run") or ""
            if "qualify_installed_cpu.py" in run:
                assert "|| true" not in run, name
                assert run.count("qualify_installed_cpu.py") == 1, (
                    f"{name} invokes the qualifier more than once, which is how a "
                    "retry would hide a product failure"
                )


def test_the_gate_adds_no_new_action_reference() -> None:
    """Kept to `run:` steps on purpose.

    A new `uses:` here would need pinning to a commit like every other one, and
    would collide with the branch that is pinning them. Everything this gate
    needs is a shell and the packaged interpreter.
    """

    spec = _workflow()
    # The repositories, not the refs: a sibling branch is replacing every ref
    # with an immutable commit, and this assertion has to survive that merge
    # while still catching a new dependency.
    repositories = {
        str(step["uses"]).split("@", 1)[0]
        for job in spec["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    }
    assert repositories == {
        "actions/checkout",
        "actions/setup-node",
        "actions/upload-artifact",
        "actions/download-artifact",
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

_STUB_SERVER = '''
"""A stand-in for launch/serve.py: the endpoints the qualifier drives."""
import argparse, json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int)
parser.add_argument("--no-browser", action="store_true")
parser.add_argument("--data-dir", type=Path)
parser.add_argument("--status-control", type=Path)
args = parser.parse_args()

SETTINGS = json.loads(Path(__file__).with_name("stub-settings.json").read_text())
STARTED = time.monotonic()
RESULT = SETTINGS["result"]


WORKSPACE = {"path": str(Path(args.data_dir) / "workspace-default")}


def capabilities():
    settled = (time.monotonic() - STARTED) >= SETTINGS["ready_after_s"]
    available = bool(settled) and SETTINGS["ever_ready"]
    return {
        "engines": [
            {"name": "beat-cpu", "available": available,
             "reason": "ready" if available else "preparing"},
            {"name": "beat-metal", "available": False, "reason": "no GPU here"},
        ],
        "cpuPreparationInFlight": not settled,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _send(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send({"status": "ok"})
        elif self.path == "/api/capabilities":
            self._send(capabilities())
        elif self.path == "/api/workspace/path":
            self._send(WORKSPACE)
        elif self.path.startswith("/api/status/"):
            self._send({"status": "complete"})
        elif self.path.startswith("/api/results/"):
            self._send(RESULT)
        else:
            self._send({})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/workspace/select":
            WORKSPACE["path"] = body["path"]
            self._send(WORKSPACE)
        elif self.path == "/api/solve":
            Path(args.data_dir).mkdir(parents=True, exist_ok=True)
            (Path(args.data_dir) / "solve-request.json").write_text(json.dumps(body))
            self._send({"job_id": "job-1"})
        else:
            self._send({})


server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
deadline = time.monotonic() + 120
while time.monotonic() < deadline:
    if args.status_control and args.status_control.exists():
        break
    time.sleep(0.05)
server.shutdown()
'''


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

    payload = _stub_payload(tmp_path, ready_after_s=1.0)
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
