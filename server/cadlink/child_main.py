"""The disposable child that is allowed to touch untrusted STEP.

Started as ``python -s -B -m server.cadlink.child_main`` by
:mod:`server.cadlink.isolation`, once per artifact, never reused.  It reads one
JSON envelope from stdin, does exactly one task, writes one JSON result into
its staging directory, and exits.

Two ordering rules matter here and are easy to break by accident:

* **Confinement happens at import**, before the envelope is read.  On Windows
  the parent assigns the job object between spawn and the first stdin write, so
  this module must be cheap to import and must not do anything interesting
  until the envelope arrives.  On POSIX the same ordering means the resource
  limits and the parent watchdog are in force before any payload is parsed.
* **Nothing heavy is imported at module scope.**  ``gmsh`` and the mesher are
  imported inside the task, after confinement, so a slow or failing import
  cannot happen in the unconfined window.

The child has no credentials, no database handle, no data directory, and no
writable path it was not given.  It reports a refusal in the same structured
result it would report a success in; the parent decides what that means.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from typing import Any


PROTOCOL_VERSION = 1
#: The parent's envelope is its own writing, but reading stdin without a bound
#: is the habit this whole change exists to remove.
_MAX_ENVELOPE_BYTES = 8 * 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024
_PARENT_POLL_S = 1.0


class ChildTaskError(Exception):
    """A refusal the parent should report with its original wording."""

    def __init__(self, message: str, *, error_type: str = "") -> None:
        super().__init__(message)
        self.error_type = error_type or type(self).__name__


# -- confinement -----------------------------------------------------------


def _limit_resources() -> None:
    """Apply the process limits that are meaningful on this platform.

    Notably *not* ``RLIMIT_AS``.  A Python process with gmsh imported reserves
    roughly 435 GB of address space on macOS against 52 MB resident, so an
    address-space cap set anywhere near the gate's resident-memory figure would
    refuse every legitimate import.  Resident memory is enforced by the parent,
    which samples it, and on Windows by the job object's commit limit.  What is
    set here is what a limit genuinely means everywhere: no core dumps, and no
    single file larger than one staged artifact is allowed to be.
    """

    try:
        import resource
    except ImportError:  # pragma: no cover - Windows has no resource module
        return
    with_soft = os.environ.get("WG_CHILD_MAX_FILE_BYTES")
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ValueError, OSError):
        pass
    if with_soft:
        try:
            cap = int(with_soft)
            hard = resource.getrlimit(resource.RLIMIT_FSIZE)[1]
            if hard != resource.RLIM_INFINITY:
                cap = min(cap, hard)
            resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
        except (ValueError, OSError):
            pass


def _block_network() -> None:
    """Refuse Python-level network access from inside the child.

    This is a guard rail, not a sandbox: it stops the Python layer and anything
    built on it, and it does not stop a native library that opens a socket
    itself.  The gate says so explicitly -- container or ``seccomp`` hardening
    may be added on top, but the process boundary is the required part, and
    this makes the intent enforceable in the layer WG actually controls.

    The *methods* are replaced, not ``socket.socket`` itself: ``ssl`` does
    ``class SSLSocket(socket)`` at import, so swapping the class out for a
    function breaks importing ``asyncio`` several layers down.
    """

    import socket

    def refused(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("the isolated CAD child is not allowed to use the network")

    for name in ("connect", "connect_ex", "bind", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            setattr(socket.socket, name, refused)
    for name in ("create_connection", "create_server", "getaddrinfo"):
        if hasattr(socket, name):
            setattr(socket, name, refused)


def _watch_parent() -> None:
    """Exit if the parent goes away, so no orphan keeps holding the STEP.

    On POSIX the child is a session leader (that is what makes the process
    group a clean kill target), which also means it does *not* get a signal
    when the parent dies.  Windows gets this from the job object's
    kill-on-close, so the watchdog is POSIX-only.
    """

    if os.name != "posix":
        return
    original = os.getppid()

    def poll() -> None:
        while True:
            time.sleep(_PARENT_POLL_S)
            if os.getppid() != original:
                os._exit(3)

    threading.Thread(target=poll, name="parent-watchdog", daemon=True).start()


def confine() -> None:
    """Apply every confinement this process can apply to itself."""

    _limit_resources()
    _block_network()


# Confinement runs at import so it is in force before the envelope is read and
# before anything heavy is imported. It is keyed on the marker the harness puts
# in the child's environment, because these are process-wide, irreversible
# changes: importing this module in the *server* to inspect it must not
# silently take the server's sockets away.
if os.environ.get("WG_ISOLATED_CAD_CHILD") == "1":
    confine()


# -- envelope --------------------------------------------------------------


def _read_envelope(stream: Any) -> dict[str, Any]:
    raw = stream.read(_MAX_ENVELOPE_BYTES + 1)
    if len(raw) > _MAX_ENVELOPE_BYTES:
        raise ChildTaskError("the task envelope exceeds the child's input limit")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ChildTaskError(f"the task envelope is not readable JSON: {exc}") from exc
    if not isinstance(envelope, dict) or envelope.get("protocol") != PROTOCOL_VERSION:
        raise ChildTaskError("the task envelope does not declare a supported protocol")
    return envelope


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _verified_source(envelope: dict[str, Any]) -> Path:
    """Re-verify the STEP the parent staged before opening it.

    Both ends check the same checksum.  The parent's check proves what it
    copied; this one proves that what the child is about to hand to OCC is
    still that file and not something swapped underneath it.
    """

    source = envelope.get("source")
    if not isinstance(source, dict):
        raise ChildTaskError("the task envelope names no source STEP")
    path = Path(str(source.get("path")))
    if not path.is_file() or path.is_symlink():
        raise ChildTaskError("the staged source STEP is missing or is a symlink")
    if _sha256_file(path) != source.get("sha256"):
        raise ChildTaskError("the staged source STEP does not match its checksum")
    if path.stat().st_size != source.get("size_bytes"):
        raise ChildTaskError("the staged source STEP does not match its declared size")
    return path


# -- tasks -----------------------------------------------------------------


def _task_inspect(
    envelope: dict[str, Any], source: Path, out_dir: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Scan the STEP text, then observe its OCC topology."""

    from server.cadlink.step_evidence import ReturnedStepError, observe_returned_step
    from server.cadlink.step_text import scan_step_text

    payload = envelope.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    contract = payload.get("contract")
    if not isinstance(contract, dict):
        raise ChildTaskError("the inspect task received no source contract")
    baseline = payload.get("baseline_fingerprint")

    text_evidence = scan_step_text(source)
    try:
        evidence = observe_returned_step(
            source, contract, baseline if isinstance(baseline, dict) else None
        )
    except ReturnedStepError as exc:
        raise ChildTaskError(str(exc), error_type="ReturnedStepError") from exc
    evidence["step_text"] = text_evidence.as_dict()
    return {"evidence": evidence}, {}


