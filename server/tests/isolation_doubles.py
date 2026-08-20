"""A deliberately badly-behaved child, driven through the real harness.

``server/cadlink/isolation.py`` accepts an ``entrypoint`` so the acceptance
tests in ``docs/plans/STEP-PARSER-ISOLATION.md`` can be demonstrated rather
than asserted about.  This module is that entrypoint: it speaks the same stdin
envelope protocol as :mod:`server.cadlink.child_main` and then does whatever
``payload["misbehaviour"]`` names -- hang, abort, overflow its result, or try
to write outside its staging directory.

It applies **no** confinement of its own.  That is the point: everything these
tests observe is enforced by the parent, so a child that simply declines to
cooperate cannot get past it.  Not collected by pytest (no ``test_`` prefix).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _record(data: bytes) -> dict[str, Any]:
    return {"size_bytes": len(data), "sha256": "sha256:" + hashlib.sha256(data).hexdigest()}


def main() -> int:
    envelope = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    payload = envelope.get("payload") or {}
    behaviour = str(payload.get("misbehaviour") or "ok")
    result_path = Path(envelope["result_path"])
    out_dir = Path(envelope["out_dir"])

    if behaviour == "hang":
        # A descendant that outlives a naive kill unless the whole tree goes.
        child_marker = payload.get("child_marker")
        if child_marker:
            Path(str(child_marker)).write_text(str(os.getpid()), encoding="utf-8")
        marker = Path(str(payload["descendant_marker"]))
        subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "-c",
                "import os,sys,time,pathlib;"
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
                "time.sleep(600)",
                str(marker),
            ]
        )
        while True:
            time.sleep(1)

    if behaviour == "clean_exit_with_descendant":
        marker = Path(str(payload["descendant_marker"]))
        subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "-c",
                "import os,sys,time,pathlib;"
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
                "time.sleep(600)",
                str(marker),
            ]
        )
        deadline = time.monotonic() + 5.0
        while not marker.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)

    if behaviour == "crash":
        # SIGABRT, the shape a native OCC failure arrives in.
        os.abort()

    if behaviour == "memory_hog":
        blocks = []
        while True:
            blocks.append(bytearray(32 * 1024 * 1024))
            for index in range(0, len(blocks[-1]), 4096):
                blocks[-1][index] = 1
            time.sleep(0.02)

    if behaviour == "oversized_result":
        _write_result(
            result_path,
            {
                "protocol": 1,
                "task": envelope["task"],
                "ok": True,
                "result": {"padding": "x" * int(payload["padding_bytes"])},
                "artifacts": {},
            },
        )
        return 0

    if behaviour == "not_json":
        result_path.write_text("{definitely not json", encoding="utf-8")
        return 0

    if behaviour == "wrong_protocol":
        _write_result(result_path, {"protocol": 99, "task": envelope["task"], "ok": True})
        return 0

    if behaviour == "no_result":
        return 0

    if behaviour == "nonfinite":
        result_path.write_text(
            '{"protocol": 1, "task": "%s", "ok": true, "result": {"value": NaN}, '
            '"artifacts": {}}' % envelope["task"],
            encoding="utf-8",
        )
        return 0

    if behaviour == "absolute_artifact":
        target = Path(str(payload["escape_target"]))
        target.write_bytes(b"escaped")
        _write_result(
            result_path,
            {
                "protocol": 1,
                "task": envelope["task"],
                "ok": True,
                "result": {},
                "artifacts": {str(target): _record(b"escaped")},
            },
        )
        return 0

    if behaviour == "traversal_artifact":
        _write_result(
            result_path,
            {
                "protocol": 1,
                "task": envelope["task"],
                "ok": True,
                "result": {},
                "artifacts": {"../../escaped.msh": _record(b"escaped")},
            },
        )
        return 0

    if behaviour == "symlink_artifact":
        target = Path(str(payload["escape_target"]))
        target.write_bytes(b"secret")
        link = out_dir / "mesh.msh"
        link.symlink_to(target)
        _write_result(
            result_path,
            {
                "protocol": 1,
                "task": envelope["task"],
                "ok": True,
                "result": {},
                "artifacts": {"mesh.msh": _record(b"secret")},
            },
        )
        return 0

    if behaviour == "undeclared_artifact":
        (out_dir / "mesh.msh").write_bytes(b"declared")
        (out_dir / "extra.msh").write_bytes(b"sneaked in")
        _write_result(
            result_path,
            {
                "protocol": 1,
                "task": envelope["task"],
                "ok": True,
                "result": {},
                "artifacts": {"mesh.msh": _record(b"declared")},
            },
        )
        return 0

    if behaviour == "checksum_mismatch":
        (out_dir / "mesh.msh").write_bytes(b"actual bytes")
        _write_result(
            result_path,
            {
                "protocol": 1,
                "task": envelope["task"],
                "ok": True,
                "result": {},
                "artifacts": {"mesh.msh": _record(b"forged bytes")},
            },
        )
        return 0

    if behaviour == "oversized_artifact":
        data = b"m" * int(payload["artifact_bytes"])
        (out_dir / "mesh.msh").write_bytes(data)
        _write_result(
            result_path,
            {
                "protocol": 1,
                "task": envelope["task"],
                "ok": True,
                "result": {},
                "artifacts": {"mesh.msh": _record(data)},
            },
        )
        return 0

    if behaviour == "unexpected_artifact_name":
        (out_dir / "mesh.msh").write_bytes(b"fine")
        _write_result(
            result_path,
            {
                "protocol": 1,
                "task": envelope["task"],
                "ok": True,
                "result": {},
                "artifacts": {"mesh.msh": _record(b"fine")},
            },
        )
        return 0

    if behaviour == "report_environment":
        source = Path(envelope["source"]["path"])
        _write_result(
            result_path,
            {
                "protocol": 1,
                "task": envelope["task"],
                "ok": True,
                "result": {
                    "environment": dict(os.environ),
                    "cwd": os.getcwd(),
                    "source_head": source.read_bytes()[:32].decode("latin-1"),
                    "ppid": os.getppid(),
                    "pid": os.getpid(),
                    "session_leader": os.getpid() == os.getsid(0) if hasattr(os, "getsid") else None,
                    "network_error": _probe_network(),
                },
                "artifacts": {},
            },
        )
        return 0

    if behaviour == "noisy_then_ok":
        for _ in range(int(payload.get("noise_lines", 5000))):
            sys.stdout.write("native chatter that nobody asked for\n")
        sys.stdout.flush()
        _write_result(
            result_path,
            {
                "protocol": 1,
                "task": envelope["task"],
                "ok": True,
                "result": {"fine": True},
                "artifacts": {},
            },
        )
        return 0

    if behaviour == "refuse":
        _write_result(
            result_path,
            {
                "protocol": 1,
                "task": envelope["task"],
                "ok": False,
                "error": {
                    "type": str(payload.get("error_type") or "ImportedMeshError"),
                    "message": str(payload.get("error_message") or "role resolution: nope"),
                    "area_drift_sources": list(payload.get("area_drift_sources") or []),
                },
            },
        )
        return 1

    data = b"$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"
    (out_dir / "mesh.msh").write_bytes(data)
    _write_result(
        result_path,
        {
            "protocol": 1,
            "task": envelope["task"],
            "ok": True,
            "result": {"built": {"ok": True}},
            "artifacts": {"mesh.msh": _record(data)},
        },
    )
    return 0


def _probe_network() -> str:
    try:
        import socket

        socket.create_connection(("127.0.0.1", 9), timeout=0.2)
    except Exception as exc:  # noqa: BLE001 - the message is the observation
        return f"{type(exc).__name__}: {exc}"
    return ""


if __name__ == "__main__":
    sys.exit(main())
