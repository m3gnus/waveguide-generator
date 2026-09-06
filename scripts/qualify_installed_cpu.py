#!/usr/bin/env python3
"""Prove an installed candidate offers BEAT's CPU backend and solves with it.

The RC builder already asks a candidate whether its backends *report* ready and
whether the server answers HTTP. Neither runs a BEAT CPU solve, so a Windows or
Linux installer could pass every existing check while the backend the product
promises on GPU-less machines had never executed a single frequency. This is
that missing step: one installed payload, one explicitly selected CPU backend,
and one real acoustic solve whose numbers are checked.

**What it qualifies.** Whatever ``--payload`` points at, using that payload's
own interpreter and its own ``app`` layer. The intended input is an *installed*
tree -- an unattended per-user Inno install, a non-root ``install.sh``, or a
``ditto`` of the ``.app`` out of the mounted DMG -- so the thing tested is what
a user receives. ``--payload-kind`` records which it was, because "the installer
produced a working application" and "the build directory contains a working
application" are different claims and only one of them is about the installer.

**What it deliberately does not do.** It does not change the product's readiness
rules, add a backend, or teach the application anything about CPU availability;
that work belongs to the engine registry and is not duplicated here. It reads
what the application reports through its own API and holds it to the contract.

Everything it needs is in the standard library and in the packaged runtime, so
it runs with no environment of its own on any of the three platforms.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


#: Small, finite and quick: two frequencies over a coarse mesh. The gate is
#: "did the CPU backend really run and return usable numbers", not "is the
#: result numerically correct" -- that is the solver qualification's job, on
#: fixtures with known answers. A big sweep here would buy nothing and would
#: turn a CI step into a half-hour one.
DESIGN: dict[str, Any] = {
    "formula": "OSSE",
    "L": 120,
    "a": 45,
    "mesh": {
        "angular_segments": 24,
        "length_segments": 12,
        "throat_resolution": 12,
        "mouth_resolution": 25,
        "wall_thickness": 3,
    },
    "simulation": {"sim_type": "freestanding", "f1": 500, "f2": 1000, "num_frequencies": 2},
}
SOLVE_FREQUENCIES = [500.0, 1000.0]

#: The engine row this gate is about, and the contract a solve with it must
#: report back. ``beat-cpu`` is selected by name rather than through AUTO: AUTO
#: is allowed to substitute, and a gate that let it would prove nothing about
#: the CPU path on a host where something else was available.
CPU_ENGINE = "beat-cpu"
CPU_RESULT_CONTRACT = {
    "engine": "hornlab-beat-bem",
    "solver_backend": "beat",
    "beat_backend": "cpu",
}

#: Engine names that mean an accelerator. Recorded, never required.
GPU_ENGINES = ("metal", "metal-bem", "beat-metal", "beat-cuda", "beat-rocm")

STARTUP_TIMEOUT_S = 300.0
CAPABILITY_TIMEOUT_S = 2700.0
CAPABILITY_POLL_S = 5.0
SOLVE_TIMEOUT_S = 1800.0
PROVISION_TIMEOUT_S = 2700.0
SHUTDOWN_TIMEOUT_S = 90.0
PROBE_TIMEOUT_S = 300.0

#: Run inside the packaged interpreter, which is the one with the BEAT package.
#: For every record in *this run's own* registry directory it opens the host's
#: endpoint, completes the ordinary hello/ping exchange, and requires the host
#: to report its own pid before anything is terminated.
#:
#: ``pid_alive`` on a recorded number is not enough, and this exists because of
#: that: after a recorded host exits the operating system may reissue its pid,
#: and an unrelated process would inherit a positive answer and then a signal.
#: A record that will not authenticate is reported and left strictly alone.
_IDENTIFY_AND_STOP = r"""
import json, sys, time
from hornlab_beat_bem import worker_registry as registry
from hornlab_beat_bem.worker_client import find_live_hosts, receive_frame, send_frame

directory, action = sys.argv[1], sys.argv[2]