def _task_mesh(
    envelope: dict[str, Any], source: Path, out_dir: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the solver mesh and, when asked, the viewport mesh."""

    from server.cadlink.step_text import scan_step_text
    from server.mesh.imported import build_imported_mesh

    payload = envelope.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    manifest = payload.get("manifest")
    sizes = payload.get("sizes")
    if not isinstance(manifest, dict) or not isinstance(sizes, dict):
        raise ChildTaskError("the mesh task received no manifest or mesh sizes")

    # The text budget applies before OCC sees the file here too. Meshing is a
    # second, independent opening of the same bytes, so it gets the same scan
    # rather than trusting that inspection already ran.
    text_evidence = scan_step_text(source)

    built = build_imported_mesh(
        source,
        manifest,
        sizes,
        skipped_source_ids=[str(value) for value in payload.get("skipped_source_ids") or ()],
        options=payload.get("options") or {},
        include_viewport_mesh=bool(payload.get("include_viewport_mesh", True)),
    )
    msh_text = built.pop("msh_text", None)
    viewport_text = built.pop("viewport_msh_text", None)
    if not isinstance(msh_text, str):
        raise ChildTaskError("the mesh build produced no solver mesh text")
    built["step_text"] = text_evidence.as_dict()

    artifacts = {"mesh.msh": _stage(out_dir, "mesh.msh", msh_text)}
    if isinstance(viewport_text, str):
        artifacts["viewport.msh"] = _stage(out_dir, "viewport.msh", viewport_text)
    return {"built": built}, artifacts


def _stage(out_dir: Path, name: str, text: str) -> dict[str, Any]:
    """Write one artifact and describe it the way the parent will verify it."""

    import hashlib

    data = text.encode("utf-8")
    path = out_dir / name
    path.write_bytes(data)
    return {
        "size_bytes": len(data),
        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
    }


def _task_viewport(
    envelope: dict[str, Any], source: Path, out_dir: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-tessellate the display mesh for a solver-mesh cache hit.

    Its own invocation because it is its own reopening of the same untrusted
    STEP: a cached solver mesh says nothing about whether reading the file
    again is safe.
    """

    from server.cadlink.step_text import scan_step_text
    from server.mesh.imported import build_imported_viewport_mesh

    payload = envelope.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    manifest = payload.get("manifest")
    recipe = payload.get("recipe")
    allocation = payload.get("tag_allocation")
    expected = payload.get("expected_geometry_hash")
    if not (
        isinstance(manifest, dict)
        and isinstance(recipe, dict)
        and isinstance(allocation, dict)
        and isinstance(expected, str)
    ):
        raise ChildTaskError("the viewport task received an incomplete request")

    text_evidence = scan_step_text(source)
    viewport = build_imported_viewport_mesh(
        source,
        manifest,
        recipe,
        expected_geometry_hash=expected,
        tag_allocation=allocation,
    )
    msh_text = viewport.pop("msh_text", None)
    if not isinstance(msh_text, str):
        raise ChildTaskError("the viewport build produced no mesh text")
    viewport["step_text"] = text_evidence.as_dict()
    return (
        {"viewport": viewport},
        {"viewport.msh": _stage(out_dir, "viewport.msh", msh_text)},
    )


_TASKS = {"inspect": _task_inspect, "mesh": _task_mesh, "viewport": _task_viewport}

#: Tasks that need a gmsh session opened around them. In the server this was
#: the persistent worker thread's job; here each child owns exactly one session
#: for exactly one task, and takes the whole process down with it if OCC does.
_GMSH_TASKS = frozenset(_TASKS)


def _open_gmsh_session() -> Any:
    """Initialize gmsh the way the worker thread does, or report why not.

    ``interruptible=False`` because gmsh's SIGINT handler may only be installed
    from the main thread of the *interpreter that owns signals*, and because a
    child that catches its own interrupt would be harder for the parent to
    stop, not easier.
    """

    try:
        import gmsh
    except ImportError as exc:
        raise ChildTaskError(
            "CAD-return ingestion requires hornlab-waveguide-mesher, gmsh, and meshio",
            error_type="ImportedMeshDependencyError",
        ) from exc
    if not gmsh.isInitialized():
        gmsh.initialize(interruptible=False)
        gmsh.option.setNumber("General.Terminal", 0)
    return gmsh


# -- entry point -----------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Coerce the numpy scalars the mesher returns into plain JSON.

    Only reached for values ``json`` cannot encode, which in practice means
    numpy. ``allow_nan=False`` still applies afterwards, so a numpy NaN is a
    refusal rather than a token the parent has to know how to reject.
    """

    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return tolist()
    raise TypeError(f"{type(value).__name__} cannot cross the isolation boundary")


def _write_result(path: Path, envelope: dict[str, Any]) -> None:
    """Publish the result atomically, so a partial write is never read."""

    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(
        json.dumps(envelope, allow_nan=False, ensure_ascii=True, default=_jsonable),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    _watch_parent()
    result_path: Path | None = None
    task_name = ""
    try:
        envelope = _read_envelope(sys.stdin.buffer)
        result_path = Path(str(envelope.get("result_path")))
        task_name = str(envelope.get("task") or "")
        out_dir = Path(str(envelope.get("out_dir")))
        handler = _TASKS.get(task_name)
        if handler is None:
            raise ChildTaskError(f"unknown isolated CAD task {task_name!r}")
        source = _verified_source(envelope)
        if task_name in _GMSH_TASKS:
            _open_gmsh_session()
        result, artifacts = handler(envelope, source, out_dir)
    except BaseException as exc:  # noqa: BLE001 - every failure is a refusal
        if result_path is None:
            # Without a result path there is nowhere to answer; the parent
            # reads the exit code and the stderr tail instead.
            traceback.print_exc()
            return 2
        error: dict[str, Any] = {
            "type": getattr(exc, "error_type", type(exc).__name__),
            "message": str(exc) or type(exc).__name__,
        }
        # ``RoleResolutionError`` carries the sources whose area drifted, and
        # ingestion turns that list into an actionable override prompt. It has
        # to survive the boundary or the refusal loses its remedy.
        drift = getattr(exc, "area_drift_sources", None)
        if isinstance(drift, (list, tuple)):
            error["area_drift_sources"] = [str(value) for value in drift]
        _write_result(
            result_path,
            {
                "protocol": PROTOCOL_VERSION,
                "task": task_name,
                "ok": False,
                "error": error,
            },
        )
        traceback.print_exc()
        return 1
    _write_result(
        result_path,
        {
            "protocol": PROTOCOL_VERSION,
            "task": task_name,
            "ok": True,
            "result": result,
            "artifacts": artifacts,
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
