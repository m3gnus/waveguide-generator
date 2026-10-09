"""One broker job = one frozen case/backend/route/repetition in a fresh child.

No provisioning, warming, meshing or broker submission is performed here.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from server.jobs.models import SolveRequest
from server.solver.context import SolverContext

from .corpus import CASES, ROOT, FrozenCase
from .json_io import dumps, read_json, write_json
from . import run_corpus as corpus_runner

# Uniform subsets of the existing catalogue axes; the original mesh is reused.
PERF_FREQUENCIES = {
    "osse-quarter": tuple(float(f) for f in range(500, 3501, 100)),
    "osse-full": tuple(float(f) for f in range(500, 3501, 100)),
    "imported-two-sources": tuple(float(f) for f in range(500, 2301, 60)),
}
# Per-invocation names: private worker registries, and the compute broker's job id,
# which differs for every broker job even when the measured environment matches.
ISOLATION_ENV = ("WG2_BEAT_WORKER_DIR", "HORNLAB_BEAT_WORKER_DIR", "HORNLAB_BROKER_JOB")
SCHEMA = "beat-perf-v1"


def interleaving_plan(repetitions: int = 3, *, backend: str = "cpu",
                      cases: tuple[str, ...] = tuple(PERF_FREQUENCIES)) -> list[dict]:
    if repetitions < 3:
        raise ValueError("Performance gate requires N >= 3 repetitions per route")
    return [{"sequence": i + 1, "case": case, "backend": backend, "route": route,
             "repetition": repetition, "frequencies_hz": PERF_FREQUENCIES[case]}
            for i, (case, repetition, route) in enumerate(
                (case, repetition, route) for case in cases
                for repetition in range(1, repetitions + 1) for route in ("hbb", "official"))]


def perf_environment(*, route: str, julia: str, hbb_depot: str, directory: Path) -> dict:
    env = corpus_runner.child_environment(official=route == "official", julia=julia,
                                         hbb_depot=hbb_depot, single_thread=False)
    # Both packages normally adopt detached hosts. New private registries make
    # adoption impossible without changing that production lifetime policy.
    env.update(WG2_BEAT_WORKER_DIR=str(directory / "official-workers"),
               HORNLAB_BEAT_WORKER_DIR=str(directory / "hbb-workers"))
    if env.get("HORNLAB_BEAT_PERSISTENT_HOST", "1").strip() == "0":
        raise ValueError("Performance requires production persistent-host policy")
    return env


def comparison_environment_hash(env: dict) -> str:
    return corpus_runner.environment_sha256({k: v for k, v in env.items() if k not in ISOLATION_ENV})


def require_identity(identity: dict) -> None:
    corpus_runner.require_clean_wg(identity)
    pins = read_json(ROOT / "pins.json")["modules"]
    for name in ("hornlab-beat-bem", "beat-engine"):
        dist = identity.get("distributions", {}).get(name, {})
        corpus_runner.require_distribution_identity(dist, name)
        if dist["revision"] != pins[name]["sha"]:
            raise ValueError(f"{name} differs from current WG pin")
    version = identity.get("julia_version", {})
    if not identity.get("julia_path") or version.get("returncode") != 0 or not version.get("stdout"):
        raise ValueError("Performance requires observed Julia path/version")


def machine_state() -> dict:
    return {"load_average": list(os.getloadavg()), "logical_cpus": os.cpu_count(),
            "power": corpus_runner.command_evidence(["pmset", "-g", "batt"])}


def ps_processes() -> dict[int, dict]:
    """One table for ancestry, PID birth identity and RSS in bytes (ps uses KiB)."""
    result = subprocess.run(["ps", "-axo", "pid=,ppid=,rss=,lstart="],
                            capture_output=True, text=True, check=True, timeout=5)
    rows = {}
    for line in result.stdout.splitlines():
        fields = line.split(maxsplit=3)
        if len(fields) == 4:
            pid, ppid, rss, start = fields
            rows[int(pid)] = {"ppid": int(ppid), "rss": int(rss) * 1024, "start": start}
    return rows


def psutil_processes(module: Any) -> dict[int, dict]:
    rows = {}
    # psutil >= 6 retains Process objects without checking PID reuse. Refresh
    # birth identities each sample (also avoids stale cached parent PIDs).
    clear_cache = getattr(module.process_iter, "cache_clear", None)
    if clear_cache is not None:
        clear_cache()
    for process in module.process_iter(["pid", "ppid", "memory_info", "create_time"]):
        try:
            info = process.info
            # Attribute collection can itself race exit/PID reuse. is_running
            # compares this object's birth identity with a fresh Process(pid).
            if not process.is_running():
                continue
            memory = info.get("memory_info")
            rows[info["pid"]] = {"ppid": info["ppid"], "rss": memory.rss if memory else None,
                                 "start": info["create_time"]}
        except module.NoSuchProcess:
            continue
        except module.AccessDenied:
            # An inaccessible unrelated system process is harmless. If it is
            # an owned root/descendant, sample() must refuse partial-tree RSS.
            rows[process.pid] = {"ppid": None, "rss": None, "start": None}
    return rows


def registry_pids(directories: tuple[Path, ...]) -> set[int]:
    """Only host records inside this invocation's exclusively created directories."""
    found = set()
    for directory in directories:
        for path in directory.rglob("*.json"):
            try:
                record = read_json(path)
                if isinstance(record, dict) and type(record.get("pid")) is int and "key" in record:
                    found.add(record["pid"])
            except (OSError, ValueError):
                # Atomic registry publication can race a scan.
                continue
    return found


