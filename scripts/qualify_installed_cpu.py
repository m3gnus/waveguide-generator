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

**The imported return, on a fresh install.** With ``--imported-engine`` it also
runs the path a CAD Link user takes on a new machine, against the same
payload and without Fusion: a fresh data directory, a committed ``.wgreturn``
copied into the selected workspace under a name with spaces and non-ASCII
characters, ingested through ``/api/cadlink/ingest``, solved through
``/api/solve`` on each named engine, and fetched again after the server is
stopped and started on the same data directory. WG has no Save command; the
jobs store is what keeps a result, so reopening it is the save-and-reopen
check.

Everything it needs is in the standard library and in the packaged runtime, so
it runs with no environment of its own on any of the three platforms.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import traceback
import time
from typing import Any
import unicodedata
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
#: report back. ``beat-cpu`` is selected by name rather than through AUTO, so
#: AUTO's preference order cannot hand the solve to an accelerator on a host
#: that has one. Naming it does not rule out a substitution, though: the server
#: still replaces a named engine it finds unavailable with what AUTO would have
#: chosen (``resolve_submission`` in ``server/jobs/runtime.py``). What catches
#: a swap is this gate: it refuses to solve unless the application offers this
#: row, and ``check_solve`` compares the contract below with what the solve
#: reported.
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
#: Ingestion meshes the return in a child process before it answers. A cold
#: runner importing gmsh and OCC for the first time spends most of this.
INGEST_TIMEOUT_S = 900.0

#: The imported-return phase, run only when ``--imported-engine`` names one.
#:
#: Every name it creates holds a space and non-ASCII characters, and it is
#: created by this script rather than by the workflow's shell, so no platform's
#: quoting can drop them before the application sees them. They are escaped so
#: the source stays ASCII and nothing can normalise them in transit: an en
#: dash and an A-umlaut; an a-ring; an o-umlaut and A-ring, A-umlaut, O-umlaut,
#: then a Greek capital omega and a CJK ideograph. Every character before those
#: two fits in Windows-1252; they do not, so a Windows run has to handle a
#: bundle path the legacy code page cannot spell.
IMPORTED_DATA_DIR_NAME = "App Data \u2013 \u00c4rende 1"
IMPORTED_WORKSPACE_NAME = "Kopia fr\u00e5n annan dator"
IMPORTED_BUNDLE_NAME = "H\u00f6gtalare \u00c5\u00c4\u00d6 \u03a9 \u97f3.wgreturn"
#: The committed linked return the phase imports; its README says how it was made.
DEFAULT_IMPORTED_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "imported-return" / "round.wgreturn"
)
#: Two frequencies, like the parametric solve. The question is whether the
#: installed payload imports, solves and keeps an imported result, not whether
#: the answer is accurate: ``scripts/qualify_imported_same_mesh.py`` owns that.
IMPORTED_FREQUENCIES = [1000.0, 2000.0]
#: The sizes the qualification fixtures mesh with (``mesh_sizes()`` in
#: ``scripts/imported_ingest_fixtures.py``). A source takes the return's own
#: ``suggested_resolution_mm`` when it gives one.
IMPORTED_RIGID_SIZE_MM = 20.0
IMPORTED_TRANSITION_MM = 30.0
IMPORTED_SOURCE_SIZE_MM = 8.0
#: The only blocking findings a fresh install may report for the fixture, as
#: kind -> the verdict it must carry (``None``: any). Freshness has to say
#: ``missing_design``, which is the evidence that the design registry was
#: empty. The fixture tags geometry rather than paint, so
#: ``source-paint-missing`` is expected. Anything else that blocks is a
#: regression, and acknowledging it would hide one.
FRESH_INSTALL_BLOCKING: dict[str, str | None] = {
    "freshness": "missing_design",
    "source-paint-missing": None,
}

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
from pathlib import Path

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


# The directory the package would use on its own. If the override did not take
# effect this is the user's own registry, and nothing below may touch it.
effective = str(registry.worker_dir())
contained = Path(effective).resolve() == Path(directory).resolve()
if not contained:
    print(json.dumps({"verified": [], "refused": [], "effective_worker_dir": effective,
                      "contained": False,
                      "note": "refused to read or signal a registry outside the gate tree"}))
    raise SystemExit(0)

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

