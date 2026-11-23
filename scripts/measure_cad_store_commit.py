"""Measure what a CAD store commit costs on this machine's disk.

The CAD store runs WAL with ``synchronous=FULL``: every commit fsyncs the log
(``FlushFileBuffers`` on Windows, ``fsync`` on macOS and Linux), and
``make_durable`` does nothing. Before that it ran ``synchronous=NORMAL`` and
``make_durable`` ran ``PRAGMA wal_checkpoint(FULL)`` at each durability
boundary. This prints one table comparing the three costs over N commits of a
realistic row (an accepted ``receive_snapshot`` operation, the row a Send to CAD
writes), each in a fresh database:

* ``NORMAL commit``                  -- old per-commit cost, not yet durable
* ``NORMAL commit + checkpoint``     -- old cost at a durability boundary
* ``checkpoint alone``               -- the old ``make_durable``
* ``FULL commit``                    -- new per-commit cost, durable on return

Run from the repository root with any Python 3.11+ (stdlib only, plus WG's own
store code)::

    python scripts/measure_cad_store_commit.py
    python scripts/measure_cad_store_commit.py --commits 500 --dir D:\\scratch

Put ``--dir`` on the disk that holds the WG data directory; the default is the
system temporary folder. The databases are deleted afterwards.

On Windows, from a checkout of the branch in PowerShell, with WG closed so the
disk is otherwise idle (the WG data directory lives under ``%APPDATA%``)::

    py -3 scripts\\measure_cad_store_commit.py --commits 500 --dir $env:APPDATA
"""

from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.cadlink.operations import request_digest  # noqa: E402
from server.cadlink.store import CadLinkStore  # noqa: E402

KIND = "receive_snapshot"


def _accept(store: CadLinkStore, index: int) -> None:
    operation_id = f"bench-{index:06d}-{'x' * 24}"
    digest = f"sha256:{index:064x}"
    inputs = {"bundle_path": f"wgreturn/{operation_id}.wgreturn", "manifest_sha256": digest}
    row, result = store.accept_operation(
        operation_id,
        KIND,
        request_digest(KIND, {}, inputs),
        {},
        inputs,
        snapshot={
            "manifest_sha256": digest,
            "project_lineage_id": "project-benchmark",
            "document_name": f"Benchmark horn {index}",
        },
    )
    if result != "created":
        raise RuntimeError(f"benchmark row {operation_id} was {result}, not created")


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[position]


def _run(folder: Path, name: str, *, synchronous: str, checkpoint: bool,
         commits: int, prefill: int) -> tuple[list[float], list[float]]:
    """Return per-commit and per-checkpoint wall times in milliseconds."""

    store = CadLinkStore(folder / f"{name}.db")
    with closing(store):
        store.initialize()
        conn = store._connect()
        # The store opens FULL; NORMAL reproduces the configuration it replaced.
        conn.execute(f"PRAGMA synchronous = {synchronous}")
        for index in range(prefill):
            _accept(store, index)
        if checkpoint:
            conn.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
        commit_ms: list[float] = []
        checkpoint_ms: list[float] = []
        for index in range(prefill, prefill + commits):
            started = time.perf_counter()
            _accept(store, index)
            committed = time.perf_counter()
            commit_ms.append((committed - started) * 1000.0)
            if checkpoint:
                # What make_durable ran before it became a no-op.
                busy, log_frames, copied = conn.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
                checkpoint_ms.append((time.perf_counter() - committed) * 1000.0)
                if busy or copied < log_frames:
                    raise RuntimeError("the checkpoint reported busy; is another process using the folder?")
    return commit_ms, checkpoint_ms


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--commits", type=int, default=200, help="timed commits per mode (default 200)")
    parser.add_argument("--prefill", type=int, default=200,
                        help="untimed rows written first, so the file is not empty (default 200)")
    parser.add_argument("--dir", type=Path, default=None,
                        help="folder for the scratch databases (default: system temporary folder)")
    args = parser.parse_args(argv)
    if args.commits < 1 or args.prefill < 0:
        parser.error("--commits must be at least 1 and --prefill at least 0")
    if args.dir is not None and not args.dir.is_dir():
        parser.error(f"--dir {args.dir} is not an existing folder")

    with tempfile.TemporaryDirectory(prefix="wg-cad-commit-", dir=args.dir) as temporary:
        folder = Path(temporary)
        normal, _ = _run(folder, "normal", synchronous="NORMAL", checkpoint=False,
                         commits=args.commits, prefill=args.prefill)
        normal_cp, checkpoints = _run(folder, "normal-checkpoint", synchronous="NORMAL", checkpoint=True,
                                      commits=args.commits, prefill=args.prefill)
        full, _ = _run(folder, "full", synchronous="FULL", checkpoint=False,
                       commits=args.commits, prefill=args.prefill)
        location = os.path.realpath(temporary)

    rows = [
        ("NORMAL commit (old, not durable)", normal),
        ("NORMAL commit + checkpoint (old boundary)", [a + b for a, b in zip(normal_cp, checkpoints)]),
        ("checkpoint alone (old make_durable)", checkpoints),
        ("FULL commit (new, durable)", full),
    ]
    print(f"CAD store commit latency, {args.commits} commits per mode after {args.prefill} prefill rows")
    print(f"{platform.system()} {platform.release()} | Python {platform.python_version()} | "
          f"SQLite {sqlite3.sqlite_version} | folder {location}")
    header = f"{'mode':<44}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}"
    print(header)
    print("-" * len(header))
    for label, values in rows:
        print(f"{label:<44}{_percentile(values, 0.5):>9.2f}{_percentile(values, 0.95):>9.2f}"
              f"{max(values):>9.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
