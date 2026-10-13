"""Child-local startup evidence for real backend tests; no production hooks."""
from __future__ import annotations

import faulthandler
import functools
from pathlib import Path
import runpy
import sys


def traced_startup_handler(handler):
    from starlette._utils import is_async_callable

    name = f"{getattr(handler, '__module__', type(handler).__module__)}." + getattr(handler, "__qualname__", type(handler).__qualname__)

    def entered():
        print(f"Startup diagnostic enter: {name}", file=sys.stderr, flush=True)

    def finished():
        print(f"Startup diagnostic exit: {name}", file=sys.stderr, flush=True)

    if is_async_callable(handler):
        @functools.wraps(handler)
        async def traced():
            entered()
            result = await handler()
            finished()
            return result
    else:
        @functools.wraps(handler)
        def traced():
            entered()
            result = handler()
            finished()
            return result
    traced._startup_diagnostic_traced = True
    return traced


def startup_trace_command(command, *, dump_after=80):
    """Run the same interpreter, script and argv with a child-owned stack timer."""
    if len(command) < 2 or Path(command[1]).suffix != ".py":
        raise ValueError("startup tracing requires an interpreter and script")
    return [command[0], str(Path(__file__).resolve()), str(dump_after), *command[1:]]


def main():
    delay = float(sys.argv[1])
    if not 0 < delay <= 80:
        raise ValueError("startup stack deadline must be positive and at most 80 seconds")
    entry = sys.argv[2]
    sys.argv = [entry, *sys.argv[3:]]
    sys.path[0] = str(Path(entry).parent)
    faulthandler.dump_traceback_later(delay)
    from fastapi.routing import APIRouter

    add_handler = APIRouter.add_event_handler

    def add_traced(self, event_type, handler):
        if event_type == "startup" and not getattr(handler, "_startup_diagnostic_traced", False):
            handler = traced_startup_handler(handler)
        return add_handler(self, event_type, handler)

    APIRouter.add_event_handler = add_traced
    try:
        runpy.run_path(entry, run_name="__main__")
    finally:
        APIRouter.add_event_handler = add_handler
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
