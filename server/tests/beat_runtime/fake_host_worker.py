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
        with self.events.open("a") as stream:
            stream.write(json.dumps({"type": "built", "pid": os.getpid(), "cwd": os.getcwd(),
                                     "kwargs": {k: str(v) if isinstance(v, Path) else v
                                                for k, v in kwargs.items()}}) + "\n")

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
        if gate:
            wait_until(lambda: Path(gate).exists(), timeout=20)
            if callback:
                callback("fixture startup finished")
        if self._info is None:
            self._info = {"type": "ready", "protocol": {"name": "beat-worker", "version": 1},
                          "engine": {"name": "BEAT Engine", "version": "fixture"},
                          "operations": ["solve", "bem_field"],
                          "request_transports": ["file", "inline_json"]}
            self.log("started")

    def submit(self, request, **kwargs):
        request = json.loads(request.read_text()) if isinstance(request, Path) else request
        self.ensure_started(**kwargs)
        self.log("submitted", name=request["name"], operation=kwargs["operation"], request=request)
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
