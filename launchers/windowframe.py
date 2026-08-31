"""Remove the OS title bar from the desktop window, and give the UI its buttons.

WG's window opens with a Windows caption above an application top bar that
already carries the brand, the file menu and every control the user reaches for.
Two stacked bars is one more than the app needs, and it is not what a modern
desktop application looks like: Claude, Spotify, VS Code and Codex all host their
own window controls inside their own chrome.

The obvious way to do that is pywebview's ``frameless=True``, and it is the wrong
way. On Windows it sets ``FormBorderStyle.None``, which clears ``WS_THICKFRAME``
-- and with it goes resizing, Aero Snap, Win+Arrow, snap layouts, the maximize
animation and the drop shadow. The window stops being a Windows window. pywebview
offers a JavaScript drag as compensation, which moves the window by feeding mouse
deltas back to Python: it lags, and it still cannot snap.

So this module does what Chromium does instead. The frame stays exactly as
Windows made it and only the caption is taken, by handling ``WM_NCCALCSIZE`` --
the single message that decides where the client area begins. Letting the default
handler run first preserves every inset Windows computed for the resize borders,
and only ``top`` is moved back. Measured on a 900x600 window: 39 px of non-client
area becomes 8 px, with ``WS_THICKFRAME``, ``WS_MINIMIZEBOX`` and
``WS_MAXIMIZEBOX`` all still set, and hit tests answering ``HTTOP``,
``HTTOPLEFT``, ``HTTOPRIGHT``, ``HTLEFT``, ``HTRIGHT``, ``HTBOTTOM`` and
``HTBOTTOMRIGHT`` -- so all eight resize grips survive and the app gains the
31 px the caption was using.

Dragging is then Windows' own rather than JavaScript's: WebView2's
``IsNonClientRegionSupportEnabled`` hands CSS ``app-region: drag`` regions to the
host as caption, which is what makes drag-to-snap, drag-to-top-to-maximize and
double-click-to-maximize work without a line of code implementing them.

Everything here is best-effort and reports whether it worked. The frontend draws
its window buttons only when the launcher answers ``customFrame: true``, so a
machine where any step fails keeps the OS title bar it already had rather than
ending up with a window that cannot be moved or closed.

Only the ctypes plumbing is Windows-only, and it is all deferred to call time:
``ctypes.WINFUNCTYPE`` and ``ctypes.wintypes`` do not exist on Linux or macOS,
and this module is imported by ``launchers.desktop`` on every platform the test
suite runs on.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, NamedTuple

log = logging.getLogger("wg.launch.frame")

GWL_STYLE = -16
GWLP_WNDPROC = -4
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WS_THICKFRAME = 0x00040000

HTCLIENT = 1
HTCAPTION = 2
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14

SM_CYFRAME = 33
SM_CXPADDEDBORDER = 92

SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020

#: How close to a corner still counts as a corner rather than an edge. Windows
#: uses roughly double the border thickness for its own frame, and matching it is
#: what stops the diagonal grip feeling like it is in the wrong place.
CORNER_GRIP = 16


class WindowBounds(NamedTuple):
    """A window rectangle in screen coordinates."""

    left: int
    top: int
    right: int
    bottom: int


def caption_free_top(requested_top: int, *, maximized: bool, border: int) -> int:
    """Where the client area should begin once the caption is given up.

    Restored, that is the top of the window rect: the caption is handed to the
    application whole. Maximized, the frame thickness has to come back, because a
    maximized window's rect deliberately overhangs the work area by exactly that
    much -- a client area that followed it there would push the app's own top
    bar, and the window buttons in it, off the top of the screen.
    """

    return requested_top + border if maximized else requested_top


def resize_hit(
    default: int,
    *,
    x: int,
    y: int,
    bounds: WindowBounds,
    border: int,
    maximized: bool,
) -> int:
    """Give the top edge back its resize grip.

    Reclaiming the caption also reclaimed the strip Windows was using as the top
    resize border, and WebView2 fills the client area, so nothing underneath
    would ever see the pointer there. What does arrive is ``HTCAPTION``, from the
    ``app-region: drag`` region the top bar declares -- so the last few pixels of
    it are upgraded to a resize grip on the way past. The other three edges are
    untouched and still resize exactly as Windows drew them.

    A maximized window is left alone: it has no edge to grab, and offering one
    would only un-maximize on a mis-click near the top of the screen.
    """

    if default not in (HTCAPTION, HTCLIENT):
        return default
    if maximized:
        return default
    if y - bounds.top >= border:
        return default
    if x - bounds.left < CORNER_GRIP:
        return HTTOPLEFT
    # ``right`` is exclusive, so the last pixel of the window is ``right - 1``.
    # Comparing it the same way as the left edge would make this corner one pixel
    # narrower than that one, which is the kind of asymmetry nobody reports and
    # everybody feels.
    if bounds.right - x <= CORNER_GRIP:
        return HTTOPRIGHT
    return HTTOP


def signed_word(value: int) -> int:
    """Read one packed coordinate out of an ``LPARAM``.

    Both halves are signed: a window on a monitor to the left of the primary one
    has negative screen coordinates, and reading them unsigned puts the pointer
    somewhere near x=65000 and silently disables the grip.
    """

    value &= 0xFFFF
    return value - 0x10000 if value >= 0x8000 else value


def _user32() -> Any:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowLongPtrW.restype = ctypes.c_longlong
    user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_longlong)
    user32.GetWindowLongPtrW.restype = ctypes.c_longlong
    user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.CallWindowProcW.restype = ctypes.c_longlong
    user32.CallWindowProcW.argtypes = (
        ctypes.c_longlong,
        wintypes.HWND,
        ctypes.c_uint,
        ctypes.c_ulonglong,
        ctypes.c_longlong,
    )
    return user32


class CustomFrame:
    """One window's replaced window procedure.

    The instance owns the callback thunk, and that ownership is the point: a
    thunk collected while Windows still holds a pointer to it is an access
    violation inside the message loop, with no Python traceback to explain it.
    The frame therefore lives as long as the window does.
    """

    def __init__(self, hwnd: int) -> None:
        import ctypes
        from ctypes import wintypes

        class NCCALCSIZE_PARAMS(ctypes.Structure):  # noqa: N801 - a Win32 structure name
            _fields_ = (("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p))

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._params_type = NCCALCSIZE_PARAMS
        self.hwnd = hwnd
        self._user32 = _user32()
        wndproc = ctypes.WINFUNCTYPE(
            ctypes.c_longlong,
            wintypes.HWND,
            ctypes.c_uint,
            ctypes.c_ulonglong,
            ctypes.c_longlong,
        )
        self._thunk = wndproc(self._proc)
        self._previous = self._user32.GetWindowLongPtrW(wintypes.HWND(hwnd), GWLP_WNDPROC)

    def install(self) -> None:
        ctypes, wintypes = self._ctypes, self._wintypes
        self._user32.SetWindowLongPtrW(
            wintypes.HWND(self.hwnd),
            GWLP_WNDPROC,
            ctypes.cast(self._thunk, ctypes.c_void_p).value,
        )
        # The non-client size is cached until something invalidates it, and
        # nothing has: ask for a recalculation now, or the caption stays put
        # until the user happens to resize the window.
        self._user32.SetWindowPos(
            wintypes.HWND(self.hwnd),
            None,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )

    def _border(self) -> int:
        return self._user32.GetSystemMetrics(SM_CYFRAME) + self._user32.GetSystemMetrics(
            SM_CXPADDEDBORDER
        )

    def _maximized(self, hwnd: int) -> bool:
        return bool(self._user32.IsZoomed(self._wintypes.HWND(hwnd)))

    def _proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == WM_NCCALCSIZE and wparam:
            return self._on_nccalcsize(hwnd, message, wparam, lparam)
        if message == WM_NCHITTEST:
            return self._on_nchittest(hwnd, message, wparam, lparam)
        return self._user32.CallWindowProcW(self._previous, hwnd, message, wparam, lparam)

    def _on_nccalcsize(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        params = self._params_type.from_address(lparam)
        requested_top = params.rgrc[0].top
        result = self._user32.CallWindowProcW(self._previous, hwnd, message, wparam, lparam)
        if result == 0:
            params.rgrc[0].top = caption_free_top(
                requested_top, maximized=self._maximized(hwnd), border=self._border()
            )
        return result

    def _on_nchittest(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        result = self._user32.CallWindowProcW(self._previous, hwnd, message, wparam, lparam)
        if result not in (HTCAPTION, HTCLIENT):
            return result
        rect = self._wintypes.RECT()
        if not self._user32.GetWindowRect(
            self._wintypes.HWND(hwnd), self._ctypes.byref(rect)
        ):
            return result
        return resize_hit(
            result,
            x=signed_word(lparam),
            y=signed_word(lparam >> 16),
            bounds=WindowBounds(rect.left, rect.top, rect.right, rect.bottom),
            border=self._border(),
            maximized=self._maximized(hwnd),
        )


def install_custom_frame(hwnd: int) -> CustomFrame | None:
    """Take the caption from ``hwnd``, or leave the window exactly as it was."""

    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes

        style = _user32().GetWindowLongPtrW(wintypes.HWND(hwnd), GWL_STYLE)
        if not style & WS_THICKFRAME:
            # Without a sizing frame there is nothing worth keeping, and taking
            # the caption would leave a window that cannot be resized at all.
            log.info("Keeping the OS title bar: the window has no sizing frame")
            return None
        frame = CustomFrame(hwnd)
        frame.install()
    except Exception:  # noqa: BLE001 - a native boundary, and a losable feature
        log.exception("Keeping the OS title bar: the custom frame could not be installed")
        return None
    return frame


def note_frame_problem(message: str) -> None:
    """Record a frame problem on this module's logger.

    ``launchers.desktop`` has no logger of its own, and giving it one would mean
    adding an import to a file two sessions are editing. The frame's problems
    belong on the frame's logger anyway.
    """

    log.warning(message)


def enable_non_client_regions(core_webview2: Any) -> bool:
    """Let CSS ``app-region`` reach Windows, so dragging is the real thing.

    Without this the top bar is inert to the pointer and the window could only be
    moved by a caption it no longer has. With it, Windows treats the region as
    caption, and every gesture that implies -- snap, snap layouts, shake,
    double-click to maximize, the window menu on right-click -- arrives free.
    """

    try:
        core_webview2.Settings.IsNonClientRegionSupportEnabled = True
    except Exception:  # noqa: BLE001 - older runtimes simply lack the property
        log.warning(
            "This WebView2 runtime has no non-client region support; "
            "the window will keep its OS title bar"
        )
        return False
    return True