class RSSSampler:
    """Sample simultaneous whole-tree RSS, including detached/reparented hosts.

    Root is the harness parent; registry roots add detached hosts and their
    Julia descendants. Remember birth identities once observed, never count a
    reused PID. Sampling failures are evidence errors, never zero-memory success.
    """
    def __init__(self, root_pid: int, directories: tuple[Path, ...], *,
                 reader: Callable | None = None, interval: float = .1) -> None:
        self.root_pid, self.directories, self.interval = root_pid, directories, interval
        self.known: dict[int, Any] = {}
        self.method = "fake" if reader else "ps"
        if reader is None:
            try:
                import psutil
            except ImportError:
                reader = ps_processes
            else:
                def reader():
                    return psutil_processes(psutil)
                self.method = "psutil"
        if self.method != "fake" and sys.platform == "darwin":
            from .mac_processes import DarwinProcesses
            try:
                native = DarwinProcesses()
            except (NotImplementedError, OSError, ValueError):
                pass  # Unsupported ABI/unavailable API: retain portable reader.
            else:
                def select_native(rows):
                    self._native_selected = self._select(rows)
                    return self._native_selected

                reader = lambda: native.read_owned(select_native, self.known.setdefault)
                self.method = "darwin-sysctl-libproc"
        self.reader = reader
        self.peak_bytes = 0
        self.samples: list[dict] = []
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _select(self, rows: dict) -> set[int]:
        if self.root_pid not in rows:
            raise ValueError("RSS sampler cannot observe harness parent")
        roots = {self.root_pid} | registry_pids(self.directories)
        # A missing birth identity cannot establish that a previously owned
        # PID is safely unrelated. Refuse partial evidence instead of silently
        # dropping an inaccessible root or known descendant from the sum.
        for pid in roots | self.known.keys():
            if pid in rows and rows[pid]["start"] is None:
                raise ValueError(f"RSS sampler cannot observe owned PID {pid}")
        selected = {pid for pid in roots if pid in rows and (
            pid not in self.known or self.known[pid] == rows[pid]["start"])}
        selected.update(pid for pid, start in self.known.items()
                        if pid in rows and rows[pid]["start"] == start)
        while True:
            children = {pid for pid, row in rows.items() if row["ppid"] in selected
                        and (pid not in self.known or self.known[pid] == row["start"])}
            if children <= selected:
                break
            selected |= children
        return selected

    def sample(self) -> None:
        rows = self.reader()
        if self.method == "darwin-sysctl-libproc":
            if self.root_pid not in rows:
                raise ValueError("RSS sampler cannot observe harness parent")
            selected = self._native_selected & rows.keys()
        else:
            selected = self._select(rows)
        for pid in selected:
            if rows[pid]["start"] is None or not isinstance(rows[pid]["rss"], int):
                raise ValueError(f"RSS sampler cannot observe owned PID {pid}")
            self.known.setdefault(pid, rows[pid]["start"])
        rss = sum(rows[pid]["rss"] for pid in selected)
        self.peak_bytes = max(self.peak_bytes, rss)
        self.samples.append({"epoch_s": time.time(), "rss_bytes": rss, "pids": sorted(selected)})

    def _run(self) -> None:
        deadline = time.monotonic()
        while not self._stop.is_set():
            try:
                self.sample()
            except Exception as exc:
                self.errors.append(f"{type(exc).__name__}: {exc}")
            deadline += self.interval
            self._stop.wait(max(0., deadline - time.monotonic()))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=6)
            if self._thread.is_alive():
                self.errors.append("RSS reader did not stop")
        return {"method": self.method, "interval_s": self.interval, "peak_rss_bytes": self.peak_bytes,
                "samples": self.samples, "errors": self.errors,
                "scope": "harness parent + fresh child + private detached hosts + Julia descendants"}

    def terminate_owned(self) -> None:
        """Failure backstop for detached hosts outside the child's process group."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            rows = self.reader()
            for pid, start in self.known.items():
                if pid != self.root_pid and pid in rows and rows[pid]["start"] == start:
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
            if sig == signal.SIGTERM:
                time.sleep(.2)


def native_threads(natives: dict, frequencies: tuple[float, ...], *, official: bool,
                   launch_threads: int) -> dict:
    blas, julia = set(), set()
    for native in natives.values():
        if getattr(native, "cancelled", False) or sorted(native.frequencies_hz) != sorted(frequencies):
            raise ValueError("Incomplete production sweep")
        rows = native.solver_log
        if len(rows) != len(frequencies):
            raise ValueError("Missing per-frequency thread diagnostics")
        for row in rows:
            diag = row.get("native_diagnostics", {})
            value = diag.get("blas_threads")
            if type(value) is not int or value < 1:
                raise ValueError("Missing observed BLAS thread count")
            blas.add(value)
            if official:
                value = diag.get("engine_provenance", {}).get("runtime", {}).get("julia_threads")
                if type(value) is not int or value < 1:
                    raise ValueError("Missing observed official Julia thread count")
                julia.add(value)
    if not natives or len(blas) != 1 or official and julia != {launch_threads}:
        raise ValueError("Thread counts change or disagree with launch policy")
    return {"julia_threads": launch_threads, "blas_threads": next(iter(blas)),
            "julia_evidence": "native engine provenance + launch policy" if official else "HBB resolved worker launch policy",
            "blas_evidence": "every channel/frequency native_diagnostics.blas_threads"}


def production_sweeps(frozen: FrozenCase, *, official: bool, backend: str, julia: str,
                      manager: Any = None, routes: tuple[Callable, Callable] | None = None,
                      clock: Callable = time.perf_counter, launch_threads: int | None = None,
                      host_observer: Callable | None = None) -> dict:
    """Retain production host/worker across both calls; never prestart it."""
    if routes is None:
        from server.solver.beat import solve_beat_from_msh_text
        from server.solver.beat_imported import solve_imported_beat_from_msh_text
        routes = solve_beat_from_msh_text, solve_imported_beat_from_msh_text
    frequencies = PERF_FREQUENCIES[frozen.case.name]
    request = SolveRequest.model_validate(dict(frozen.request, options=dict(
        frozen.request["options"], engine=f"beat-{backend}", frequencies_hz=list(frequencies),
        frequency_spacing="linear", accuracy="accurate")))
    context = SolverContext.from_request(request, solver_mode="full_3d") if frozen.record is None else None
    text = frozen.mesh_bytes.decode()
    results = []
    hosts = []
    for _ in range(2):
        natives, callbacks = {}, []
        started = 0.

        def on_result(*args: Any) -> None:
            now = clock()
            if not callbacks:
                callbacks.append(now - started)

        def capture(channel: str, native: Any) -> None:
            if channel in natives:
                raise ValueError("Duplicate native channel result")
            natives[channel] = native

        kwargs = {"backend": backend, "_official": official, "_precision": "float32",
                  "_julia_executable": julia, "_worker_manager": manager,
                  "result_callback": on_result, "_native_result_callback": capture}
        # Only choose the executable; all thread/precision/lifetime defaults
        # remain the production SolveConfig defaults (single, persistent=True).
        if not official:
            kwargs["_hbb_options"] = {"julia_executable": julia}
        started = clock()
        if frozen.record is None:
            response = routes[0](text, context, mesh_stats=frozen.mesh_stats, **kwargs)
        else:
            response = routes[1](text, request, frozen.record, **kwargs)
        elapsed = clock() - started
        # Observe the policy after the first call, so its core-count resolution
        # and any production cache misses remain inside the first solve timer.
        if launch_threads is None:
            if official:
                from server.solver.beat_runtime.threads import resolve_julia_threads
                launch_threads = resolve_julia_threads(backend)
            else:
                from server.solver.beat_threads import beat_julia_threads
                from hornlab_beat_bem.worker import _resolve_julia_threads
                launch_threads = int(_resolve_julia_threads(beat_julia_threads(backend)))
        if not callbacks:
            raise ValueError("Production sweep emitted no frequency result callback")
        expected = {"source"} if frozen.record is None else {c.id for c in request.geometry.drive_channels}
        if set(natives) != expected:
            raise ValueError("Production sweep missing native channels")
        results.append({"first_result_s": callbacks[0], "total_s": elapsed,
                        "threads": native_threads(natives, frequencies, official=official,
                                                  launch_threads=launch_threads)})
        if host_observer:
            observed = host_observer()
            if len(observed) != 1:
                raise ValueError("Expected exactly one retained production host")
            hosts.append(observed)
        del response
        natives.clear()
    if results[0]["threads"] != results[1]["threads"]:
        raise ValueError("Cold/warm thread policies differ")
    if hosts and hosts[0] != hosts[1]:
        raise ValueError("Warm sweep did not reuse the first sweep's host")
    return {"first_result_s": results[0]["first_result_s"], "first_sweep_s": results[0]["total_s"],
            "warm_sweep_s": results[1]["total_s"], "warm_first_result_s": results[1]["first_result_s"],
            "threads": results[0]["threads"], "warm_host_reused": bool(hosts)}


def host_facts(directory: Path) -> list[dict]:
    return [{"pid": r["pid"], "key": r["key"]} for p in directory.rglob("*.json")
            if isinstance(r := read_json(p), dict) and "pid" in r and "key" in r]


def engine_child(job_path: Path, target: Path) -> int:
    job = read_json(job_path)
    manager = None
    result = dict(job, schema=SCHEMA)
    official = job["route"] == "official"

    def interrupted(signum, frame):
        raise TimeoutError(f"performance child interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        registry = job_path.parent / ("official-workers" if official else "hbb-workers")
        if registry.exists() and any(registry.iterdir()):
            raise ValueError("Fresh child requires an empty private host registry")
        result["identity"] = corpus_runner.capture_identity(job["julia"])
        require_identity(result["identity"])
        frozen = corpus_runner.load_frozen(Path(job["frozen"]), CASES[job["case"]])
        result["mesh_sha256"] = frozen.sha256
        result["request_sha256"] = hashlib.sha256(dumps(frozen.request).encode()).hexdigest()
        if official:
            from server.solver.beat_runtime.manager import WorkerManager
            # Host mode matches production; private directory blocks adoption.
            manager = WorkerManager(directory=Path(job_path.parent / "official-workers"))
        result["timing"] = production_sweeps(frozen, official=official, backend=job["backend"],
                                            julia=job["julia"], manager=manager,
                                            host_observer=lambda: host_facts(registry))
        result["hosts"] = host_facts(registry)
        if len(result["hosts"]) != 1:
            raise ValueError("Expected exactly one retained production host")
        resolved = int(result["hosts"][0]["key"]["julia_threads"])
        if resolved != result["timing"]["threads"]["julia_threads"]:
            raise ValueError("Retained host key differs from resolved Julia thread policy")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if manager is not None:
                manager.shutdown()
            if not official:
                from hornlab_beat_bem.worker import shutdown_workers
                shutdown_workers()
        except Exception as exc:
            result["error"] = f"Worker cleanup failed: {type(exc).__name__}: {exc}"
    write_json(target, result)
    return int("error" in result)


def run_one(*, case: str, backend: str, route: str, repetition: int, sequence: int,
            frozen: Path, output: Path, julia: str, hbb_depot: str, timeout_s: float = 180.) -> dict:
    if repetition < 1 or sequence < 1 or not math.isfinite(timeout_s) or not 0 < timeout_s <= 180:
        raise ValueError("Positive repetition/sequence and timeout <= 180 s required")
    # Refuse dirty trees before making directories or launching Julia/version.
    corpus_runner.wg_identity()
    frozen = corpus_runner.external_path(frozen, "--frozen")
    output = corpus_runner.empty_output(output)
    env = perf_environment(route=route, julia=julia, hbb_depot=hbb_depot, directory=output)
    loaded = corpus_runner.load_frozen(frozen, CASES[case])
    if loaded.case.precision != "float32":
        raise ValueError("Performance requires production float32")
    if route == "official":
        corpus_runner.require_official_ready(backend, julia)
    job = {"case": case, "backend": backend, "route": route, "repetition": repetition,
           "sequence": sequence, "frozen": str(frozen), "julia": julia,
           "frequencies_hz": PERF_FREQUENCIES[case], "precision": "float32"}
    write_json(output / "job.json", job)
    target = output / "perf.json"
    command = [sys.executable, "-m", "scripts.beat_conformance.run_perf",
               "--engine-child", str(output / "job.json"), "--engine-output", str(target)]
    sampler = RSSSampler(os.getpid(), (output / "hbb-workers", output / "official-workers"))
    before = machine_state()
    started = time.time()
    result = dict(job, schema=SCHEMA)
    sampler.start()
    try:
        code = corpus_runner.launch_child(command, env, output / "child.log", timeout_s)
        if target.exists():
            result = read_json(target)
        if code or not target.exists():
            result.setdefault("error", f"Performance child exit {code}; inspect child.log")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        ended = time.time()
        memory = sampler.stop()
        if result.get("error"):
            sampler.terminate_owned()
    result.update(started_at_epoch_s=started, ended_at_epoch_s=ended,
                  machine_before=before, machine_after=machine_state(), memory=memory,
                  environment=corpus_runner.relevant_environment(env),
                  environment_sha256=corpus_runner.environment_sha256(env),
                  comparison_environment_sha256=comparison_environment_hash(env))
    if memory["errors"] or not memory["samples"] or not memory["peak_rss_bytes"]:
        result.setdefault("error", "Whole-tree RSS sampling failed")
    write_json(target, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="write A,B,A,B job plan; never starts a child")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--case", choices=tuple(PERF_FREQUENCIES))
    parser.add_argument("--backend", choices=("cpu", "metal"), default="cpu")
    parser.add_argument("--route", choices=("hbb", "official"))
    parser.add_argument("--repetition", type=int)
    parser.add_argument("--sequence", type=int, help="position in the interleaving plan")
    parser.add_argument("--frozen", type=Path, help="read-only existing corpus directory with frozen.json/surface.msh")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--julia")
    parser.add_argument("--hbb-depot")
    parser.add_argument("--timeout-s", type=float, default=180.)
    parser.add_argument("--engine-child", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--engine-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.engine_child:
            return engine_child(args.engine_child, args.engine_output)
        if args.plan:
            plan = interleaving_plan(args.repetitions, backend=args.backend,
                                     cases=(args.case,) if args.case else tuple(PERF_FREQUENCIES))
            if args.output:
                target = corpus_runner.external_path(args.output, "--output")
                target.parent.mkdir(parents=True, exist_ok=True)
                write_json(target, {"schema": SCHEMA, "jobs": plan})
            else:
                print(dumps({"schema": SCHEMA, "jobs": plan}))
            return 0
        for name in ("case", "route", "repetition", "sequence", "frozen", "output", "julia", "hbb_depot"):
            if getattr(args, name) is None:
                parser.error(f"--{name.replace('_', '-')} is required for acquisition")
        result = run_one(**{k: getattr(args, k) for k in (
            "case", "backend", "route", "repetition", "sequence", "frozen", "output", "julia", "hbb_depot", "timeout_s")})
        print(dumps({k: result.get(k) for k in ("case", "route", "repetition", "timing", "error")}))
        return int("error" in result)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
