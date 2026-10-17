"""Give Gmsh's OpenMP runtime an initial thread that outlives every Gmsh call.

The Gmsh 4.15.2 macOS library links its own copy of the LLVM OpenMP runtime.
That runtime registers whichever thread first calls into it as the process's
initial thread and keeps using that thread's state for the life of the process.
When that thread is a worker that later exits -- the gmsh owner thread after
``shutdown_gmsh_worker``, or a preview executor thread after its service shuts
down -- the next ``gmsh.initialize()`` on any other thread faults inside
``__kmpc_critical_with_hint`` (SIGSEGV or SIGBUS). The minimal reproduction is
initialize/finalize on a thread, join it, then initialize on the main thread.

``pin_gmsh_openmp_initial_thread`` makes the first OpenMP call itself, from the
main thread when it is the caller and otherwise from a daemon thread parked for
the rest of the process, so the registered thread never goes away. Call it
before any in-process ``gmsh.initialize()``; once the runtime has been
initialized by a thread that has already exited, pinning comes too late.
"""

from __future__ import annotations

import threading
from typing import Any


PIN_THREAD_NAME = "gmsh-openmp-root"

_lock = threading.Lock()
_pinned = False


def _first_openmp_call(gmsh: Any) -> None:
    function = getattr(getattr(gmsh, "lib", None), "omp_get_max_threads", None)
    if function is not None:
        function()


def pin_gmsh_openmp_initial_thread() -> None:
    """Make a never-exiting thread the OpenMP initial thread. Idempotent."""

    global _pinned
    if _pinned:
        return
    with _lock:
        if _pinned:
            return
        try:
            import gmsh
        except Exception:  # the caller's own import gives the useful error
            return
        if threading.current_thread() is threading.main_thread():
            _first_openmp_call(gmsh)
        else:
            called = threading.Event()
            failure: list[BaseException] = []

            def hold() -> None:
                try:
                    _first_openmp_call(gmsh)
                except BaseException as exc:  # pragma: no cover - native lookup only
                    failure.append(exc)
                finally:
                    called.set()
                # Never woken: the thread, and the stack the runtime recorded
                # for it, stay alive until the process exits.
                threading.Event().wait()

            threading.Thread(target=hold, name=PIN_THREAD_NAME, daemon=True).start()
            called.wait()
            if failure:
                raise failure[0]
        _pinned = True


__all__ = ["PIN_THREAD_NAME", "pin_gmsh_openmp_initial_thread"]
