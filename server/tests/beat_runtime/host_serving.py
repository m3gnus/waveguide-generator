"""Run a bound WorkerHost's serve() on a thread and prove that it stops."""

from __future__ import annotations

from contextlib import contextmanager
import threading


class StopCountingListener:
    """The host's listener, counting accept() calls begun once it is stopping.

    serve() checks _stopping before every accept and polls accept with a short
    timeout, so at most one call (the one it was about to make when the stop
    came) may begin after stopping, however late the thread is scheduled. With
    ``closing_wakes_accept=False``, close() leaves the socket open until serve()
    has returned: closing a listener from another thread need not interrupt a
    pending accept, on Windows in particular, so serve() must not rely on it.
    """

    def __init__(self, owner, *, closing_wakes_accept=True):
        self.owner, self.listener = owner, owner._server
        self.closing_wakes_accept = closing_wakes_accept
        self.after_stop = 0

    def settimeout(self, value):
        self.listener.settimeout(value)

    def accept(self):
        if self.owner._stopping.is_set():
            self.after_stop += 1
        return self.listener.accept()

    def close(self):
        if self.closing_wakes_accept:
            self.listener.close()


@contextmanager
def serving(owner, *, closing_wakes_accept=True):
    """Serve ``owner`` (already bound) on a thread; close it and join on exit.

    The body may join the yielded thread itself to wait for a self-initiated
    exit (idle expiry, accept-error budget).
    """
    listener = owner._server = StopCountingListener(owner, closing_wakes_accept=closing_wakes_accept)
    thread = threading.Thread(target=owner.serve)
    thread.start()
    try:
        yield thread
    finally:
        try:
            owner.close()
            # Assert that serve() stops at its next poll, not how soon the
            # runner schedules it: a hosted Windows runner stalled every
            # worker for over two seconds and failed a 2 s join here. A real
            # hang is caught by pytest.ini's faulthandler_timeout.
            thread.join()
            assert listener.after_stop <= 1
        finally:
            listener.listener.close()
