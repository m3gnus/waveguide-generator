import os
import subprocess

from server.cadlink.intake import native_source


def _run_isolated(argv, cwd):
    # -I ignores the working directory and PYTHONPATH, as a bundled
    # interpreter's ._pth file does.
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    return subprocess.run(
        [argv[0], "-I", *argv[1:]], cwd=cwd, env=env, capture_output=True, text=True, timeout=120
    )


def test_the_worker_imports_server_without_the_working_directory_or_pythonpath(tmp_path):
    stage = tmp_path / "missing-stage"
    run = _run_isolated(native_source._worker_command(stage), tmp_path)

    assert "No module named 'server'" not in run.stderr
    # It got as far as reading its request: the worker module itself ran.
    assert run.returncode != 0 and "request.json" in run.stderr


def test_the_worker_command_passes_the_stage_as_its_only_argument(tmp_path):
    argv = native_source._worker_command(tmp_path)

    assert argv[-2:] == ["server.cadlink.intake.native_source", str(tmp_path)]
    assert "-m" not in argv
