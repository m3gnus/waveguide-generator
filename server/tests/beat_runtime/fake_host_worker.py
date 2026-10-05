"""Public engine stand-in for fast detached-host tests; never starts Julia."""

from __future__ import annotations

import json
import os
import copy
from pathlib import Path
import threading
import time


class EngineWorker:
    def __init__(self, **kwargs):
        self.events = Path(kwargs["environment"]["TEST_EVENTS"])
        self.environment = kwargs["environment"]
        self._info = None
        self._stream = None
        if kwargs["environment"].get("TEST_FAIL"):
            raise RuntimeError("fixture constructor failure")
        time.sleep(float(kwargs["environment"].get("TEST_DELAY", "0")))
        # Fixture logs must never persist the test runner's ambient secrets.
        logged_kwargs = dict(kwargs)
        logged_kwargs["environment"] = {
            name: value for name, value in kwargs["environment"].items()
            if name.startswith(("JULIA_", "BLAB_", "TEST_")) or name == "PATH"
        }
        with self.events.open("a") as stream:
            stream.write(json.dumps({"type": "built", "pid": os.getpid(), "cwd": os.getcwd(),
                                     "kwargs": {k: str(v) if isinstance(v, Path) else v
                                                for k, v in logged_kwargs.items()}}) + "\n")

    def terminate(self):
        time.sleep(float(self.environment.get("TEST_TERMINATE_DELAY", "0")))
        if self.environment.get("TEST_TERMINATE_HANG"):
            self.log("termination_hung")
            threading.Event().wait()
        if self._stream is not None:
            self._stream.closed.set()
        self._info = None
        with self.events.open("a") as stream:
            stream.write(json.dumps({"type": "terminated", "pid": os.getpid()}) + "\n")

    def log(self, kind, **kwargs):
        with self.events.open("a") as stream:
            stream.write(json.dumps({"type": kind, **kwargs}) + "\n")

    @property
    def worker_info(self):
        return copy.deepcopy(self._info)

    def ensure_started(self, **kwargs):
        callback = kwargs.get("status_callback")
        if callback:
            callback("fixture startup")
        gate = self.environment.get("TEST_START_GATE")
        marker = self.environment.get("TEST_START_ONCE_MARKER")
        skip = marker and Path(marker).exists()
        if marker and not skip:
            Path(marker).touch()
        if gate and not skip:
            wait_until(lambda: Path(gate).exists(), timeout=20)
            if callback:
                callback("fixture startup finished")
        if self._info is None:
            self._info = {"type": "ready", "protocol": {"name": "beat-worker", "version": 1},
                          "engine": {"name": "BEAT Engine", "version": "fixture"},
                          "operations": ["solve", "bem_field"],
                          "request_transports": ["file", "inline_json"]}
            if self.environment.get("TEST_COMPILED"):
                from server.tests.beat_runtime.test_probe import WORKER_INFO
                self._info = copy.deepcopy(WORKER_INFO)
            self.log("started")

    def submit(self, request, **kwargs):
        request = json.loads(request.read_text()) if isinstance(request, Path) else request
        if "compiled_system" in request:
            request = dict(request, name="compiled probe")
            if self.environment.get("TEST_PROBE_GATE"):
                request["release_path"] = self.environment["TEST_PROBE_GATE"]
        self.ensure_started(**kwargs)
        self.log("submitted", name=request["name"], operation=kwargs.get("operation", "solve"), request=request)
        self._stream = _Stream(self, request)
        return self._stream


class _Stream:
    def __init__(self, worker, request):
        self.worker, self.request = worker, request
        self.closed = threading.Event()
        self.first = True
        self.terminal = False

    def __next__(self):
        if self.closed.is_set():
            raise StopIteration
        if self.first:
            self.first = False
            if "compiled_system" in self.request:
                from server.tests.beat_runtime.test_probe import result
                return result(backend=self.request["solver_options"]["bem_backend"])
            return {"type": "result", "name": self.request["name"],
                    "payload": "x" * self.request.get("payload_size", 0)}
        gate = self.request.get("release_path")
        if gate:
            self.worker.log("reading", name=self.request["name"])
            wait_until(lambda: self.closed.is_set() or Path(gate).exists(), timeout=20)
        if self.closed.is_set():
            raise RuntimeError("fixture worker terminated during blocked read")
        if self.request.get("read_error"):
            raise RuntimeError("fixture reader failed")
        self.terminal = True
        self.worker.log("completed", name=self.request["name"])
        return {"type": "completed", "solved_count": 1}

    def close(self):
        if self.closed.is_set():
            return
        self.worker.log("closed", name=self.request["name"], terminal=self.terminal)
        if not self.terminal and self.request.get("close_error"):
            raise RuntimeError("fixture retirement failed")
        if not self.terminal and self.request.get("close_unexpected_error"):
            raise LookupError("fixture unexpected retirement failure")
        if not self.terminal and self.request.get("close_hang"):
            threading.Event().wait()
        if not self.terminal and "compiled_system" in self.request:
            # Model the official closeable stream retiring abandoned Julia.
            self.worker.terminate()
        self.closed.set()


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


def events(key):
    path = Path(key["environment"]["TEST_EVENTS"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
