"""Measure resident memory of an invocation's owned process tree."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from .json_io import read_json

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

                def reader():
                    return native.read_owned(select_native, self.known.setdefault)
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
        # Windows has no SIGKILL; os.kill with SIGTERM already terminates there.
        for sig in dict.fromkeys((signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM))):
            rows = self.reader()
            for pid, start in self.known.items():
                if pid != self.root_pid and pid in rows and rows[pid]["start"] == start:
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
            if sig == signal.SIGTERM:
                time.sleep(.2)
