"""The server command line, defined once for the launcher and for the server.

``launch.serve`` imports the whole application in order to start it -- uvicorn,
``server.app``, the solvers behind it -- so the launcher cannot ask it what
arguments are valid without paying that import on every start. It therefore
used not to ask at all: anything the launcher did not recognise as a display
flag was forwarded to the server process, so ``--help`` became a *server*
argument, argparse printed usage into a pipe nobody reads, and the GUI event
loop kept running with no window able to explain itself. Reported on Fedora 44
on 2026-09-05 as "``waveguide-generator --help`` hangs forever"; the same hole
swallowed every typo, so ``--no-brwoser`` opened a window that never exited
instead of naming the flag.

The option surface therefore lives here, in a module that imports argparse and
pathlib and nothing else. Both parsers are built from it, which is what lets
the launcher reject a typo *before* a window exists and keeps the two from
ever disagreeing about what is accepted.
"""

from __future__ import annotations

import argparse
from pathlib import Path


#: What users type, on every platform: the installed command, the ``~/.local/bin``
#: symlink and the macOS/Windows launchers all present this name. argparse would
#: otherwise derive ``prog`` from ``sys.argv[0]`` and title the help
#: ``desktop.py``, which is a file nobody invoked.
PROGRAM_NAME = "waveguide-generator"


def add_server_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add every argument ``launch.serve`` accepts, and return the parser.

    The two suppressed options are the launcher-to-server private channel:
    they are real and must keep parsing, but they are not something a user is
    ever meant to type, so they stay out of ``--help``.
    """

    parser.add_argument("--port", type=int, help="preferred local port (default: 3100)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument(
        "--data-dir", type=Path, help="override the application data directory"
    )
    parser.add_argument("--status-control", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    return parser
