"""Exercise the selected backend through the production manager and session."""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any

from .manager import WorkerManager, get_manager
from .probe import compiled_probe
from .session import SolveSession


def warm_up(*, beat_backend: str = "cpu", mode: str = "worker",
            worker_manager: WorkerManager | None = None, **options: Any) -> None:
    """Off does nothing; worker starts Julia; tiny proves a compiled solve."""
    if mode not in {"off", "worker", "tiny"}:
        raise ValueError("Warm-up mode must be off, worker, or tiny")
    if mode == "off":
        return
    client = (worker_manager or get_manager()).get_worker(beat_backend, **options)
    if mode == "worker":
        lease = client.acquire(lambda: None)
        try:
            lease.start()
            lease.finish("completed")
        finally:
            lease.close()
        return

    with SolveSession() as session:
        assert session.directory is not None

        class ProbeWorker:
            worker_info: dict | None = None

            def submit(self, request_path: Path) -> Iterator[dict]:
                def negotiated(info: dict | None, request: dict, operation: str) -> None:
                    self.worker_info = info

                session.submit(client, json.loads(request_path.read_text(encoding="utf-8")),
                               negotiate=negotiated)
                return session.events()

        proof = compiled_probe(ProbeWorker(), directory=session.directory, backend=beat_backend)
        if not proof.ready:
            raise RuntimeError(proof.reason)
