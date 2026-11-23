"""Measure what a CAD store durability boundary costs on this machine's disk.

The CAD store runs WAL with ``synchronous=FULL``: every commit syncs the log
(``FlushFileBuffers`` on Windows, ``fsync`` on macOS and Linux) before it
returns, and nothing checkpoints. Before that it ran ``synchronous=NORMAL`` and
checkpointed with ``PRAGMA wal_checkpoint(FULL)`` once per durability boundary
(a Send, an acknowledgement, a dismissal). Real flows commit about 1.5 times per
boundary; ``--per-boundary`` (default 2) sets how many commits one boundary
holds here. Each commit writes a realistic row (an accepted
``receive_snapshot`` operation, the row a Send to CAD writes).

The modes run interleaved, one boundary of each per round, so disk and thermal
drift land on all of them alike. It prints, per boundary unless stated:

* ``old: NORMAL commits + checkpoint`` -- what a boundary cost before
* ``new: FULL commits``                -- what a boundary costs now
* ``FULL, one commit``                 -- per commit
* ``checkpoint alone``                 -- the old boundary's checkpoint

``--fullfsync`` sets ``PRAGMA fullfsync`` and ``checkpoint_fullfsync`` on every
connection. On macOS that makes each sync an ``F_FULLFSYNC``, which also flushes
the drive's cache: the closest macOS stand-in for Windows ``FlushFileBuffers``.
Elsewhere it changes nothing.

Run from the repository root with any Python 3.11+ (stdlib only, plus WG's own
store code)::

    python scripts/measure_cad_store_commit.py
    python scripts/measure_cad_store_commit.py --boundaries 200 --fullfsync

Put ``--dir`` on the disk that holds the WG data directory; the default is the
system temporary folder. The databases are deleted afterwards.

On Windows, from a checkout of the branch in PowerShell, with WG closed so the
disk is otherwise idle (the WG data directory lives under ``%APPDATA%``)::

    py -3 scripts\\measure_cad_store_commit.py --boundaries 300 --dir $env:APPDATA
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
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


def _open(stack: ExitStack, path: Path, *, synchronous: str, fullfsync: bool,
          prefill: int) -> tuple[CadLinkStore, sqlite3.Connection]:
    store = stack.enter_context(closing(CadLinkStore(path)))
    store.initialize()
    # The store's own thread-local connection, so the settings apply to its writes.
    conn = store._connect()
    # The store opens FULL; NORMAL reproduces the configuration it replaced.
    conn.execute(f"PRAGMA synchronous = {synchronous}")
    if fullfsync:
        conn.execute("PRAGMA fullfsync = 1")
        conn.execute("PRAGMA checkpoint_fullfsync = 1")
    for index in range(prefill):
        _accept(store, index)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    return store, conn


def _checkpoint(conn: sqlite3.Connection) -> None:
    busy, log_frames, copied = conn.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
    if busy or copied < log_frames:
        raise RuntimeError("the checkpoint reported busy; is another process using the folder?")


def measure(folder: Path, *, boundaries: int, per_boundary: int, prefill: int,
            fullfsync: bool) -> dict[str, list[float]]:
    """Per-boundary (and per-commit) wall times in milliseconds, modes interleaved."""

    times: dict[str, list[float]] = {"old": [], "new": [], "commit": [], "checkpoint": []}
    with ExitStack() as stack:
        old, old_conn = _open(stack, folder / "normal.db", synchronous="NORMAL",
                              fullfsync=fullfsync, prefill=prefill)
        new, _ = _open(stack, folder / "full.db", synchronous="FULL",
                       fullfsync=fullfsync, prefill=prefill)
        index = prefill
        for _ in range(boundaries):
            started = time.perf_counter()
            for offset in range(per_boundary):
                _accept(old, index + offset)
            committed = time.perf_counter()
            _checkpoint(old_conn)
            finished = time.perf_counter()
            times["old"].append((finished - started) * 1000.0)
            times["checkpoint"].append((finished - committed) * 1000.0)

            started = time.perf_counter()
            for offset in range(per_boundary):
                before = time.perf_counter()
                _accept(new, index + offset)
                times["commit"].append((time.perf_counter() - before) * 1000.0)
            times["new"].append((time.perf_counter() - started) * 1000.0)
            index += per_boundary
    return times


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--boundaries", type=int, default=200,
                        help="timed durability boundaries per mode (default 200)")
    parser.add_argument("--per-boundary", type=int, default=2,
                        help="commits per boundary (default 2; real flows average about 1.5)")
    parser.add_argument("--prefill", type=int, default=200,
                        help="untimed rows written first, so the file is not empty (default 200)")
    parser.add_argument("--fullfsync", action="store_true",
                        help="flush the drive cache on every sync (macOS F_FULLFSYNC)")
    parser.add_argument("--dir", type=Path, default=None,
                        help="folder for the scratch databases (default: system temporary folder)")
    args = parser.parse_args(argv)
    if args.boundaries < 1 or args.per_boundary < 1 or args.prefill < 0:
        parser.error("--boundaries and --per-boundary must be at least 1 and --prefill at least 0")
    if args.dir is not None and not args.dir.is_dir():
        parser.error(f"--dir {args.dir} is not an existing folder")

    with tempfile.TemporaryDirectory(prefix="wg-cad-commit-", dir=args.dir) as temporary:
        times = measure(Path(temporary), boundaries=args.boundaries,
                        per_boundary=args.per_boundary, prefill=args.prefill,
                        fullfsync=args.fullfsync)
        location = os.path.realpath(temporary)

    rows = [
        (f"old: {args.per_boundary} NORMAL commits + checkpoint", times["old"]),
        (f"new: {args.per_boundary} FULL commits", times["new"]),
        ("FULL, one commit", times["commit"]),
        ("checkpoint alone (old boundary)", times["checkpoint"]),
    ]
    print(f"CAD store durability boundary, {args.boundaries} boundaries per mode, "
          f"{args.per_boundary} commits each, interleaved, after {args.prefill} prefill rows"
          f"{', fullfsync' if args.fullfsync else ''}")
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
