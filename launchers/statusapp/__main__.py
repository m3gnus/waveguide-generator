"""Run the WG2 status window, or the original terminal server with --no-gui."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--no-gui" in arguments:
        arguments.remove("--no-gui")
        from launch.serve import main as serve

        return serve(arguments)

    from .controller import StatusController

    controller = StatusController(server_args=arguments)
    try:
        from .view import run
    except ImportError as exc:
        print(
            "Waveguide Generator v2 could not open its status window because tkinter "
            f"is unavailable: {exc}\nRun again with --no-gui for terminal mode.",
            file=sys.stderr,
        )
        return 1
    return run(controller)


if __name__ == "__main__":
    raise SystemExit(main())
