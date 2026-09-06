"""The isolated Qt probe must never leave a renderer behind."""

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from launchers.desktop import _run_linux_qt_probe


@pytest.mark.skipif(os.name != "posix", reason="Linux probe uses POSIX process groups")
@pytest.mark.parametrize("timeout", [False, True])
def test_probe_cleans_descendants_on_exit_and_timeout(tmp_path: Path, timeout: bool):
    heartbeat = tmp_path / "renderer-heartbeat"
    renderer = (
        "import time\nfrom pathlib import Path\n"
        f"p = Path({str(heartbeat)!r})\n"
        "while True:\n    p.write_text(str(time.monotonic_ns()))\n    time.sleep(.02)\n"
    )
    probe = (
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        f"subprocess.Popen([sys.executable, '-c', {renderer!r}])\n"
        f"p = Path({str(heartbeat)!r})\n"
        "deadline = time.monotonic() + 5\n"
        "while not p.exists() and time.monotonic() < deadline: time.sleep(.01)\n"
        "assert p.exists()\nprint('probe started', flush=True)\n"
        + ("time.sleep(60)\n" if timeout else "")
    )
    command = [sys.executable, "-c", probe]
    options = dict(env=dict(os.environ), stdin=subprocess.DEVNULL, timeout=1 if timeout else 10)
    started = time.monotonic()
    if timeout:
        with pytest.raises(subprocess.TimeoutExpired):
            _run_linux_qt_probe(command, **options)
    else:
        result = _run_linux_qt_probe(command, **options)
        assert result.returncode == 0, result.stderr
        assert "probe started" in result.stdout
    assert time.monotonic() - started < 8
    assert heartbeat.exists()
    last_write = heartbeat.read_text()
    time.sleep(.15)
    assert heartbeat.read_text() == last_write, "the renderer survived its probe"