print(json.dumps({"verified": verified, "refused": refused,
                  "effective_worker_dir": effective, "contained": True}))
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

    Everything this gate can redirect goes into *this run's* directory rather
    than the user's, which is stricter than the launcher and leaves the machine
    as it was found. That includes Julia's depot, which nothing else sets: see
    ``JULIA_DEPOT_PATH`` below for what it buys and what it changes.
    ``XDG_DOCUMENTS_DIR`` moves the default workspace off POSIX hosts; Windows
    has no supported equivalent, so there the workspace default is recorded
    rather than silently accepted, and no export is written by this gate.
    """

    caches = work / "caches"
    documents = work / "documents"
    for directory in (
        caches / "pycache",
        caches / "numba",
        caches / "matplotlib",
        documents,
        work / "julia-depot",
    ):
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
        # HORNLAB_BEAT_RUNTIME_DIR isolates the *provisioning record*, not
        # Julia's own package store. The pinned package launches Julia with
        # ``{**os.environ, ...}`` and never sets JULIA_DEPOT_PATH, so without
        # this a run reads and writes the caller's ``~/.julia`` -- and a report
        # claiming every cache was isolated would be wrong.
        #
        # It is also a stricter test than a user's machine gets, and that is
        # worth naming rather than glossing: with an empty depot the
        # application's own preparation has to fetch everything, so a pass here
        # is closer to clean-machine evidence, and a run that reuses a populated
        # ``~/.julia`` is not.
        JULIA_DEPOT_PATH=str(work / "julia-depot"),
        # The name the pinned package actually reads is WORKER_DIR_ENV_VAR, and
        # it is HORNLAB_BEAT_WORKER_DIR. An invented name is not an isolation
        # failure that shows up as an error: the override is simply ignored, the
        # workers land in the *user's* default registry, and the directory this
        # gate then reports as isolated is one nothing ever wrote to. The
        # containment check below exists because that failure is silent.
        HORNLAB_BEAT_WORKER_DIR=str(work / "beat-registry"),
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


def http_bytes(base: str, path: str, *, timeout: float = 120.0) -> tuple[bytes, dict[str, str]]:
    """The exact bytes of a response and its headers, for comparing stored results."""

    request = Request(f"{base}{path}", headers={"Accept": "application/json"})  # noqa: S310 - loopback
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback
        content = response.read()
        headers = {name.lower(): value for name, value in response.headers.items()}
    return content, headers


def api(base: str, path: str, body: Any = None, *, what: str, timeout: float = 120.0) -> Any:
    """``http``, with a refusal turned into a failure that says what was refused.

    The application answers a refused ingest or solve with a body naming the
    reason. An ``HTTPError`` on its own reports only the status, which is the
    least useful thing to find in a failed gate's report.
    """

    try:
        return http(base, path, body, timeout=timeout)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:2000]
        raise QualificationError(f"{what} was refused with HTTP {exc.code}: {detail}") from exc
    except (URLError, OSError) as exc:
        raise QualificationError(f"{what} failed: {type(exc).__name__}: {exc}") from exc


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

    def __exit__(self, _type: object, exc: BaseException | None, _traceback: object) -> None:
        if exc is None:
            self.stop()
            return
        # The failure that ended the block is what a reader needs first.
        # Stopping can fail too -- a non-zero exit, a stop that will not
        # finish -- and raising that here would replace the cause with a
        # symptom of it. It is kept as a note on the original instead, which
        # ``main`` reports beside the error.
        try:
            self.stop()
        except Exception as problem:  # noqa: BLE001 - kept as a note, not swallowed
            exc.add_note(f"then stopping the server also failed: {problem}")

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

    def await_complete(self, job: str) -> Any:
        def check() -> Any:
            status = http(self.base, f"/api/status/{job}")
            state = str(status.get("status"))
            if state in ("error", "cancelled"):
                raise QualificationError(
                    f"job {job} ended {state}: {json.dumps(status)[:1500]}"
                )
            return status if state == "complete" else None

        return wait_for(check, SOLVE_TIMEOUT_S, f"job {job} to complete", interval=2.0)

    def completed(self, job: str) -> Any:
        self.await_complete(job)
        return http(self.base, f"/api/results/{job}", timeout=300.0)

    def stored_results(self, job: str) -> tuple[bytes, str]:
        """The exact stored bytes of a job's results, and their SHA-256.

        The route serves the bytes the jobs store holds and names their digest
        in a header. The digest is computed here as well, and a disagreement
        fails, so a comparison across a restart compares what was stored rather
        than what either side said about it.
        """

        try:
            content, headers = http_bytes(self.base, f"/api/results/{job}", timeout=300.0)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:1000]
            raise QualificationError(
                f"the results of job {job} could not be fetched: HTTP {exc.code} {detail}"
            ) from exc
        digest = hashlib.sha256(content).hexdigest()
        declared = headers.get("x-wg-results-sha256")
        if declared is not None and declared != digest:
            raise QualificationError(
                f"job {job}'s results hash to {digest}, but the server declared {declared}"
            )
        return content, digest


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _inside(candidate: object, parent: Path) -> bool:
    """Is *candidate* the same path as *parent*, or under it?

    Both sides are resolved. Resolving only one is how this reads False on
    macOS, where a temporary root is reached through ``/var`` and reported
    through ``/private/var`` -- and a run would then refuse its own correctly
    isolated workspace. Comparing parents rather than string prefixes keeps
    ``/tmp/run-elsewhere`` from counting as inside ``/tmp/run``.
    """

    if not isinstance(candidate, str) or not candidate:
        return False
    try:
        resolved = Path(candidate).resolve()
        anchor = Path(parent).resolve()
    except OSError:
        return False
    return resolved == anchor or anchor in resolved.parents


def workspace_isolation(base: str, work: Path) -> dict[str, Any]:
    """Put this run's workspace inside its own tree, before anything solves.

    ``--data-dir`` is not this. ``launch/serve.py`` resolves the workspace
    through ``documents_root()``, which on POSIX honours ``XDG_DOCUMENTS_DIR``
    -- set in the environment above -- and on Windows has no supported
    override at all. So on Windows the startup default really is the user's
    Documents, and the workspace is moved into this run's tree through the same
    API the application's own settings use, before the first solve. Asserting
    that ``WG2_DATA_DIR`` alone isolates the workspace would be false there.

    Called before solving on purpose: a run that wrote its first result into
    somebody's Documents and only then checked would already have done the
    thing this exists to prevent.
    """

    documents = (work / "documents").resolve()
    before = http(base, "/api/workspace/path")
    started_inside = _inside(before.get("path"), documents)
    established = False
    if not started_inside:
        target = work / "workspace"
        target.mkdir(parents=True, exist_ok=True)
        http(base, "/api/workspace/select", {"path": str(target)})
        established = True
    current = http(base, "/api/workspace/path")
    if not _inside(current.get("path"), work):
        raise QualificationError(
            f"the workspace is outside this run's temporary tree: {current!r}"
        )
    return {
        "startup_path": before.get("path"),
        "startup_was_isolated": started_inside,
        "established_through_the_api": established,
        "workspace_path": current.get("path"),
        "documents_override": (
            "XDG_DOCUMENTS_DIR"
            if platform.system() != "Windows"
            else "unavailable on Windows; the workspace was selected through the API and "
            "the startup default above was the user's Documents"
        ),
    }


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


def diagnose_preparation(
    interpreter: Path, app: Path, environment: dict[str, str], output: Path
) -> dict[str, Any]:
    """Run the documented provisioning command *after* the gate has already failed.

    **This can never make a candidate pass.** It is reached only from the
    failure path, with an explicit flag, and its result is attached to a report
    whose verdict is already "not qualified".

    The distinction matters because an earlier version of this file did the
    opposite: when the application had not prepared the CPU runtime, it ran this
    command, restarted, and passed. That masked the exact user requirement the
    gate exists to check -- BEAT CPU offered on every supported computer, by the
    application itself -- and turned "the product does not do this" into a
    green step. What the application will not do for a user, this must not do
    for the build.

    What it is good for is diagnosis: when a runner reports the row unavailable,
    the provisioning transcript says whether the runtime can be built there at
    all, which separates "the product never tried" from "the host cannot".

    ``HORNLAB_BEAT_RUNTIME_DIR`` is this run's own directory, so no previously
    provisioned record is read -- but discovery also consults
    ``HORNLAB_BEAT_JULIA`` and ``PATH``. Reusing a Julia already on the host
    says nothing about a clean machine, so which happened is recorded.
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
    log = output / "cpu-provision-diagnosis.log"
    log.write_text(transcript, encoding="utf-8")
    runtime_dir = environment["HORNLAB_BEAT_RUNTIME_DIR"]
    state_path = Path(runtime_dir) / "state-cpu.json"
    state: dict[str, Any] = {}
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    julia = str(state.get("julia_executable", ""))
    inside = bool(julia) and julia.startswith(runtime_dir)
    downloaded = "download" in transcript.lower()
    return {
        "note": "diagnosis only; this cannot qualify a candidate",
        "command_exit_code": completed.returncode,
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
    engine was selected **by name**, so AUTO's preference order did not choose
    it. The solve reported ``beat_backend`` ``cpu``, which is the backend that
    ran, not the one that was asked for -- a named engine can still be
    substituted when it is unavailable, and ``check_solve`` has already refused
    a result that does not carry the CPU contract. And the accelerator rows
    the application itself reports are recorded: on a host where none is
    available, a CPU solve is independence by construction; on a host where
    one is, it is evidence that selecting CPU keeps CPU.
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


#: The directivity planes every solve here asks for. The imported request names
#: them in ``polar_config.enabled_axes``. The parametric request names none,
#: and these are ``PolarConfig.enabled_axes``' default in
#: ``server/jobs/models.py``, so they are what it asked for too.
REQUESTED_PLANES = ("horizontal", "vertical", "diagonal")


def _plane_values(plane: str, rows: list[Any], bad: list[str]) -> tuple[int, int]:
    """Count a directivity plane's values, and never its angles.

    Each row is one frequency's ``[angle, value]`` pairs, as the result builder
    writes them. The angle is an axis -- non-zero in any plane that has one --
    so it is held to being finite and never counted as evidence of a solve.
    """

    total = non_zero = 0
    for index, row in enumerate(rows):
        if not isinstance(row, list):
            raise QualificationError(
                f"directivity plane {plane!r} row {index} is not a list of points"
            )
        for point in row:
            if not (isinstance(point, list) and len(point) == 2):
                raise QualificationError(
                    f"directivity plane {plane!r} row {index} holds {point!r}, not an "
                    "[angle, value] pair"
                )
            _numbers(point[0], f"result.directivity.{plane}[{index}].angle", bad)
            count, found = _numbers(point[1], f"result.directivity.{plane}[{index}].value", bad)
            total += count
            non_zero += found
    return total, non_zero


def check_axes(
    result: dict[str, Any], planes: tuple[str, ...] = REQUESTED_PLANES
) -> dict[str, Any]:
    """The result's data are there, aligned to its frequencies, finite, and not all zero.

    Only data count as evidence that something was solved. The frequency axis
    and the directivity angles are axes, non-zero in any result that has them:
    counting them, as this used to, passed a result with real frequencies and
    nothing else. So on-axis SPL and every requested directivity plane must
    each be present and carry a finite non-zero value, while phase and the
    axes are held only to being finite and aligned. A ``None`` stays
    documented absence, but a field with nothing else in it has not been solved.
    """

    frequencies = result.get("frequencies")
    if not isinstance(frequencies, list) or not frequencies:
        raise QualificationError("the result carries no frequency axis")
    bad: list[str] = []
    _numbers(frequencies, "result.frequencies", bad)

    on_axis = result.get("spl_on_axis")
    if not isinstance(on_axis, dict) or not isinstance(on_axis.get("spl"), list):
        raise QualificationError("the result carries no spl_on_axis.spl values")
    misaligned = {
        key: len(values)
        for key in ("frequencies", "spl", "phase_degrees")
        if isinstance(values := on_axis.get(key), list) and len(values) != len(frequencies)
    }
    if misaligned:
        raise QualificationError(
            f"spl_on_axis is not aligned to the {len(frequencies)} frequencies: {misaligned}"
        )
    _numbers(on_axis.get("frequencies"), "result.spl_on_axis.frequencies", bad)
    _numbers(on_axis.get("phase_degrees"), "result.spl_on_axis.phase_degrees", bad)
    spl_total, spl_non_zero = _numbers(on_axis["spl"], "result.spl_on_axis.spl", bad)

    directivity = result.get("directivity")
    if not isinstance(directivity, dict):
        raise QualificationError("the result carries no directivity")
    missing = [plane for plane in planes if not isinstance(directivity.get(plane), list)]
    if missing:
        raise QualificationError(f"the requested directivity planes {missing} are missing")
    counts: dict[str, tuple[int, int, int]] = {}
    for plane, rows in directivity.items():
        if not isinstance(rows, list):
            continue
        if len(rows) != len(frequencies):
            raise QualificationError(
                f"directivity plane {plane!r} has {len(rows)} rows for "
                f"{len(frequencies)} frequencies"
            )
        counts[plane] = (len(rows), *_plane_values(plane, rows, bad))

    if bad:
        raise QualificationError(f"the result carries non-finite numbers: {bad[:10]}")
    if spl_non_zero == 0:
        raise QualificationError(
            "spl_on_axis.spl carries no finite non-zero value; nothing was solved"
        )
    empty = [plane for plane in planes if counts[plane][2] == 0]
    if empty:
        raise QualificationError(
            f"directivity planes {empty} carry no finite non-zero value; nothing was solved"
        )
    return {
        "frequencies": len(frequencies),
        "numbers_checked": spl_total + sum(count[1] for count in counts.values()),
        "finite_non_zero": spl_non_zero + sum(count[2] for count in counts.values()),
        "spl_values": spl_total,
        "directivity_planes": {plane: count[0] for plane, count in counts.items()},
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

    **Every unsuccessful outcome raises.** A probe that exited non-zero, output
    that cannot be read, a host still running after it was signalled, and a
    registry that is not this run's are all states in which what was or was not
    stopped is unknown, and a gate that returns them as data has already
    decided they do not matter. The caller turns any of them into a failed
    qualification, keeping an earlier failure if there was one.
    """

    completed = subprocess.run(  # noqa: S603 - packaged interpreter, fixed program
        [
            str(interpreter),
            "-c",
            _IDENTIFY_AND_STOP,
            environment["HORNLAB_BEAT_WORKER_DIR"],
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
        raise QualificationError(
            f"the worker cleanup probe exited {completed.returncode}, so what it did or "
            f"did not stop is unknown: {completed.stderr[-1000:]}"
        )
    try:
        answer = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise QualificationError(f"unreadable worker cleanup output: {exc}") from exc
    still_alive = [
        record for record in answer.get("verified", []) if record.get("still_alive")
    ]
    if still_alive:
        raise QualificationError(
            f"workers this run started are still running after cleanup: {still_alive}"
        )
    if answer.get("contained") is not True:
        # The override did not take effect, so the registry the package would
        # have used is the user's own. Nothing there belongs to this run, and
        # authenticating as itself does not make a stranger's worker ours.
        raise QualificationError(
            "the isolated worker registry did not take effect: the package resolves "
            f"{answer.get('effective_worker_dir')!r}, not "
            f"{environment['HORNLAB_BEAT_WORKER_DIR']!r}. Nothing was signalled."
        )
    return answer


# ---------------------------------------------------------------------------
# The imported return, on a fresh install
# ---------------------------------------------------------------------------


def _same_path(candidate: object, expected: Path) -> bool:
    """Do a path the application reported and one this run chose name one place?

    Both sides are resolved, as in ``_inside``, and compared in NFC and under
    the platform's case rule. A filesystem may hand a non-ASCII name back
    decomposed, and an A-umlaut as one code point and as ``A`` followed by a
    combining diaeresis name the same folder.
    """

    if not isinstance(candidate, str) or not candidate:
        return False
    try:
        resolved = (Path(candidate).resolve(), Path(expected).resolve())
    except OSError:
        return False
    first, second = (
        os.path.normcase(unicodedata.normalize("NFC", str(path))) for path in resolved
    )
    return first == second


def _name_traits(name: str) -> dict[str, bool]:
    return {"spaces": " " in name, "non_ascii": any(ord(character) > 127 for character in name)}


def _finding_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in ("id", "kind", "blocking", "verdict") if key in item}


def verify_return_bundle(bundle: Path) -> dict[str, Any]:
    """Hold a ``.wgreturn`` to its own manifest, and read what the phase needs.

    Ingest checks the same checksums first and would refuse a damaged bundle
    anyway. Checking here names the file and the field before a server has
    been started for it, and checking the copy as well shows the exotic name
    cost nothing on the way in.
    """

    manifest_path = bundle / "wgreturn.json"
    if not manifest_path.is_file():
        raise QualificationError(f"{bundle} holds no wgreturn.json; it is not a return bundle")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise QualificationError(f"{manifest_path} lists no files")
    for name, entry in sorted(files.items()):
        path = bundle / name
        if not path.is_file():
            raise QualificationError(f"{bundle.name} lists {name}, which is missing")
        data = path.read_bytes()
        declared = entry if isinstance(entry, dict) else {}
        if len(data) != declared.get("size_bytes"):
            raise QualificationError(
                f"{bundle.name}/{name} is {len(data)} bytes; its manifest says "
                f"{declared.get('size_bytes')}"
            )
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if digest != declared.get("sha256"):
            raise QualificationError(
                f"{bundle.name}/{name} has sha256 {digest}; its manifest says "
                f"{declared.get('sha256')}"
            )
    anchor = (manifest.get("coordinate_system") or {}).get("solver_anchor_instance_id")
    instances = [item for item in manifest.get("instances") or [] if isinstance(item, dict)]
    chosen = next(
        (item for item in instances if item.get("instance_id") == anchor),
        instances[0] if instances else {},
    )
    design_id = chosen.get("design_id")
    if not isinstance(design_id, str) or not design_id:
        raise QualificationError(f"{manifest_path} names no design for its anchor instance")
    sources = [
        item for item in manifest.get("sources") or [] if isinstance(item, dict) and item.get("id")
    ]
    if not sources:
        raise QualificationError(f"{manifest_path} declares no sources")
    return {
        "files": sorted(files),
        "return_id": (manifest.get("return") or {}).get("id"),
        "design_id": design_id,
        "source_ids": [str(item["id"]) for item in sources],
        "source_sizes_mm": {
            str(item["id"]): float(item.get("suggested_resolution_mm") or IMPORTED_SOURCE_SIZE_MM)
            for item in sources
        },
    }


def blocking_acknowledgements(record: Mapping[str, Any]) -> dict[str, Any]:
    """The acknowledgements a fresh install's imported solve needs, and no more.

    A blocking finding holds a solve until the request acknowledges it as
    ``"<report_sha256>:<finding id>"``, in ``acknowledged_findings`` on
    ``ImportedGeometrySource``. Acknowledging every blocking finding would pass
    any regression that added one, so only the kinds a fresh install must
    report are accepted (``FRESH_INSTALL_BLOCKING``). Freshness has to be there
    and say ``missing_design``: that is how the application says its design
    registry had never heard of this return's design.
    """

    report = record.get("report_sha256")
    if not isinstance(report, str) or not report:
        raise QualificationError(
            "the ingestion record carries no report_sha256, so none of its findings "
            "can be acknowledged"
        )
    findings = [item for item in record.get("findings") or [] if isinstance(item, Mapping)]
    blocking = [item for item in findings if item.get("blocking") is True]
    unexpected = [item for item in blocking if item.get("kind") not in FRESH_INSTALL_BLOCKING]
    if unexpected:
        raise QualificationError(
            "a fresh install reported blocking findings it should not have: "
            f"{[_finding_summary(item) for item in unexpected]}. Acknowledging them "
            "would hide whatever caused them"
        )
    wrong = []
    for item in blocking:
        wanted = FRESH_INSTALL_BLOCKING[str(item.get("kind"))]
        if wanted is not None and item.get("verdict") != wanted:
            wrong.append(_finding_summary(item))
    if wrong:
        raise QualificationError(
            f"a fresh install reported {wrong}; its freshness verdict must be missing_design"
        )
    if not any(item.get("kind") == "freshness" for item in blocking):
        raise QualificationError(
            "the return reported no freshness finding with verdict missing_design, so the "
            "design registry already knew its design and this was not a fresh install"
        )
    if any(not item.get("id") for item in blocking):
        raise QualificationError(f"a blocking finding carries no id: {blocking}")
    return {
        "report_sha256": report,
        "acknowledged": [f"{report}:{item['id']}" for item in blocking],
        "blocking": [_finding_summary(item) for item in blocking],
        "informational": [
            _finding_summary(item) for item in findings if item.get("blocking") is not True
        ],
    }


def check_imported_result(
    result: Mapping[str, Any], requested: str, planes: tuple[str, ...] = REQUESTED_PLANES
) -> dict[str, Any]:
    """An imported solve ran on the engine asked for, with numbers on every channel.

    The parametric solve's contract, applied per channel. The job's own record
    of the engine that ran, ``metadata.solver_engine.engine``, must be the one
    requested, so a substitution fails instead of qualifying the wrong
    backend; and every channel's axes must be finite and not all zero.
    """

    metadata = result.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    solver_engine = metadata.get("solver_engine")
    solver_engine = solver_engine if isinstance(solver_engine, Mapping) else {}
    engine = solver_engine.get("engine")
    if engine != requested:
        raise QualificationError(
            f"an imported solve requested on {requested!r} reported "
            f"solver_engine.engine {engine!r}"
        )
    if metadata.get("geometry_type") != "imported":
        raise QualificationError(
            f"an imported solve on {requested!r} reported geometry_type "
            f"{metadata.get('geometry_type')!r}, not imported"
        )
    channels = result.get("channels")
    if not isinstance(channels, Mapping) or not channels:
        raise QualificationError(f"the imported result on {requested!r} carries no channels")
    checked: dict[str, Any] = {}
    for channel, payload in channels.items():
        if not isinstance(payload, Mapping):
            raise QualificationError(
                f"channel {channel!r} of the imported result on {requested!r} is not an object"
            )
        try:
            checked[str(channel)] = check_axes(dict(payload), planes)
        except QualificationError as exc:
            raise QualificationError(
                f"channel {channel!r} of the imported result on {requested!r}: {exc}"
            ) from exc
    return {
        "solver_engine": dict(solver_engine),
        "geometry_type": metadata.get("geometry_type"),
        "channels": checked,
    }


def await_engine_rows(
    server: Server, names: list[str], output: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Wait until every named engine row is available, or preparation has stopped.

    ``await_cpu_row``'s settling rule over several rows: the wait ends at once
    when all of them are available, and ends with the application's own
    reasons when one still is not once CPU preparation is no longer in flight.
    """

    deadline = time.monotonic() + CAPABILITY_TIMEOUT_S
    samples: list[dict[str, Any]] = []
    while True:
        capabilities = server.capabilities()
        rows = {name: engine_row(capabilities, name) for name in names}
        in_flight = bool(capabilities.get("cpuPreparationInFlight"))
        samples.append(
            {
                "at": round(time.monotonic(), 1),
                "cpuPreparationInFlight": in_flight,
                "rows": {
                    name: {"available": row.get("available"), "reason": row.get("reason")}
                    for name, row in rows.items()
                },
            }
        )
        if all(row.get("available") is True for row in rows.values()):
            settled = "available"
        elif not in_flight and len(samples) > 1:
            settled = "not-preparing"
        elif time.monotonic() >= deadline:
            settled = "timeout"
        else:
            time.sleep(CAPABILITY_POLL_S)
            continue
        (output / "imported-capability-samples.json").write_text(
            json.dumps(samples, indent=2), encoding="utf-8"
        )
        return capabilities, {"settled": settled, "samples": len(samples)}


def _drive_channels(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One drive channel per default channel the return's own sources name."""

    skipped = {str(item) for item in record.get("skipped_source_ids") or []}
    channels: dict[str, list[str]] = {}
    for source in record.get("sources") or []:
        if not isinstance(source, Mapping) or not source.get("id"):
            continue
        source_id = str(source["id"])
        if source_id in skipped:
            continue
        channel = str(source.get("default_drive_channel_id") or f"drive-{source_id}")
        channels.setdefault(channel, []).append(source_id)
    if not channels:
        raise QualificationError("the ingestion record names no source to drive")
    return [
        {"id": channel, "source_ids": ids, "motion": "normal"} for channel, ids in channels.items()
    ]


def _listed_jobs(base: str, when: str) -> dict[str, Mapping[str, Any]]:
    listing = api(base, "/api/jobs?limit=200", what=f"listing the jobs {when}")
    return {
        str(item.get("id")): item
        for item in (listing or {}).get("items") or []
        if isinstance(item, Mapping)
    }


def qualify_imported_return(
    interpreter: Path,
    app: Path,
    environment: dict[str, str],
    work: Path,
    output: Path,
    *,
    required: list[str],
    when_offered: list[str],
    fixture: Path,
    section: dict[str, Any],
) -> None:
    """Import, prepare, solve, save and reopen a CAD return on a fresh install.

    Fills *section* in place, as ``qualify`` fills the report, so a failure
    keeps every step it had already established.

    **Fresh.** A data directory this run creates and nothing has used: it must
    not exist beforehand, and once the server is up its jobs list and design
    registry must both be empty. Engine runtimes live outside the data
    directory; BEAT's CPU runtime is the one the application prepared for the
    parametric solve, in this run's own runtime directory.

    **Where.** The data directory and the workspace are created here, under
    names with spaces and non-ASCII characters. The workspace is selected
    through the application's own API twice: as the run workspace, which on
    Windows otherwise defaults to the user's Documents, and as the CAD Link
    folder, which is the root ``bundlePath`` resolves against. A fresh install
    has no CAD Link folder, and ingest refuses without one.

    **Import and prepare.** Ingest is synchronous: it meshes the return and
    answers with the record, whose blocking findings the solve acknowledges.

    **Solve.** One job per engine, each named explicitly. A required engine the
    candidate does not offer fails the gate. A when-offered engine it does not
    offer is recorded with the application's reason and skipped.

    **Save and reopen.** WG keeps results in its jobs store. The server is
    stopped the way the product stops it and started again on the same data
    directory, and every job must still be listed as complete, with stored
    results byte-identical to those fetched before the restart.
    """

    fixture = fixture.expanduser().resolve()
    data_dir = work / IMPORTED_DATA_DIR_NAME
    workspace = work / IMPORTED_WORKSPACE_NAME
    bundle = workspace / "wgreturn" / IMPORTED_BUNDLE_NAME
    bundle_path = f"wgreturn/{IMPORTED_BUNDLE_NAME}"
    section.update(
        {
            "requested": {"required": list(required), "when_offered": list(when_offered)},
            "paths": {
                "data_dir": str(data_dir),
                "workspace": str(workspace),
                "bundle": str(bundle),
                "bundle_path_in_request": bundle_path,
                "names": {
                    name: _name_traits(name)
                    for name in (
                        IMPORTED_DATA_DIR_NAME,
                        IMPORTED_WORKSPACE_NAME,
                        IMPORTED_BUNDLE_NAME,
                    )
                },
            },
        }
    )
    verified = verify_return_bundle(fixture)
    section["fixture"] = {"path": str(fixture), **verified}
    existed = data_dir.exists()
    section["fresh"] = {"data_dir_existed_before": existed}
    if existed:
        raise QualificationError(
            f"{data_dir} already exists. The imported phase qualifies a fresh install and "
            "will not reuse a data directory; give --work a new directory"
        )
    workspace.mkdir(parents=True, exist_ok=True)
    section["engines"] = []
    solved: list[dict[str, Any]] = []
    status = work / "status"

    with Server(
        interpreter, app, environment, data_dir, status / "imported-1",
        output / "imported-server-1.log",
    ) as server:
        base = server.base
        jobs_before = len(_listed_jobs(base, "of the fresh install"))
        designs = api(base, "/api/cadlink/designs", what="reading the fresh design registry")
        designs_before = len((designs or {}).get("items") or [])
        section["fresh"].update(jobs_before=jobs_before, designs_before=designs_before)
        if jobs_before:
            raise QualificationError(
                f"the fresh data directory already lists {jobs_before} jobs, so this is not "
                "a fresh install"
            )
        if designs_before:
            raise QualificationError(
                f"the design registry of the fresh install already lists {designs_before} "
                "designs, so this is not a fresh install"
            )

        capabilities, settled = await_engine_rows(server, list(required), output)
        section["capabilities"] = settled
        planned: list[dict[str, Any]] = []
        for name in required:
            row = engine_row(capabilities, name)
            entry = {
                "engine": name,
                "requirement": "required",
                "offered": row.get("available") is True,
                "reason": row.get("reason"),
            }
            section["engines"].append(entry)
            if not entry["offered"]:
                entry["decision"] = "not offered"
                raise QualificationError(
                    f"the candidate did not offer imported engine {name!r} "
                    f"({settled['settled']}): {row.get('reason')!r}"
                )
            planned.append(entry)
        for name in when_offered:
            row = engine_row(capabilities, name)
            entry = {
                "engine": name,
                "requirement": "when-offered",
                "offered": row.get("available") is True,
                "reason": row.get("reason") if row else f"the candidate reports no {name!r} engine",
            }
            section["engines"].append(entry)
            if entry["offered"]:
                planned.append(entry)
            else:
                entry["decision"] = "not offered"

        # Both selections before anything is imported or solved, and both read
        # back: a run that wrote into somebody's Documents and only then
        # checked would already have done what the check exists to prevent.
        api(base, "/api/workspace/select", {"path": str(workspace)},
            what="selecting the run workspace")
        runs = api(base, "/api/workspace/path", what="reading the run workspace")
        api(base, "/api/cad-workspace/select", {"path": str(workspace)},
            what="selecting the CAD Link folder")
        cad = api(base, "/api/cad-workspace/path", what="reading the CAD Link folder")
        section["workspace"] = {"runs": runs.get("path"), "cad": cad.get("path")}
        for label, answer in (("run workspace", runs), ("CAD Link folder", cad)):
            if not _same_path(answer.get("path"), workspace):
                raise QualificationError(
                    f"the application reports its {label} as {answer.get('path')!r}, "
                    f"not {str(workspace)!r}"
                )

        bundle.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(fixture, bundle)
        section["fixture"]["copy_verified"] = verify_return_bundle(bundle)["files"]
        sizes = verified["source_sizes_mm"]
        request = {
            "bundlePath": bundle_path,
            "mesh": {
                "rigidSizeMm": IMPORTED_RIGID_SIZE_MM,
                "transitionMm": IMPORTED_TRANSITION_MM,
                "sourceSizeMm": sizes,
            },
            "expectedDesignId": verified["design_id"],
        }
        record = api(base, "/api/cadlink/ingest", request, what="ingesting the return",
                     timeout=INGEST_TIMEOUT_S)
        if not isinstance(record, Mapping) or not record.get("ingest_id"):
            raise QualificationError(f"ingest answered without an ingest_id: {str(record)[:500]}")
        (output / "imported-ingest-record.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
        section["ingest"] = {
            "request": request,
            "ingest_id": record.get("ingest_id"),
            "report_sha256": record.get("report_sha256"),
            "manifest_sha256": record.get("manifest_sha256"),
            "artifact_sha256": record.get("artifact_sha256"),
            "findings": [
                _finding_summary(item)
                for item in record.get("findings") or []
                if isinstance(item, Mapping)
            ],
        }
        acknowledged = blocking_acknowledgements(record)["acknowledged"]
        section["ingest"]["acknowledged"] = acknowledged

        geometry = {
            "type": "imported",
            "ingest_id": record["ingest_id"],
            "manifest_sha256": record.get("manifest_sha256"),
            "artifact_sha256": record.get("artifact_sha256"),
            "drive_channels": _drive_channels(record),
            "mesh": {
                "rigid_size_mm": IMPORTED_RIGID_SIZE_MM,
                "transition_mm": IMPORTED_TRANSITION_MM,
                "source_size_mm": sizes,
            },
            "acknowledged_findings": acknowledged,
        }
        polar = record.get("polar_grid_derivation")
        angle_range = (polar if isinstance(polar, Mapping) else {}).get("angle_range") or [
            -180.0, 180.0, 73,
        ]
        for entry in planned:
            engine = entry["engine"]
            accepted = api(
                base,
                "/api/solve",
                {
                    "geometry": geometry,
                    "options": {
                        "engine": engine,
                        "frequencies_hz": list(IMPORTED_FREQUENCIES),
                        "polar_config": {
                            "angle_range": list(angle_range),
                            "distance": 2.0,
                            "enabled_axes": list(REQUESTED_PLANES),
                        },
                    },
                },
                what=f"submitting the imported solve on {engine}",
            )
            job = str((accepted or {}).get("job_id") or "")
            if not job:
                raise QualificationError(f"the imported solve on {engine} returned no job id")
            entry["job_id"] = job
            server.await_complete(job)
            content, digest = server.stored_results(job)
            (output / f"imported-result-{engine}.json").write_bytes(content)
            entry["results_sha256"] = digest
            checked = check_imported_result(json.loads(content), engine)
            entry.update(
                decision="solved",
                solver_engine=checked["solver_engine"],
                channels=checked["channels"],
            )
            solved.append(entry)
        # Listed before the restart as well as after it: a job the running
        # application does not list is one the user could not find even
        # without closing it, and "found again" would then prove nothing.
        listed = _listed_jobs(base, "after solving")
        section["listed_before_restart"] = [
            entry["job_id"]
            for entry in solved
            if (listed.get(entry["job_id"]) or {}).get("status") == "complete"
        ]
        unlisted = [
            entry["job_id"]
            for entry in solved
            if entry["job_id"] not in section["listed_before_restart"]
        ]
        if unlisted:
            raise QualificationError(
                f"jobs {unlisted} are not listed as complete after solving, before any restart"
            )

    # WG has no Save: the jobs store is what keeps a result. Stop the way the
    # product stops, start again on the same data directory, and find it.
    reopen: dict[str, Any] = {"jobs": {}}
    section["reopen"] = reopen
    with Server(
        interpreter, app, environment, data_dir, status / "imported-2",
        output / "imported-server-2.log",
    ) as server:
        listed = _listed_jobs(server.base, "after the restart")
        for entry in solved:
            job = entry["job_id"]
            row = {
                "listed": (listed.get(job) or {}).get("status") == "complete",
                "equal": False,
                "sha256_before": entry["results_sha256"],
                "sha256_after": None,
            }
            reopen["jobs"][job] = row
            _content, digest = server.stored_results(job)
            row["sha256_after"] = digest
            row["equal"] = digest == entry["results_sha256"]
        # A record the restarted application cannot find is this check
        # failing, not a refused call: keep the answer and judge it below,
        # after the other things the restart must find have been read too.
        try:
            again = api(
                server.base,
                f"/api/cadlink/ingest/{section['ingest']['ingest_id']}",
                what="reading the ingestion record after the restart",
            )
        except QualificationError as exc:
            again = None
            reopen["ingest_record_error"] = str(exc)
        reopen["ingest_record_reopened"] = (
            isinstance(again, Mapping)
            and again.get("report_sha256") == section["ingest"]["report_sha256"]
        )
        cad = api(server.base, "/api/cad-workspace/path",
                  what="reading the CAD Link folder after the restart")
        reopen["cad_workspace_persisted"] = _same_path(cad.get("path"), workspace)

    missing = [job for job, row in reopen["jobs"].items() if not row["listed"]]
    if missing:
        raise QualificationError(
            f"jobs {missing} are not listed as complete after a restart on the same data "
            "directory"
        )
    changed = [job for job, row in reopen["jobs"].items() if not row["equal"]]
    if changed:
        raise QualificationError(f"the stored results of jobs {changed} changed across the restart")
    if not reopen["ingest_record_reopened"]:
        raise QualificationError(
            "the ingestion record did not survive the restart: "
            + (reopen.get("ingest_record_error") or "it came back with another report_sha256")
        )
    if not reopen["cad_workspace_persisted"]:
        raise QualificationError("the CAD Link folder selection did not survive the restart")


def qualify(arguments: argparse.Namespace, report: dict[str, Any]) -> None:
    """Fill *report* in place, so a failure keeps everything established so far.

    The caller owns the dictionary and writes it out whatever happens. Returning
    it instead meant a run that raised in the solve reported nothing at all --
    not the pins it had already checked, not the manifest, not the capability
    samples that say what the application was doing when it stopped.
    """

    resources, app, interpreter = resolve_payload(arguments.payload)
    work = arguments.work.expanduser().resolve()
    output = arguments.output.expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    data_dir = work / "data"
    environment = isolated_environment(app, work)
    expected_pins = pins_from_file(arguments.pins_json)
    expected_pins.update(arguments.expected_pin or {})

    report.update({
        "payload": str(resources),
        "payload_kind": arguments.payload_kind,
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "interpreter": str(interpreter),
        "isolated": {
            "data_dir": str(data_dir),
            "beat_runtime_dir": environment["HORNLAB_BEAT_RUNTIME_DIR"],
            "worker_registry": environment["HORNLAB_BEAT_WORKER_DIR"],
            "julia_depot": environment["JULIA_DEPOT_PATH"],
            "python_cache": environment["PYTHONPYCACHEPREFIX"],
            "numba_cache": environment["NUMBA_CACHE_DIR"],
            "note": (
                "every cache this gate can redirect is inside the run's own tree, "
                "Julia's depot included; the application's provisioning inherits it"
            ),
        },
    })
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
    # Recorded before anything can start a worker, so the cleanup in ``main``
    # knows where to look even if the very next call raises.
    arguments.cleanup = (interpreter, environment, output)
    control = work / "status" / "stop-1"
    # One server for the whole gate: the row the application published and the
    # solve that uses it are then the same process, so "offered" cannot describe
    # one run while the solve describes another.
    with Server(interpreter, app, environment, data_dir, control, output / "server.log") as server:
        report["cpu_preparation"] = await_cpu_row(server, output)
        capabilities = server.capabilities()
        row = engine_row(capabilities, CPU_ENGINE)
        if row.get("available") is not True:
            # **The gate ends here, and does not reach around the product.**
            # The requirement is that the application offers BEAT CPU on every
            # supported computer, which since 69b1ed0 includes macOS. Running
            # the provisioning command here and restarting -- which this file
            # used to do -- would turn "the product did not prepare it" into a
            # passing step, and the requirement would be unqualified by the very
            # gate written to qualify it.
            report["cpu_offered"] = {
                "available": row.get("available"),
                "reason": row.get("reason"),
                "settled": report["cpu_preparation"]["settled"],
            }
            if arguments.diagnose_preparation_failure:
                report["preparation_diagnosis"] = diagnose_preparation(
                    interpreter, app, environment, output
                )
            raise QualificationError(
                f"the application did not offer {CPU_ENGINE} by itself "
                f"({report['cpu_preparation']['settled']}): {row.get('reason')!r}"
            )
        report["cpu_offered"] = {
            "available": True,
            "after": "the application's own preparation",
            "settled": report["cpu_preparation"]["settled"],
        }
        report["workspace"] = workspace_isolation(server.base, work)
        result = server.completed(server.solve(SOLVE_FREQUENCIES, CPU_ENGINE))

    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    report["solve"] = check_solve(result, expected_pins)
    report["gpu_independence"] = gpu_independence(capabilities, report["solve"])

    # After the parametric gate has passed, and on servers of its own: the
    # imported phase needs a data directory nothing has used, and a restart,
    # neither of which the single parametric server may have.
    if arguments.imported_engine:
        section: dict[str, Any] = {}
        report["imported_return"] = section
        qualify_imported_return(
            interpreter,
            app,
            environment,
            work,
            output,
            required=arguments.imported_engine,
            when_offered=arguments.imported_engine_when_offered,
            fixture=arguments.imported_fixture,
            section=section,
        )


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
    parser.add_argument(
        "--diagnose-preparation-failure",
        action="store_true",
        help=(
            "when the application does not offer the CPU backend, additionally run the "
            "documented provisioning command and attach its transcript. Diagnosis only: "
            "the run has already failed and this cannot change that"
        ),
    )
    parser.add_argument(
        "--imported-engine",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "also qualify a fresh-install CAD return: import the committed .wgreturn, "
            "solve it on this engine, and reopen the result after a restart. The "
            "candidate must offer the engine (repeatable)"
        ),
    )
    parser.add_argument(
        "--imported-engine-when-offered",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "solve the imported return on this engine too when the candidate's capability "
            "row reports it available; otherwise record the application's reason and go "
            "on (repeatable; needs at least one --imported-engine)"
        ),
    )
    parser.add_argument(
        "--imported-fixture",
        type=Path,
        default=DEFAULT_IMPORTED_FIXTURE,
        help="the .wgreturn bundle the imported phase copies in (default: the committed one)",
    )
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-tree-sha256")
    return parser


def check_imported_arguments(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    """Refuse an imported-engine selection that could qualify nothing or says two things."""

    required = list(dict.fromkeys(arguments.imported_engine))
    optional = list(dict.fromkeys(arguments.imported_engine_when_offered))
    both = sorted(set(required) & set(optional))
    if both:
        parser.error(
            f"{', '.join(both)} is given as both --imported-engine and "
            "--imported-engine-when-offered"
        )
    if optional and not required:
        parser.error(
            "--imported-engine-when-offered needs at least one --imported-engine: a run "
            "whose only imported engines were optional could qualify nothing"
        )
    arguments.imported_engine = required
    arguments.imported_engine_when_offered = optional


def _keep_notes(report: dict[str, Any], exc: BaseException) -> None:
    """Report what went wrong after the failure, without letting it replace it."""

    notes = [str(note) for note in getattr(exc, "__notes__", ()) or ()]
    if notes:
        report["additional_failures"] = notes


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    check_imported_arguments(parser, arguments)
    arguments.expected_pin = parse_pins(arguments.expected_pin)
    arguments.cleanup = None
    started = time.time()
    report: dict[str, Any] = {}
    failure: str | None = None
    try:
        qualify(arguments, report)
    except QualificationError as exc:
        failure = str(exc)
        _keep_notes(report, exc)
    except Exception as exc:  # noqa: BLE001 - an unexpected failure is still a failure
        failure = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        _keep_notes(report, exc)
    finally:
        # Whatever happened -- a refused pin, a solve that never finished, an
        # exception nobody predicted -- a worker this run started must not be
        # left behind, and everything established before the failure has to
        # survive it. Both used to happen only after a *successful* solve.
        if arguments.cleanup is not None:
            try:
                report["worker_cleanup"] = stop_our_workers(*arguments.cleanup)
            except QualificationError as exc:
                report["worker_cleanup"] = {"refused": str(exc)}
                failure = failure or str(exc)
            except Exception as exc:  # noqa: BLE001 - any cleanup failure is a failure
                # This used to record the exception and leave the verdict alone,
                # so a TimeoutExpired or an OSError after a perfectly good solve
                # came out as qualified=true and exit 0 -- with workers possibly
                # still running and nothing said about them. A cleanup whose
                # outcome is unknown is not a pass. The earlier failure wins
                # when there is one, because that is what a reader needs first.
                detail = f"{type(exc).__name__}: {exc}"
                report["worker_cleanup"] = {"error": detail}
                report["worker_cleanup"]["traceback"] = traceback.format_exc()
                failure = failure or f"the worker cleanup did not complete: {detail}"
        report["qualified"] = failure is None
        if failure is not None:
            report["error"] = failure
        report["seconds"] = round(time.time() - started)
        arguments.output.mkdir(parents=True, exist_ok=True)
        (arguments.output / "cpu-qualification.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
    if failure is not None:
        # A failure can now name a path with non-ASCII characters, and a
        # Windows runner's piped stderr is not UTF-8. The report above keeps
        # the text exactly; this line only has to survive the console.
        line = f"CPU qualification FAILED: {failure}"
        print(line.encode("ascii", "backslashreplace").decode("ascii"), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