def identity(record):
    try:
        connection = record.endpoint.connect(timeout=5.0)
    except (OSError, ValueError):
        return None
    try:
        connection.settimeout(10.0)
        send_frame(connection, {"op": "hello", "protocol": registry.PROTOCOL_VERSION,
                                "key_id": record.identifier, "key": record.key,
                                "token": record.token})
        reply = receive_frame(connection)
        if reply is None or str(reply.get("type", "")) != "hello_ok":
            return None
        engine = reply.get("engine_pid")
        send_frame(connection, {"op": "ping"})
        for _ in range(8):
            event = receive_frame(connection)
            if event is None:
                return None
            if str(event.get("type", "")) == "pong":
                if event.get("host_pid") != record.pid:
                    return None
                return {"host_pid": record.pid,
                        "engine_pid": engine if isinstance(engine, int) else -1}
        return None
    except Exception:
        return None
    finally:
        try:
            connection.close()
        except OSError:
            pass


verified, refused = [], []
for host in find_live_hosts(directory):
    found = identity(host)
    if found is None:
        if registry.pid_alive(host.pid):
            refused.append({"pid": host.pid, "reason": "did not authenticate as itself"})
        continue
    verified.append(found)
    if action == "stop":
        registry.terminate_pid(host.pid)

if action == "stop" and verified:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if not any(registry.pid_alive(item["host_pid"]) for item in verified):
            break
        time.sleep(0.2)
    verified = [dict(item, still_alive=registry.pid_alive(item["host_pid"]))
                for item in verified]

