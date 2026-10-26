"""Exercise the selected backend through the production manager and session."""

from __future__ import annotations

from collections.abc import Callable, Generator
import json
from pathlib import Path
from typing import Any

from .manager import WorkerBusy, WorkerManager, get_manager
from .probe import compiled_probe
from .session import SolveSession


def warm_up(*, beat_backend: str = "cpu", mode: str = "worker",
            worker_manager: WorkerManager | None = None,
            cancellation_callback: Callable[[], None] | None = None,
            admission_callback: Callable[[], None] | None = None,
            idle_only: bool = False, wait_for_idle: bool = False, **options: Any) -> bool:
    """Off does nothing; worker starts Julia; tiny proves a compiled solve."""
    if mode not in {"off", "worker", "tiny"}:
        raise ValueError("Warm-up mode must be off, worker, or tiny")
    if mode == "off":
        return False
    client = (worker_manager or get_manager()).get_worker(beat_backend, **options)

    def check_admission() -> None:
        if cancellation_callback is not None:
            cancellation_callback()
        if admission_callback is not None:
            admission_callback()

    if idle_only and client.solved:
        return True
    if idle_only and wait_for_idle:
        client = client.wait_until_idle(check_admission)
        if client.solved:
            return True
    if mode == "worker":
        lease = client.acquire(check_admission, wait=not idle_only)
        try:
            lease.start()
            lease.finish("completed")
        finally:
            lease.close()
        return True

    with SolveSession(cancellation_callback=cancellation_callback,
                      admission_callback=admission_callback) as session:
        assert session.directory is not None

        class ProbeWorker:
            worker_info: dict | None = None
            events: Generator[dict, None, None] | None = None
            busy = False

            def submit(self, request_path: Path) -> Generator[dict, None, None]:
                def negotiated(info: dict | None, request: dict, operation: str) -> None:
                    self.worker_info = info

                try:
                    session.submit(client, json.loads(request_path.read_text(encoding="utf-8")),
                                   negotiate=negotiated, wait_for_worker=not idle_only)
                except WorkerBusy:
                    self.busy = True
                    raise
                self.events = session.events()
                return self.events

        worker = ProbeWorker()
        try:
            proof = compiled_probe(worker, directory=session.directory, backend=beat_backend)
            if worker.busy:
                return False
            if not proof.ready:
                raise RuntimeError(proof.reason)
        finally:
            if worker.events is not None:
                worker.events.close()
    return True
