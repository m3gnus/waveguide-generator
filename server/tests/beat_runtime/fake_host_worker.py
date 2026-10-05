"""Subprocess fixture: constructor/termination only, never starts Julia."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time


class EngineWorker:
    def __init__(self, **kwargs):
        self.events = Path(kwargs["environment"]["TEST_EVENTS"])
        if kwargs["environment"].get("TEST_FAIL"):
            raise RuntimeError("fixture constructor failure")
        time.sleep(float(kwargs["environment"].get("TEST_DELAY", "0")))
        with self.events.open("a") as stream:
            stream.write(json.dumps({"type": "built", "pid": os.getpid(), "cwd": os.getcwd(),
                                     "kwargs": {k: str(v) if isinstance(v, Path) else v
                                                for k, v in kwargs.items()}}) + "\n")

    def terminate(self):
        with self.events.open("a") as stream:
            stream.write(json.dumps({"type": "terminated", "pid": os.getpid()}) + "\n")

    def ensure_started(self, **kwargs):
        raise AssertionError("PR 18 must not start Julia")

    def submit(self, *args, **kwargs):
        raise AssertionError("PR 18 must refuse submission")


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