print(json.dumps({"verified": verified, "refused": refused}))
"""

#: Read PEP 610 metadata from the packaged interpreter. Asked of the runtime
#: that will run the solve, never of the interpreter running this file: an
#: editable install resolves to a working tree and would qualify whatever
#: happens to be checked out there.
_READ_PINS = (
    "import importlib.metadata as m, json, sys\n"
    "out = {}\n"
    "for name in sys.argv[1:]:\n"
    "    try:\n"
    "        raw = m.distribution(name).read_text('direct_url.json')\n"
    "    except Exception as exc:\n"
    "        out[name] = {'error': f'not installed: {exc}'}\n"
    "        continue\n"
    "    if raw is None:\n"
    "        out[name] = {'error': 'no PEP 610 direct_url.json'}\n"
    "        continue\n"
    "    payload = json.loads(raw)\n"
    "    out[name] = {'commit': payload.get('vcs_info', {}).get('commit_id'),\n"
    "                 'editable': bool(payload.get('dir_info', {}).get('editable', False))}\n"
    "print(json.dumps(out))\n"
)


class QualificationError(RuntimeError):
    """The candidate did not meet the gate. Always fatal, always explained."""


# ---------------------------------------------------------------------------
# Payload layout
# ---------------------------------------------------------------------------


def resolve_payload(payload: Path) -> tuple[Path, Path, Path]:
    """Return (resources, app root, packaged interpreter) for this platform.

    The macOS shape is recognised from the payload itself rather than from
    ``sys.platform``, so a mistake is a clear message instead of a missing file
    three steps later.
    """

    resolved = payload.expanduser().resolve()
    if not resolved.is_dir():
        raise QualificationError(f"--payload is not a directory: {resolved}")
    resources = resolved
    if (resolved / "Contents" / "Resources").is_dir():
        resources = resolved / "Contents" / "Resources"
    app = resources / "app"
    if not app.is_dir():
        raise QualificationError(
            f"no app layer at {app}. Point --payload at an installed application "
            "root (the directory holding 'app' and 'runtime'), or at a macOS .app."
        )
    windows = resources / "runtime" / "python.exe"
    posix = resources / "runtime" / "bin" / "python3.13"
    interpreter = windows if windows.is_file() else posix
    if not interpreter.is_file():
        raise QualificationError(
            f"no packaged interpreter at {windows} or {posix}; this payload has no runtime layer"
        )
    return resources, app, interpreter


def isolated_environment(app: Path, work: Path) -> dict[str, str]:
    """The environment the packaged launchers build, pointed at this run's tree.

    ``WG2_BUNDLE`` and ``WG2_APP_ROOT`` are what the native launchers set, so
    the application behaves as an installed product rather than a checkout. The
    cache redirections are the launchers' too, and they matter for more than
    tidiness on macOS: the bundle is ad-hoc signed and must stay byte-identical
    after it runs, so bytecode or a numba kernel cache written beside its
    sources breaks the seal.

    Everything is redirected into *this run's* directory rather than the user's,
    which is stricter than the launcher and leaves the machine as it was found.
    ``XDG_DOCUMENTS_DIR`` moves the default workspace off POSIX hosts; Windows
    has no supported equivalent, so there the workspace default is recorded
    rather than silently accepted, and no export is written by this gate.
    """

    caches = work / "caches"
    documents = work / "documents"
    for directory in (caches / "pycache", caches / "numba", caches / "matplotlib", documents):
        directory.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        environment.pop(name, None)
    environment.update(
        WG2_BUNDLE="1",
        WG2_APP_ROOT=str(app),
        PYTHONPYCACHEPREFIX=str(caches / "pycache"),
        NUMBA_CACHE_DIR=str(caches / "numba"),
        MPLCONFIGDIR=str(caches / "matplotlib"),
        HORNLAB_BEAT_RUNTIME_DIR=str(work / "beat-runtime"),
        HORNLAB_BEAT_WORKER_REGISTRY=str(work / "beat-registry"),
    )
    if platform.system() != "Windows":
        environment["XDG_DOCUMENTS_DIR"] = str(documents)
    return environment


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def check_manifest(app: Path, expected: dict[str, str | None]) -> dict[str, Any]:
    """Hold the layer's own manifest to what the build says it produced.

    Every field is optional and each is reported as checked or not supplied, so
    a run that asserted nothing cannot read afterwards as a run that asserted
    and passed.
    """

    path = app / "APP-MANIFEST.json"
    if not path.is_file():
        raise QualificationError(
            f"{path} is missing; this gate qualifies a built app layer, not a checkout"
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    report: dict[str, Any] = {
        "manifest": {
            key: manifest.get(key)
            for key in ("version", "commit", "runtimeId", "treeSha256")
        }
    }
    for field, value in expected.items():
        if value is None:
            report[field] = "not supplied"
            continue
        actual = str(manifest.get(field, ""))
        if actual != value:
            raise QualificationError(
                f"APP-MANIFEST {field} is {actual!r}, expected {value!r}"
            )
        report[field] = "checked"
    return report


def check_pins(
    interpreter: Path, expected: dict[str, str], environment: dict[str, str]
) -> dict[str, Any]:
    """Every expected module commit, read from the packaged runtime."""

    if not expected:
        raise QualificationError(
            "no --expected-pin was supplied; this gate exists to assert provenance"
        )
    completed = subprocess.run(  # noqa: S603 - packaged interpreter, fixed program
        [str(interpreter), "-c", _READ_PINS, *sorted(expected)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_S,
        check=False,
    )
    if completed.returncode != 0:
        raise QualificationError(f"could not read installed pins: {completed.stderr[-2000:]}")
    found = json.loads(completed.stdout.strip().splitlines()[-1])
    for name, commit in sorted(expected.items()):
        entry = found.get(name, {})
        if entry.get("error"):
            raise QualificationError(f"pin {name}: {entry['error']}")
        if entry.get("editable"):
            raise QualificationError(
                f"pin {name} is installed editable, which is not a packaged runtime"
            )
        if str(entry.get("commit")) != commit:
            raise QualificationError(
                f"pin {name} is {entry.get('commit')!r}, expected {commit!r}"
            )
    return {"checked": sorted(expected), "found": found}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def http(base: str, path: str, body: Any = None, *, timeout: float = 120.0) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(f"{base}{path}", data=data, headers=headers)  # noqa: S310 - loopback
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback
        payload = response.read()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except ValueError:
        return payload


def wait_for(call: Any, seconds: float, what: str, *, interval: float = 1.0) -> Any:
    """Poll until *call* returns something truthy, or fail saying what was awaited."""

    deadline = time.monotonic() + seconds
    last: str = "it never answered"
    while time.monotonic() < deadline:
        try:
            answer = call()
        except (HTTPError, URLError, OSError, ValueError) as exc:
            last = f"{type(exc).__name__}: {exc}"
        else:
            if answer:
                return answer
            last = "it answered, but not with what was awaited"
        time.sleep(interval)
    raise QualificationError(f"timed out after {seconds:.0f}s waiting for {what} ({last})")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# ---------------------------------------------------------------------------
# One run of the packaged server
# ---------------------------------------------------------------------------


class Server:
    """One ``launch/serve.py`` run, stopped the way the product stops it."""

    def __init__(
        self,
        interpreter: Path,
        app: Path,
        environment: dict[str, str],
        data_dir: Path,
        control: Path,
        log_path: Path,
    ) -> None:
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log_path = log_path
        self._control = control
        self._control.parent.mkdir(parents=True, exist_ok=True)
        self._log = log_path.open("w", encoding="utf-8")
        self._process = subprocess.Popen(  # noqa: S603 - packaged interpreter, fixed program
            [
                str(interpreter),
                str(app / "launch" / "serve.py"),
                "--port",
                str(self.port),
                "--no-browser",
                "--data-dir",
                str(data_dir),
                "--status-control",
                str(control),
            ],
            cwd=str(app),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )

    def __enter__(self) -> Server:
        try:
            wait_for(
                lambda: http(self.base, "/health"),
                STARTUP_TIMEOUT_S,
                f"the packaged server to answer /health (log: {self.log_path.name})",
            )
        except QualificationError:
            self.stop(force=True)
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def stop(self, *, force: bool = False) -> None:
        """Ask through the status-control file, then wait, then insist.

        The control file is how the status window asks the server to stop, so it
        is how this stops it: terminating instead would qualify a shutdown path
        the product does not use. Only this run's own child is ever signalled.
        """

        if self._process.poll() is None:
            self._control.touch()
            try:
                self._process.wait(timeout=SHUTDOWN_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self._process.terminate()
                try:
                    self._process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=30)
        self._log.close()
        if not force and self._process.returncode not in (0, None):
            raise QualificationError(
                f"the packaged server exited {self._process.returncode}; see {self.log_path}"
            )

    def capabilities(self) -> dict[str, Any]:
        return http(self.base, "/api/capabilities", timeout=180.0)

    def solve(self, frequencies: list[float], engine: str) -> str:
        return http(
            self.base,
            "/api/solve",
            {
                "design": DESIGN,
                "options": {
                    "engine": engine,
                    "solver_mode": "full_3d",
                    "frequencies_hz": frequencies,
                },
            },
        )["job_id"]

    def completed(self, job: str) -> Any:
        def check() -> Any:
            status = http(self.base, f"/api/status/{job}")
            state = str(status.get("status"))
            if state in ("error", "cancelled"):
                raise QualificationError(
                    f"job {job} ended {state}: {json.dumps(status)[:1500]}"
                )
            return status if state == "complete" else None

        wait_for(check, SOLVE_TIMEOUT_S, f"job {job} to complete", interval=2.0)
        return http(self.base, f"/api/results/{job}", timeout=300.0)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def engine_row(capabilities: dict[str, Any], name: str) -> dict[str, Any]:
    for row in capabilities.get("engines", []):
        if row.get("name") == name:
            return row
    return {}


def await_cpu_row(server: Server, output: Path) -> dict[str, Any]:
    """Wait for the application's own preparation to settle, and report it.

    The packaged application provisions the CPU runtime itself, in a background
    thread, on the platforms whose users need it -- that is the supported
    product path and this waits for it rather than reaching around it. The wait
    ends when the row becomes available or when the application says it has
    stopped preparing, so a host that will never offer it fails in bounded time
    with the application's own reason rather than on a timeout.
    """

    deadline = time.monotonic() + CAPABILITY_TIMEOUT_S
    samples: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        capabilities = server.capabilities()
        row = engine_row(capabilities, CPU_ENGINE)
        in_flight = bool(capabilities.get("cpuPreparationInFlight"))
        last = {
            "available": row.get("available"),
            "reason": row.get("reason"),
            "cpuPreparationInFlight": in_flight,
        }
        samples.append(dict(last, at=round(time.monotonic(), 1)))
        if row.get("available") is True:
            (output / "cpu-capability-samples.json").write_text(
                json.dumps(samples, indent=2), encoding="utf-8"
            )
            return {"settled": "available", "samples": len(samples), "final": last}
        if not in_flight and samples and len(samples) > 1:
            (output / "cpu-capability-samples.json").write_text(
                json.dumps(samples, indent=2), encoding="utf-8"
            )
            return {"settled": "not-preparing", "samples": len(samples), "final": last}
        time.sleep(CAPABILITY_POLL_S)
    (output / "cpu-capability-samples.json").write_text(
        json.dumps(samples, indent=2), encoding="utf-8"
    )
    return {"settled": "timeout", "samples": len(samples), "final": last}


def provision_cpu(
    interpreter: Path, app: Path, environment: dict[str, str], output: Path
) -> dict[str, Any]:
    """Run the documented provisioning command, and say where its Julia came from.

    This is the command the application's own unavailable reason tells a user to
    run, so it is the supported explicit path rather than a way around one. It
    is only reached when the application did not prepare the runtime itself,
    which on macOS is by design.

    ``HORNLAB_BEAT_RUNTIME_DIR`` is this run's own directory, so no previously
    provisioned record is read -- but discovery also consults
    ``HORNLAB_BEAT_JULIA`` and ``PATH``. Reusing a Julia already on the host is
    real evidence that the packaged application can provision and solve; it is
    **not** evidence about a clean machine, so which happened is recorded here
    and must not be quoted as the other.
    """

    completed = subprocess.run(  # noqa: S603 - packaged interpreter, documented module
        [str(interpreter), "-m", "hornlab_beat_bem.provision", "--backend", "cpu"],
        cwd=str(app),
        env=environment,
        capture_output=True,
        text=True,
        timeout=PROVISION_TIMEOUT_S,
        check=False,
    )
    transcript = completed.stdout + completed.stderr
    log = output / "cpu-provision.log"
    log.write_text(transcript, encoding="utf-8")
    if completed.returncode != 0:
        raise QualificationError(
            f"CPU provisioning failed ({completed.returncode}); see {log.name}"
        )
    runtime_dir = environment["HORNLAB_BEAT_RUNTIME_DIR"]
    state_path = Path(runtime_dir) / "state-cpu.json"
    state: dict[str, Any] = {}
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    julia = str(state.get("julia_executable", ""))
    inside = bool(julia) and julia.startswith(runtime_dir)
    downloaded = "download" in transcript.lower()
    return {
        "status": state.get("status"),
        "julia_executable": julia,
        "julia_env_var_set": bool(environment.get("HORNLAB_BEAT_JULIA")),
        "julia_inside_this_runs_runtime_dir": inside,
        "claim": (
            "a Julia was provisioned inside this run's own runtime directory, so this is "
            "fresh-state provisioning"
            if inside and downloaded
            else "a Julia already present on this host was reused; this says nothing about "
            "a machine with no Julia installed"
        ),
    }


def gpu_independence(capabilities: dict[str, Any], solve: dict[str, Any]) -> dict[str, Any]:
    """What this run establishes about the CPU path not needing an accelerator.

    Three facts, and the claim is only ever as strong as the host allows. The
    engine was selected **by name**, so AUTO could not substitute anything. The
    solve reported ``beat_backend`` ``cpu``, which is the backend that ran, not
    the one that was asked for. And the accelerator rows the application itself
    reports are recorded: on a host where none is available, a CPU solve is
    independence by construction; on a host where one is, it is evidence that
    selecting CPU keeps CPU.
    """

    accelerators = {
        name: {
            "available": engine_row(capabilities, name).get("available"),
            "reason": engine_row(capabilities, name).get("reason"),
        }
        for name in GPU_ENGINES
        if engine_row(capabilities, name)
    }
    any_available = any(row.get("available") is True for row in accelerators.values())
    return {
        "engine_selected_by_name": CPU_ENGINE,
        "beat_backend_reported_by_the_solve": solve.get("beat_backend"),
        "accelerator_rows": accelerators,
        "any_accelerator_available": any_available,
        "claim": (
            "an accelerator was available on this host and the CPU backend still ran, so "
            "selecting CPU is not silently upgraded"
            if any_available
            else "no accelerator was available on this host, so the CPU backend solved "
            "without one; this is the GPU-less case the gate exists for"
        ),
    }


def _numbers(values: Any, where: str, bad: list[str]) -> tuple[int, int]:
    """Count every number under *values*, and name every non-finite one.

    ``None`` is left alone: the result contract says a missing value stays
    missing rather than being interpolated, so a null is documented absence.
    ``NaN`` and infinities are not -- they are a solve that produced nothing
    usable while still answering, which is exactly what this has to catch.
    """

    if values is None or isinstance(values, bool):
        return 0, 0
    if isinstance(values, (int, float)):
        if not math.isfinite(values):
            bad.append(f"{where}={values!r}")
            return 1, 0
        return 1, 1 if values != 0 else 0
    if isinstance(values, list):
        total = non_zero = 0
        for index, item in enumerate(values):
            count, finite = _numbers(item, f"{where}[{index}]", bad)
            total += count
            non_zero += finite
        return total, non_zero
    if isinstance(values, dict):
        total = non_zero = 0
        for key, item in values.items():
            count, finite = _numbers(item, f"{where}.{key}", bad)
            total += count
            non_zero += finite
        return total, non_zero
    return 0, 0


def check_axes(result: dict[str, Any]) -> dict[str, Any]:
    """The axes exist, are aligned to the frequency axis, and are all finite."""

    frequencies = result.get("frequencies")
    if not isinstance(frequencies, list) or not frequencies:
        raise QualificationError("the result carries no frequency axis")
    bad: list[str] = []
    total, non_zero = _numbers(
        {key: result.get(key) for key in ("frequencies", "spl_on_axis", "directivity")},
        "result",
        bad,
    )
    if bad:
        raise QualificationError(f"the result carries non-finite numbers: {bad[:10]}")
    if non_zero == 0:
        raise QualificationError("every number in the result is zero; nothing was solved")

    on_axis = result.get("spl_on_axis") or {}
    misaligned = {
        key: len(values)
        for key in ("frequencies", "spl", "phase_degrees")
        if isinstance(values := on_axis.get(key), list) and len(values) != len(frequencies)
    }
    if misaligned:
        raise QualificationError(
            f"spl_on_axis is not aligned to the {len(frequencies)} frequencies: {misaligned}"
        )
    directivity = result.get("directivity") or {}
    planes = {}
    for plane, rows in directivity.items():
        if isinstance(rows, list):
            if len(rows) != len(frequencies):
                raise QualificationError(
                    f"directivity plane {plane!r} has {len(rows)} rows for "
                    f"{len(frequencies)} frequencies"
                )
            planes[plane] = len(rows)
    return {
        "frequencies": len(frequencies),
        "numbers_checked": total,
        "finite_non_zero": non_zero,
        "directivity_planes": planes,
    }


def check_solve(result: dict[str, Any], expected_pins: dict[str, str]) -> dict[str, Any]:
    """The solve reported the CPU backend, drifted from nothing, and has numbers."""

    metadata = result.get("metadata", {})
    actual = {key: metadata.get(key) for key in CPU_RESULT_CONTRACT}
    if actual != CPU_RESULT_CONTRACT:
        raise QualificationError(
            f"a {CPU_ENGINE!r} solve reported {actual!r}, expected {CPU_RESULT_CONTRACT!r}"
        )
    provenance = result.get("provenance", {})
    drift = provenance.get("dependency_drift")
    if drift != []:
        raise QualificationError(f"the application reported dependency drift: {drift}")
    shas = provenance.get("dependency_shas") or {}
    mismatched = {
        name: {"solve_reported": shas.get(name), "expected": commit}
        for name, commit in expected_pins.items()
        if name in shas and str(shas[name]) != commit
    }
    if mismatched:
        raise QualificationError(f"the solve ran against other module commits: {mismatched}")
    return {
        "engine": metadata.get("engine"),
        "solver_backend": metadata.get("solver_backend"),
        "beat_backend": metadata.get("beat_backend"),
        "dependency_drift": drift,
        "pins_cross_checked": sorted(set(expected_pins) & set(shas)),
        "axes": check_axes(result),
    }


def stop_our_workers(
    interpreter: Path, environment: dict[str, str], output: Path
) -> dict[str, Any]:
    """Stop the BEAT hosts this run started, and only those.

    The registry is this run's own directory, and every record in it must
    authenticate as itself over its own endpoint before it is signalled. A
    record that will not is reported and left alone -- a recorded pid may since
    have been reissued to something that has nothing to do with this.
    """

    completed = subprocess.run(  # noqa: S603 - packaged interpreter, fixed program
        [
            str(interpreter),
            "-c",
            _IDENTIFY_AND_STOP,
            environment["HORNLAB_BEAT_WORKER_REGISTRY"],
            "stop",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_S,
        check=False,
    )
    (output / "worker-cleanup.log").write_text(
        completed.stdout + completed.stderr, encoding="utf-8"
    )
    if completed.returncode != 0:
        return {"error": completed.stderr[-1000:]}
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        return {"error": f"unreadable cleanup output: {exc}"}


def qualify(arguments: argparse.Namespace) -> dict[str, Any]:
    resources, app, interpreter = resolve_payload(arguments.payload)
    work = arguments.work.expanduser().resolve()
    output = arguments.output.expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    data_dir = work / "data"
    environment = isolated_environment(app, work)
    expected_pins = pins_from_file(arguments.pins_json)
    expected_pins.update(arguments.expected_pin or {})

    report: dict[str, Any] = {
        "payload": str(resources),
        "payload_kind": arguments.payload_kind,
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "interpreter": str(interpreter),
        "isolated": {
            "data_dir": str(data_dir),
            "beat_runtime_dir": environment["HORNLAB_BEAT_RUNTIME_DIR"],
            "worker_registry": environment["HORNLAB_BEAT_WORKER_REGISTRY"],
        },
    }
    expected_identity = expectations_from_build_manifest(arguments.build_manifest)
    for field, value in (
        ("version", arguments.expected_version),
        ("commit", arguments.expected_commit),
        ("treeSha256", arguments.expected_tree_sha256),
    ):
        if value is not None:
            expected_identity[field] = value
    report["app_manifest"] = check_manifest(app, expected_identity)
    report["pins"] = check_pins(interpreter, expected_pins, environment)

    # A control file per launch, never one shared between them. The server
    # watches for that file appearing and stops when it does, so a second launch
    # pointed at the first one's file would be told to stop before it had
    # finished starting -- which is exactly what happened the first time this
    # ran against a stub.
    control = work / "status" / "stop-1"
    # One server for the whole gate wherever possible: the row the application
    # published and the solve that uses it are then the same process, so
    # "offered" cannot describe one run while the solve describes another.
    with Server(interpreter, app, environment, data_dir, control, output / "server.log") as server:
        report["cpu_preparation"] = await_cpu_row(server, output)
        capabilities = server.capabilities()
        offered = engine_row(capabilities, CPU_ENGINE).get("available") is True
        if offered:
            report["cpu_offered"] = {
                "available": True,
                "after": "the application's own preparation",
            }
            result = server.completed(server.solve(SOLVE_FREQUENCIES, CPU_ENGINE))

    if not offered:
        # The application did not prepare the CPU runtime itself. That is by
        # design on macOS, and elsewhere it is what the documented command
        # exists for -- so run that command and ask the application again,
        # rather than deciding readiness on the application's behalf.
        report["explicit_provisioning"] = provision_cpu(interpreter, app, environment, output)
        with Server(
            interpreter,
            app,
            environment,
            data_dir,
            work / "status" / "stop-2",
            output / "server-2.log",
        ) as server:
            capabilities = server.capabilities()
            row = engine_row(capabilities, CPU_ENGINE)
            if row.get("available") is not True:
                raise QualificationError(
                    f"{CPU_ENGINE} is still not offered after the documented provisioning "
                    f"command succeeded: {row.get('reason')!r}"
                )
            report["cpu_offered"] = {"available": True, "after": "explicit provisioning"}
            result = server.completed(server.solve(SOLVE_FREQUENCIES, CPU_ENGINE))

    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    report["solve"] = check_solve(result, expected_pins)
    report["gpu_independence"] = gpu_independence(capabilities, report["solve"])
    report["worker_cleanup"] = stop_our_workers(interpreter, environment, output)
    return report


def pins_from_file(path: Path | None) -> dict[str, str]:
    """Every module commit the build declared, read from ``pins.json``.

    Preferred over listing them on the command line, because the point of the
    check is that the payload matches what *this build* pinned: a hand-typed
    list can be edited to match whatever was found, and a file that the build
    also consumed cannot.
    """

    if path is None:
        return {}
    payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    modules = payload.get("modules")
    if not isinstance(modules, dict) or not modules:
        raise QualificationError(f"{path} declares no modules")
    pins: dict[str, str] = {}
    for name, entry in sorted(modules.items()):
        sha = entry.get("sha") if isinstance(entry, dict) else None
        if not isinstance(sha, str) or not sha:
            raise QualificationError(f"{path} gives no sha for {name!r}")
        pins[str(name)] = sha
    return pins


def expectations_from_build_manifest(path: Path | None) -> dict[str, str | None]:
    """The version, commit and tree digest the build recorded for its app layer.

    Compared against the manifest inside the payload, so installing the wrong
    artifact -- a stale one, or another platform's -- is caught before anything
    is started rather than after a solve has been attributed to it.
    """

    if path is None:
        return {"version": None, "commit": None, "treeSha256": None}
    manifest = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    expected: dict[str, str | None] = {}
    for field in ("version", "commit", "treeSha256"):
        value = manifest.get(field)
        if not isinstance(value, str) or not value:
            raise QualificationError(f"{path} records no {field}")
        expected[field] = value
    return expected


def parse_pins(values: list[str] | None) -> dict[str, str]:
    pins: dict[str, str] = {}
    for item in values or []:
        name, separator, commit = item.partition("=")
        if not separator or not name.strip() or not commit.strip():
            raise SystemExit(f"--expected-pin wants name=commit, got {item!r}")
        pins[name.strip()] = commit.strip()
    return pins


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--payload",
        type=Path,
        required=True,
        help="installed application root (holding app/ and runtime/), or a macOS .app",
    )
    parser.add_argument(
        "--payload-kind",
        default="unspecified",
        help="how the payload was produced: installer, tarball, dmg, or build-directory",
    )
    parser.add_argument("--work", type=Path, required=True, help="private scratch directory")
    parser.add_argument("--output", type=Path, required=True, help="logs and the JSON report")
    parser.add_argument(
        "--expected-pin",
        action="append",
        metavar="NAME=COMMIT",
        help="a module distribution name and the commit the build pinned (repeatable)",
    )
    parser.add_argument(
        "--pins-json",
        type=Path,
        help="the build's pins.json; every module in it becomes an expected pin",
    )
    parser.add_argument(
        "--build-manifest",
        type=Path,
        help="the update-app-*.manifest.json the build produced, as the expected identity",
    )
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-tree-sha256")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    arguments.expected_pin = parse_pins(arguments.expected_pin)
    started = time.time()
    try:
        report = qualify(arguments)
    except QualificationError as exc:
        failure = {"qualified": False, "error": str(exc), "seconds": round(time.time() - started)}
        arguments.output.mkdir(parents=True, exist_ok=True)
        (arguments.output / "cpu-qualification.json").write_text(
            json.dumps(failure, indent=2), encoding="utf-8"
        )
        print(f"CPU qualification FAILED: {exc}", file=sys.stderr)
        return 1
    report["qualified"] = True
    report["seconds"] = round(time.time() - started)
    (arguments.output / "cpu-qualification.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
