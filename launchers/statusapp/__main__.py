"""Run the WG status window, or the original terminal server with --no-gui."""

from __future__ import annotations

from pathlib import Path
import sys

#: Checked before terminal mode hands over to the server. This is the same file
#: ``server.app.FRONTEND_DIST`` is built from, resolved from this module so the
#: check costs no application import.
FRONTEND_INDEX = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--no-gui" in arguments:
        arguments.remove("--no-gui")
        # The status window refuses to start the backend when the interface is
        # missing (controller.poll). Terminal mode bypasses that guard by going
        # straight to the server, which then raises a starlette RuntimeError
        # from deep inside create_app -- "Directory '.../frontend/dist' does not
        # exist" -- and a traceback reads as a broken application rather than an
        # unbuilt one. Refuse in the same words the status window uses, so the
        # two modes cannot disagree about what is wrong.
        if not FRONTEND_INDEX.is_file():
            from .controller import missing_frontend_reason

            print(
                "Waveguide Generator did not start because the interface is "
                f"missing: {missing_frontend_reason()}",
                file=sys.stderr,
            )
            return 1

        from launch.serve import main as serve

        return serve(arguments)

    from .controller import StatusController

    controller = StatusController(server_args=arguments)
    try:
        from .view import run
    except ImportError as exc:
        print(
            "Waveguide Generator could not open its status window because tkinter "
            f"is unavailable: {exc}\nRun again with --no-gui for terminal mode.",
            file=sys.stderr,
        )
        return 1
    return run(controller)


if __name__ == "__main__":
    raise SystemExit(main())
