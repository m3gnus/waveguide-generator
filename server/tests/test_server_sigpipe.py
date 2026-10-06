"""POSIX proof of the installed gate's quick reopen/shutdown SIGPIPE race.

Use the qualifier's real subprocess and stop protocol. Only the expensive
native warmup and HTTP application are doubles: libc changes SIGPIPE behind
Python's cache, and a worker exits between is_alive() and its shutdown send.
"""

from __future__ import annotations

from contextlib import nullcontext
import os
from pathlib import Path
import sys

import pytest

from scripts import qualify_installed_cpu as gate


pytestmark = pytest.mark.skipif(os.name != "posix", reason="SIGPIPE is POSIX-only")
REPO_ROOT = Path(__file__).resolve().parents[2]

_SERVER = r'''
import argparse
import asyncio
import ctypes
import multiprocessing
import os
from pathlib import Path
import signal
import stat
import sys
from types import SimpleNamespace

from launch import serve
from server.mesh import gmsh_worker
from server.platform.signal_rearm import restore_sigpipe_ignore
from server.platform.warmup import BackgroundWarmup
from server.solver.bempp_process import BemppProcessHost

if os.environ["DISABLE_SIGPIPE_REARM"] == "1":
    # Negative control: the old launcher restored only the shutdown signals.
    serve.restore_sigpipe_ignore = lambda: None

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int)
parser.add_argument("--data-dir", type=Path)
parser.add_argument("--status-control", type=Path)
args, _ = parser.parse_known_args()
assert stat.S_ISREG(os.fstat(1).st_mode)
assert os.fstat(1) == os.fstat(2)
marker = args.data_dir / "saved-result"
reopening = marker.exists()

c_signal = ctypes.CDLL(None).signal
c_signal.argtypes = [ctypes.c_int, ctypes.c_void_p]
c_signal.restype = ctypes.c_void_p
initialized = False

def native_boundary(boundary):
    if reopening and boundary == os.environ["RESET_BOUNDARY"]:
        c_signal(signal.SIGPIPE, ctypes.c_void_p(int(signal.SIG_DFL)))
        # Python's cached disposition cannot detect this native change.
        assert signal.getsignal(signal.SIGPIPE) == signal.SIG_IGN
        print("native SIGPIPE reset at " + boundary, flush=True)

def initialize(**kwargs):
    global initialized
    assert kwargs == {"interruptible": False}
    initialized = True
    native_boundary("initialize")

def finalize():
    global initialized
    initialized = False
    native_boundary("finalize")

sys.modules["gmsh"] = SimpleNamespace(
    isInitialized=lambda: initialized, initialize=initialize, finalize=finalize,
)

def work():
    if os.environ["DISABLE_SIGPIPE_REARM"] == "1":
        return  # keep the negative control's fatal write at shutdown
    # Exercise the initialize rearm independently of the finalize rearm.
    reader, writer = os.pipe()
    os.close(reader)
    try:
        try:
            os.write(writer, b"native work writing to a closed pipe")
        except BrokenPipeError:
            pass
        else:
            raise AssertionError("a pipe without a reader accepted a write")
    finally:
        os.close(writer)

async def main():
    server = SimpleNamespace(should_exit=False, force_exit=False)
    with serve._capture_shutdown_signals(server):
        # Same ordering as create_app: schedule warmup, then the one-shot
        # startup restore. Native work starts after the startup handler returns.
        warmup = BackgroundWarmup("gmsh-session", lambda: gmsh_worker.run_on_gmsh_worker(work))
        await warmup.start()
        restore_sigpipe_ignore()
        await warmup.task

        print("health ready", flush=True)
        while not args.status_control.exists():
            await asyncio.sleep(0.01)

        # Real worker shutdown send, with a deterministic peer-exit race.
        # Output still goes to an open regular file, never a closed log pipe.
        parent, peer = multiprocessing.Pipe(duplex=True)
        class ExitingWorker:
            alive = True
            def is_alive(self):
                if self.alive:
                    self.alive = False
                    peer.close()
                    return True
                return False
            def join(self, *args):
                pass
        host = BemppProcessHost()
        host._connection = parent
        host._process = ExitingWorker()
        host.close()
        print("worker shutdown survived", flush=True)
        marker.write_text("result retained")
        await warmup.stop()
        await gmsh_worker.shutdown_gmsh_worker()

asyncio.run(main())
'''


@pytest.mark.parametrize("boundary", ["initialize", "finalize"])
@pytest.mark.parametrize("rearm", [True, False], ids=["repaired", "negative-control"])
def test_imported_server_second_start_keeps_the_sigpipe_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, rearm: bool,
) -> None:
    app = tmp_path / "payload" / "app"
    launcher = app / "launch" / "serve.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(_SERVER, encoding="utf-8")
    environment = dict(
        os.environ, PYTHONPATH=str(REPO_ROOT), RESET_BOUNDARY=boundary,
        DISABLE_SIGPIPE_REARM=str(int(not rearm)),
    )
    monkeypatch.setattr(gate, "STARTUP_TIMEOUT_S", 5.0)
    monkeypatch.setattr(gate, "SHUTDOWN_TIMEOUT_S", 5.0)
    monkeypatch.setattr(gate, "POLL_INTERVAL_S", 0.02)
    # Readiness uses the log so this test also runs in sandboxes that deny
    # binding loopback sockets. Popen's streams and stop checks stay real.
    monkeypatch.setattr(gate, "free_port", lambda: 3100)
    for start in (1, 2):
        log = tmp_path / f"imported-server-{start}.log"
        monkeypatch.setattr(
            gate, "http",
            lambda *_args, **_kwargs: "health ready" in log.read_text(encoding="utf-8"),
        )
        dies = start == 2 and not rearm
        expected = (
            pytest.raises(gate.QualificationError, match=r"exited -13;.*imported-server-2.log")
            if dies else nullcontext()
        )
        with expected:
            with gate.Server(
                Path(sys.executable), app, environment, tmp_path / "data",
                tmp_path / "status" / f"imported-{start}", log,
            ):
                pass
        transcript = log.read_text(encoding="utf-8")
        assert ("worker shutdown survived" in transcript) is not dies
        if start == 2:
            assert f"native SIGPIPE reset at {boundary}" in transcript
    assert (tmp_path / "data" / "saved-result").read_text() == "result retained"
