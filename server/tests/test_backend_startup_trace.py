"""Startup diagnostics preserve handlers, arguments and original failures."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

from server.tests.backend_startup_trace import startup_trace_command, traced_startup_handler


@pytest.mark.parametrize("asynchronous", [False, True])
def test_startup_markers_preserve_callback_value_and_failure(asynchronous, capsys):
    calls = []

    def operation():
        calls.append("called")
        return 17

    async def async_operation():
        return operation()

    handler = traced_startup_handler(async_operation if asynchronous else operation)
    value = asyncio.run(handler()) if asynchronous else handler()
    assert value == 17 and calls == ["called"]
    output = capsys.readouterr().err
    assert "Startup diagnostic enter:" in output and "Startup diagnostic exit:" in output

    def refused():
        raise RuntimeError("original startup refusal")

    async def async_refused():
        return refused()

    handler = traced_startup_handler(async_refused if asynchronous else refused)
    with pytest.raises(RuntimeError, match="original startup refusal"):
        asyncio.run(handler()) if asynchronous else handler()
    output = capsys.readouterr().err
    assert "Startup diagnostic enter:" in output and "Startup diagnostic exit:" not in output


def test_child_trace_preserves_interpreter_entry_arguments_and_exit(tmp_path):
    entry = tmp_path / "entry.py"
    entry.write_text("import json,sys; print(json.dumps([sys.executable,sys.argv,sys.path[0]])); sys.exit(7)")
    command = [sys.executable, str(entry), "a b", "--headless"]
    completed = subprocess.run(startup_trace_command(command), capture_output=True, text=True, timeout=15)
    assert completed.returncode == 7, completed.stderr
    interpreter, arguments, script_directory = json.loads(completed.stdout)
    assert Path(interpreter) == Path(sys.executable)
    assert arguments == command[1:]
    assert script_directory == str(entry.parent)


def test_startup_trace_rejects_non_script_commands():
    with pytest.raises(ValueError, match="interpreter and script"):
        startup_trace_command([sys.executable, "-m", "server"])


def test_callable_instance_keeps_async_startup_semantics(capsys):
    class Handler:
        async def __call__(self):
            return 23

    result = asyncio.run(traced_startup_handler(Handler())())
    assert result == 23
    assert "Startup diagnostic exit:" in capsys.readouterr().err
